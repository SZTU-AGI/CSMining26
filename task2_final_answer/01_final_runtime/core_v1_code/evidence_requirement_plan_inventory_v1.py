#!/usr/bin/env python3
"""FRECA EvidenceRequirement plan inventory v1.

Zero-API census for CP1..CP41 EvidenceRequirement plans.

Checks only already-existing artifacts:
- canonical contract exists;
- CandidateLedger exists;
- RuleSetRelation exists;
- EvidenceRequirement plan exists;
- live evidence_reasoning_v2.validate_evidence_requirements accepts the plan;
- all EvidenceRequirement atom_ids belong to the current satisfaction AST;
- every satisfaction ATOM is covered by at least one EvidenceRequirement.

This script does NOT compile/recompile plans and does NOT call any model/API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import freca_core_v1 as core
import evidence_reasoning_v2 as er


CP_IDS = [f"CP{i}" for i in range(1, 42)]


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unwrap_contract(bundle: dict) -> dict:
    value = bundle.get("contract")
    return value if isinstance(value, dict) else bundle


def satisfaction_atoms(expr: dict | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def walk(node):
        if not isinstance(node, dict):
            raise ValueError("Missing/invalid satisfaction expression")
        op = str(node.get("op") or "").upper()

        if op == "ATOM":
            atom_id = str(node.get("atom_id") or "").strip()
            if not atom_id:
                raise ValueError("ATOM node missing atom_id")
            if atom_id not in seen:
                seen.add(atom_id)
                out.append(atom_id)
            return

        if op == "ALL":
            children = node.get("children")
            if not isinstance(children, list) or not children:
                raise ValueError("ALL node requires non-empty children")
            for child in children:
                walk(child)
            return

        raise ValueError(
            f"Observed production inventory supports ATOM/ALL satisfaction; got {op!r}"
        )

    walk(expr)
    return out


def audit_one(contract_dir: Path, cp_id: str) -> dict:
    paths = {
        "contract": contract_dir / f"{cp_id}.json",
        "ledger": contract_dir / f"{cp_id}_candidate_ledger.json",
        "relation": contract_dir / f"{cp_id}_rule_set_relation.json",
        "plan": contract_dir / f"{cp_id}_evidence_requirements.json",
    }

    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        return {
            "cp_id": cp_id,
            "status": "MISSING_PLAN" if missing == ["plan"] else "MISSING_DEPENDENCY",
            "missing": missing,
            "paths": {k: str(v) for k, v in paths.items()},
        }

    try:
        cp = core.get_cp(cp_id)
        contract_bundle = load_json(paths["contract"])
        contract = unwrap_contract(contract_bundle)
        ledger = load_json(paths["ledger"])
        relation = load_json(paths["relation"])
        plan = load_json(paths["plan"])

        # Re-run the CURRENT live validator without any model call.
        validated = er.validate_evidence_requirements(
            plan,
            cp,
            contract,
            ledger,
            relation,
        )

        atom_refs = satisfaction_atoms(contract.get("satisfaction"))
        atom_set = set(atom_refs)

        reqs = validated.get("requirements") or []
        by_atom = {atom_id: [] for atom_id in atom_refs}
        outside = []

        for req in reqs:
            atom_id = str(req.get("atom_id") or "")
            rid = str(req.get("requirement_id") or "")
            if atom_id not in atom_set:
                outside.append(rid)
            else:
                by_atom[atom_id].append(req)

        uncovered = [
            atom_id for atom_id, rows in by_atom.items()
            if not rows
        ]

        decisive_by_atom = {
            atom_id: sum(
                1 for row in rows
                if row.get("decisiveness") == "DECISIVE"
            )
            for atom_id, rows in by_atom.items()
        }

        if outside:
            status = "INVALID_ATOM_TARGET"
        elif uncovered:
            status = "ATOM_COVERAGE_GAP"
        else:
            status = "VALID"

        return {
            "cp_id": cp_id,
            "status": status,
            "plan_sha256": sha256_file(paths["plan"]),
            "requirement_count": len(reqs),
            "decisive_count": sum(
                1 for r in reqs if r.get("decisiveness") == "DECISIVE"
            ),
            "corroboration_only_count": sum(
                1 for r in reqs if r.get("decisiveness") == "CORROBORATION_ONLY"
            ),
            "satisfaction_atom_ids": atom_refs,
            "requirements_by_atom": {
                atom_id: [
                    str(r.get("requirement_id") or "")
                    for r in rows
                ]
                for atom_id, rows in by_atom.items()
            },
            "decisive_count_by_atom": decisive_by_atom,
            "uncovered_satisfaction_atom_ids": uncovered,
            "requirements_outside_satisfaction_ast": outside,
            "paths": {k: str(v) for k, v in paths.items()},
        }

    except Exception as exc:
        return {
            "cp_id": cp_id,
            "status": "INVALID_PLAN",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "paths": {k: str(v) for k, v in paths.items()},
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results_v2/evidence_requirement_plan_inventory_v1.json"
        ),
    )
    args = p.parse_args()

    rows = [audit_one(args.contract_dir, cp_id) for cp_id in CP_IDS]
    counts = Counter(row["status"] for row in rows)

    valid = [row["cp_id"] for row in rows if row["status"] == "VALID"]
    missing = [
        row["cp_id"] for row in rows
        if row["status"] in {"MISSING_PLAN", "MISSING_DEPENDENCY"}
    ]
    review = [
        row["cp_id"] for row in rows
        if row["status"] not in {"VALID", "MISSING_PLAN", "MISSING_DEPENDENCY"}
    ]

    report = {
        "schema": "freca-evidence-requirement-plan-inventory-v1",
        "contract_dir": str(args.contract_dir),
        "expected_cp_count": 41,
        "valid_plan_count": len(valid),
        "all_41_valid_and_atom_covered": len(valid) == 41,
        "status_counts": dict(sorted(counts.items())),
        "valid_cp_ids": valid,
        "missing_cp_ids": missing,
        "review_cp_ids": review,
        "rows": rows,
        "api_called": False,
        "answer_comparator_used": False,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 80)
    print("FRECA EVIDENCE REQUIREMENT PLAN INVENTORY V1")
    print("=" * 80)
    print("Valid plans:", len(valid), "/ 41")
    print("ALL 41 VALID + ATOM-COVERED:", report["all_41_valid_and_atom_covered"])
    print()
    print("Status counts:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")

    print()
    print("Per CP:")
    for row in rows:
        extra = ""
        if row["status"] == "VALID":
            extra = (
                f" | ERs={row['requirement_count']}"
                f" | atoms={','.join(row['satisfaction_atom_ids'])}"
            )
        elif row.get("error"):
            extra = f" | {row['error_type']}: {row['error']}"
        elif row.get("missing"):
            extra = " | missing=" + ",".join(row["missing"])
        elif row.get("uncovered_satisfaction_atom_ids"):
            extra = (
                " | uncovered="
                + ",".join(row["uncovered_satisfaction_atom_ids"])
            )
        print(f"  {row['cp_id']}: {row['status']}{extra}")

    print()
    print("API called: False")
    print("Answer comparator used: False")
    print("Saved:", args.output)


if __name__ == "__main__":
    main()
