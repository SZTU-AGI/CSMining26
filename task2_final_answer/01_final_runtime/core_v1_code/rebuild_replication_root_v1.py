#!/usr/bin/env python3
"""Rebuild a FRECA replication root with the CURRENT retrieval/identity/alignment stack.

Purpose
-------
Older pilot requirement_result files may predate candidate-universe v1.1.
They cannot be fairly compared against Pilot C because their persisted universe
can be only the old model-context top-k.

This script creates a NEW replication root without overwriting the old result.

Frozen semantics
----------------
- Reuse the existing evidence_requirement_plan EXACTLY from the old root.
- Do NOT recompile EvidenceRequirements or legal interpretation.
- Reparse the same case package with the existing Core parser.
- Rebuild RetrievalNeeds with the current deterministic adapter.
- Run the current candidate-generation / candidate-universe pipeline.
- Run the current body-first identity gate.
- Run the current FactCandidate/alignment/validator pipeline.
- Run only the current minimal proof gate (still unlocked).
- Persist transparent DeepSeek cost telemetry.
- Do NOT execute repair.
- Do NOT emit final 1/0/N/A.
- Do NOT consume answer comparators or historical labels.

The resulting file is intended to be bootstrapped by bootstrap_frozen_root_v1.py
before paired A/B replication.
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


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_replication_root(
    *,
    old_requirement_result: dict,
    evidence_chunks: list[dict],
    build_needs: Callable[[dict], list[dict]],
    retrieve: Callable[..., list[dict]],
    build_identity_report: Callable[..., dict],
    apply_identity: Callable[[list[dict], dict], list[dict]],
    align: Callable[[dict, list[dict]], list[dict]],
    evaluate_minimal_proof: Callable[[dict, list[dict], list[dict]], dict],
    retrieval_top_k: int = 12,
    capture_telemetry: bool = True,
) -> dict:
    plan = copy.deepcopy(
        old_requirement_result["evidence_requirement_plan"]
    )

    plan_before_sha = sha256_json(plan)

    cp_id = str(
        old_requirement_result.get("cp_id")
        or plan.get("cp_id")
        or ""
    )
    case_id = str(
        old_requirement_result.get("case_id")
        or ""
    )

    if not cp_id:
        raise ValueError("cp_id missing from frozen root/plan")

    if not case_id:
        raise ValueError("case_id missing from frozen root")

    needs = build_needs(plan)

    traces = retrieve(
        evidence_chunks,
        needs,
        top_k=retrieval_top_k,
    )

    identity_report = build_identity_report(
        evidence_chunks,
        output_identifier=case_id,
    )

    traces = apply_identity(
        traces,
        identity_report,
    )

    # Hard requirement: this is a CURRENT candidate-universe root, not another
    # old top-k root.
    for trace in traces:
        if not isinstance(
            trace.get("candidate_universe"),
            list,
        ):
            raise RuntimeError(
                f"{trace.get('need_id')}: current retrieval did not persist "
                "candidate_universe; replication root would be invalid"
            )

        if not trace.get(
            "candidate_universe_persisted",
            False,
        ):
            raise RuntimeError(
                f"{trace.get('need_id')}: candidate_universe_persisted != True"
            )

    if capture_telemetry:
        with capture_deepseek_telemetry() as events:
            alignments = align(
                plan,
                traces,
            )
        cost = summarize_telemetry(events)
    else:
        alignments = align(
            plan,
            traces,
        )
        cost = {
            "schema":
                "freca-core-cost-telemetry-v1",
            "status":
                "TEST_STUB",
            "request_attempt_count":
                0,
            "successful_call_count":
                0,
            "failed_call_count":
                0,
            "prompt_tokens":
                0,
            "completion_tokens":
                0,
            "total_tokens":
                0,
            "wall_time_ms":
                0,
            "semantic_configuration_modified":
                False,
            "answer_comparator_used":
                False,
        }

    proof = evaluate_minimal_proof(
        plan,
        traces,
        alignments,
    )

    # Re-assert plan immutability after all downstream functions.
    if sha256_json(plan) != plan_before_sha:
        raise RuntimeError(
            "Frozen evidence_requirement_plan was mutated downstream"
        )

    universe_summary = {}

    for trace in traces:
        need_id = str(trace.get("need_id"))

        universe = trace.get(
            "candidate_universe",
            [],
        )

        context = trace.get(
            "candidates",
            [],
        )

        universe_summary[need_id] = {
            "candidate_universe_count":
                len(universe),

            "model_context_count":
                len(context),

            "candidate_universe_persisted":
                bool(
                    trace.get(
                        "candidate_universe_persisted",
                        False,
                    )
                ),

            "coverage_requirement":
                trace.get("coverage_requirement"),

            "candidate_generation_policy":
                trace.get(
                    "candidate_generation_policy"
                ),

            "context_packing_policy":
                trace.get(
                    "context_packing_policy"
                ),
        }

    result = {
        "schema":
            "freca-core-requirement-reasoning-v2-replication-root-v1",

        "cp_id":
            cp_id,

        "case_id":
            case_id,

        "source_old_requirement_result_sha256":
            sha256_json(old_requirement_result),

        "frozen_evidence_requirement_plan_sha256":
            plan_before_sha,

        "evidence_requirement_plan":
            plan,

        "retrieval_needs":
            needs,

        "identity_admissibility":
            identity_report,

        "retrieval_traces":
            traces,

        "alignments":
            alignments,

        "proof_gate":
            proof,

        "root_build_cost_telemetry":
            cost,

        "replication_root_policy": {
            "evidence_requirement_plan_recompiled":
                False,

            "old_root_overwritten":
                False,

            "repair_executed":
                False,

            "final_label":
                None,

            "answer_comparator_used":
                False,

            "human_or_historical_labels_used":
                False,

            "retrieval_top_k":
                retrieval_top_k,

            "candidate_universe_required":
                True,
        },

        "candidate_universe_summary":
            universe_summary,
    }

    result["replication_root_sha256"] = sha256_json(
        result
    )

    return result


def run_self_tests() -> None:
    old = {
        "cp_id":
            "CPX",
        "case_id":
            "case-x",
        "evidence_requirement_plan": {
            "cp_id":
                "CPX",
            "requirements": [
                {
                    "requirement_id":
                        "ER1",
                    "atom_id":
                        "A1",
                }
            ],
        },
    }

    chunks = [
        {
            "id":
                "doc:P1",
            "text":
                "fixture",
        }
    ]

    def build_needs(plan):
        return [
            {
                "need_id":
                    "ER1.attack",
                "requirement_id":
                    "ER1",
                "direction":
                    "ATTACK",
                "coverage_requirement":
                    "CANDIDATE_DISCOVERY",
            }
        ]

    def retrieve(chunks, needs, *, top_k):
        return [
            {
                **needs[0],
                "candidate_universe_persisted":
                    True,
                "candidate_universe": [
                    {
                        "evidence_id":
                            "doc:P1",
                        "text":
                            "fixture",
                    }
                ],
                "candidates": [
                    {
                        "evidence_id":
                            "doc:P1",
                        "text":
                            "fixture",
                    }
                ],
            }
        ]

    def identity_report(chunks, output_identifier):
        return {
            "case_id":
                output_identifier,
        }

    def apply_identity(traces, identity):
        out = copy.deepcopy(traces)

        for trace in out:
            for row in trace["candidate_universe"]:
                row["identity_use_decision"] = "ADMIT_DIRECT"
                row[
                    "identity_decisive_proof_eligible"
                ] = True

            trace["candidates"] = copy.deepcopy(
                trace["candidate_universe"]
            )

        return out

    def align(plan, traces):
        return [
            {
                "alignment_evidence_id":
                    "doc:P1#fc1",
                "fact_candidate_id":
                    "fc1",
                "evidence_id":
                    "doc:P1",
                "requirement_id":
                    "ER1",
                "retrieval_need_ids":
                    ["ER1.attack"],
                "relation":
                    "IRRELEVANT",
                "argument_admission_channel":
                    "REJECTED",
                "argument_truth_bearing":
                    False,
            }
        ]

    def minimal(plan, traces, alignments):
        return {
            "coverage_complete":
                False,
            "internal_outcome":
                "UNKNOWN",
            "submission_label":
                None,
        }

    root = build_replication_root(
        old_requirement_result=old,
        evidence_chunks=chunks,
        build_needs=build_needs,
        retrieve=retrieve,
        build_identity_report=identity_report,
        apply_identity=apply_identity,
        align=align,
        evaluate_minimal_proof=minimal,
        capture_telemetry=False,
    )

    assert (
        root["frozen_evidence_requirement_plan_sha256"]
        == sha256_json(old["evidence_requirement_plan"])
    )

    assert (
        root["candidate_universe_summary"][
            "ER1.attack"
        ]["candidate_universe_count"]
        == 1
    )

    assert (
        root["replication_root_policy"][
            "evidence_requirement_plan_recompiled"
        ]
        is False
    )

    assert (
        root["replication_root_policy"][
            "answer_comparator_used"
        ]
        is False
    )

    assert root["proof_gate"]["submission_label"] is None

    print("rebuild_replication_root_v1 self-tests: PASS")
    print("  frozen EvidenceRequirement plan reused byte-semantically")
    print("  current persisted candidate universe required")
    print("  old requirement root not overwritten")
    print("  telemetry slot persisted")
    print("  no repair / final label / answer comparator")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--old-requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--case",
        type=str,
    )

    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=12,
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

        if args.old_requirement_result is None:
            return

    if args.old_requirement_result is None:
        parser.error(
            "--old-requirement-result is required"
        )

    old = load_json(
        args.old_requirement_result
    )

    case_id = (
        args.case
        or old.get("case_id")
    )

    if not case_id:
        parser.error(
            "--case is required when old root has no case_id"
        )

    import freca_core_v1 as core
    import evidence_reasoning_v2 as er
    from identity_admissibility_v1 import (
        apply_identity_gate_to_traces,
        build_body_first_identity_report,
    )

    case_dir = core.find_case_dir(
        str(case_id)
    )

    evidence_chunks = core.load_case_evidence(
        case_dir
    )

    root = build_replication_root(
        old_requirement_result=old,
        evidence_chunks=evidence_chunks,
        build_needs=er.build_retrieval_needs,
        retrieve=er.retrieve_requirement_candidates,
        build_identity_report=build_body_first_identity_report,
        apply_identity=apply_identity_gate_to_traces,
        align=er.align_requirement_evidence,
        evaluate_minimal_proof=er.evaluate_minimal_proof_gate,
        retrieval_top_k=args.retrieval_top_k,
        capture_telemetry=True,
    )

    cp_id = str(
        root["cp_id"]
    )

    output = (
        args.output
        or Path("results_v2/replication_roots")
        / f"{case_id}_{cp_id}_requirement_reasoning_v2.json"
    )

    save_json(
        root,
        output,
    )

    print("=" * 78)
    print("FRECA REPLICATION ROOT REBUILD V1")
    print("=" * 78)

    print()
    print("Case:", case_id)
    print("CP:", cp_id)
    print(
        "Frozen plan SHA:",
        root["frozen_evidence_requirement_plan_sha256"],
    )

    print()
    print("Candidate universes:")

    for need_id, summary in sorted(
        root["candidate_universe_summary"].items()
    ):
        print(
            f"  {need_id}: "
            f"universe={summary['candidate_universe_count']} "
            f"context={summary['model_context_count']} "
            f"persisted={summary['candidate_universe_persisted']}"
        )

    cost = root["root_build_cost_telemetry"]

    print()
    print("Root-build cost telemetry:")
    print(
        "  attempts/success/fail:",
        cost.get("request_attempt_count"),
        cost.get("successful_call_count"),
        cost.get("failed_call_count"),
    )
    print(
        "  prompt/completion/total tokens:",
        cost.get("prompt_tokens"),
        cost.get("completion_tokens"),
        cost.get("total_tokens"),
    )
    print(
        "  wall_time_ms:",
        cost.get("wall_time_ms"),
    )

    print()
    print("Plan recompiled: False")
    print("Repair executed: False")
    print("Final label: None")
    print("Old root overwritten: False")
    print("Saved:", output)


if __name__ == "__main__":
    main()
