#!/usr/bin/env python3
"""FRECA Production Freeze Audit v2.

v1 invariants
+ Layer7->Layer11 outcome adapter contract
+ unique fold boundary contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import proof_standard_v1_1 as proof
import action_gate_v1_1 as action
import production_stop_gate_v1 as stop
import fold_policy_v3_core as fold
import core_outcome_adapter_v1 as adapter


def run_audit() -> dict:
    checks = {}

    checks["temporal_unknown_unresolved"] = (
        proof._row_temporal_status(
            {"temporal_relation": "UNKNOWN"}
        )[0] == "UNRESOLVED"
    )

    checks["temporal_out_of_scope_fail"] = (
        proof._row_temporal_status(
            {"temporal_relation": "OUT_OF_SCOPE"}
        )[0] == "FAIL"
    )

    checks["temporal_overlap_unresolved"] = (
        proof._row_temporal_status(
            {"temporal_relation": "OVERLAPS"}
        )[0] == "UNRESOLVED"
    )

    # executed-history check is covered by action_gate_v1_1 self-tests;
    # call its behavior through a tiny fixture only if tempfile is available.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        p = td / "p.json"
        e = td / "e.json"

        p.write_text(
            json.dumps({
                "actions": [{
                    "action_id": "p",
                    "goal_id": "g",
                    "action_signature": "sha256:p",
                    "execution_status": "PLANNED_NOT_EXECUTED",
                }]
            }),
            encoding="utf-8",
        )

        e.write_text(
            json.dumps({
                "action_executions": [{
                    "action_id": "e",
                    "goal_id": "g",
                    "action_signature": "sha256:e",
                    "action_execution_status": "EXECUTED",
                }]
            }),
            encoding="utf-8",
        )

        sigs, _ = action.load_prior_actions([p, e])

    checks["history_executed_only"] = (
        "sha256:e" in sigs
        and "sha256:p" not in sigs
    )

    d = stop.decide_after_round(
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
        d["decision"] == "DEFER"
    )

    rr = {
        "case_id": "fixture-case",
        "cp_id": "CPX",
        "evidence_requirement_plan": {
            "requirements": [
                {
                    "requirement_id": "ER1",
                    "decisiveness": "DECISIVE",
                }
            ]
        },
    }

    contract = {
        "contract": {
            "applicability": {
                "op": "CONST",
                "value": True,
            },
            "non_applicability": {
                "op": "CONST",
                "value": False,
            },
        }
    }

    proof_bundle = {
        "bundle_sha256": "sha256:p",
        "requirement_reports": [
            {
                "requirement_id": "ER1",
                "statement_id": "stmt-er1",
                "accepted_state": "TRUE",
                "contradiction_state": "NONE",
                "support_proof": {
                    "report_id": "ps",
                    "accepted_direction": True,
                },
                "attack_proof": {
                    "report_id": "pa",
                    "accepted_direction": False,
                },
            }
        ],
        "post_proof_argument": {
            "status": "RUN",
            "accepted_argument_evaluation": {
                "state": "TRUE",
                "standing_pro_argument_ids": ["arg-pro"],
                "standing_con_argument_ids": [],
                "conflicted_argument_ids": [],
                "undecided_argument_ids": [],
            },
        },
    }

    outcome = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=contract,
        proof_bundle=proof_bundle,
    )

    ev = outcome["evaluations"][0]

    checks["adapter_proven_compliant"] = (
        ev["internal_outcome"]
        == "PROVEN_COMPLIANT"
    )

    checks["adapter_emits_no_final_label"] = (
        outcome.get("submission_label") is None
        and ev.get("submission_label") is None
    )

    fd = fold.fold_envelope([
        {
            "valid": True,
            "internal_outcome": ev["internal_outcome"],
            "fold_gate_report": ev["fold_gate_report"],
        }
    ])

    checks["fold_is_unique_label_boundary"] = (
        fd["label"] == "1"
    )

    nonconst = {
        "contract": {
            "applicability": {
                "op": "ATOM",
                "atom_id": "AX",
            },
            "non_applicability": {
                "op": "CONST",
                "value": False,
            },
        }
    }

    unknown = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=nonconst,
        proof_bundle=proof_bundle,
    )

    checks["unsupported_scope_stays_unknown"] = (
        unknown["common_internal_outcome"]
        == "UNKNOWN"
    )

    unknown_fold = fold.fold_envelope([
        {
            "valid": True,
            "internal_outcome": "UNKNOWN",
            "fold_gate_report": {},
        }
    ])

    checks["unknown_fold_marked_fallback"] = (
        unknown_fold["label"] == "0"
        and unknown_fold["benchmark_fallback"] is True
    )

    all_pass = all(checks.values())

    return {
        "schema":
            "freca-core-production-freeze-audit-v2",
        "all_pass":
            all_pass,
        "checks":
            checks,
        "answer_comparator_used":
            False,
        "ready_for_contract_shape_inventory":
            all_pass,
        "ready_for_full_4100":
            False,
        "full_4100_blocker":
            (
                "Contract-shape coverage and staged end-to-end dry run "
                "must pass before full production."
            ),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_audit()

    print("=" * 72)
    print("FRECA PRODUCTION FREEZE AUDIT V2")
    print("=" * 72)

    for name, passed in result["checks"].items():
        print(
            f"{name}: "
            f"{'PASS' if passed else 'FAIL'}"
        )

    print()
    print("ALL PASS:", result["all_pass"])
    print(
        "READY FOR CONTRACT-SHAPE INVENTORY:",
        result["ready_for_contract_shape_inventory"],
    )
    print(
        "READY FOR FULL 4100:",
        result["ready_for_full_4100"],
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
