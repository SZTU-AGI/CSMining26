#!/usr/bin/env python3
"""DeepSeek planner-value adapter for FRECA tree-search experiment v1.

The planner scores a PRE-EXISTING legal repair action sequence from 0..4.
It cannot create actions, evidence IDs, proof states, or final labels.

Raw case documents are not provided. The input is a compact state summary:
OpenGoals + proof blockers + coverage state + already validated legal actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from telemetry_capture_v1 import (
    capture_deepseek_telemetry,
    summarize_telemetry,
)


SYSTEM_PROMPT = r"""
You are the planner-value component of an isolated FRECA repair-routing
experiment.

You do NOT decide compliance.
You do NOT create repair actions.
You do NOT create evidence.
You do NOT modify proof state.
You do NOT output 1, 0, N/A, compliant, or non-compliant conclusions.

You receive:
1. a compact unresolved-state summary;
2. a sequence of repair actions that has already been generated and validated
   by the deterministic ActionGate.

Score only how promising THIS EXISTING SEQUENCE is for resolving the stated
decisive OpenGoals under the supplied blockers.

Use exactly this frozen scale:

4 = directly targets a DECISIVE unresolved blocker with a legal, novel,
    executable action sequence.
3 = plausibly targets goal-aligned validated signal but actual goal resolution
    remains uncertain.
2 = likely useful coverage/disposition gain without direct decisive-goal
    resolution.
1 = weak relevance or high redundancy risk.
0 = illegal, duplicate, targetless, irrelevant, or outside the stated goal.

Important:
- finding evidence in the opposite direction from the OpenGoal is not
  goal-aligned;
- more evidence is not automatically better;
- do not assume an action will succeed;
- do not infer facts not present in the summary;
- do not mention or predict a final answer label.

