#!/usr/bin/env python3
"""FRECA experiment arm — CHANNEL_COMPLETION_ONLY v1.

Purpose
-------
Run an isolated equal-budget experimental arm from the frozen root:

    Arm A: TOP_K / NEXT_BATCH expansion
    Arm B: CHANNEL_COMPLETION_ONLY (this file)

This arm targets one frozen RetrievalNeed (default ER1.attack).

It:
  1. reloads the SAME current-case evidence package;
  2. performs a deterministic full-case STRUCTURE scan over every parsed
     evidence chunk;
  3. records all scanned records in a retrieval-trace delta;
  4. selects at most N records that were NOT in the frozen candidate universe,
     using parser/structural stable order only;
  5. runs the SAME identity gate;
  6. runs the SAME FactCandidate -> DeepSeek alignment -> validator pipeline;
  7. emits a repair-round-like artifact plus a restricted retrieval trace update.

It does NOT:
  - read Arm-A repair/feedback artifacts;
  - read human / historical / consensus labels;
  - change the alignment prompt or validator;
  - infer ATTACK merely because the RetrievalNeed direction is ATTACK;
  - claim semantic coverage merely because the structure scan is complete;
  - change ProofStandard or a final label.

"Full structure scan" here means the current Core parser's complete set of
stable evidence records was traversed and registered in parser order.
Expensive semantic alignment remains separately budgeted.
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
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json(value).encode("utf-8")
        ).hexdigest()
    )


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(str(x) for x in parts)
    return (
        prefix
        + "-"
        + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def chunk_id(chunk: dict) -> str:
    for key in ("id", "evidence_id", "chunk_id"):
        value = chunk.get(key)
        if value:
            return str(value)
    raise ValueError(
        f"Evidence chunk has no stable ID: {sorted(chunk)}"
    )


def chunk_text(chunk: dict) -> str:
    for key in ("text", "content", "raw_text"):
        value = chunk.get(key)
        if value is not None:
            return str(value)
    raise ValueError(
        f"Evidence chunk has no text: {chunk_id(chunk)}"
    )


def trace_for_need(
    requirement_result: dict,
    need_id: str,
) -> dict:
    rows = [
        row
        for row in requirement_result.get("retrieval_traces", [])
        if str(row.get("need_id")) == need_id
    ]

    if len(rows) != 1:
        raise ValueError(
            f"Expected exactly one trace for {need_id}; found {len(rows)}"
        )

    return rows[0]


def parent_ids_assessed_for_need(
    requirement_result: dict,
    need_id: str,
) -> set[str]:
    return {
        str(row.get("evidence_id"))
        for row in requirement_result.get("alignments", [])
        if (
            need_id
            in (
                row.get("retrieval_need_ids", [])
                or []
            )
            and row.get("evidence_id")
        )
    }


def full_structure_records(
    evidence_chunks: list[dict],
) -> list[dict]:
    """Register every parsed evidence record in parser order.

    No semantic keyword/reranker is used.  Parser order is the stable order
    emitted by the existing Core evidence loader.
    """

    records = []
    seen = set()

    for scan_index, chunk in enumerate(evidence_chunks):
        evidence_id = chunk_id(chunk)

        if evidence_id in seen:
            raise ValueError(
                f"Duplicate evidence record in case parse: {evidence_id}"
            )

        seen.add(evidence_id)

        item = copy.deepcopy(chunk)
        item["id"] = evidence_id
        item["evidence_id"] = evidence_id
        item["text"] = chunk_text(chunk)

        methods = list(
            item.get("retrieval_methods", [])
            or []
        )

        if "STRUCTURE_FULL_SCAN" not in methods:
            methods.append("STRUCTURE_FULL_SCAN")

        item["retrieval_methods"] = methods
        item["structure_scan_index"] = scan_index

        records.append(item)

    return records


def build_structure_trace_delta(
    *,
    requirement_result: dict,
    evidence_chunks: list[dict],
    need_id: str,
    parent_budget: int,
) -> tuple[dict, dict]:
    if parent_budget < 1:
        raise ValueError("parent_budget must be >= 1")

    base_trace = trace_for_need(
        requirement_result,
        need_id,
    )

    old_universe = (
        base_trace.get("candidate_universe")
        or base_trace.get("candidates")
        or []
    )

    old_ids = {
        str(row.get("evidence_id") or row.get("id"))
        for row in old_universe
        if row.get("evidence_id") or row.get("id")
    }

    already_assessed = parent_ids_assessed_for_need(
        requirement_result,
        need_id,
    )

    structure_records = full_structure_records(
        evidence_chunks
    )

    new_records = [
        row
        for row in structure_records
        if str(row["evidence_id"]) not in old_ids
    ]

    # Stable parser/structural order.  No label, relation, model score,
    # negative keyword, or Arm-A result participates.
    selectable = [
        row
        for row in new_records
        if str(row["evidence_id"]) not in already_assessed
    ]

    selected = selectable[:parent_budget]

    delta = {
        "schema":
            "freca-core-retrieval-trace-channel-update-v1",

        "need_id":
            need_id,

        "channel":
            "STRUCTURE",

        "update_type":
            "EXECUTE_MISSING_CHANNEL",

        "base_trace_sha256":
            sha256_json(base_trace),

        "structure_scan": {
            "mode":
                "FULL_CASE_STRUCTURE_RECORD_SCAN",

            "scan_chunk_count":
                len(structure_records),

            "full_scan":
                True,

            "generated_new_candidate_count":
                len(new_records),

            "selected_for_semantic_alignment_count":
                len(selected),

            "parent_alignment_budget":
                parent_budget,

            "selection_order":
                "PARSER_STABLE_ORDER",

            "selection_uses_semantic_score":
                False,

            "selection_uses_answer_comparator":
                False,
        },

        # Restricted delta.  Feedback v1.1 may ONLY append these candidates;
        # query, typed scan, lexical scan, and old candidate records are frozen.
        "candidate_universe_additions":
            new_records,

        "selected_candidate_ids":
            [
                str(row["evidence_id"])
                for row in selected
            ],

        "structure_full_scan":
            True,

        "structure_scan_complete":
            True,

        "prohibited_mutations": [
            "query",
            "query_variants",
            "typed_fact_scan",
            "raw_lexical_scan",
            "coverage_requirement",
            "existing_candidate_semantics",
        ],
    }

    delta["update_sha256"] = sha256_json(delta)

    selected_trace = copy.deepcopy(base_trace)

    # Alignment gets ONLY the equal-budget newly discovered structure records.
    selected_trace["candidate_universe"] = copy.deepcopy(selected)
    selected_trace["candidate_universe_ids"] = [
        str(row["evidence_id"])
        for row in selected
    ]
    selected_trace["candidate_universe_count"] = len(selected)
    selected_trace["model_context_candidate_ids"] = [
        str(row["evidence_id"])
        for row in selected
    ]
    selected_trace["model_context_count"] = len(selected)
    selected_trace["candidates"] = copy.deepcopy(selected)
    selected_trace["candidate_count_checked_by_model"] = len(selected)
    selected_trace["coverage_status"] = (
        "EXPERIMENT_CHANNEL_COMPLETION_SELECTED_BATCH"
    )

    return delta, selected_trace


def annotate_selected_trace(
    *,
    selected_trace: dict,
    requirement_result: dict,
    evidence_chunks: list[dict],
) -> dict:
    from identity_admissibility_v1 import (
        apply_identity_gate_to_traces,
        build_body_first_identity_report,
    )

    identity_report = requirement_result.get(
        "identity_admissibility"
    )

    if not isinstance(identity_report, dict):
        identity_report = build_body_first_identity_report(
            evidence_chunks,
            output_identifier=str(
                requirement_result.get("case_id", "")
            ),
        )

    annotated = apply_identity_gate_to_traces(
        [selected_trace],
        identity_report,
    )

    if len(annotated) != 1:
        raise RuntimeError(
            "Identity gate did not return exactly one trace"
        )

    return annotated[0]


def execute_arm(
    *,
    requirement_result: dict,
    evidence_chunks: list[dict],
    need_id: str,
    parent_budget: int,
    aligner: Callable[[dict, list[dict]], list[dict]] | None = None,
) -> dict:
    delta, selected_trace = build_structure_trace_delta(
        requirement_result=requirement_result,
        evidence_chunks=evidence_chunks,
        need_id=need_id,
        parent_budget=parent_budget,
    )

    annotated_trace = annotate_selected_trace(
        selected_trace=selected_trace,
        requirement_result=requirement_result,
        evidence_chunks=evidence_chunks,
    )

    # Copy identity annotations from selected trace back into the restricted
    # trace additions so Coverage can deterministically dispose excluded items.
    annotated_by_id = {
        str(row.get("evidence_id") or row.get("id")):
            row
        for row in annotated_trace.get(
            "candidate_universe",
            annotated_trace.get("candidates", []),
        )
    }

    for row in delta["candidate_universe_additions"]:
        key = str(row.get("evidence_id") or row.get("id"))

        annotated = annotated_by_id.get(key)

        if annotated is None:
            # The identity gate only annotated the selected semantic batch.
            # Unselected structure records remain registered but must not gain
            # invented identity status.  They stay unassessed for coverage.
            continue

        for field in (
            "source_id",
            "identity_relation_to_case",
            "identity_use_decision",
            "identity_decisive_proof_eligible",
            "identity_reason_code",
        ):
            if field in annotated:
                row[field] = copy.deepcopy(
                    annotated[field]
                )

    if aligner is None:
        import evidence_reasoning_v2 as er

        def aligner(plan_arg, traces_arg):
            return er.align_requirement_evidence(
                plan_arg,
                traces_arg,
                batch_size=8,
            )

    plan = requirement_result[
        "evidence_requirement_plan"
    ]

    with capture_deepseek_telemetry() as telemetry_events:
        raw_alignments = aligner(
            plan,
            [annotated_trace],
        )

    cost_telemetry = summarize_telemetry(
        telemetry_events
    )

    old_ids = {
        str(
            row.get("alignment_evidence_id")
            or row.get("fact_candidate_id")
            or ""
        )
        for row in requirement_result.get("alignments", [])
    }

    novel = [
        row
        for row in raw_alignments
        if str(
            row.get("alignment_evidence_id")
            or row.get("fact_candidate_id")
            or ""
        )
        not in old_ids
    ]

    expected_direction = (
        "ATTACK"
        if need_id.endswith(".attack")
        else (
            "SUPPORT"
            if need_id.endswith(".support")
            else None
        )
    )

    truth_bearing = [
        row
        for row in novel
        if (
            row.get("relation") in {"SUPPORT", "ATTACK"}
            and row.get("argument_admission_channel") == "DIRECT"
            and row.get("argument_truth_bearing") is True
        )
    ]

    goal_aligned = [
        row
        for row in truth_bearing
        if row.get("relation") == expected_direction
    ]

    off_goal = [
        row
        for row in truth_bearing
        if row.get("relation") != expected_direction
    ]

    semantic = [
        row
        for row in novel
        if row.get("relation") in {"SUPPORT", "ATTACK"}
    ]

    direct = [
        row
        for row in novel
        if row.get("argument_admission_channel") == "DIRECT"
    ]

    conditional = [
        row
        for row in novel
        if row.get("argument_admission_channel") == "CONDITIONAL"
    ]

    rejected = [
        row
        for row in novel
        if row.get("argument_admission_channel") == "REJECTED"
    ]

    root_hash = sha256_json(requirement_result)

    action_signature_payload = {
        "experiment_arm":
            "CHANNEL_COMPLETION_ONLY",
        "frozen_root_sha256":
            root_hash,
        "need_id":
            need_id,
        "action_type":
            "EXECUTE_MISSING_CHANNEL",
        "channel":
            "STRUCTURE",
        "parent_alignment_budget":
            parent_budget,
        "selection_order":
            "PARSER_STABLE_ORDER",
        "trace_update_sha256":
            delta["update_sha256"],
    }

    action_signature = sha256_json(
        action_signature_payload
    )

    action_id = stable_id(
        "action",
        "CHANNEL_COMPLETION_ONLY",
        need_id,
        action_signature,
    )

    execution = {
        "schema":
            "freca-core-experiment-action-execution-v1",

        "execution_id":
            stable_id(
                "experiment-exec",
                action_id,
            ),

        "action_id":
            action_id,

        "goal_id":
            None,

        "goal_type":
            (
                "FIND_ATTACK"
                if expected_direction == "ATTACK"
                else "FIND_SUPPORT"
            ),

        "goal_direction":
            expected_direction,

        "action_type":
            "EXECUTE_MISSING_CHANNEL",

        "action_signature":
            action_signature,

        "need_id":
            need_id,

        "channel_set": [
            "STRUCTURE"
        ],

        "experiment_arm":
            "CHANNEL_COMPLETION_ONLY",

        "parent_alignment_budget":
            parent_budget,

        "selected_parent_ids":
            delta["selected_candidate_ids"],

        "selected_parent_count":
            len(delta["selected_candidate_ids"]),

        "retrieval_trace_updates": [
            delta
        ],

        "new_alignments":
            novel,

        "new_alignment_count":
            len(novel),

        "semantic_alignment_count":
            len(semantic),

        "direct_argument_input_count":
            len(direct),

        "conditional_argument_input_count":
            len(conditional),

        "rejected_alignment_count":
            len(rejected),

        "truth_bearing_alignment_count":
            len(truth_bearing),

        "goal_aligned_truth_bearing_count":
            len(goal_aligned),

        "off_goal_truth_bearing_count":
            len(off_goal),

        "cost_telemetry":
            cost_telemetry,

        "signal_status":
            (
                "NEW_VALIDATED_SIGNAL"
                if novel
                else "NO_NEW_VALIDATED_SIGNAL"
            ),

        "action_execution_status":
            "EXECUTED",

        "required_return_path": [
            "LAYER7_RETRIEVAL_TRACE",
            "LAYER7_ALIGNMENT_VALIDATOR",
            "LAYER7_COVERAGE",
            "LAYER7_ARGUMENT_GRAPH",
            "LAYER7_PROOF_STANDARD",
        ],

        "upstream_artifacts_mutated":
            False,

        "proof_state_modified":
            False,

        "final_label":
            None,

        "answer_comparator_used":
            False,

        "arm_a_artifacts_read":
            False,
    }

    execution["execution_sha256"] = sha256_json(
        execution
    )

    round_bundle = {
        "schema":
            "freca-core-experiment-round-artifacts-v1",

        "round_artifact_bundle_id":
            stable_id(
                "experiment-round",
                "CHANNEL_COMPLETION_ONLY",
                need_id,
                root_hash,
                str(parent_budget),
            ),

        "experiment_arm":
            "CHANNEL_COMPLETION_ONLY",

        "frozen_root_sha256":
            root_hash,

        "round_index":
            1,

        "planned_action_ids": [
            action_id
        ],

        "executed_action_ids": [
            action_id
        ],

        "missing_action_ids":
            [],

        "round_execution_complete":
            True,

        "action_executions": [
            execution
        ],

        "retrieval_trace_updates": [
            delta
        ],

        "new_alignment_ids": sorted(
            {
                str(
                    row.get("alignment_evidence_id")
                    or row.get("fact_candidate_id")
                )
                for row in novel
                if (
                    row.get("alignment_evidence_id")
                    or row.get("fact_candidate_id")
                )
            }
        ),

        "new_alignment_count":
            len(novel),

        "any_new_validated_signal":
            bool(novel),

        "parent_alignment_budget":
            parent_budget,

        "budget_policy":
            "EQUAL_MAX_PARENT_ALIGNMENT_BUDGET_WITH_ARM_A",

        "cost_telemetry":
            cost_telemetry,

        "upstream_artifacts_mutated":
            False,

        "proof_state_modified":
            False,

        "final_label":
            None,

        "next_step":
            "RUN_REPAIR_FEEDBACK_V1_1",
    }

    round_bundle["bundle_sha256"] = sha256_json(
        round_bundle
    )

    return round_bundle


def run_self_tests() -> None:
    rr = {
        "case_id":
            "case-x",

        "identity_admissibility": {
            "source_relations": {},
        },

        "evidence_requirement_plan": {
            "requirements": [
                {
                    "requirement_id": "ER1",
                    "atom_id": "A1",
                    "decisiveness": "DECISIVE",
                    "proposition_to_establish": "fixture",
                    "query_sources": [],
                }
            ]
        },

        "retrieval_traces": [
            {
                "need_id": "ER1.attack",
                "requirement_id": "ER1",
                "direction": "ATTACK",
                "coverage_requirement": "CANDIDATE_DISCOVERY",
                "candidate_universe": [
                    {
                        "evidence_id": "doc:P1",
                        "text": "old",
                        "identity_use_decision": "ADMIT_DIRECT",
                    }
                ],
                "candidate_universe_ids": ["doc:P1"],
                "candidates": [
                    {
                        "evidence_id": "doc:P1",
                        "text": "old",
                        "identity_use_decision": "ADMIT_DIRECT",
                    }
                ],
            }
        ],

        "alignments": [
            {
                "evidence_id": "doc:P1",
                "retrieval_need_ids": ["ER1.attack"],
                "alignment_evidence_id": "doc:P1#old",
            }
        ],
    }

    chunks = [
        {"id": "doc:P1", "text": "old", "file": "doc"},
        {"id": "doc:P2", "text": "new one", "file": "doc"},
        {"id": "doc:T1:R1", "text": "new row", "file": "doc"},
    ]

    delta, trace = build_structure_trace_delta(
        requirement_result=rr,
        evidence_chunks=chunks,
        need_id="ER1.attack",
        parent_budget=2,
    )

    assert (
        delta["structure_scan"]["scan_chunk_count"] == 3
    )

    assert (
        delta["structure_scan"]["generated_new_candidate_count"] == 2
    )

    assert delta["selected_candidate_ids"] == [
        "doc:P2",
        "doc:T1:R1",
    ]

    assert trace["candidate_universe_count"] == 2

    # Isolate the parts of execute_arm that do not require the real identity
    # module by monkey-patching the annotator for the synthetic test.
    global annotate_selected_trace
    original = annotate_selected_trace

    try:
        def fake_annotate(
            *,
            selected_trace,
            requirement_result,
            evidence_chunks,
        ):
            out = copy.deepcopy(selected_trace)

            for row in out["candidate_universe"]:
                row["identity_use_decision"] = "ADMIT_DIRECT"
                row["identity_decisive_proof_eligible"] = True

            out["candidates"] = copy.deepcopy(
                out["candidate_universe"]
            )

            return out

        annotate_selected_trace = fake_annotate

        def stub_aligner(plan, traces):
            assert len(traces) == 1

            return [
                {
                    "alignment_evidence_id":
                        "doc:P2#fc-new",

                    "fact_candidate_id":
                        "fc-new",

                    "evidence_id":
                        "doc:P2",

                    "retrieval_need_ids":
                        ["ER1.attack"],

                    "relation":
                        "ATTACK",

                    "argument_admission_channel":
                        "DIRECT",

                    "argument_truth_bearing":
                        True,

                    "exact_quote":
                        "new one",

                    "fact_candidate": {
                        "fact_candidate_id":
                            "fc-new",
                        "quote":
                            "new one",
                    },
                }
            ]

        bundle = execute_arm(
            requirement_result=rr,
            evidence_chunks=chunks,
            need_id="ER1.attack",
            parent_budget=2,
            aligner=stub_aligner,
        )

    finally:
        annotate_selected_trace = original

    ex = bundle["action_executions"][0]

    assert bundle["round_execution_complete"] is True
    assert ex["goal_aligned_truth_bearing_count"] == 1
    assert ex["off_goal_truth_bearing_count"] == 0
    assert bundle["proof_state_modified"] is False
    assert bundle["final_label"] is None

    print("strategy_b_channel_completion_v1_1 self-tests: PASS")
    print("  full parser-record structure scan is separate from alignment budget")
    print("  only records outside frozen candidate universe enter B semantic batch")
    print("  ATTACK need does not force semantic relation")
    print("  restricted retrieval-trace delta emitted")
    print("  no Arm-A artifact / answer comparator input")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--case",
        type=str,
    )

    parser.add_argument(
        "--need-id",
        type=str,
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
        parser.error(
            "--requirement-result is required unless only --self-test is used"
        )

    rr = load_json(
        args.requirement_result
    )

    case_id = (
        args.case
        or rr.get("case_id")
    )

    if not case_id:
        parser.error(
            "--case is required when requirement-result has no case_id"
        )

    import freca_core_v1 as core

    case_dir = core.find_case_dir(
        str(case_id)
    )

    evidence_chunks = core.load_case_evidence(
        case_dir
    )

    bundle = execute_arm(
        requirement_result=rr,
        evidence_chunks=evidence_chunks,
        need_id=args.need_id,
        parent_budget=args.parent_budget,
    )

    output = (
        args.output
        or args.requirement_result.with_name(
            args.requirement_result.stem
            + "_strategy_b_channel_completion_r1.json"
        )
    )

    save_json(
        bundle,
        output,
    )

    ex = bundle["action_executions"][0]
    update = bundle["retrieval_trace_updates"][0]

    print("=" * 72)
    print("FRECA STRATEGY B — CHANNEL_COMPLETION_ONLY V1.1")
    print("=" * 72)

    print()
    print("Frozen root:", bundle["frozen_root_sha256"])
    print("Need:", ex["need_id"])
    print("Direction:", ex["goal_direction"])

    print()
    print("STRUCTURE channel:")
    print(
        "  scanned records:",
        update["structure_scan"]["scan_chunk_count"],
    )
    print(
        "  new outside frozen universe:",
        update["structure_scan"]["generated_new_candidate_count"],
    )
    print(
        "  selected parent records:",
        ex["selected_parent_count"],
        "/",
        ex["parent_alignment_budget"],
    )

    print()
    print("Alignment:")
    print(
        "  new alignments:",
        ex["new_alignment_count"],
    )
    print(
        "  semantic:",
        ex["semantic_alignment_count"],
    )
    print(
        "  direct:",
        ex["direct_argument_input_count"],
    )
    print(
        "  conditional:",
        ex["conditional_argument_input_count"],
    )
    print(
        "  rejected:",
        ex["rejected_alignment_count"],
    )
    print(
        "  truth-bearing:",
        ex["truth_bearing_alignment_count"],
    )
    print(
        "  goal-aligned truth-bearing:",
        ex["goal_aligned_truth_bearing_count"],
    )
    print(
        "  off-goal truth-bearing:",
        ex["off_goal_truth_bearing_count"],
    )

    print()
    cost = bundle["cost_telemetry"]
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
