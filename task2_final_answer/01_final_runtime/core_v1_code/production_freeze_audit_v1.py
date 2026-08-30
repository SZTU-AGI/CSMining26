#!/usr/bin/env python3
"""One-shot FRECA Production Freeze v1 invariant audit."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import proof_standard_v1_1 as proof
import action_gate_v1_1 as action
import production_stop_gate_v1 as stop
import fold_policy_v3_core as fold


def run_audit() -> dict:
    checks = {}

    checks["temporal_unknown_unresolved"] = (
        proof._row_temporal_status(
            {"temporal_relation": "UNKNOWN"}
        )[0]
        == "UNRESOLVED"
    )

    checks["temporal_out_of_scope_fail"] = (
        proof._row_temporal_status(
            {"temporal_relation": "OUT_OF_SCOPE"}
        )[0]
        == "FAIL"
    )

    checks["temporal_overlap_unresolved"] = (
        proof._row_temporal_status(
            {"temporal_relation": "OVERLAPS"}
        )[0]
        == "UNRESOLVED"
    )

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        planned = td / "planned.json"
        executed = td / "executed.json"

        planned.write_text(
            json.dumps(
                {
                    "actions": [
                        {
                            "action_id": "p",
                            "goal_id": "g",
                            "action_signature": "sha256:planned",
                            "execution_status": "PLANNED_NOT_EXECUTED",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        executed.write_text(
            json.dumps(
                {
                    "action_executions": [
                        {
                            "action_id": "e",
                            "goal_id": "g",
                            "action_signature": "sha256:executed",
                            "action_execution_status": "EXECUTED",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        sigs, _ = action.load_prior_actions(
            [planned, executed]
        )

        checks["history_executed_only"] = (
            "sha256:executed" in sigs
            and "sha256:planned" not in sigs
        )

    stop_result = stop.decide_after_round(
        evaluation_diff={
            "new_evidence_ids": [],
            "new_fact_candidate_ids": ["fc"],
            "new_alignment_ids": ["a"],
            "resolved_goal_ids": [],
            "new_goal_ids": [],
            "changed_statement_states": {},
            "new_conflict_ids": [],
            "before_bundle_id": "b",
            "after_bundle_id": "a",
            "substantive_change": True,
        },
        round_index=1,
        max_rounds=2,
    )

    checks["no_goal_change_hard_stop"] = (
        stop_result["decision"] == "DEFER"
        and "NO_GOAL_STATE_CHANGE"
        in stop_result["stop_reasons"]
    )

    unknown = fold.fold_branch(
        {
            "internal_outcome": "UNKNOWN",
            "fold_gate_report": {},
        }
    )

    checks["unknown_only_via_benchmark_fallback"] = (
        unknown["label"] == "0"
        and unknown["benchmark_fallback"] is True
        and unknown["finality"]
        == "UNKNOWN_BENCHMARK_FALLBACK"
    )

    na = fold.fold_branch(
        {
            "internal_outcome": "PROVEN_NOT_APPLICABLE",
            "fold_gate_report": {
                "positive_non_applicability_proven": True,
                "na_countercheck_passed": True,
                "activity_counterevidence_standing": False,
            },
        }
    )

    checks["na_requires_positive_gate"] = (
        na["label"] == "N/A"
    )

    one = {
        "internal_outcome": "PROVEN_COMPLIANT",
        "fold_gate_report": {
            "applicability_standing": True,
            "all_decisive_requirements_meet_standard": True,
            "decisive_rebuttal_standing": False,
            "decisive_attack_or_violation_standing": False,
        },
    }

    na_branch = {
        "internal_outcome": "PROVEN_NOT_APPLICABLE",
        "fold_gate_report": {
            "positive_non_applicability_proven": True,
            "na_countercheck_passed": True,
            "activity_counterevidence_standing": False,
        },
    }

    one_na = fold.fold_envelope(
        [one, na_branch]
    )

    checks["one_na_production_prefers_na"] = (
        one_na["label"] == "N/A"
        and one_na["benchmark_fallback"] is True
    )

    all_pass = all(checks.values())

    return {
        "schema": "freca-core-production-freeze-audit-v1",
        "all_pass": all_pass,
        "checks": checks,
        "answer_comparator_used": False,
        "production_ready_for_batch_runner":
            all_pass,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_audit()

    print("=" * 72)
    print("FRECA PRODUCTION FREEZE AUDIT V1")
    print("=" * 72)

    for name, passed in result["checks"].items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")

    print()
    print("ALL PASS:", result["all_pass"])
    print(
        "READY FOR BATCH-RUNNER PHASE:",
        result["production_ready_for_batch_runner"],
    )

    if args.output:
        args.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.output.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Saved:", args.output)

    if not result["all_pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
