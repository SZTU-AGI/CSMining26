#!/usr/bin/env python3
"""FRECA experiment arm A — TOP_K_NEXT_BATCH_EXPANSION v1.

Frozen experimental definition:
  - same frozen requirement-result / coverage root;
  - one RetrievalNeed (default ER1.attack);
  - select the next N UNASSESSED parents in persisted candidate-universe order;
  - run the existing identity annotations already present in the trace;
  - run the SAME FactCandidate -> DeepSeek alignment -> validator adapter;
  - no query change, no reranker, no new retrieval channel;
  - no answer comparator.

This is the generalized replication form of Pilot-C action
ALIGN_NEXT_CANDIDATE_BATCH.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from telemetry_capture_v1 import (
    capture_deepseek_telemetry,
    summarize_telemetry,
)


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


def stable_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256(
        "\n".join(str(x) for x in parts).encode("utf-8")
    ).hexdigest()[:20]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def trace_for_need(rr: dict, need_id: str) -> dict:
    rows = [
        row
        for row in rr.get("retrieval_traces", [])
        if str(row.get("need_id")) == need_id
    ]
    if len(rows) != 1:
        raise ValueError(
            f"Expected exactly one trace for {need_id}; found {len(rows)}"
        )
    return rows[0]


def coverage_for_need(cov: dict, need_id: str) -> dict:
    rows = [
        row
        for row in cov.get("need_reports", [])
        if str(row.get("need_id")) == need_id
    ]
    if len(rows) != 1:
        raise ValueError(
            f"Expected exactly one coverage report for {need_id}; found {len(rows)}"
        )
    return rows[0]


def select_next_batch(
    *,
    requirement_result: dict,
    coverage_before: dict,
    need_id: str,
    parent_budget: int,
) -> tuple[list[dict], dict]:
    if parent_budget < 1:
        raise ValueError("parent_budget must be >= 1")

    trace = trace_for_need(
        requirement_result,
        need_id,
    )
    cov = coverage_for_need(
        coverage_before,
        need_id,
    )

    universe = (
        trace.get("candidate_universe")
        or trace.get("candidates")
        or []
    )

    unassessed = set(
        str(x)
        for x in cov.get(
            "unassessed_candidate_ids",
            cov.get("universe_unassessed_candidate_ids", []),
        )
    )

    selected = []

    for row in universe:
        evidence_id = str(
            row.get("evidence_id")
            or row.get("id")
            or ""
        )

        if not evidence_id:
            continue

        if evidence_id not in unassessed:
            continue

        selected.append(copy.deepcopy(row))

        if len(selected) >= parent_budget:
            break

    mini_trace = copy.deepcopy(trace)

    mini_trace["candidate_universe"] = copy.deepcopy(selected)
    mini_trace["candidate_universe_ids"] = [
        str(row.get("evidence_id") or row.get("id"))
        for row in selected
    ]
    mini_trace["candidate_universe_count"] = len(selected)
    mini_trace["model_context_candidate_ids"] = list(
        mini_trace["candidate_universe_ids"]
    )
    mini_trace["model_context_count"] = len(selected)
    mini_trace["candidates"] = copy.deepcopy(selected)
    mini_trace["candidate_count_checked_by_model"] = len(selected)
    mini_trace["coverage_status"] = (
        "EXPERIMENT_NEXT_UNASSESSED_BATCH"
    )

    diag = {
        "need_id":
            need_id,
        "unassessed_before_count":
            len(unassessed),
        "selected_parent_ids":
            mini_trace["candidate_universe_ids"],
        "selected_parent_count":
            len(selected),
        "parent_alignment_budget":
            parent_budget,
        "selection_order":
            "PERSISTED_CANDIDATE_UNIVERSE_ORDER",
        "query_modified":
            False,
        "retrieval_channel_added":
            False,
        "answer_comparator_used":
            False,
    }

    return selected, mini_trace, diag


def execute_arm(
    *,
    requirement_result: dict,
    coverage_before: dict,
    need_id: str,
    parent_budget: int,
    aligner: Callable[[dict, list[dict]], list[dict]] | None = None,
    capture_telemetry: bool = True,
) -> dict:
    selected, mini_trace, diag = select_next_batch(
        requirement_result=requirement_result,
        coverage_before=coverage_before,
        need_id=need_id,
        parent_budget=parent_budget,
    )

    plan = requirement_result["evidence_requirement_plan"]

    if aligner is None:
        import evidence_reasoning_v2 as er

        def aligner(plan_arg, traces_arg):
            return er.align_requirement_evidence(
                plan_arg,
                traces_arg,
                batch_size=8,
            )

    if capture_telemetry:
        with capture_deepseek_telemetry() as events:
            new_alignments = aligner(
                plan,
                [mini_trace],
            )
        cost = summarize_telemetry(events)
    else:
        new_alignments = aligner(
            plan,
            [mini_trace],
        )
        cost = {
            "schema": "freca-core-cost-telemetry-v1",
            "status": "TEST_STUB",
            "provider": "NONE",
            "request_attempt_count": 0,
            "successful_call_count": 0,
            "failed_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "wall_time_ms": 0,
            "semantic_configuration_modified": False,
            "answer_comparator_used": False,
        }

    expected_direction = (
        "ATTACK"
        if need_id.endswith(".attack")
        else (
            "SUPPORT"
            if need_id.endswith(".support")
            else None
        )
    )

    truth = [
        row
        for row in new_alignments
        if (
            row.get("relation") in {"SUPPORT", "ATTACK"}
            and row.get("argument_admission_channel") == "DIRECT"
            and row.get("argument_truth_bearing") is True
        )
    ]

    goal_aligned = [
        row
        for row in truth
        if row.get("relation") == expected_direction
    ]

    off_goal = [
        row
        for row in truth
        if row.get("relation") != expected_direction
    ]

    root_hash = sha256_json(requirement_result)
    cov_hash = sha256_json(coverage_before)

    signature_payload = {
        "experiment_arm":
            "TOP_K_NEXT_BATCH_EXPANSION",
        "frozen_requirement_root_sha256":
            root_hash,
        "frozen_coverage_root_sha256":
            cov_hash,
        "need_id":
            need_id,
        "action_type":
            "ALIGN_NEXT_CANDIDATE_BATCH",
        "parent_alignment_budget":
            parent_budget,
        "selected_parent_ids":
            diag["selected_parent_ids"],
    }

    action_signature = sha256_json(signature_payload)
    action_id = stable_id(
        "action",
        "TOP_K_NEXT_BATCH_EXPANSION",
        need_id,
        action_signature,
    )

    execution = {
        "schema":
            "freca-core-experiment-action-execution-v1",
        "execution_id":
            stable_id("experiment-exec", action_id),
        "action_id":
            action_id,
        "goal_id":
            None,
        "goal_type":
            "FIND_ATTACK"
            if expected_direction == "ATTACK"
            else "FIND_SUPPORT",
        "goal_direction":
            expected_direction,
        "action_type":
            "ALIGN_NEXT_CANDIDATE_BATCH",
        "action_signature":
            action_signature,
        "need_id":
            need_id,
        "experiment_arm":
            "TOP_K_NEXT_BATCH_EXPANSION",
        "parent_alignment_budget":
            parent_budget,
        "target_artifact_ids":
            diag["selected_parent_ids"],
        "selected_parent_ids":
            diag["selected_parent_ids"],
        "selected_parent_count":
            len(selected),
        "new_alignments":
            new_alignments,
        "new_alignment_count":
            len(new_alignments),
        "truth_bearing_alignment_count":
            len(truth),
        "goal_aligned_truth_bearing_count":
            len(goal_aligned),
        "off_goal_truth_bearing_count":
            len(off_goal),
        "selection_diagnostics":
            diag,
        "cost_telemetry":
            cost,
        "signal_status":
            "NEW_VALIDATED_SIGNAL"
            if new_alignments
            else "NO_NEW_VALIDATED_SIGNAL",
        "action_execution_status":
            "EXECUTED",
        "upstream_artifacts_mutated":
            False,
        "proof_state_modified":
            False,
        "final_label":
            None,
        "answer_comparator_used":
            False,
    }

    execution["execution_sha256"] = sha256_json(execution)

    bundle = {
        "schema":
            "freca-core-experiment-round-artifacts-v1",
        "round_artifact_bundle_id":
            stable_id(
                "experiment-round",
                "TOP_K_NEXT_BATCH_EXPANSION",
                need_id,
                root_hash,
                cov_hash,
                str(parent_budget),
            ),
        "experiment_arm":
            "TOP_K_NEXT_BATCH_EXPANSION",
        "frozen_requirement_root_sha256":
            root_hash,
        "frozen_coverage_root_sha256":
            cov_hash,
        "round_index":
            1,
        "planned_action_ids":
            [action_id],
        "executed_action_ids":
            [action_id],
        "missing_action_ids":
            [],
        "round_execution_complete":
            True,
        "action_executions":
            [execution],
        "new_alignment_ids":
            sorted({
                str(
                    row.get("alignment_evidence_id")
                    or row.get("fact_candidate_id")
                )
                for row in new_alignments
                if (
                    row.get("alignment_evidence_id")
                    or row.get("fact_candidate_id")
                )
            }),
        "new_alignment_count":
            len(new_alignments),
        "any_new_validated_signal":
            bool(new_alignments),
        "parent_alignment_budget":
            parent_budget,
        "budget_policy":
            "EQUAL_MAX_PARENT_ALIGNMENT_BUDGET",
        "cost_telemetry":
            cost,
        "upstream_artifacts_mutated":
            False,
        "proof_state_modified":
            False,
        "final_label":
            None,
        "next_step":
            "RUN_REPAIR_FEEDBACK_V1_2",
    }

    bundle["bundle_sha256"] = sha256_json(bundle)

    return bundle


def run_self_tests() -> None:
    rr = {
        "evidence_requirement_plan": {
            "requirements": [
                {
                    "requirement_id": "ER1",
                    "atom_id": "A1",
                    "decisiveness": "DECISIVE",
                }
            ]
        },
        "retrieval_traces": [
            {
                "need_id": "ER1.attack",
                "requirement_id": "ER1",
                "direction": "ATTACK",
                "candidate_universe": [
                    {"evidence_id": "e1", "text": "a"},
                    {"evidence_id": "e2", "text": "b"},
                    {"evidence_id": "e3", "text": "c"},
                ],
            }
        ],
    }

    cov = {
        "need_reports": [
            {
                "need_id": "ER1.attack",
                "unassessed_candidate_ids": ["e2", "e3"],
            }
        ]
    }

    selected, trace, diag = select_next_batch(
        requirement_result=rr,
        coverage_before=cov,
        need_id="ER1.attack",
        parent_budget=1,
    )

    assert diag["selected_parent_ids"] == ["e2"]
    assert trace["candidate_universe_count"] == 1

    def stub_aligner(plan, traces):
        return [
            {
                "alignment_evidence_id": "e2#fc1",
                "fact_candidate_id": "fc1",
                "evidence_id": "e2",
                "retrieval_need_ids": ["ER1.attack"],
                "relation": "ATTACK",
                "argument_admission_channel": "DIRECT",
                "argument_truth_bearing": True,
                "exact_quote": "b",
                "fact_candidate": {
                    "fact_candidate_id": "fc1",
                    "quote": "b",
                },
            }
        ]

    bundle = execute_arm(
        requirement_result=rr,
        coverage_before=cov,
        need_id="ER1.attack",
        parent_budget=1,
        aligner=stub_aligner,
        capture_telemetry=False,
    )

    ex = bundle["action_executions"][0]

    assert ex["selected_parent_ids"] == ["e2"]
    assert ex["goal_aligned_truth_bearing_count"] == 1
    assert bundle["proof_state_modified"] is False
    assert bundle["final_label"] is None

    print("strategy_a_next_batch_v1 self-tests: PASS")
    print("  selects next unassessed persisted-universe parents only")
    print("  no query/reranker/channel modification")
    print("  same alignment adapter boundary preserved")
    print("  cost telemetry artifact slot present")
    print("  no answer comparator input")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )
    parser.add_argument(
        "--coverage-before",
        type=Path,
    )
    parser.add_argument(
        "--need-id",
        default="ER1.attack",
    )
    parser.add_argument(
        "--parent-budget",
        type=int,
        default=24,
    )
    parser.add_argument(
        "--output",
        type=Path,
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        if args.requirement_result is None:
            return

    if args.requirement_result is None:
        parser.error("--requirement-result is required")
    if args.coverage_before is None:
        parser.error("--coverage-before is required")

    rr = load_json(args.requirement_result)
    cov = load_json(args.coverage_before)

    bundle = execute_arm(
        requirement_result=rr,
        coverage_before=cov,
        need_id=args.need_id,
        parent_budget=args.parent_budget,
    )

    output = (
        args.output
        or args.requirement_result.with_name(
            args.requirement_result.stem
            + "_armA_next_batch_r1.json"
        )
    )

    save_json(bundle, output)

    ex = bundle["action_executions"][0]
    cost = bundle["cost_telemetry"]

    print("=" * 72)
    print("FRECA STRATEGY A — TOP_K_NEXT_BATCH_EXPANSION V1")
    print("=" * 72)
    print()
    print("Need:", ex["need_id"])
    print("Direction:", ex["goal_direction"])
    print(
        "Selected parents:",
        ex["selected_parent_count"],
        "/",
        ex["parent_alignment_budget"],
    )
    print("New alignments:", ex["new_alignment_count"])
    print(
        "Truth-bearing:",
        ex["truth_bearing_alignment_count"],
    )
    print(
        "Goal-aligned truth-bearing:",
        ex["goal_aligned_truth_bearing_count"],
    )
    print(
        "Off-goal truth-bearing:",
        ex["off_goal_truth_bearing_count"],
    )
    print()
    print("Cost telemetry:")
    print(
        "  attempts/success/fail:",
        cost.get("request_attempt_count"),
        cost.get("successful_call_count"),
        cost.get("failed_call_count"),
    )
    print(
        "  tokens prompt/completion/total:",
        cost.get("prompt_tokens"),
        cost.get("completion_tokens"),
        cost.get("total_tokens"),
    )
    print(
        "  wall_time_ms:",
        cost.get("wall_time_ms"),
    )
    print()
    print("Proof modified:", bundle["proof_state_modified"])
    print("Final label:", bundle["final_label"])
    print("Saved:", output)


if __name__ == "__main__":
    main()
