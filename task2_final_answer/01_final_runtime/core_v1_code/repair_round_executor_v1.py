#!/usr/bin/env python3
"""FRECA Core Repair Round Executor v1.

Completes the remaining actions of ONE already-frozen RepairPlan round.

Supported:
  - ASSESS_INFORMATION_RELIABILITY
  - ALIGN_NEXT_CANDIDATE_BATCH

Existing RESOLVE_TIME execution(s) may be supplied and are preserved verbatim.

Important:
  - this executes actions but does NOT merge them into upstream artifacts;
  - original requirement result is never overwritten;
  - action 3 reuses evidence_reasoning_v2.align_requirement_evidence(), i.e.
    the same FactCandidate -> model -> validator path as the current Core;
  - reliability is conservative: grounding/identity alone never passes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


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


def actions_by_id(repair_plan: dict) -> dict[str, dict]:
    return {
        str(row["action_id"]): row
        for row in repair_plan.get("actions", [])
    }


def action_by_index(repair_plan: dict, index: int) -> dict:
    actions = repair_plan.get("actions", [])
    if index < 1 or index > len(actions):
        raise ValueError(f"action-index {index} out of range")
    return actions[index - 1]


def alignment_lookup(requirement_result: dict) -> dict[str, dict]:
    out = {}
    for row in requirement_result.get("alignments", []):
        fact = row.get("fact_candidate") or {}
        for key in (
            row.get("alignment_evidence_id"),
            row.get("fact_candidate_id"),
            fact.get("fact_candidate_id"),
            row.get("evidence_id"),
        ):
            if key:
                out[str(key)] = row
    return out


def trace_lookup(requirement_result: dict) -> dict[str, dict]:
    return {
        str(row["need_id"]): row
        for row in requirement_result.get("retrieval_traces", [])
    }


def existing_execution_action_ids(executions: list[dict]) -> set[str]:
    return {
        str(row["action_id"])
        for row in executions
        if row.get("action_id")
    }


# ============================================================================
# Reliability action
# ============================================================================


def build_reliability_assessment(
    *,
    action: dict,
    target_artifact_id: str,
    alignment: dict | None,
) -> dict:
    """Create a conservative InformationReliabilityAssessment.

    Exact quote validation and identity admission do NOT establish underlying
    information reliability.  Without explicit accuracy/completeness/control
    tests, the assessment remains UNRESOLVED and only permits the inference that
    the statement/document exists.
    """

    if alignment is None:
        assessment = {
            "assessment_id": stable_id(
                "reliability",
                action["action_id"],
                target_artifact_id,
            ),
            "action_id": action["action_id"],
            "target_artifact_id": target_artifact_id,
            "fact_candidate_id": None,
            "evidence_id": None,
            "information_origin": "UNKNOWN",
            "acquisition_mode": "UNKNOWN",
            "accuracy_test_state": "NOT_PERFORMED",
            "completeness_test_state": "NOT_PERFORMED",
            "precision_detail_state": "UNKNOWN",
            "generating_control_state": "NOT_TESTED",
            "corroboration_state": "UNCORROBORATED",
            "permitted_inference_scope": "STATEMENT_MADE",
            "status": "UNRESOLVED",
            "basis_artifact_ids": [target_artifact_id],
            "reason_codes": [
                "TARGET_ARTIFACT_NOT_RESOLVED_TO_ALIGNMENT",
                "INFORMATION_ORIGIN_NOT_ESTABLISHED",
                "ACCURACY_TEST_NOT_PERFORMED",
                "COMPLETENESS_TEST_NOT_PERFORMED",
                "PRECISION_NOT_ASSESSED",
                "GENERATING_CONTROL_NOT_TESTED",
            ],
        }
        assessment["assessment_sha256"] = sha256_json(assessment)
        return assessment

    fact = alignment.get("fact_candidate") or {}
    fact_candidate_id = (
        alignment.get("fact_candidate_id")
        or fact.get("fact_candidate_id")
    )

    exact_quote = str(
        alignment.get("exact_quote")
        or fact.get("quote")
        or ""
    ).strip()

    reason_codes = [
        "INFORMATION_ORIGIN_NOT_ESTABLISHED",
        "ACCURACY_TEST_NOT_PERFORMED",
        "COMPLETENESS_TEST_NOT_PERFORMED",
        "PRECISION_NOT_ASSESSED",
        "GENERATING_CONTROL_NOT_TESTED",
        "INDEPENDENT_CORROBORATION_NOT_ESTABLISHED",
    ]

    if not exact_quote:
        reason_codes.append("SOURCE_QUOTE_MISSING")

    assessment = {
        "assessment_id": stable_id(
            "reliability",
            action["action_id"],
            target_artifact_id,
            str(fact_candidate_id),
        ),
        "action_id": action["action_id"],
        "target_artifact_id": target_artifact_id,
        "fact_candidate_id": fact_candidate_id,
        "evidence_id": alignment.get("evidence_id"),

        # Do not infer company/external origin from filenames or package location.
        "information_origin": "UNKNOWN",

        # Source speech_act is not the audit acquisition mode.
        "acquisition_mode": "UNKNOWN",

        "accuracy_test_state": "NOT_PERFORMED",
        "completeness_test_state": "NOT_PERFORMED",
        "precision_detail_state": "UNKNOWN",
        "generating_control_state": "NOT_TESTED",
        "corroboration_state": "UNCORROBORATED",

        # Exact grounded source establishes at most that the statement was made.
        "permitted_inference_scope": "STATEMENT_MADE",

        # Core extension consumed later by proof_standard_v1.
        "status": "UNRESOLVED",

        "source_quote": exact_quote,
        "identity_use_decision": alignment.get(
            "identity_use_decision"
        ),
        "relation": alignment.get("relation"),

        "basis_artifact_ids": [
            target_artifact_id,
            *(
                [str(alignment.get("alignment_evidence_id"))]
                if alignment.get("alignment_evidence_id")
                else []
            ),
        ],
        "reason_codes": sorted(set(reason_codes)),
    }
    assessment["assessment_sha256"] = sha256_json(assessment)
    return assessment


def execute_reliability_action(
    *,
    action: dict,
    requirement_result: dict,
) -> dict:
    lookup = alignment_lookup(requirement_result)

    assessments = [
        build_reliability_assessment(
            action=action,
            target_artifact_id=str(target),
            alignment=lookup.get(str(target)),
        )
        for target in action.get("target_artifact_ids", [])
    ]

    passed = sum(
        row["status"] == "PASS"
        for row in assessments
    )
    unresolved = sum(
        row["status"] == "UNRESOLVED"
        for row in assessments
    )
    failed = sum(
        row["status"] == "FAIL"
        for row in assessments
    )

    signal_status = (
        "NEW_VALIDATED_SIGNAL"
        if passed or failed
        else "NO_NEW_VALIDATED_SIGNAL"
    )

    execution = {
        "schema": "freca-core-repair-action-execution-v1",
        "execution_id": stable_id(
            "repair-exec",
            action["action_id"],
            "reliability",
        ),
        "action_id": action["action_id"],
        "goal_id": action["goal_id"],
        "action_type": action["action_type"],
        "action_signature": action["action_signature"],
        "target_artifact_ids": [
            str(x)
            for x in action.get("target_artifact_ids", [])
        ],
        "information_reliability_assessments": assessments,
        "passed_count": passed,
        "unresolved_count": unresolved,
        "failed_count": failed,
        "signal_status": signal_status,
        "action_execution_status": "EXECUTED",
        "required_return_path": [
            "LAYER5_INFORMATION_RELIABILITY",
            "LAYER7_PROOF_STANDARD_REEVALUATION",
        ],
        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,
    }
    execution["execution_sha256"] = sha256_json(execution)
    return execution


# ============================================================================
# Alignment next batch action
# ============================================================================


def build_mini_trace(
    *,
    action: dict,
    requirement_result: dict,
) -> dict:
    need_id = str(action.get("query_plan_id") or "")
    traces = trace_lookup(requirement_result)

    if need_id not in traces:
        raise ValueError(
            f"Cannot find original retrieval trace for need {need_id}"
        )

    source_trace = traces[need_id]

    universe = source_trace.get("candidate_universe")
    if not isinstance(universe, list):
        raise ValueError(
            f"Trace {need_id} has no candidate_universe"
        )

    by_id = {
        str(row.get("evidence_id")): row
        for row in universe
    }

    target_ids = [
        str(x)
        for x in action.get("target_artifact_ids", [])
    ]

    missing = [
        target
        for target in target_ids
        if target not in by_id
    ]

    if missing:
        raise ValueError(
            "RepairAction targets are missing from candidate universe: "
            + repr(missing[:5])
        )

    candidates = [
        dict(by_id[target])
        for target in target_ids
    ]

    # Preserve exactly the need metadata expected by _alignment_pairs.
    mini_trace = {
        key: value
        for key, value in source_trace.items()
        if key not in {
            "candidates",
            "candidate_universe",
            "candidate_universe_ids",
            "model_context_candidate_ids",
        }
    }

    mini_trace["candidates"] = candidates
    mini_trace["candidate_universe"] = candidates
    mini_trace["candidate_universe_ids"] = target_ids
    mini_trace["model_context_candidate_ids"] = target_ids
    mini_trace["model_context_count"] = len(target_ids)
    mini_trace["coverage_status"] = "REPAIR_ALIGN_NEXT_BATCH"

    return mini_trace


def execute_alignment_action(
    *,
    action: dict,
    requirement_result: dict,
    aligner: Callable[[dict, list[dict]], list[dict]] | None = None,
) -> dict:
    mini_trace = build_mini_trace(
        action=action,
        requirement_result=requirement_result,
    )

    plan = requirement_result["evidence_requirement_plan"]

    if aligner is None:
        import evidence_reasoning_v2 as er

        def aligner(
            plan_arg: dict,
            traces_arg: list[dict],
        ) -> list[dict]:
            return er.align_requirement_evidence(
                plan_arg,
                traces_arg,
                batch_size=8,
            )

    new_alignments = aligner(
        plan,
        [mini_trace],
    )

    old_alignment_ids = {
        str(
            row.get("alignment_evidence_id")
            or row.get("fact_candidate_id")
            or ""
        )
        for row in requirement_result.get("alignments", [])
    }

    novel = [
        row
        for row in new_alignments
        if str(
            row.get("alignment_evidence_id")
            or row.get("fact_candidate_id")
            or ""
        )
        not in old_alignment_ids
    ]

    direct = sum(
        row.get("argument_admission_channel") == "DIRECT"
        for row in novel
    )
    conditional = sum(
        row.get("argument_admission_channel") == "CONDITIONAL"
        for row in novel
    )
    rejected = sum(
        row.get("argument_admission_channel") == "REJECTED"
        for row in novel
    )

    semantic = sum(
        row.get("relation") in {"SUPPORT", "ATTACK"}
        for row in novel
    )

    execution = {
        "schema": "freca-core-repair-action-execution-v1",
        "execution_id": stable_id(
            "repair-exec",
            action["action_id"],
            "alignment",
        ),
        "action_id": action["action_id"],
        "goal_id": action["goal_id"],
        "action_type": action["action_type"],
        "action_signature": action["action_signature"],
        "need_id": action.get("query_plan_id"),
        "target_artifact_ids": [
            str(x)
            for x in action.get("target_artifact_ids", [])
        ],
        "mini_trace": mini_trace,
        "new_alignments": novel,
        "new_alignment_count": len(novel),
        "semantic_alignment_count": semantic,
        "direct_argument_input_count": direct,
        "conditional_argument_input_count": conditional,
        "rejected_alignment_count": rejected,
        "signal_status": (
            "NEW_VALIDATED_SIGNAL"
            if novel
            else "NO_NEW_VALIDATED_SIGNAL"
        ),
        "action_execution_status": "EXECUTED",
        "required_return_path": [
            "LAYER7_ALIGNMENT_VALIDATOR",
            "LAYER7_COVERAGE",
            "LAYER7_ARGUMENT_GRAPH",
            "LAYER7_PROOF_STANDARD",
        ],
        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,
    }
    execution["execution_sha256"] = sha256_json(execution)
    return execution


# ============================================================================
# Round bundle
# ============================================================================


def normalize_existing_execution(payload: dict) -> dict:
    # Existing repair_executor_v1 RESOLVE_TIME artifact is already a valid
    # execution record; preserve it exactly as an embedded historical artifact.
    return payload


def execute_remaining_round_actions(
    *,
    repair_plan: dict,
    requirement_result: dict,
    existing_executions: list[dict],
    action_indices: list[int],
    aligner: Callable[[dict, list[dict]], list[dict]] | None = None,
) -> dict:
    existing_ids = existing_execution_action_ids(existing_executions)

    executions = [
        normalize_existing_execution(row)
        for row in existing_executions
    ]

    for index in action_indices:
        action = action_by_index(
            repair_plan,
            index,
        )

        action_id = str(action["action_id"])

        if action_id in existing_ids:
            raise ValueError(
                f"Action {action_id} already has an execution artifact"
            )

        action_type = action.get("action_type")

        if action_type == "ASSESS_INFORMATION_RELIABILITY":
            execution = execute_reliability_action(
                action=action,
                requirement_result=requirement_result,
            )

        elif action_type == "ALIGN_NEXT_CANDIDATE_BATCH":
            execution = execute_alignment_action(
                action=action,
                requirement_result=requirement_result,
                aligner=aligner,
            )

        else:
            raise ValueError(
                "repair_round_executor_v1 supports only remaining action types "
                "ASSESS_INFORMATION_RELIABILITY and ALIGN_NEXT_CANDIDATE_BATCH; "
                f"got {action_type}"
            )

        executions.append(execution)
        existing_ids.add(action_id)

    planned_ids = {
        str(row["action_id"])
        for row in repair_plan.get("actions", [])
    }

    executed_ids = {
        str(row["action_id"])
        for row in executions
        if row.get("action_id")
    }

    missing_action_ids = sorted(
        planned_ids
        - executed_ids
    )

    new_alignment_ids = []
    reliability_ids = []
    temporal_ids = []

    for execution in executions:
        for row in execution.get("new_alignments", []):
            value = (
                row.get("alignment_evidence_id")
                or row.get("fact_candidate_id")
            )
            if value:
                new_alignment_ids.append(str(value))

        for row in execution.get(
            "information_reliability_assessments",
            [],
        ):
            reliability_ids.append(
                str(row["assessment_id"])
            )

        for row in execution.get(
            "temporal_assessments",
            [],
        ):
            temporal_ids.append(
                str(row["assessment_id"])
            )

    round_complete = not missing_action_ids

    bundle = {
        "schema": "freca-core-repair-round-artifacts-v1",
        "round_artifact_bundle_id": stable_id(
            "repair-round",
            repair_plan["plan_id"],
            str(repair_plan["round_index"]),
            *sorted(executed_ids),
        ),
        "repair_plan_id": repair_plan["plan_id"],
        "round_index": repair_plan["round_index"],
        "planned_action_ids": sorted(planned_ids),
        "executed_action_ids": sorted(executed_ids),
        "missing_action_ids": missing_action_ids,
        "round_execution_complete": round_complete,
        "action_executions": executions,

        "new_alignment_ids": sorted(set(new_alignment_ids)),
        "new_alignment_count": len(set(new_alignment_ids)),
        "information_reliability_assessment_ids": sorted(
            set(reliability_ids)
        ),
        "temporal_assessment_ids": sorted(set(temporal_ids)),

        "any_new_validated_signal": any(
            row.get("signal_status")
            in {
                "NEW_VALIDATED_SIGNAL",
                "PARTIAL_NEW_SIGNAL",
            }
            for row in executions
        ),

        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,

        "next_step": (
            "MERGE_VALIDATED_ARTIFACTS_AND_RERUN_AFFECTED_LAYER7"
            if round_complete
            else "EXECUTE_REMAINING_PLANNED_ACTIONS"
        ),
    }

    bundle["bundle_sha256"] = sha256_json(bundle)
    return bundle


# ============================================================================
# Tests
# ============================================================================


def run_self_tests() -> None:
    rr = {
        "evidence_requirement_plan": {
            "requirements": [
                {
                    "requirement_id": "ER2",
                    "atom_id": "A1",
                    "decisiveness": "DECISIVE",
                    "proposition_to_establish": "fixture",
                    "query_sources": [],
                }
            ]
        },
        "retrieval_traces": [
            {
                "need_id": "ER2.attack",
                "requirement_id": "ER2",
                "direction": "ATTACK",
                "candidate_universe": [
                    {
                        "evidence_id": "doc:P1",
                        "text": "fixture adverse observation",
                        "identity_use_decision": "ADMIT_DIRECT",
                        "identity_decisive_proof_eligible": True,
                    },
                    {
                        "evidence_id": "doc:P2",
                        "text": "fixture second observation",
                        "identity_use_decision": "ADMIT_DIRECT",
                        "identity_decisive_proof_eligible": True,
                    },
                ],
            }
        ],
        "alignments": [
            {
                "requirement_id": "ER2",
                "alignment_evidence_id": "doc:P0#fc-a",
                "fact_candidate_id": "fc-a",
                "evidence_id": "doc:P0",
                "exact_quote": "Existing adverse basis.",
                "relation": "ATTACK",
                "identity_use_decision": "ADMIT_DIRECT",
                "fact_candidate": {
                    "fact_candidate_id": "fc-a",
                    "quote": "Existing adverse basis.",
                },
            }
        ],
    }

    repair = {
        "plan_id": "plan",
        "round_index": 1,
        "actions": [
            {
                "action_id": "a1",
                "goal_id": "g-time",
                "action_type": "RESOLVE_TIME",
                "action_signature": "sha256:a1",
                "target_artifact_ids": ["doc:P0#fc-a"],
                "query_plan_id": "ER2.attack",
            },
            {
                "action_id": "a2",
                "goal_id": "g-rel",
                "action_type": "ASSESS_INFORMATION_RELIABILITY",
                "action_signature": "sha256:a2",
                "target_artifact_ids": ["doc:P0#fc-a"],
                "query_plan_id": "ER2.attack",
            },
            {
                "action_id": "a3",
                "goal_id": "g-align",
                "action_type": "ALIGN_NEXT_CANDIDATE_BATCH",
                "action_signature": "sha256:a3",
                "target_artifact_ids": ["doc:P1", "doc:P2"],
                "query_plan_id": "ER2.attack",
            },
        ],
    }

    existing = [
        {
            "schema": "freca-core-repair-execution-v1",
            "action_id": "a1",
            "action_type": "RESOLVE_TIME",
            "signal_status": "NO_NEW_VALIDATED_SIGNAL",
            "temporal_assessments": [
                {
                    "assessment_id": "t1",
                    "status": "UNKNOWN",
                }
            ],
        }
    ]

    def stub_aligner(plan, traces):
        assert len(traces) == 1
        assert len(traces[0]["candidates"]) == 2
        return [
            {
                "alignment_evidence_id": "doc:P1#fc-new",
                "fact_candidate_id": "fc-new",
                "evidence_id": "doc:P1",
                "relation": "ATTACK",
                "argument_admission_channel": "DIRECT",
            }
        ]

    bundle = execute_remaining_round_actions(
        repair_plan=repair,
        requirement_result=rr,
        existing_executions=existing,
        action_indices=[2, 3],
        aligner=stub_aligner,
    )

    assert bundle["round_execution_complete"] is True
    assert bundle["new_alignment_count"] == 1
    assert len(
        bundle["information_reliability_assessment_ids"]
    ) == 1
    assert bundle["any_new_validated_signal"] is True
    assert bundle["proof_state_modified"] is False
    assert bundle["final_label"] is None

    rel_exec = next(
        x for x in bundle["action_executions"]
        if x.get("action_id") == "a2"
    )

    assessment = rel_exec[
        "information_reliability_assessments"
    ][0]

    assert assessment["status"] == "UNRESOLVED"
    assert assessment["accuracy_test_state"] == "NOT_PERFORMED"
    assert assessment["completeness_test_state"] == "NOT_PERFORMED"

    print("repair_round_executor_v1 self-tests: PASS")
    print("  existing RESOLVE_TIME execution preserved")
    print("  reliability assessment stays conservative/unresolved")
    print("  next batch reuses alignment adapter")
    print("  entire RepairPlan round can be completed")
    print("  upstream proof/result remains untouched")


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--repair-plan", type=Path)
    parser.add_argument("--requirement-result", type=Path)
    parser.add_argument(
        "--existing-execution",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument(
        "--action-index",
        type=int,
        action="append",
        default=[],
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        if (
            args.repair_plan is None
            and args.requirement_result is None
        ):
            return

    if args.repair_plan is None or args.requirement_result is None:
        parser.error(
            "--repair-plan and --requirement-result are required "
            "unless only --self-test is used"
        )

    if not args.action_index:
        parser.error(
            "provide at least one --action-index for remaining actions"
        )

    repair_plan = load_json(args.repair_plan)
    requirement_result = load_json(args.requirement_result)

    existing = [
        load_json(path)
        for path in args.existing_execution
    ]

    bundle = execute_remaining_round_actions(
        repair_plan=repair_plan,
        requirement_result=requirement_result,
        existing_executions=existing,
        action_indices=args.action_index,
    )

    output = (
        args.output
        or args.repair_plan.with_name(
            args.repair_plan.stem
            + "_round_artifacts_v1.json"
        )
    )

    save_json(bundle, output)

    print("=" * 72)
    print("FRECA REPAIR ROUND EXECUTOR V1")
    print("=" * 72)
    print()
    print("Round:", bundle["round_index"])
    print(
        "Executed/planned:",
        len(bundle["executed_action_ids"]),
        "/",
        len(bundle["planned_action_ids"]),
    )
    print(
        "Round complete:",
        bundle["round_execution_complete"],
    )
    print()

    for execution in bundle["action_executions"]:
        print(
            execution.get("action_id"),
            execution.get("action_type"),
            "signal=",
            execution.get("signal_status"),
        )

        if (
            execution.get("action_type")
            == "ASSESS_INFORMATION_RELIABILITY"
        ):
            print(
                "  reliability:",
                {
                    "pass": execution.get("passed_count"),
                    "unresolved": execution.get(
                        "unresolved_count"
                    ),
                    "fail": execution.get("failed_count"),
                },
            )

        if (
            execution.get("action_type")
            == "ALIGN_NEXT_CANDIDATE_BATCH"
        ):
            print(
                "  new alignments:",
                execution.get("new_alignment_count"),
                "semantic=",
                execution.get("semantic_alignment_count"),
                "direct=",
                execution.get("direct_argument_input_count"),
                "conditional=",
                execution.get(
                    "conditional_argument_input_count"
                ),
                "rejected=",
                execution.get("rejected_alignment_count"),
            )

    print()
    print(
        "Round new alignments:",
        bundle["new_alignment_count"],
    )
    print(
        "Any new validated signal:",
        bundle["any_new_validated_signal"],
    )
    print(
        "Proof modified:",
        bundle["proof_state_modified"],
    )
    print(
        "Final label:",
        bundle["final_label"],
    )
    print(
        "Next step:",
        bundle["next_step"],
    )
    print(
        "Saved:",
        output,
    )


if __name__ == "__main__":
    main()
