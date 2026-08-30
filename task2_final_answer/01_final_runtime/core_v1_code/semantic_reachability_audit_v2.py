#!/usr/bin/env python3
"""Zero-API Phase 1 reachability audit for one completed V1 coordinate.

This diagnostic deliberately reuses a saved V1 initial requirement result and
the saved V1 repair-round artifact.  It never regenerates retrieval,
FactCandidates, or alignments.  Model entry points are replaced with a hard
failure guard before any downstream code is evaluated.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import action_gate_v1_1 as action_gate
import freca_core_v1 as core
import open_goal_v1
import production_runner_v1 as runner


SCHEMA_VERSION = "freca-production-v2-phase1-reachability-v1"
AUDITOR_VERSION = "SEMANTIC_REACHABILITY_AUDIT_V2_PHASE1_1"


# This is a diagnostic route catalog, not an implementation of the routes.
# current_v1_action records the closest action name that exists in the live V1
# action vocabulary.  A null value means the blocker has no executable V1
# repair action and must be reported as a reachability gap.
BLOCKER_ROUTE_CATALOG: dict[str, dict[str, Any]] = {
    "NO_DIRECT_SUPPORT_BASIS": {
        "current_v1_action": "ALIGN_NEXT_CANDIDATE_BATCH",
        "terminal_limitation": "CANDIDATE_UNIVERSE_EXHAUSTED_NO_SUPPORT",
    },
    "NO_EXPLICIT_VIOLATION_BASIS": {
        "current_v1_action": "ALIGN_NEXT_CANDIDATE_BATCH",
        "terminal_limitation": "CANDIDATE_UNIVERSE_EXHAUSTED_NO_EXPLICIT_ADVERSE_FACT",
    },
    "IDENTITY_GATE_FAILED": {
        "current_v1_action": "RESOLVE_IDENTITY",
        "terminal_limitation": "IDENTITY_REMAINS_UNRESOLVED",
    },
    "NO_TEMPORAL_BASIS_ROWS": {
        "current_v1_action": "ALIGN_NEXT_CANDIDATE_BATCH",
        "terminal_limitation": "NO_DIRECTIONAL_BASIS_FOR_TEMPORAL_ASSESSMENT",
    },
    "TEMPORAL_ASSESSMENT_MISSING": {
        "current_v1_action": "RESOLVE_TIME",
        "terminal_limitation": "TEMPORAL_FACTS_UNAVAILABLE_OR_UNRESOLVED",
    },
    "TEMPORAL_ASSESSMENT_CONFLICT": {
        "current_v1_action": "RESOLVE_TIME",
        "terminal_limitation": "TEMPORAL_ASSESSMENT_CONFLICT_UNRESOLVED",
    },
    "TEMPORAL_SCOPE_EXPLICITLY_FAILED": {
        "current_v1_action": "RESOLVE_TIME",
        "terminal_limitation": "EVIDENCE_OUT_OF_APPLICABLE_PERIOD",
    },
    "TEMPORAL_SCOPE_UNRESOLVED": {
        "current_v1_action": "RESOLVE_TIME",
        "terminal_limitation": "TEMPORAL_SCOPE_REMAINS_UNRESOLVED",
    },
    "NO_RELIABILITY_BASIS_ROWS": {
        "current_v1_action": "ALIGN_NEXT_CANDIDATE_BATCH",
        "terminal_limitation": "NO_DIRECTIONAL_BASIS_FOR_RELIABILITY_ASSESSMENT",
    },
    "INFORMATION_RELIABILITY_MISSING": {
        "current_v1_action": "ASSESS_INFORMATION_RELIABILITY",
        "terminal_limitation": "RELIABILITY_FACTS_UNAVAILABLE_OR_UNRESOLVED",
    },
    "INFORMATION_RELIABILITY_UNRESOLVED": {
        "current_v1_action": "ASSESS_INFORMATION_RELIABILITY",
        "terminal_limitation": "INFORMATION_RELIABILITY_REMAINS_UNRESOLVED",
    },
    "INFORMATION_RELIABILITY_FAILED": {
        "current_v1_action": "ASSESS_INFORMATION_RELIABILITY",
        "terminal_limitation": "INFORMATION_RELIABILITY_EXPLICITLY_FAILED",
    },
    "EVIDENCE_QUALITY_RELIABILITY_UNRESOLVED": {
        "current_v1_action": "ASSESS_INFORMATION_RELIABILITY",
        "terminal_limitation": "EVIDENCE_QUALITY_REMAINS_UNRESOLVED",
    },
    "EVIDENCE_QUALITY_RELIABILITY_FAILED": {
        "current_v1_action": "ASSESS_INFORMATION_RELIABILITY",
        "terminal_limitation": "EVIDENCE_QUALITY_EXPLICITLY_FAILED",
    },
    "COVERAGE_INCOMPLETE": {
        "current_v1_action": None,
        "required_v2_action": "COMPLETE_TARGETED_COVERAGE",
        "terminal_limitation": "TARGETED_COVERAGE_PROCEDURE_UNAVAILABLE_OR_INCOMPLETE",
    },
    "CONTRADICTION_BLOCKING": {
        "current_v1_action": "CHECK_REBUTTAL",
        "terminal_limitation": "CONTRADICTION_REMAINS_BLOCKING",
    },
    "CONTRADICTION_NOT_DEFEATED": {
        "current_v1_action": "CHECK_REBUTTAL",
        "terminal_limitation": "CONTRADICTION_NOT_DEFEATED_AFTER_COUNTERCHECK",
    },
}


ACTION_ARTIFACT_EFFECTS = {
    "ALIGN_NEXT_CANDIDATE_BATCH": [
        "retrieval_traces.candidate_disposition",
        "alignments",
        "RequirementCoverage",
        "ProofGateReport",
        "ArgumentEvaluation",
        "OpenGoalLedger",
    ],
    "RESOLVE_TIME": [
        "TemporalAssessment",
        "alignment.temporal_assessment",
        "alignment.temporal_relation",
        "ProofGateReport.temporal_pass",
        "ArgumentEvaluation",
        "OpenGoalLedger",
    ],
    "ASSESS_INFORMATION_RELIABILITY": [
        "InformationReliabilityAssessment",
        "alignment.information_reliability",
        "ProofGateReport.reliability_pass",
        "ArgumentEvaluation",
        "OpenGoalLedger",
    ],
    "COMPLETE_TARGETED_COVERAGE": [
        "TargetedCoverageProcedureArtifact",
        "RequirementCoverage.proof_coverage_pass",
        "ProofGateReport.coverage_pass",
        "ArgumentEvaluation",
        "OpenGoalLedger",
    ],
    "RESOLVE_IDENTITY": [
        "EventIdentityAssessment",
        "EvidenceUseDecision",
        "ProofGateReport.identity_pass",
    ],
    "CHECK_REBUTTAL": [
        "counterevidence retrieval/alignment artifacts",
        "ProofGateReport.contradiction_state",
        "ArgumentEvaluation",
    ],
}


ACTION_GATE_EXECUTABLE_TYPES = {
    "ALIGN_NEXT_CANDIDATE_BATCH",
    "RESOLVE_TIME",
    "ASSESS_INFORMATION_RELIABILITY",
    "VALIDATE_CITATION",
    "CHECK_EXCEPTION",
    "CHECK_REBUTTAL",
}

IMPLEMENTED_EXECUTOR_TYPES = {
    "ALIGN_NEXT_CANDIDATE_BATCH",
    "RESOLVE_TIME",
    "ASSESS_INFORMATION_RELIABILITY",
}

V1_PRODUCTION_DISPATCH_TYPES = {
    "ALIGN_NEXT_CANDIDATE_BATCH",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return "sha256:" + sha256_bytes(canonical_json(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def proof_blockers(proof: dict) -> list[dict]:
    rows = []
    for requirement in proof.get("requirement_reports", []):
        requirement_id = str(requirement.get("requirement_id"))
        for key, direction in (
            ("support_proof", "SUPPORT"),
            ("attack_proof", "ATTACK"),
        ):
            directional = requirement.get(key) or {}
            rows.append(
                {
                    "requirement_id": requirement_id,
                    "direction": direction,
                    "accepted_direction": bool(
                        directional.get("accepted_direction", False)
                    ),
                    "basis_artifact_ids": list(
                        directional.get("basis_artifact_ids", []) or []
                    ),
                    "failure_codes": list(
                        directional.get("failure_codes", []) or []
                    ),
                }
            )
    return rows


def goal_rows(open_goals: dict) -> list[dict]:
    rows = []
    for goal in open_goals.get("goals", []):
        executable = action_gate.executable_action_types(goal)
        rows.append(
            {
                "goal_id": goal.get("goal_id"),
                "goal_type": goal.get("goal_type"),
                "requirement_or_proposition": goal.get("target_proposition_id"),
                "blocking_reason_codes": list(
                    goal.get("blocking_reason_codes", []) or []
                ),
                "available_action_types": list(
                    goal.get("available_action_types", []) or []
                ),
                "action_gate_executable_action_types": executable,
                "target_artifact_ids": list(
                    (goal.get("core_extension") or {}).get(
                        "target_artifact_ids", []
                    )
                    or []
                ),
            }
        )
    return rows


def compare_layer7(recomputed: dict, directory: Path) -> dict:
    paths = {
        "requirement_result": directory / "requirement_for_proof.json",
        "coverage": directory / "coverage_v1_1.json",
        "proof": directory / "proof_standard_v1_1.json",
        "procedure": directory / "procedure_objective_v1.json",
        "open_goals": directory / "open_goals_v1.json",
    }
    result = {}
    for key, path in paths.items():
        saved = load_json(path)
        current = recomputed[key]
        result[key] = {
            "saved_path": str(path.resolve()),
            "saved_sha256": sha256_json(saved),
            "recomputed_sha256": sha256_json(current),
            "exact_match": canonical_json(saved) == canonical_json(current),
        }
    result["all_exact_match"] = all(
        row["exact_match"]
        for row in result.values()
        if isinstance(row, dict) and "exact_match" in row
    )
    return result


def build_static_reachability_report() -> dict:
    proof_source_path = Path("proof_standard_v1_1.py")
    proof_source_text = proof_source_path.read_text(encoding="utf-8")
    source_literals = set(
        re.findall(r'"([A-Z][A-Z0-9_]+)"', proof_source_text)
    )
    discovered_blockers = sorted(
        value
        for value in source_literals
        if (
            value.startswith("NO_DIRECT_")
            or value.startswith("NO_EXPLICIT_")
            or value.startswith("NO_TEMPORAL_")
            or value.startswith("NO_RELIABILITY_")
            or value.startswith("TEMPORAL_ASSESSMENT_")
            or value.startswith("TEMPORAL_SCOPE_")
            or value.startswith("INFORMATION_RELIABILITY_")
            or value.startswith("EVIDENCE_QUALITY_RELIABILITY_")
            or value
            in {
                "IDENTITY_GATE_FAILED",
                "COVERAGE_INCOMPLETE",
                "CONTRADICTION_BLOCKING",
                "CONTRADICTION_NOT_DEFEATED",
            }
        )
    )
    catalog_blockers = sorted(BLOCKER_ROUTE_CATALOG)
    catalog_missing_blockers = sorted(
        set(discovered_blockers) - set(catalog_blockers)
    )
    catalog_extra_blockers = sorted(
        set(catalog_blockers) - set(discovered_blockers)
    )

    allowed_actions = sorted(open_goal_v1.ALLOWED_ACTION_TYPES)
    rows = []
    for blocker, route in sorted(BLOCKER_ROUTE_CATALOG.items()):
        current_action = route.get("current_v1_action")
        action_declared = bool(current_action and current_action in allowed_actions)
        action_gate_executable = bool(
            current_action and current_action in ACTION_GATE_EXECUTABLE_TYPES
        )
        executor_implemented = bool(
            current_action and current_action in IMPLEMENTED_EXECUTOR_TYPES
        )
        production_dispatched = bool(
            current_action and current_action in V1_PRODUCTION_DISPATCH_TYPES
        )
        end_to_end_route = bool(
            action_declared
            and action_gate_executable
            and executor_implemented
            and production_dispatched
        )
        rows.append(
            {
                "proof_blocker": blocker,
                "current_v1_action": current_action,
                "current_v1_action_declared": action_declared,
                "current_v1_action_gate_executable_type": action_gate_executable,
                "current_v1_executor_implemented": executor_implemented,
                "current_v1_production_dispatch_reachable": production_dispatched,
                "required_v2_action": route.get("required_v2_action"),
                "required_explicit_terminal_limitation": route[
                    "terminal_limitation"
                ],
                "current_v1_blocker_specific_terminal_implemented": False,
                "artifact_effects": ACTION_ARTIFACT_EFFECTS.get(
                    str(current_action or route.get("required_v2_action")), []
                ),
                "current_v1_end_to_end_route_present": end_to_end_route,
            }
        )

    missing_current_routes = [
        row["proof_blocker"]
        for row in rows
        if not (
            row["current_v1_end_to_end_route_present"]
            or row["current_v1_blocker_specific_terminal_implemented"]
        )
    ]
    missing_route_spec = [
        row["proof_blocker"]
        for row in rows
        if not (
            row.get("current_v1_action")
            or row.get("required_v2_action")
            or row.get("required_explicit_terminal_limitation")
        )
    ]

    return {
        "schema": "freca-blocker-to-action-static-reachability-v1",
        "proof_standard_source": str(proof_source_path.resolve()),
        "proof_standard_sha256": sha256_file(proof_source_path),
        "proof_blocker_discovery_method": (
            "Pinned-source uppercase reason-code scan over temporal, reliability, "
            "identity, coverage, contradiction, and missing-basis code families"
        ),
        "discovered_proof_blockers": discovered_blockers,
        "catalog_missing_discovered_blockers": catalog_missing_blockers,
        "catalog_entries_not_found_in_pinned_source": catalog_extra_blockers,
        "action_gate_source": str(Path("action_gate_v1_1.py").resolve()),
        "action_gate_sha256": sha256_file(Path("action_gate_v1_1.py")),
        "declared_v1_action_types": allowed_actions,
        "action_gate_executable_types": sorted(ACTION_GATE_EXECUTABLE_TYPES),
        "implemented_executor_types": sorted(IMPLEMENTED_EXECUTOR_TYPES),
        "v1_production_dispatch_types": sorted(V1_PRODUCTION_DISPATCH_TYPES),
        "routes": rows,
        "catalog_blocker_count": len(rows),
        "missing_current_v1_end_to_end_routes": missing_current_routes,
        "missing_any_action_or_terminal_route_specification": missing_route_spec,
        "current_v1_reachability_status": (
            "PASS" if not missing_current_routes else "FAIL"
        ),
        "route_catalog_completeness_status": (
            "PASS"
            if (
                not missing_route_spec
                and not catalog_missing_blockers
                and not catalog_extra_blockers
            )
            else "FAIL"
        ),
        "interpretation": (
            "The catalog is diagnostic only. End-to-end reachability requires "
            "an allowed action, ActionGate support, an implemented executor, and "
            "a production dispatcher route. It does not claim successful resolution."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    task_dir = args.task_dir.resolve()
    output_dir = args.output_dir.resolve()
    if task_dir == output_dir or task_dir in output_dir.parents:
        raise ValueError("Phase 1 output must not be written inside the V1 task directory")

    required = [
        task_dir / "task_meta.json",
        task_dir / "decision.json",
        task_dir / "initial" / "requirement_result.json",
        task_dir / "initial" / "layer7" / "proof_standard_v1_1.json",
        task_dir / "initial" / "layer7" / "open_goals_v1.json",
        task_dir / "repair" / "round-1" / "round_bundle.json",
        task_dir / "repair" / "round-1" / "after" / "proof_standard_v1_1.json",
        task_dir / "repair" / "round-1" / "stop_decision.json",
        task_dir / "core_outcome_adapter_v1.json",
        task_dir / "fold_decision_v3.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required V1 artifacts: " + repr(missing))

    v1_hashes_before = {
        str(path.relative_to(task_dir)): sha256_file(path)
        for path in sorted(task_dir.rglob("*"))
        if path.is_file()
    }

    api_calls_attempted: list[dict[str, Any]] = []

    def reject_api_call(*_args: Any, **kwargs: Any) -> dict:
        api_calls_attempted.append(
            {
                "model": kwargs.get("model"),
                "reason": "MODEL_ENTRYPOINT_CALLED_DURING_ZERO_API_PHASE",
            }
        )
        raise RuntimeError("ZERO_API_GUARD: model/API call attempted")

    original_deepseek_json = core.deepseek_json
    core.deepseek_json = reject_api_call

    try:
        initial_rr = load_json(task_dir / "initial" / "requirement_result.json")
        contract = load_json(args.contract.resolve())

        initial_root = runner.build_layer7(
            requirement_result=copy.deepcopy(initial_rr),
            contract=contract,
        )
        initial_comparison = compare_layer7(
            initial_root,
            task_dir / "initial" / "layer7",
        )

        round_bundle = load_json(
            task_dir / "repair" / "round-1" / "round_bundle.json"
        )
        after_root, hard_gates, evaluation_diff = runner.run_repair_round(
            before=initial_root,
            contract=contract,
            round_bundle=round_bundle,
        )
        after_comparison = compare_layer7(
            after_root,
            task_dir / "repair" / "round-1" / "after",
        )

        saved_stop = load_json(
            task_dir / "repair" / "round-1" / "stop_decision.json"
        )
        recomputed_stop = runner.stop_gate.decide_after_round(
            evaluation_diff=evaluation_diff,
            repair_round=round_bundle,
            round_index=1,
            max_rounds=2,
        )

        recomputed_outcome, recomputed_fold = runner.build_outcome_and_fold(
            root=after_root,
            contract=contract,
        )
        saved_outcome = load_json(task_dir / "core_outcome_adapter_v1.json")
        saved_fold = load_json(task_dir / "fold_decision_v3.json")
        saved_decision = load_json(task_dir / "decision.json")
        task_meta = load_json(task_dir / "task_meta.json")
        admission = load_json(task_dir / "repair" / "round-1" / "admission.json")

        initial_blockers = proof_blockers(initial_root["proof"])
        after_blockers = proof_blockers(after_root["proof"])
        goals = goal_rows(initial_root["open_goals"])
        executable_actions = sorted(
            {
                action
                for goal in goals
                for action in goal["action_gate_executable_action_types"]
            }
        )
        executed_actions = [
            {
                "action_id": row.get("action_id"),
                "goal_id": row.get("goal_id"),
                "action_type": row.get("action_type"),
                "action_execution_status": row.get("action_execution_status"),
                "new_alignment_count": row.get("new_alignment_count", 0),
                "artifact_effects": ACTION_ARTIFACT_EFFECTS.get(
                    str(row.get("action_type")), []
                ),
            }
            for row in round_bundle.get("action_executions", [])
        ]

        static_report = build_static_reachability_report()
        observed_codes = sorted(
            {
                code
                for row in initial_blockers
                for code in row["failure_codes"]
            }
        )
        observed_routes = {
            row["proof_blocker"]: row
            for row in static_report["routes"]
            if row["proof_blocker"] in observed_codes
        }

        report = {
            "schema": SCHEMA_VERSION,
            "auditor_version": AUDITOR_VERSION,
            "phase": "PHASE_1_ZERO_API_DEFECT_REPRODUCTION",
            "coordinate": {
                "task_dir": str(task_dir),
                "case_uid": saved_decision.get("case_uid"),
                "cp_id": saved_decision.get("cp_id"),
                "input_fingerprint": task_meta.get("input_fingerprint"),
                "initial_requirement_result_sha256": "sha256:"
                + sha256_file(task_dir / "initial" / "requirement_result.json"),
                "saved_round_bundle_sha256": "sha256:"
                + sha256_file(
                    task_dir / "repair" / "round-1" / "round_bundle.json"
                ),
            },
            "reproduction": {
                "initial_layer7_exact_match": initial_comparison["all_exact_match"],
                "initial_layer7_artifacts": initial_comparison,
                "after_saved_v1_round_exact_match": after_comparison["all_exact_match"],
                "after_layer7_artifacts": after_comparison,
                "hard_gates_exact_match": canonical_json(hard_gates)
                == canonical_json(
                    load_json(task_dir / "repair" / "round-1" / "hard_gates.json")
                ),
                "evaluation_diff_exact_match": canonical_json(evaluation_diff)
                == canonical_json(
                    load_json(
                        task_dir / "repair" / "round-1" / "evaluation_diff.json"
                    )
                ),
                "stop_decision_exact_match": canonical_json(recomputed_stop)
                == canonical_json(saved_stop),
                "core_outcome_exact_match": canonical_json(recomputed_outcome)
                == canonical_json(saved_outcome),
                "fold_exact_match": canonical_json(recomputed_fold)
                == canonical_json(saved_fold),
            },
            "initial_proof_blockers": initial_blockers,
            "generated_open_goals": goals,
            "admitted_actions": {
                "round_admitted_to_production_state": admission.get(
                    "admitted_to_production_state"
                ),
                "production_policy_id": round_bundle.get("production_policy_id"),
                "production_admitted_primitive": round_bundle.get("repair_primitive"),
                "planned_action_ids": round_bundle.get("planned_action_ids", []),
            },
            "action_gate_executable_actions": executable_actions,
            "executed_actions_in_v1": executed_actions,
            "unexecuted_but_action_gate_executable_actions": sorted(
                set(executable_actions)
                - {str(row["action_type"]) for row in executed_actions}
            ),
            "action_artifact_effects": ACTION_ARTIFACT_EFFECTS,
            "after_v1_round_proof_blockers": after_blockers,
            "observed_blocker_routes": observed_routes,
            "why_final_state_remained_unknown": {
                "initial_internal_outcome": initial_root["proof"].get(
                    "internal_outcome"
                ),
                "recomputed_final_internal_outcome": recomputed_outcome.get(
                    "common_internal_outcome"
                ),
                "recomputed_fold_label": recomputed_fold.get("label"),
                "recomputed_fold_finality": recomputed_fold.get("finality"),
                "proof_blocker_net_reduction": (
                    evaluation_diff.get("effect_vector", {})
                    .get("proof_blocker_delta", {})
                    .get("net_blocker_reduction")
                ),
                "resolved_decisive_goal_count": (
                    evaluation_diff.get("effect_vector", {}).get(
                        "resolved_decisive_goal_count"
                    )
                ),
                "stop_reasons": recomputed_stop.get("stop_reasons", []),
                "causal_chain": [
                    "V1 production admitted only ALIGN_NEXT_CANDIDATE_BATCH",
                    "RESOLVE_TIME was executable but not dispatched",
                    "ASSESS_INFORMATION_RELIABILITY was executable but not dispatched",
                    "COVERAGE_INCOMPLETE had no current V1 TARGETED_COMPLETE executor",
                    "the saved alignment round removed zero proof blockers",
                    "NO_GOAL_STATE_CHANGE stopped repair",
                    "InternalOutcome remained UNKNOWN",
                    "FOLD-POLICY-v3 emitted UNKNOWN_BENCHMARK_FALLBACK -> 0",
                ],
            },
            "safety": {
                "alignment_regenerated": False,
                "retrieval_regenerated": False,
                "fact_candidates_regenerated": False,
                "answer_comparator_used": False,
                "human_or_historical_labels_used": False,
                "v1_artifacts_written": False,
            },
        }

    finally:
        core.deepseek_json = original_deepseek_json

    v1_hashes_after = {
        str(path.relative_to(task_dir)): sha256_file(path)
        for path in sorted(task_dir.rglob("*"))
        if path.is_file()
    }
    v1_unchanged = v1_hashes_before == v1_hashes_after

    api_audit = {
        "schema": "freca-zero-api-audit-v1",
        "phase": "PHASE_1",
        "guarded_entrypoint": "freca_core_v1.deepseek_json",
        "guard_installed": True,
        "attempted_calls": api_calls_attempted,
        "attempted_call_count": len(api_calls_attempted),
        "api_call_count": 0,
        "zero_api_pass": len(api_calls_attempted) == 0,
        "v1_coordinate_hashes_unchanged": v1_unchanged,
    }

    static_report["report_sha256"] = sha256_json(static_report)
    api_audit["report_sha256"] = sha256_json(api_audit)

    report["api_call_audit"] = api_audit
    report["v1_coordinate_hashes_unchanged"] = v1_unchanged
    report["phase1_reproduction_status"] = (
        "PASS"
        if (
            report["reproduction"]["initial_layer7_exact_match"]
            and report["reproduction"]["after_saved_v1_round_exact_match"]
            and report["reproduction"]["stop_decision_exact_match"]
            and report["reproduction"]["core_outcome_exact_match"]
            and report["reproduction"]["fold_exact_match"]
            and api_audit["zero_api_pass"]
            and v1_unchanged
        )
        else "FAIL"
    )
    report["report_sha256"] = sha256_json(report)

    save_json(report, output_dir / "semantic_reachability_report.json")
    save_json(static_report, output_dir / "blocker_to_action_static_report.json")
    save_json(api_audit, output_dir / "api_call_audit.json")

    print("Phase 1 status:", report["phase1_reproduction_status"])
    print("Coordinate:", report["coordinate"]["case_uid"], report["coordinate"]["cp_id"])
    print("Initial exact match:", report["reproduction"]["initial_layer7_exact_match"])
    print("After exact match:", report["reproduction"]["after_saved_v1_round_exact_match"])
    print("Final outcome:", recomputed_outcome.get("common_internal_outcome"))
    print("Fold:", recomputed_fold.get("label"), recomputed_fold.get("finality"))
    print("API calls:", api_audit["api_call_count"])
    print("Static V1 reachability:", static_report["current_v1_reachability_status"])
    print("Saved:", output_dir)


if __name__ == "__main__":
    main()
