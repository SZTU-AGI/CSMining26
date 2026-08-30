#!/usr/bin/env python3
"""Zero-API replay of saved Phase 5 rounds after semantic alignment-ID repair."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import freca_core_v1 as core
import production_runner_v2 as runner
import repair_feedback_v1_2 as feedback


FROZEN_V1_TREE_DIGEST = (
    "2e5c90ec718c4387941da347c31d69e260356ceef33072f165281704ae2dbad4"
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def save(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def v1_tree_digest(root: Path) -> str:
    found = subprocess.run(
        ["find", root.as_posix(), "-type", "f", "-print0"],
        check=True,
        stdout=subprocess.PIPE,
    )
    ordered = subprocess.run(
        ["sort", "-z"],
        check=True,
        input=found.stdout,
        stdout=subprocess.PIPE,
    )
    leaf_manifest = subprocess.run(
        ["xargs", "-0", "sha256sum"],
        check=True,
        input=ordered.stdout,
        stdout=subprocess.PIPE,
    )
    return hashlib.sha256(leaf_manifest.stdout).hexdigest()


def load_saved_arm_a(coordinate_dir: Path) -> dict:
    arm_a = coordinate_dir / "arm_a"
    return {
        "requirement_result": load(arm_a / "requirement_result_v2.json"),
        "coverage": load(arm_a / "coverage_v2.json"),
        "proof": load(arm_a / "proof_standard_v2.json"),
        "proof_gate_applicability": load(arm_a / "proof_gate_applicability_v2.json"),
        "procedure_plan": load(arm_a / "procedure_objective_v2.json"),
        "open_goals": load(arm_a / "open_goals_v2.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-root",
        type=Path,
        default=Path("results_v2/production_run_v2_live_gate"),
    )
    parser.add_argument("--contracts-dir", type=Path, default=Path("contracts_v2"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_v2/production_run_v2_phase6_offline_collision_replay"),
    )
    args = parser.parse_args()

    api_call_count = 0

    def forbidden_api(*_args: Any, **_kwargs: Any) -> dict:
        nonlocal api_call_count
        api_call_count += 1
        raise RuntimeError("PHASE6_ZERO_API_GUARD")

    core.deepseek_json = forbidden_api

    live_report = load(args.live_root / "phase5_live_gate_report.json")
    rows = []

    for item in live_report.get("coordinates", []):
        coordinate_name = f"{item['case_uid']}__{item['cp_id']}"
        coordinate_dir = args.live_root / coordinate_name
        before = load_saved_arm_a(coordinate_dir)
        bundle = load(coordinate_dir / "arm_b" / "round_bundle.json")
        contract = load(args.contracts_dir / f"{item['cp_id']}.json")

        raw_alignment_rows = sum(
            len(execution.get("new_alignments", []) or [])
            for execution in bundle.get("action_executions", [])
        )
        semantic_rows = feedback.round_new_alignments(bundle)
        duplicate_conflicts = feedback.duplicate_alignment_semantic_violations(bundle)

        merged_rr, merge_diagnostics = runner.merge_round_artifacts(
            before["requirement_result"], bundle
        )
        after = runner.build_layer7_v2(requirement_result=merged_rr, contract=contract)
        hard_gates = runner.evaluate_hard_gates_v2(
            before=before["requirement_result"],
            after=after["requirement_result"],
            proof_before=before["proof"],
            proof_after=after["proof"],
            round_bundle=bundle,
        )
        diff = feedback.build_evaluation_diff(
            before_rr=before["requirement_result"],
            after_rr=after["requirement_result"],
            coverage_before=before["coverage"],
            coverage_after=after["coverage"],
            proof_before=before["proof"],
            proof_after=after["proof"],
            open_goals_before=before["open_goals"],
            open_goals_after=after["open_goals"],
            round_bundle=bundle,
            hard_gates=hard_gates,
        )
        diff["v2_merge_diagnostics"] = merge_diagnostics
        outcome, fold = runner.build_outcome_and_fold(after, contract)
        original_outcome = load(coordinate_dir / "arm_b" / "outcome.json")
        signals = feedback.verified_signal_gain(bundle)

        # Diagnostic-only next round: model actions remain forbidden. This
        # records the remaining admitted deterministic actions without
        # converting terminal input limitations into executable work.
        (
            diagnostic_after,
            diagnostic_plan,
            diagnostic_bundle,
            diagnostic_hard_gates,
            diagnostic_diff,
        ) = runner.run_repair_round_v2(
            before=after,
            contract=contract,
            policy=load(Path("production_repair_policy_v2_live_gate.json")),
            round_index=2,
            allow_model_actions=False,
        )
        diagnostic_outcome, diagnostic_fold = runner.build_outcome_and_fold(
            diagnostic_after, contract
        )
        diagnostic_executions = diagnostic_bundle.get("action_executions", []) or []
        temporal_statuses = [
            str(assessment.get("status"))
            for execution in diagnostic_executions
            for assessment in execution.get("temporal_assessments", []) or []
        ]
        reliability_statuses = [
            str(assessment.get("status"))
            for execution in diagnostic_executions
            for assessment in execution.get(
                "information_reliability_assessments", []
            )
            or []
        ]

        output_coordinate = args.output_dir / coordinate_name
        runner.save_layer(after, output_coordinate / "after")
        save(hard_gates, output_coordinate / "hard_gates.json")
        save(diff, output_coordinate / "evaluation_diff.json")
        save(outcome, output_coordinate / "outcome.json")
        save(fold, output_coordinate / "fold.json")
        diagnostic_dir = output_coordinate / "diagnostic_zero_api_next_round"
        runner.save_layer(diagnostic_after, diagnostic_dir / "after")
        save(diagnostic_plan, diagnostic_dir / "repair_plan.json")
        save(diagnostic_bundle, diagnostic_dir / "round_bundle.json")
        save(diagnostic_hard_gates, diagnostic_dir / "hard_gates.json")
        save(diagnostic_diff, diagnostic_dir / "evaluation_diff.json")
        save(diagnostic_outcome, diagnostic_dir / "outcome.json")
        save(diagnostic_fold, diagnostic_dir / "fold.json")

        rows.append(
            {
                "case_uid": item["case_uid"],
                "cp_id": item["cp_id"],
                "raw_model_alignment_rows": raw_alignment_rows,
                "semantic_alignment_rows_after_deduplication": len(semantic_rows),
                "cross_path_duplicate_rows": raw_alignment_rows - len(semantic_rows),
                "duplicate_semantic_judgment_conflicts": duplicate_conflicts,
                "appended_alignment_count": len(
                    merge_diagnostics.get("appended_alignment_ids", []) or []
                ),
                "goal_aligned_verified_signal_count": signals[
                    "goal_aligned_verified_signal_count"
                ],
                "truth_bearing_alignment_count": signals[
                    "truth_bearing_alignment_count"
                ],
                "off_goal_verified_signal_count": signals[
                    "off_goal_verified_signal_count"
                ],
                "resolved_decisive_goal_count": diff["effect_vector"][
                    "resolved_decisive_goal_count"
                ],
                "original_arm_b_internal_outcome": original_outcome.get(
                    "common_internal_outcome"
                ),
                "replayed_arm_b_internal_outcome": outcome.get("common_internal_outcome"),
                "internal_outcome_changed_from_original_arm_b": (
                    original_outcome.get("common_internal_outcome")
                    != outcome.get("common_internal_outcome")
                ),
                "all_hard_gates_pass": hard_gates["all_hard_gates_pass"],
                "remaining_open_goal_count": len(after["open_goals"].get("goals", []) or []),
                "diagnostic_next_round_action_types": [
                    execution.get("action_type") for execution in diagnostic_executions
                ],
                "diagnostic_temporal_assessment_statuses": temporal_statuses,
                "diagnostic_reliability_assessment_statuses": reliability_statuses,
                "diagnostic_resolved_decisive_goal_count": diagnostic_diff[
                    "effect_vector"
                ]["resolved_decisive_goal_count"],
                "diagnostic_internal_outcome": diagnostic_outcome.get(
                    "common_internal_outcome"
                ),
                "diagnostic_all_hard_gates_pass": diagnostic_hard_gates[
                    "all_hard_gates_pass"
                ],
            }
        )

    observed_v1_digest = v1_tree_digest(Path("results_v2/production_run_v1_shards"))
    report = {
        "schema": "freca-production-v2-phase6-offline-collision-replay-v1",
        "source_phase5_report_sha256": live_report.get("report_sha256"),
        "zero_api_guard_enabled": True,
        "api_call_count": api_call_count,
        "coordinate_count": len(rows),
        "coordinates": rows,
        "totals": {
            "raw_model_alignment_rows": sum(row["raw_model_alignment_rows"] for row in rows),
            "semantic_alignment_rows_after_deduplication": sum(
                row["semantic_alignment_rows_after_deduplication"] for row in rows
            ),
            "cross_path_duplicate_rows": sum(row["cross_path_duplicate_rows"] for row in rows),
            "truth_bearing_alignment_count": sum(
                row["truth_bearing_alignment_count"] for row in rows
            ),
            "goal_aligned_verified_signal_count": sum(
                row["goal_aligned_verified_signal_count"] for row in rows
            ),
            "off_goal_verified_signal_count": sum(
                row["off_goal_verified_signal_count"] for row in rows
            ),
            "resolved_decisive_goal_count": sum(
                row["resolved_decisive_goal_count"] for row in rows
            ),
            "internal_outcome_change_count": sum(
                int(row["internal_outcome_changed_from_original_arm_b"]) for row in rows
            ),
            "diagnostic_temporal_assessment_count": sum(
                len(row["diagnostic_temporal_assessment_statuses"]) for row in rows
            ),
            "diagnostic_resolved_temporal_assessment_count": sum(
                sum(
                    status == "RESOLVED"
                    for status in row["diagnostic_temporal_assessment_statuses"]
                )
                for row in rows
            ),
            "diagnostic_resolved_decisive_goal_count": sum(
                row["diagnostic_resolved_decisive_goal_count"] for row in rows
            ),
        },
        "all_hard_gates_pass": all(
            row["all_hard_gates_pass"]
            and row["diagnostic_all_hard_gates_pass"]
            for row in rows
        ),
        "all_duplicate_semantic_judgments_consistent": all(
            not row["duplicate_semantic_judgment_conflicts"] for row in rows
        ),
        "v1_preservation": {
            "expected_tree_digest": FROZEN_V1_TREE_DIGEST,
            "observed_tree_digest": observed_v1_digest,
            "unchanged": observed_v1_digest == FROZEN_V1_TREE_DIGEST,
        },
        "confirmed_implementation_defects_repaired": [
            {
                "code": "ALIGNMENT_SEMANTIC_IDENTITY_COLLISION",
                "effect": (
                    "Evidence-only deduplication collapsed distinct requirement relations "
                    "and dropped directional retrieval-need bindings."
                ),
            },
        ],
        "rejected_implementation_defect_hypotheses": [
            {
                "code": "TEMPORAL_V2_BLOCKER_NOT_ROUTED",
                "finding": (
                    "Rejected after contract/classifier inspection: "
                    "TEMPORAL_REQUIREMENT_UNRESOLVED means no typed temporal-required "
                    "basis exists and already has an explicit non-executable terminal route."
                ),
            }
        ],
        "remaining_external_or_procedure_blockers": [
            "ADD_TYPED_TEMPORAL_APPLICABILITY_BASIS_TO_FROZEN_CONTRACT_OR_EVIDENCE_REQUIREMENT",
            "ACQUIRE_TRUTH_BEARING_ACTUAL_PERFORMANCE_RECORDS_FOR_RELIABILITY",
            "COMPLETE_PURPOSE_SPECIFIC_CANDIDATE_DISPOSITION_AND_COUNTEREVIDENCE_PROCEDURE",
        ],
        "scale_recommendation": "NO-GO",
        "interpretation": (
            "This replay tests a proven semantic-identity merge defect using already-saved "
            "model outputs. It does not claim accuracy and does not authorize a paid run."
        ),
    }
    report["report_sha256"] = sha256_json(report)
    save(report, args.output_dir / "phase6_offline_collision_replay_report.json")

    if api_call_count != 0:
        raise RuntimeError("Phase 6 attempted an API call")
    if not report["all_hard_gates_pass"]:
        raise RuntimeError("Phase 6 hard gate failure")
    if not report["v1_preservation"]["unchanged"]:
        raise RuntimeError("V1 tree changed")

    print(
        "Phase 6 offline replay:",
        f"coordinates={len(rows)}",
        f"raw_rows={report['totals']['raw_model_alignment_rows']}",
        f"semantic_rows={report['totals']['semantic_alignment_rows_after_deduplication']}",
        f"resolved_goals={report['totals']['resolved_decisive_goal_count']}",
        f"outcome_changes={report['totals']['internal_outcome_change_count']}",
        "api_calls=0",
        "recommendation=NO-GO",
    )


if __name__ == "__main__":
    main()
