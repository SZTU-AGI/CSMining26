#!/usr/bin/env python3
"""FRECA post-contract-gate production readiness gate v3.

Deterministically composes already-produced audit artifacts:

  production_freeze_audit_v2
  + contract_shape_inventory_v1_3
  + multi_atom_contract_audit_v1_3
  + one or more no-API end_to_end_semantic_replay_v1 outputs

This script:
  - calls no API,
  - does not mutate contracts/core artifacts,
  - does not use an answer comparator,
  - does not reinterpret legal/factual evidence.

"ready_for_full_4100" means the blockers explicitly frozen by
production_freeze_audit_v2 have now been discharged under this gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_DIRECT_STATUS = "CURRENT_CORE_DIRECTLY_REPRESENTABLE"
EXPECTED_SAT_OPS = {"ATOM", "ALL"}
EXPECTED_MULTI_CP_IDS = {"CP6", "CP23", "CP27", "CP30", "CP37", "CP40"}


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result(checks: dict[str, bool], details: dict[str, Any]) -> dict:
    return {
        "checks": checks,
        "details": details,
        "pass": all(checks.values()),
    }


def audit_production_freeze(x: dict) -> dict:
    checks_map = x.get("checks")
    all_checks_true = (
        isinstance(checks_map, dict)
        and bool(checks_map)
        and all(v is True for v in checks_map.values())
    )
    checks = {
        "schema_v2": x.get("schema") == "freca-core-production-freeze-audit-v2",
        "all_pass": x.get("all_pass") is True,
        "all_individual_checks_true": all_checks_true,
        "answer_comparator_not_used": x.get("answer_comparator_used") is False,
        "ready_for_contract_shape_inventory": (
            x.get("ready_for_contract_shape_inventory") is True
        ),
    }
    return result(checks, {
        "legacy_ready_for_full_4100": x.get("ready_for_full_4100"),
        "legacy_full_4100_blocker": x.get("full_4100_blocker"),
        "individual_checks": checks_map,
    })


def audit_inventory(x: dict) -> dict:
    rows = x.get("contracts")
    rows = rows if isinstance(rows, list) else []
    status_counts = x.get("status_counts")
    status_counts = status_counts if isinstance(status_counts, dict) else {}
    registry = x.get("cp_registry")
    registry = registry if isinstance(registry, dict) else {}
    runtime = x.get("runtime_capability")
    runtime = runtime if isinstance(runtime, dict) else {}

    cp_ids = {str(r.get("cp_id")) for r in rows if isinstance(r, dict)}
    expected_ids = {f"CP{i}" for i in range(1, 42)}

    all_rows_direct = (
        len(rows) == 41
        and all(
            isinstance(r, dict)
            and r.get("current_core_status") == EXPECTED_DIRECT_STATUS
            and not (r.get("blockers") or [])
            for r in rows
        )
    )

    supported_ops = set(runtime.get("supported_satisfaction_ops") or [])

    checks = {
        "schema_v1_3": x.get("schema") == "freca-core-contract-shape-inventory-v1.3",
        "canonical_41_present": (
            x.get("all_41_canonical_contracts_present") is True
            and x.get("canonical_contract_count") == 41
            and cp_ids == expected_ids
        ),
        "registry_41_resolved": (
            registry.get("all_41_resolved") is True
            and registry.get("resolved_count") == 41
            and not (registry.get("registry_missing_cp_ids") or [])
            and not (registry.get("registry_criterion_missing_cp_ids") or [])
        ),
        "all_41_directly_representable": (
            status_counts.get(EXPECTED_DIRECT_STATUS) == 41
            and all_rows_direct
        ),
        "recursive_all_installed": runtime.get("recursive_all_installed") is True,
        "multi_atom_projection_supported": (
            runtime.get("multi_atom_projection_supported") is True
        ),
        "satisfaction_ops_exactly_atom_all": supported_ops == EXPECTED_SAT_OPS,
        "answer_comparator_not_used": x.get("answer_comparator_used") is False,
    }
    return result(checks, {
        "status_counts": status_counts,
        "operator_cp_counts": x.get("operator_cp_counts"),
        "runtime_capability": runtime,
    })


def audit_multi_atom(x: dict) -> dict:
    rows = x.get("contracts")
    rows = rows if isinstance(rows, list) else []
    runtime = x.get("runtime_preflight")
    runtime = runtime if isinstance(runtime, dict) else {}

    cp_ids = {str(r.get("cp_id")) for r in rows if isinstance(r, dict)}

    semantic_rows_ok = bool(rows) and all(
        isinstance(r, dict)
        and r.get("all_support") == "PROVEN_COMPLIANT"
        and r.get("one_attack") == "PROVEN_NON_COMPLIANT"
        and r.get("one_both") == "CONFLICTING"
        for r in rows
    )

    checks = {
        "schema_v1_3": x.get("schema") == "freca-multi-atom-contract-audit-v1-3",
        "all_pass": x.get("all_pass") is True,
        "exact_observed_multi_atom_set": cp_ids == EXPECTED_MULTI_CP_IDS,
        "semantic_rows_pass": semantic_rows_ok,
        "helper_four_valued_fix": runtime.get("helper_four_valued_fix") is True,
        "argument_core_delegates_to_helper": (
            runtime.get("argument_core_delegates_to_helper") is True
        ),
        "api_not_called": x.get("api_called") is False,
        "answer_comparator_not_used": x.get("answer_comparator_used") is False,
    }
    return result(checks, {
        "cp_ids": sorted(cp_ids),
        "runtime_preflight": runtime,
    })


def audit_e2e(x: dict) -> dict:
    outcome = x.get("argument_evaluation_bundle")
    outcome = outcome if isinstance(outcome, dict) else {}
    evaluations = outcome.get("evaluations")
    evaluations = evaluations if isinstance(evaluations, list) else []
    ev = evaluations[0] if evaluations and isinstance(evaluations[0], dict) else {}

    fold = x.get("fold_decision")
    fold = fold if isinstance(fold, dict) else {}

    internal = ev.get("internal_outcome")
    fold_semantics_ok = True
    if internal == "UNKNOWN":
        fold_semantics_ok = (
            fold.get("benchmark_fallback") is True
            and fold.get("finality") == "UNKNOWN_BENCHMARK_FALLBACK"
            and str(fold.get("label")) == "0"
        )

    checks = {
        "schema_v1": x.get("schema") == "freca-core-end-to-end-semantic-replay-v1",
        "case_id_present": bool(str(x.get("case_id") or "").strip()),
        "cp_id_present": bool(str(x.get("cp_id") or "").strip()),
        "proof_present": isinstance(x.get("proof"), dict),
        "argument_evaluation_present": bool(ev),
        "fold_decision_present": bool(fold),
        "unknown_fold_consistent_if_applicable": fold_semantics_ok,
        "api_not_called": x.get("api_called") is False,
        "answer_comparator_not_used": x.get("answer_comparator_used") is False,
    }
    return result(checks, {
        "case_id": x.get("case_id"),
        "cp_id": x.get("cp_id"),
        "applicability_state": ev.get("applicability_state"),
        "satisfaction_state": ev.get("satisfaction_state"),
        "violation_state": ev.get("violation_state"),
        "internal_outcome": internal,
        "fold_label": fold.get("label"),
        "fold_finality": fold.get("finality"),
        "benchmark_fallback": fold.get("benchmark_fallback"),
    })


def build_report(
    *,
    production_freeze_path: Path,
    inventory_path: Path,
    multi_atom_path: Path,
    e2e_paths: list[Path],
) -> dict:
    if not e2e_paths:
        raise ValueError("At least one --e2e replay artifact is required")

    freeze = audit_production_freeze(load_json(production_freeze_path))
    inventory = audit_inventory(load_json(inventory_path))
    multi = audit_multi_atom(load_json(multi_atom_path))

    e2e_rows = []
    for path in e2e_paths:
        row = audit_e2e(load_json(path))
        row["path"] = str(path)
        row["sha256"] = sha256_file(path)
        e2e_rows.append(row)

    checks = {
        "production_freeze_v2_pass": freeze["pass"],
        "contract_shape_41_of_41_pass": inventory["pass"],
        "multi_atom_semantics_pass": multi["pass"],
        "staged_e2e_replays_pass": (
            len(e2e_rows) >= 2
            and all(row["pass"] for row in e2e_rows)
        ),
        "no_answer_comparator_anywhere": (
            freeze["checks"]["answer_comparator_not_used"]
            and inventory["checks"]["answer_comparator_not_used"]
            and multi["checks"]["answer_comparator_not_used"]
            and all(
                row["checks"]["answer_comparator_not_used"]
                for row in e2e_rows
            )
        ),
        "no_api_in_semantic_audits": (
            multi["checks"]["api_not_called"]
            and all(row["checks"]["api_not_called"] for row in e2e_rows)
        ),
    }

    ready = all(checks.values())

    return {
        "schema": "freca-core-production-readiness-gate-v3",
        "scope": (
            "POST_CONTRACT_GATE_READINESS_FOR_FULL_4100_UNDER_FROZEN_V2_BLOCKERS"
        ),
        "ready_for_full_4100": ready,
        "all_pass": ready,
        "checks": checks,
        "evidence": {
            "production_freeze_v2": {
                "path": str(production_freeze_path),
                "sha256": sha256_file(production_freeze_path),
                "audit": freeze,
            },
            "contract_shape_inventory_v1_3": {
                "path": str(inventory_path),
                "sha256": sha256_file(inventory_path),
                "audit": inventory,
            },
            "multi_atom_contract_audit_v1_3": {
                "path": str(multi_atom_path),
                "sha256": sha256_file(multi_atom_path),
                "audit": multi,
            },
            "end_to_end_replays": e2e_rows,
        },
        "legacy_v2_blocker_discharged": ready,
        "legacy_v2_blocker": freeze["details"].get("legacy_full_4100_blocker"),
        "api_called": False,
        "answer_comparator_used": False,
        "upstream_artifacts_mutated": False,
    }


def self_test() -> None:
    # Focused invariant: UNKNOWN must remain benchmark fallback at Fold.
    good = {
        "schema": "freca-core-end-to-end-semantic-replay-v1",
        "case_id": "CASE",
        "cp_id": "CP12",
        "proof": {},
        "argument_evaluation_bundle": {
            "evaluations": [{
                "internal_outcome": "UNKNOWN",
                "applicability_state": "TRUE",
                "satisfaction_state": "UNKNOWN",
                "violation_state": "UNKNOWN",
            }]
        },
        "fold_decision": {
            "label": "0",
            "finality": "UNKNOWN_BENCHMARK_FALLBACK",
            "benchmark_fallback": True,
        },
        "api_called": False,
        "answer_comparator_used": False,
    }
    assert audit_e2e(good)["pass"] is True

    bad = json.loads(json.dumps(good))
    bad["fold_decision"]["benchmark_fallback"] = False
    assert audit_e2e(bad)["pass"] is False

    print("production_readiness_gate_v3 self-tests: PASS")
    print("  UNKNOWN fold fallback invariant")
    print("  no API / no comparator gate semantics")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--production-freeze", type=Path)
    p.add_argument("--contract-inventory", type=Path)
    p.add_argument("--multi-atom-audit", type=Path)
    p.add_argument("--e2e", type=Path, action="append", default=[])
    p.add_argument("--output", type=Path)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    if args.self_test:
        self_test()
        if (
            args.production_freeze is None
            and args.contract_inventory is None
            and args.multi_atom_audit is None
            and not args.e2e
        ):
            return

    required = {
        "--production-freeze": args.production_freeze,
        "--contract-inventory": args.contract_inventory,
        "--multi-atom-audit": args.multi_atom_audit,
        "--output": args.output,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        p.error("missing required arguments: " + ", ".join(missing))
    if len(args.e2e) < 2:
        p.error("at least two --e2e replay artifacts are required")

    report = build_report(
        production_freeze_path=args.production_freeze,
        inventory_path=args.contract_inventory,
        multi_atom_path=args.multi_atom_audit,
        e2e_paths=args.e2e,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 80)
    print("FRECA PRODUCTION READINESS GATE V3")
    print("=" * 80)
    for name, passed in report["checks"].items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    print()
    print("ALL PASS:", report["all_pass"])
    print("READY FOR FULL 4100:", report["ready_for_full_4100"])
    print("LEGACY V2 BLOCKER DISCHARGED:", report["legacy_v2_blocker_discharged"])
    print("API called: False")
    print("Answer comparator used: False")
    print("Saved:", args.output)

    if not report["all_pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