Return JSON only:
{
  "score": 0,
  "reason_codes": ["..."],
  "rationale": "brief explanation"
}
"""


FORBIDDEN_OUTPUT_KEYS = {
    "label",
    "final_label",
    "submission_label",
    "candidate_label",
    "compliance_label",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def build_state_summary(
    *,
    open_goals: dict,
    proof: dict,
    coverage: dict,
) -> dict:
    goals = []

    for goal in open_goals.get("goals", []) or []:
        goals.append({
            "goal_id": goal.get("goal_id"),
            "goal_type": goal.get("goal_type"),
            "estimated_verdict_impact":
                goal.get("estimated_verdict_impact"),
            "target_statement_id":
                goal.get("target_statement_id"),
            "prior_need_ids":
                list(goal.get("prior_need_ids") or []),
            "available_action_types":
                list(goal.get("available_action_types") or []),
            "direction":
                (goal.get("core_extension") or {}).get("direction"),
        })

    proof_rows = []
    for row in proof.get("requirement_reports", []) or []:
        proof_rows.append({
            "requirement_id": row.get("requirement_id"),
            "accepted_state": row.get("accepted_state"),
            "contradiction_state": row.get("contradiction_state"),
            "support_failure_codes":
                list(
                    (row.get("support_proof") or {}).get(
                        "failure_codes"
                    )
                    or []
                ),
            "attack_failure_codes":
                list(
                    (row.get("attack_proof") or {}).get(
                        "failure_codes"
                    )
                    or []
                ),
        })

    coverage_rows = []
    for row in coverage.get("requirement_summaries", []) or []:
        coverage_rows.append({
            "requirement_id": row.get("requirement_id"),
            "proof_coverage_pass":
                row.get("proof_coverage_pass"),
            "coverage_status":
                row.get("coverage_status"),
            "unassessed_candidate_count":
                row.get("unassessed_candidate_count"),
        })

    return {
        "case_uid": open_goals.get("case_uid"),
        "cp_id": open_goals.get("cp_id"),
        "open_goals": goals,
        "proof_blockers": proof_rows,
        "coverage": coverage_rows,
        "answer_comparator_used": False,
    }


def action_view(action: dict) -> dict:
    return {
        "action_id": action.get("action_id"),
        "goal_id": action.get("goal_id"),
        "action_type": action.get("action_type"),
        "action_signature": action.get("action_signature"),
        "target_artifact_ids":
            list(action.get("target_artifact_ids") or []),
        "query_plan_id": action.get("query_plan_id"),
        "constraint_delta":
            action.get("constraint_delta"),
        "expected_signal_types":
            list(action.get("expected_signal_types") or []),
    }


def validate_planner_output(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Planner output must be a JSON object")

    bad_keys = FORBIDDEN_OUTPUT_KEYS & set(raw)
    if bad_keys:
        raise ValueError(
            f"Planner emitted forbidden final-label keys: {sorted(bad_keys)}"
        )

    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("Planner score must be numeric")

    score = float(score)
    if score < 0 or score > 4:
        raise ValueError("Planner score must be in [0,4]")

    reason_codes = raw.get("reason_codes") or []
    if not isinstance(reason_codes, list):
        raise ValueError("reason_codes must be a list")

    rationale = str(raw.get("rationale") or "").strip()

    return {
        "score": score,
        "reason_codes": [str(x) for x in reason_codes],
        "rationale": rationale,
    }


class MemoizedDeepSeekPlanScorer:
    def __init__(
        self,
        *,
        state_summary: dict,
        model: str = "deepseek-v4-pro",
        thinking: bool = False,
        caller: Callable[..., dict] | None = None,
        capture_telemetry_enabled: bool = True,
    ):
        self.state_summary = state_summary
        self.model = model
        self.thinking = thinking
        self.caller = caller
        self.capture_telemetry_enabled = capture_telemetry_enabled
        self.cache: dict[str, dict] = {}
        self.trace: list[dict] = []

    def _call(self, sequence: tuple[dict, ...]) -> dict:
        if self.caller is None:
            import freca_core_v1 as core
            caller = core.deepseek_json
        else:
            caller = self.caller

        payload = {
            "state": self.state_summary,
            "candidate_action_sequence": [
                action_view(action)
                for action in sequence
            ],
            "frozen_value_scale": {
                "4": "direct DECISIVE blocker target",
                "3": "plausible goal-aligned signal",
                "2": "coverage/disposition gain",
                "1": "weak/redundant",
                "0": "illegal/irrelevant",
            },
        }

        user_prompt = (
            "Score the supplied existing action sequence.\n\n"
            + json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
        )

        if self.capture_telemetry_enabled:
            with capture_deepseek_telemetry() as events:
                raw = caller(
                    model=self.model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    thinking=self.thinking,
                    max_tokens=1200,
                )
            cost = summarize_telemetry(events)
        else:
            raw = caller(
                model=self.model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                thinking=self.thinking,
                max_tokens=1200,
            )
            cost = {
                "status": "TEST_STUB",
                "request_attempt_count": 0,
                "total_tokens": 0,
            }

        validated = validate_planner_output(raw)

        return {
            **validated,
            "cost_telemetry": cost,
        }

    def __call__(self, sequence: tuple[dict, ...]) -> float:
        key = sha256_json([
            action.get("action_signature")
            for action in sequence
        ])

        if key not in self.cache:
            result = self._call(sequence)
            self.cache[key] = result
            self.trace.append({
                "sequence_sha256": key,
                "sequence_signatures": [
                    str(action.get("action_signature"))
                    for action in sequence
                ],
                **result,
            })

        return float(self.cache[key]["score"])

    def telemetry_summary(self) -> dict:
        total_tokens = 0
        request_attempts = 0

        for row in self.trace:
            cost = row.get("cost_telemetry") or {}
            total_tokens += int(cost.get("total_tokens") or 0)
            request_attempts += int(
                cost.get("request_attempt_count") or 0
            )

        return {
            "model": self.model,
            "unique_plans_scored": len(self.trace),
            "request_attempt_count": request_attempts,
            "total_tokens": total_tokens,
            "trace": self.trace,
        }


def run_self_tests() -> None:
    state = {
        "case_uid": "case-x",
        "cp_id": "CP12",
        "open_goals": [
            {
                "goal_id": "g1",
                "goal_type": "FIND_ATTACK",
                "estimated_verdict_impact": "DECISIVE",
            }
        ],
    }

    action = {
        "action_id": "a1",
        "goal_id": "g1",
        "action_type": "ALIGN_NEXT_CANDIDATE_BATCH",
        "action_signature": "sha256:a1",
        "target_artifact_ids": ["e1"],
    }

    calls = {"n": 0}

    def stub_caller(**kwargs):
        calls["n"] += 1
        assert "candidate_action_sequence" in kwargs["user_prompt"]
        return {
            "score": 3,
            "reason_codes": ["DECISIVE_GOAL_TARGET"],
            "rationale": "Targets the stated attack goal without assuming success.",
        }

    scorer = MemoizedDeepSeekPlanScorer(
        state_summary=state,
        caller=stub_caller,
        capture_telemetry_enabled=False,
    )

    seq = (action,)
    assert scorer(seq) == 3.0
    assert scorer(seq) == 3.0
    assert calls["n"] == 1

    try:
        validate_planner_output({
            "score": 4,
            "final_label": "1",
        })
    except ValueError:
        pass
    else:
        raise AssertionError("forbidden label key was accepted")

    print("tree_search_planner_v1 self-tests: PASS")
    print("  planner only scores existing action sequences")
    print("  score constrained to 0..4")
    print("  memoization prevents duplicate planner calls")
    print("  final-label keys rejected")
    print("  zero API under self-test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_tests()


if __name__ == "__main__":
    main()
