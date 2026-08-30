#!/usr/bin/env python3
"""FRECA Production Repair Stop Gate v1.

Small D8.10 transplant.

The gate is evaluated only after a COMPLETE RepairPlan round and the affected
layers have been rerun. It never evaluates individual actions in isolation.

Immediate stop/defer reasons:
  NO_NEW_ARTIFACT
  NO_NEW_EVIDENCE_OR_ALIGNMENT
  NO_GOAL_STATE_CHANGE
  REPEATED_EVALUATION_HASH
  NO_NOVEL_ACTION
  NEW_BLOCKING_CONFLICT
  BUDGET_EXHAUSTED
  TOOL_OR_MODEL_FAILURE_WITHOUT_FALLBACK

Important:
  substantive_change=True does NOT override NO_GOAL_STATE_CHANGE.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


IMMEDIATE_STOP_REASONS = {
    "NO_NEW_ARTIFACT",
    "NO_NEW_EVIDENCE_OR_ALIGNMENT",
    "NO_GOAL_STATE_CHANGE",
    "REPEATED_EVALUATION_HASH",
    "NO_NOVEL_ACTION",
    "NEW_BLOCKING_CONFLICT",
    "BUDGET_EXHAUSTED",
    "TOOL_OR_MODEL_FAILURE_WITHOUT_FALLBACK",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def derive_reasons(
    *,
    evaluation_diff: dict,
    repair_round: dict | None = None,
    round_index: int = 1,
    max_rounds: int = 2,
) -> list[str]:
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    stop_diag = (
        evaluation_diff.get("stop_gate_diagnostic")
        or evaluation_diff.get("candidate_stop_gate")
        or {}
    )

    for reason in stop_diag.get("candidate_stop_reasons", []) or []:
        if reason in IMMEDIATE_STOP_REASONS:
            add(reason)

    # Recompute critical D8.10 reasons so the production gate does not depend
    # on a diagnostic field being present.
    if (
        not evaluation_diff.get("new_evidence_ids", [])
        and not evaluation_diff.get("new_fact_candidate_ids", [])
        and not evaluation_diff.get("new_alignment_ids", [])
    ):
        add("NO_NEW_ARTIFACT")

    if (
        not evaluation_diff.get("new_evidence_ids", [])
        and not evaluation_diff.get("new_alignment_ids", [])
    ):
        add("NO_NEW_EVIDENCE_OR_ALIGNMENT")

    if (
        not evaluation_diff.get("resolved_goal_ids", [])
        and not evaluation_diff.get("new_goal_ids", [])
        and not evaluation_diff.get("changed_statement_states", {})
    ):
        add("NO_GOAL_STATE_CHANGE")

    before_id = evaluation_diff.get("before_bundle_id")
    after_id = evaluation_diff.get("after_bundle_id")

    if (
        before_id is not None
        and after_id is not None
        and before_id == after_id
    ):
        add("REPEATED_EVALUATION_HASH")

    if evaluation_diff.get("new_conflict_ids", []):
        add("NEW_BLOCKING_CONFLICT")

    if repair_round is not None:
        planned = set(
            str(x)
            for x in repair_round.get("planned_action_ids", [])
        )
        executed = set(
            str(x)
            for x in repair_round.get("executed_action_ids", [])
        )

        if planned and not executed:
            add("NO_NOVEL_ACTION")

        failed = [
            row
            for row in repair_round.get("action_executions", []) or []
            if str(
                row.get("action_execution_status")
                or row.get("execution_status")
                or ""
            ).upper()
            in {"FAILED", "TOOL_FAILURE", "MODEL_FAILURE"}
        ]

        if failed and not repair_round.get("fallback_succeeded", False):
            add("TOOL_OR_MODEL_FAILURE_WITHOUT_FALLBACK")

    if round_index >= max_rounds:
        add("BUDGET_EXHAUSTED")

    return reasons


def decide_after_round(
    *,
    evaluation_diff: dict,
    repair_round: dict | None = None,
    round_index: int = 1,
    max_rounds: int = 2,
) -> dict:
    if round_index < 1:
        raise ValueError("round_index must be >= 1")

    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")

    reasons = derive_reasons(
        evaluation_diff=evaluation_diff,
        repair_round=repair_round,
        round_index=round_index,
        max_rounds=max_rounds,
    )

    immediate = [
        r for r in reasons
        if r in IMMEDIATE_STOP_REASONS
    ]

    if immediate:
        return {
            "decision": "DEFER",
            "allow_next_repair_round": False,
            "stop_reasons": immediate,
            "substantive_change":
                bool(evaluation_diff.get("substantive_change", False)),
            "policy":
                "D8.10_IMMEDIATE_STOP_AFTER_COMPLETE_ROUND",
        }

    return {
        "decision": "CONTINUE_REPAIR",
        "allow_next_repair_round": True,
        "stop_reasons": [],
        "substantive_change":
            bool(evaluation_diff.get("substantive_change", False)),
        "policy":
            "D8.10_CONTINUE_ONLY_WITH_GOAL_ADVANCE_AND_NOVELTY",
    }


def run_self_tests() -> None:
    # Critical frozen rule:
    # real information may increase while goals do not advance.
    diff = {
        "new_evidence_ids": [],
        "new_fact_candidate_ids": ["fc-new"],
        "new_alignment_ids": ["a-new"],
        "resolved_goal_ids": [],
        "new_goal_ids": [],
        "changed_statement_states": {},
        "new_conflict_ids": [],
        "before_bundle_id": "before",
        "after_bundle_id": "after",
        "substantive_change": True,
    }

    decision = decide_after_round(
        evaluation_diff=diff,
        round_index=1,
        max_rounds=2,
    )

    assert decision["decision"] == "DEFER"
    assert decision["allow_next_repair_round"] is False
    assert "NO_GOAL_STATE_CHANGE" in decision["stop_reasons"]

    advancing = dict(diff)
    advancing["resolved_goal_ids"] = ["g1"]

    decision2 = decide_after_round(
        evaluation_diff=advancing,
        round_index=1,
        max_rounds=2,
    )

    assert decision2["decision"] == "CONTINUE_REPAIR"

    budget = decide_after_round(
        evaluation_diff=advancing,
        round_index=2,
        max_rounds=2,
    )

    assert budget["decision"] == "DEFER"
    assert "BUDGET_EXHAUSTED" in budget["stop_reasons"]

    print("production_stop_gate_v1 self-tests: PASS")
    print("  substantive_change does not override NO_GOAL_STATE_CHANGE")
    print("  goal advance can permit next round")
    print("  max round is a hard stop")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-diff", type=Path)
    parser.add_argument("--repair-round", type=Path)
    parser.add_argument("--round-index", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        if args.evaluation_diff is None:
            return

    if args.evaluation_diff is None:
        parser.error("--evaluation-diff is required")

    diff = load_json(args.evaluation_diff)
    rr = load_json(args.repair_round) if args.repair_round else None

    result = decide_after_round(
        evaluation_diff=diff,
        repair_round=rr,
        round_index=args.round_index,
        max_rounds=args.max_rounds,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
