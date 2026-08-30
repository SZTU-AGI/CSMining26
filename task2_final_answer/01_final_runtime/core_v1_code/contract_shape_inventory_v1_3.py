#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, json, re
from pathlib import Path

CANONICAL_RE = re.compile(r"^CP([1-9]|[1-3][0-9]|4[01])\.json$")

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def unwrap_contract(bundle: dict) -> dict:
    value = bundle.get("contract")
    return value if isinstance(value, dict) else bundle

def expr_shape(expr) -> str:
    if not isinstance(expr, dict):
        return "MISSING"
    op = str(expr.get("op") or "UNKNOWN").upper()
    if op == "CONST":
        return f"CONST:{expr.get('value')}"
    if op == "ATOM":
        return "ATOM"
    children = expr.get("children")
    if isinstance(children, list):
        return f"{op}(" + ",".join(expr_shape(c) for c in children) + ")"
    child = expr.get("child")
    if isinstance(child, dict):
        return f"{op}({expr_shape(child)})"
    return op

def collect_ops(expr, out=None):
    out = [] if out is None else out
    if not isinstance(expr, dict):
        out.append("MISSING")
        return out
    op = str(expr.get("op") or "UNKNOWN").upper()
    out.append(op)
    for child in expr.get("children") or []:
        collect_ops(child, out)
    if isinstance(expr.get("child"), dict):
        collect_ops(expr["child"], out)
    return out

def detect_runtime_capability(core_dir: Path) -> dict:
    files = {
        "evidence": core_dir / "evidence_reasoning_v2.py",
        "argument": core_dir / "argument_core_v1.py",
        "adapter": core_dir / "core_outcome_adapter_v1.py",
    }
    markers = {
        "evidence": "FRECA MULTI-ATOM EVIDENCE OVERRIDE V1",
        "argument": "FRECA MULTI-ATOM ARGUMENT OVERRIDE V1",
        "adapter": "FRECA RECURSIVE ALL ADAPTER OVERRIDE V1",
    }

    marker_status = {}
    for key, path in files.items():
        if not path.exists():
            marker_status[key] = False
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        marker_status[key] = markers[key] in text

    recursive_all_installed = all(marker_status.values())

    return {
        "recursive_all_installed": recursive_all_installed,
        "markers": marker_status,
        "supported_applicability_ops": ["CONST"],
        "supported_non_applicability_ops": ["CONST"],
        "supported_satisfaction_ops": (
            ["ATOM", "ALL"] if recursive_all_installed else ["ATOM"]
        ),
        "multi_atom_projection_supported": recursive_all_installed,
    }


def classify_capability(contract: dict, runtime_capability: dict):
    blockers = []
    app = contract.get("applicability")
    na = contract.get("non_applicability")
    sat = contract.get("satisfaction")

    if not (isinstance(app, dict) and str(app.get("op")).upper() == "CONST"):
        blockers.append("NON_CONST_APPLICABILITY")

    if not (isinstance(na, dict) and str(na.get("op")).upper() == "CONST"):
        blockers.append("NON_CONST_NON_APPLICABILITY")

    supported_sat = set(
        runtime_capability.get("supported_satisfaction_ops") or ["ATOM"]
    )
    unsupported = set(collect_ops(sat)) - supported_sat
    if unsupported:
        blockers.append(
            "UNSUPPORTED_SATISFACTION_OPERATORS:"
            + ",".join(sorted(unsupported))
        )

    atom_count = len(contract.get("atoms") or [])
    if (
        atom_count != 1
        and not runtime_capability.get(
            "multi_atom_projection_supported",
            False,
        )
    ):
        blockers.append(
            "MULTI_ATOM_CONTRACT_REQUIRES_GENERAL_GRAPH_PROJECTION"
        )

    return (
        ("NEEDS_PRODUCTION_ADAPTER_WORK", blockers)
        if blockers else
        ("CURRENT_CORE_DIRECTLY_REPRESENTABLE", [])
    )

def cp_number(path: Path) -> int:
    return int(CANONICAL_RE.fullmatch(path.name).group(1))

def registry_status():
    try:
        import freca_core_v1 as core
    except Exception as exc:
        return [
            {"cp_id": f"CP{i}", "registry_resolved": False,
             "criterion_present": False, "error": f"IMPORT_FAILED:{exc}"}
            for i in range(1, 42)
        ]
    rows = []
    for i in range(1, 42):
        cp_id = f"CP{i}"
        try:
            cp = core.get_cp(cp_id)
            criterion = cp.get("criterion") if isinstance(cp, dict) else None
            rows.append({
                "cp_id": cp_id,
                "registry_resolved": True,
                "criterion_present": bool(str(criterion or "").strip()),
                "error": None,
            })
        except Exception as exc:
            rows.append({
                "cp_id": cp_id,
                "registry_resolved": False,
                "criterion_present": False,
                "error": str(exc),
            })
    return rows


