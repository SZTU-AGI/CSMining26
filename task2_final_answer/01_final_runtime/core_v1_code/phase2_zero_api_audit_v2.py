#!/usr/bin/env python3
"""Machine-readable Phase 2 implementation audit over a real zero-API run.

This is intentionally not the Phase 3 synthetic/mutation suite.  It checks
that the V2 interfaces requested in Sections 5.1--5.5 were exercised or
explicitly terminated by the saved real-coordinate smoke run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import production_repair_dispatcher_v2 as dispatcher
import production_runner_v2 as runner
from procedure_executor_v2 import COVERAGE_PURPOSES, validate_procedure_artifact


REQUIRED_PROCEDURE_FIELDS = {
    "coverage_purpose",
    "requirement_id",
    "proposition_id",
    "candidate_universe_identity",
    "required_retrieval_channels",
    "executed_retrieval_channels",
    "candidate_disposition_counts",
    "deterministic_exclusions_and_reasons",
    "model_assessed_candidate_count",
    "parse_readability_gaps",
    "identity_time_exclusions",
    "counterevidence_scan_result",
    "completion_status",
    "completion_basis_ids",
    "procedure_artifact_sha256",
}

BLOCKER_ROUTES = {
    "NO_DIRECT_SUPPORT_BASIS": "ALIGN_NEXT_CANDIDATE_BATCH",
    "NO_EXPLICIT_VIOLATION_BASIS": "ALIGN_NEXT_CANDIDATE_BATCH",
    "NO_TEMPORAL_BASIS_ROWS": "ALIGN_NEXT_CANDIDATE_BATCH",
    "NO_RELIABILITY_BASIS_ROWS": "ALIGN_NEXT_CANDIDATE_BATCH",
    "IDENTITY_GATE_FAILED": "ALIGN_NEXT_CANDIDATE_BATCH",
    "TEMPORAL_SCOPE_UNRESOLVED": "RESOLVE_TIME",
    "TEMPORAL_SCOPE_EXPLICITLY_FAILED": "RESOLVE_TIME",
    "INFORMATION_RELIABILITY_UNRESOLVED": "ASSESS_INFORMATION_RELIABILITY",
    "INFORMATION_RELIABILITY_FAILED": "ASSESS_INFORMATION_RELIABILITY",
    "COVERAGE_INCOMPLETE": "COMPLETE_TARGETED_COVERAGE",
    "CONTRADICTION_BLOCKING": "ALIGN_NEXT_CANDIDATE_BATCH",
    "CONTRADICTION_NOT_DEFEATED": "ALIGN_NEXT_CANDIDATE_BATCH",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def save(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--v1-tree-digest", required=True)
    parser.add_argument("--expected-v1-tree-digest", required=True)
    parser.add_argument("--bridge-initial-dir", type=Path, required=True)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    initial = run_dir / "initial"
    repair = run_dir / "repair" / "round-1"
    proof = load(initial / "proof_standard_v2.json")
    coverage = load(initial / "coverage_v2.json")
    applicability = load(initial / "proof_gate_applicability_v2.json")
    goals = load(initial / "open_goals_v2.json")
    plan = load(repair / "repair_plan_v2.json")
    bundle = load(repair / "round_bundle_v2.json")
    gates = load(repair / "hard_gates_v2.json")
    stop = load(repair / "stop_decision_v2.json")
    run_report = load(run_dir / "run_report_v2.json")

    blockers = sorted(
        {
            code
            for row in proof.get("requirement_reports", [])
            for code in row.get("failure_codes", [])
        }
    )
    terminal_codes = {
        str(row.get("blocker_code"))
        for row in plan.get("terminal_limitations", [])
        if row.get("blocker_code")
    }
    route_rows = []
    for blocker in blockers:
        action = BLOCKER_ROUTES.get(blocker)
        terminal = blocker in terminal_codes
        route_rows.append(
            {
                "blocker": blocker,
                "executable_action_type": action,
                "explicit_terminal_limitation": terminal,
                "route_present": bool(action or terminal),
            }
        )

    executions = bundle.get("action_executions", [])
    procedures = [
        artifact
        for execution in executions
        for artifact in execution.get("targeted_coverage_procedure_artifacts", [])
    ]
    procedure_rows = []
    for artifact in procedures:
        valid, validation_reasons = validate_procedure_artifact(artifact)
        missing_fields = sorted(REQUIRED_PROCEDURE_FIELDS - set(artifact))
        safe_incomplete = bool(
            artifact.get("completion_status") == "INCOMPLETE"
            and (
                artifact.get("parse_readability_gaps")
                or (artifact.get("candidate_disposition_counts") or {}).get("unassessed")
                or artifact.get("reason_codes")
            )
        )
        procedure_rows.append(
            {
                "procedure_artifact_id": artifact.get("procedure_artifact_id"),
                "need_id": artifact.get("need_id"),
                "coverage_purpose": artifact.get("coverage_purpose"),
                "completion_status": artifact.get("completion_status"),
                "missing_required_fields": missing_fields,
                "validator_pass": valid,
                "validator_reason_codes": validation_reasons,
                "incomplete_state_preserved_for_material_gap": safe_incomplete,
            }
        )

    action_types = [row.get("action_type") for row in plan.get("actions", [])]
    executed_types = [row.get("action_type") for row in executions]
    model_limitations = [
        row
        for row in plan.get("terminal_limitations", [])
        if row.get("reason_code") == "MODEL_ACTION_NOT_ADMITTED_IN_ZERO_API_MODE"
    ]

    bridge_dir = args.bridge_initial_dir.resolve()
    bridge_root = {
        "requirement_result": load(bridge_dir / "requirement_result.json"),
        "coverage": load(bridge_dir / "layer7" / "coverage_v1_1.json"),
        "procedure": load(bridge_dir / "layer7" / "procedure_objective_v1.json"),
        "open_goals": load(bridge_dir / "layer7" / "open_goals_v1.json"),
    }
    policy = load(Path("production_repair_policy_v2.json"))
    bridge_plan = dispatcher.build_repair_plan(
        root=bridge_root,
        policy=policy,
        round_index=1,
        allow_model_actions=False,
    )
    bridge_bundle = dispatcher.execute_repair_plan(
        plan=bridge_plan,
        root=bridge_root,
    )
    _bridge_merged, bridge_diagnostics = runner.merge_round_artifacts(
        bridge_root["requirement_result"], bridge_bundle
    )
    bridge_planned_types = [
        row.get("action_type") for row in bridge_plan.get("actions", [])
    ]
    bridge_executed_types = [
        row.get("action_type")
        for row in bridge_bundle.get("action_executions", [])
    ]
    bridge_temporal_count = sum(
        len(row.get("temporal_assessments", []))
        for row in bridge_bundle.get("action_executions", [])
    )
    bridge_reliability_count = sum(
        len(row.get("information_reliability_assessments", []))
        for row in bridge_bundle.get("action_executions", [])
    )
    checks = {
        "coverage_purposes_typed": all(
            row.get("coverage_purpose") in COVERAGE_PURPOSES
            for row in coverage.get("need_reports", [])
        ),
        "targeted_procedures_executed": bool(procedures),
        "targeted_procedure_fields_complete": all(
            not row["missing_required_fields"] for row in procedure_rows
        ),
        "targeted_procedure_artifacts_valid": all(
            row["validator_pass"] for row in procedure_rows
        ),
        "material_gaps_did_not_become_complete": all(
            row["incomplete_state_preserved_for_material_gap"]
            for row in procedure_rows
        ),
        "temporal_classifications_typed": all(
            row.get("state")
            in {"TEMPORAL_REQUIRED", "TEMPORAL_NOT_REQUIRED", "TEMPORAL_UNRESOLVED"}
            for row in applicability.get("temporal_classifications", [])
        ),
        "temporal_unresolved_has_terminal_route": all(
            row.get("requirement_id")
            in {
                item.get("requirement_id")
                for item in goals.get("terminal_limitations", [])
                if item.get("blocker_code") == "TEMPORAL_REQUIREMENT_UNRESOLVED"
            }
            for row in applicability.get("temporal_classifications", [])
            if row.get("state") == "TEMPORAL_UNRESOLVED"
        ),
        "missing_information_never_became_pass": applicability.get(
            "missing_information_became_pass"
        )
        is False,
        "not_required_never_collapsed_to_pass": applicability.get(
            "not_required_collapsed_to_pass"
        )
        is False,
        "all_planned_actions_executed": plan.get("actions") is not None
        and bundle.get("round_execution_complete") is True
        and action_types == executed_types,
        "zero_api_model_actions_explicitly_limited": bool(model_limitations),
        "resolve_time_dispatch_and_merge_connected": (
            "RESOLVE_TIME" in bridge_planned_types
            and "RESOLVE_TIME" in bridge_executed_types
            and bridge_temporal_count > 0
            and len(bridge_diagnostics.get("attached_temporal_assessment_ids", []))
            == bridge_temporal_count
        ),
        "reliability_dispatch_and_merge_connected": (
            "ASSESS_INFORMATION_RELIABILITY" in bridge_planned_types
            and "ASSESS_INFORMATION_RELIABILITY" in bridge_executed_types
            and bridge_reliability_count > 0
            and len(
                bridge_diagnostics.get("attached_reliability_assessment_ids", [])
            )
            == bridge_reliability_count
        ),
        "all_observed_blockers_have_action_or_terminal_route": all(
            row["route_present"] for row in route_rows
        ),
        "hard_gates_pass": gates.get("all_hard_gates_pass") is True,
        "no_goal_state_change_hard_stop_preserved": "NO_GOAL_STATE_CHANGE"
        in stop.get("stop_reasons", []),
        "zero_api_pass": (run_report.get("api_call_audit") or {}).get(
            "zero_api_pass"
        )
        is True,
        "v1_tree_digest_unchanged": args.v1_tree_digest
        == args.expected_v1_tree_digest,
        "v1_output_not_written": run_report.get("v1_output_written") is False,
    }

    report = {
        "schema": "freca-production-v2-phase2-zero-api-audit-v1",
        "phase": "PHASE_2_IMPLEMENTATION_ONLY",
        "phase3_synthetic_or_mutation_validation_started": False,
        "real_coordinate": {"case_uid": "case-004", "cp_id": "CP4"},
        "run_dir": str(run_dir),
        "observed_blocker_routes": route_rows,
        "planned_action_types": action_types,
        "executed_action_types": executed_types,
        "terminal_limitations": plan.get("terminal_limitations", []),
        "targeted_procedures": procedure_rows,
        "real_saved_open_goal_bridge_check": {
            "initial_dir": str(bridge_dir),
            "planned_action_types": bridge_planned_types,
            "executed_action_types": bridge_executed_types,
            "round_execution_complete": bridge_bundle.get(
                "round_execution_complete"
            ),
            "temporal_assessment_count": bridge_temporal_count,
            "attached_temporal_assessment_count": len(
                bridge_diagnostics.get("attached_temporal_assessment_ids", [])
            ),
            "reliability_assessment_count": bridge_reliability_count,
            "attached_reliability_assessment_count": len(
                bridge_diagnostics.get("attached_reliability_assessment_ids", [])
            ),
        },
        "api_call_audit": run_report.get("api_call_audit"),
        "v1_preservation": {
            "tree_digest_method": (
                "find production_run_v1_shards -type f -print0 | sort -z | "
                "xargs -0 sha256sum | sha256sum"
            ),
            "expected_tree_digest": args.expected_v1_tree_digest,
            "observed_tree_digest": args.v1_tree_digest,
            "unchanged": args.v1_tree_digest == args.expected_v1_tree_digest,
        },
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }
    report["report_sha256"] = sha256_json(report)
    save(report, args.output.resolve())
    if report["status"] != "PASS":
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit("Phase 2 audit failed: " + ", ".join(failed))
    print("Phase 2 zero-API implementation audit: PASS")


if __name__ == "__main__":
    main()