def run_self_tests():
    positives = ["CP1.json", "CP12.json", "CP41.json"]
    negatives = [
        "CP0.json",
        "CP42.json",
        "CP12_candidate_ledger.json",
        "CP12_evidence_requirements.json",
        "CP12.json.bak",
        "xCP12.json",
    ]

    for name in positives:
        assert CANONICAL_RE.fullmatch(name), name

    for name in negatives:
        assert not CANONICAL_RE.fullmatch(name), name

    print("contract_shape_inventory_v1_3 self-tests: PASS")
    print("  CP1/CP12/CP41 canonical names accepted")
    print("  side artifacts / out-of-range CP ids rejected")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--contract-dir", type=Path, default=Path("contracts_v2"))
    p.add_argument("--core-dir", type=Path, default=Path("."))
    p.add_argument("--output", type=Path)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    if args.self_test:
        run_self_tests()
        if not args.contract_dir.exists():
            return

    files = sorted(
        [
            x for x in args.contract_dir.iterdir()
            if x.is_file() and CANONICAL_RE.fullmatch(x.name)
        ],
        key=cp_number,
    )

    present = {cp_number(x) for x in files}
    missing = [i for i in range(1, 42) if i not in present]

    runtime_capability = detect_runtime_capability(args.core_dir)

    rows = []
    for path in files:
        bundle = load_json(path)
        contract = unwrap_contract(bundle)
        status, blockers = classify_capability(contract, runtime_capability)
        file_cp = f"CP{cp_number(path)}"
        cp_meta = bundle.get("cp") if isinstance(bundle.get("cp"), dict) else {}
        embedded = contract.get("cp_id") or cp_meta.get("cp_id")
        rows.append({
            "cp_id": file_cp,
            "embedded_cp_id": embedded,
            "filename_matches_embedded_cp_id": embedded in (None, file_cp),
            "path": str(path),
            "atom_count": len(contract.get("atoms") or []),
            "applicability_shape": expr_shape(contract.get("applicability")),
            "satisfaction_shape": expr_shape(contract.get("satisfaction")),
            "non_applicability_shape": expr_shape(contract.get("non_applicability")),
            "operators": sorted(set(
                collect_ops(contract.get("applicability"))
                + collect_ops(contract.get("satisfaction"))
                + collect_ops(contract.get("non_applicability"))
            )),
            "current_core_status": status,
            "blockers": blockers,
        })

    status_counts = collections.Counter(r["current_core_status"] for r in rows)
    operator_counts = collections.Counter(
        op for r in rows for op in r["operators"]
    )

    registry = registry_status()
    registry_missing = [r["cp_id"] for r in registry if not r["registry_resolved"]]
    criterion_missing = [
        r["cp_id"] for r in registry
        if r["registry_resolved"] and not r["criterion_present"]
    ]

    result = {
        "schema": "freca-core-contract-shape-inventory-v1.3",
        "canonical_filename_rule": CANONICAL_RE.pattern,
        "contract_dir": str(args.contract_dir),
        "canonical_contract_count": len(rows),
        "expected_contract_count": 41,
        "all_41_canonical_contracts_present": len(rows) == 41 and not missing,
        "missing_canonical_contract_ids": [f"CP{i}" for i in missing],
        "status_counts": dict(status_counts),
        "operator_cp_counts": dict(sorted(operator_counts.items())),
        "contracts": rows,
        "cp_registry": {
            "resolved_count": sum(r["registry_resolved"] for r in registry),
            "all_41_resolved": not registry_missing,
            "registry_missing_cp_ids": registry_missing,
            "registry_criterion_missing_cp_ids": criterion_missing,
            "rows": registry,
        },
        "runtime_capability": runtime_capability,
        "answer_comparator_used": False,
    }

    print("=" * 78)
    print("FRECA CANONICAL CONTRACT SHAPE INVENTORY V1.3")
    print("=" * 78)
    print("Canonical contracts:", len(rows), "/ 41")
    print("Core CP registry resolved:", result["cp_registry"]["resolved_count"], "/ 41")
    if missing:
        print("Missing canonical contracts:", ", ".join(f"CP{i}" for i in missing))
    if registry_missing:
        print("Registry unresolved:", ", ".join(registry_missing))
    print()
    print("Runtime adapter:")
    print("  recursive ALL installed:", runtime_capability["recursive_all_installed"])
    print("  satisfaction ops:", ", ".join(runtime_capability["supported_satisfaction_ops"]))
    print("  multi-atom projection:", runtime_capability["multi_atom_projection_supported"])
    print()
    print("Capability:")
    for k, v in sorted(status_counts.items()):
        print(f"  {k}: {v}")
    if not status_counts:
        print("  <none>")
    print()
    print("Operators:")
    for k, v in sorted(operator_counts.items()):
        print(f"  {k}: {v} CP(s)")
    if not operator_counts:
        print("  <none>")
    print()
    print("Canonical per-CP:")
    for row in rows:
        print(
            f"  {row['cp_id']}: atoms={row['atom_count']} | "
            f"app={row['applicability_shape']} | "
            f"sat={row['satisfaction_shape']} | "
            f"na={row['non_applicability_shape']} | "
            f"{row['current_core_status']}"
        )
        if row["blockers"]:
            print("    blockers:", ", ".join(row["blockers"]))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print()
        print("Saved:", args.output)

if __name__ == "__main__":
    main()
