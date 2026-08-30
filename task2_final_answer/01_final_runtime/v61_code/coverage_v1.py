#!/usr/bin/env python3
"""FRECA Core RequirementCoverage substrate v1.

This module audits whether the CURRENT retrieval/alignment trace is sufficient
to claim RequirementCoverage COMPLETE under the frozen Core policy.

It does NOT change retrieval, alignment, proof, or labels.

Frozen minimal coverage policy:
    - RAW_LEXICAL required
    - TYPED_FACT required
    - STRUCTURE required
    - SEMANTIC_VECTOR not required
    - top-k / model context never implies complete
    - candidate universe must be preserved and fully disposed
    - contradictory evidence does not reduce coverage
    - no adverse inference is produced here

The current Core trace is expected to remain incomplete until retrieval is
refactored to separate:
    candidate universe
        from
    model context batch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


REQUIRED_CHANNELS = (
    "RAW_LEXICAL",
    "TYPED_FACT",
    "STRUCTURE",
)


def canonical_json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(
    value: Any,
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json(value).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def load_json(
    path: Path,
) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def save_json(
    value: Any,
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def stable_id(
    *parts: str,
) -> str:
    raw = "\n".join(
        str(x)
        for x in parts
    )

    return (
        "cov-"
        + hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()[:20]
    )


def alignment_id(
    row: dict,
) -> str:
    return str(
        row.get(
            "alignment_evidence_id"
        )
        or row.get(
            "fact_candidate_id"
        )
        or row.get(
            "evidence_id"
        )
        or "unknown-alignment"
    )


# ============================================================================
# Trace channel interpretation
# ============================================================================


def _variant_full_ids_preserved(
    trace: dict,
) -> bool:
    """Current Core stores only top_ids, not complete ranked IDs.

    If a future trace explicitly carries full_ids or universe_ids, this helper
    recognizes it without changing the schema contract.
    """

    variants = trace.get(
        "query_variants",
        [],
    )

    if not variants:
        return False

    for variant in variants:
        if (
            "full_ids"
            not in variant
            and "universe_ids"
            not in variant
        ):
            return False

    return True



def lexical_channel(
    trace: dict,
) -> dict:
    """Audit execution of RAW_LEXICAL without equating all positive scores to candidates."""

    raw = trace.get(
        "raw_lexical_scan"
    )

    variants = trace.get(
        "query_variants",
        [],
    )

    if isinstance(
        raw,
        dict,
    ):
        scan_count = int(
            raw.get(
                "scan_chunk_count",
                0,
            )
            or 0
        )

        scan_complete = bool(
            raw.get(
                "scan_complete",
                False,
            )
        )

        generated_union_count = int(
            raw.get(
                "generated_union_count",
                0,
            )
            or 0
        )

        mode = str(
            raw.get(
                "candidate_generation_mode",
                "UNKNOWN",
            )
        )

    else:
        scan_count = int(
            trace.get(
                "retrieval_scan_chunk_count",
                0,
            )
            or 0
        )

        scan_complete = bool(
            variants
        )

        generated_union_count = 0

        mode = (
            "LEGACY_VARIANT_TRACE"
        )

    return {
        "channel":
            "RAW_LEXICAL",
        "executed":
            bool(
                variants
            ),
        "mode":
            mode,
        "scanned_record_count":
            scan_count,
        "scan_complete":
            scan_complete,
        "generated_candidate_count":
            generated_union_count,
        "candidate_limit_per_variant":
            (
                raw.get(
                    "candidate_limit_per_variant"
                )
                if isinstance(
                    raw,
                    dict,
                )
                else trace.get(
                    "candidate_limit"
                )
            ),
        "complete_for_candidate_discovery":
            bool(
                variants
                and scan_complete
            ),
    }




def typed_channel(
    trace: dict,
) -> dict:
    typed = trace.get(
        "typed_fact_scan"
    )

    if not isinstance(
        typed,
        dict,
    ):
        return {
            "channel":
                "TYPED_FACT",
            "executed":
                False,
            "mode":
                "NOT_EXECUTED",
            "scanned_record_count":
                0,
            "matched_count":
                0,
            "complete_for_candidate_discovery":
                False,
        }

    scan_count = int(
        typed.get(
            "scan_chunk_count",
            0,
        )
        or 0
    )

    full_case_count = int(
        trace.get(
            "retrieval_scan_chunk_count",
            0,
        )
        or 0
    )

    matched_count = int(
        typed.get(
            "matched_count",
            0,
        )
        or 0
    )

    full_scan = bool(
        typed.get(
            "full_case_scan",
            False,
        )
        or (
            scan_count > 0
            and (
                full_case_count == 0
                or scan_count
                == full_case_count
            )
        )
    )

    return {
        "channel":
            "TYPED_FACT",
        "executed":
            True,
        "mode":
            (
                "FULL_CASE_SCAN"
                if full_scan
                else "PARTIAL_SCAN"
            ),
        "scanned_record_count":
            scan_count,
        "matched_count":
            matched_count,
        "target_kinds":
            typed.get(
                "target_kinds",
                [],
            ),
        "wanted_natures":
            typed.get(
                "wanted_natures",
                [],
            ),
        "complete_for_candidate_discovery":
            full_scan,
    }





def structure_channel(
    trace: dict,
) -> dict:
    structure = (
        trace.get(
            "structure_scan"
        )
        or {}
    )

    universe = trace.get(
        "candidate_universe"
    )

    if not isinstance(
        universe,
        list,
    ):
        universe = trace.get(
            "candidates",
            [],
        )

    neighbour_hits = [
        candidate
        for candidate
        in universe
        if (
            "STRUCTURE_NEIGHBOUR"
            in candidate.get(
                "retrieval_methods",
                [],
            )
        )
    ]

    executed = bool(
        structure.get(
            "executed",
            False,
        )
        or neighbour_hits
    )

    mode = str(
        structure.get(
            "mode",
            (
                "SEED_NEIGHBOUR_RESCUE_ONLY"
                if neighbour_hits
                else "NOT_EXECUTED"
            ),
        )
    )

    return {
        "channel":
            "STRUCTURE",
        "executed":
            executed,
        "mode":
            mode,
        "scanned_record_count":
            int(
                structure.get(
                    "scan_chunk_count",
                    0,
                )
                or 0
            ),
        "seed_count":
            int(
                structure.get(
                    "seed_count",
                    0,
                )
                or 0
            ),
        "generated_candidate_count":
            int(
                structure.get(
                    "generated_candidate_count",
                    len(
                        neighbour_hits
                    ),
                )
                or 0
            ),
        # For CANDIDATE_DISCOVERY we require the registered structure procedure
        # to execute; we do NOT pretend neighbour rescue is a full structural
        # population scan.
        "complete_for_candidate_discovery":
            executed,
        "full_scan":
            bool(
                structure.get(
                    "full_scan",
                    False,
                )
            ),
    }




# ============================================================================
# Candidate disposition
# ============================================================================



def candidate_disposition(
    *,
    trace: dict,
    alignments: list[dict],
) -> dict:
    """Disposition over the FULL candidate universe, not only model context."""

    need_id = str(
        trace.get(
            "need_id",
            "",
        )
    )

    universe = trace.get(
        "candidate_universe"
    )

    if not isinstance(
        universe,
        list,
    ):
        universe = trace.get(
            "candidates",
            [],
        )

    context = trace.get(
        "candidates",
        [],
    )

    rows_for_need = [
        row
        for row in alignments
        if need_id
        in (
            row.get(
                "retrieval_need_ids",
                [],
            )
            or []
        )
    ]

    rows_by_parent = {}

    for row in rows_for_need:
        parent = str(
            row.get(
                "evidence_id",
                "",
            )
        )

        rows_by_parent.setdefault(
            parent,
            [],
        ).append(
            row
        )

    assessed = []
    excluded = []
    conditional = []
    unassessed = []

    for candidate in universe:
        evidence_id = str(
            candidate.get(
                "evidence_id",
                "",
            )
        )

        use = str(
            candidate.get(
                "identity_use_decision",
                "",
            )
        )

        # Deterministic Layer-5 dispositions are fully assessed without model
        # semantic alignment.
        if use in {
            "EXCLUDE_SUBSTANTIVE",
            "CONTEXT_ONLY",
            "GAP_SIGNAL_ONLY",
        }:
            assessed.append(
                evidence_id
            )
            excluded.append(
                evidence_id
            )
            continue

        rows = rows_by_parent.get(
            evidence_id,
            [],
        )

        if rows:
            assessed.append(
                evidence_id
            )

            if (
                use
                == "ADMIT_CONDITIONAL"
                or any(
                    row.get(
                        "argument_admission_channel"
                    )
                    == "CONDITIONAL"
                    for row
                    in rows
                )
            ):
                conditional.append(
                    evidence_id
                )

            continue

        # ADMIT_DIRECT/ADMIT_CONDITIONAL is only an identity/use decision.
        # Without a semantic relation disposition, the candidate remains
        # unassessed for RequirementCoverage.
        unassessed.append(
            evidence_id
        )

        if (
            use
            == "ADMIT_CONDITIONAL"
        ):
            conditional.append(
                evidence_id
            )

    return {
        "candidate_universe_count":
            len(
                universe
            ),
        "context_selected_count":
            len(
                context
            ),
        "universe_assessed_count":
            len(
                assessed
            ),
        "universe_unassessed_candidate_ids":
            sorted(
                set(
                    unassessed
                )
            ),
        "universe_excluded_candidate_ids":
            sorted(
                set(
                    excluded
                )
            ),
        "universe_conditional_candidate_ids":
            sorted(
                set(
                    conditional
                )
            ),
        "alignment_ids":
            [
                alignment_id(
                    row
                )
                for row
                in rows_for_need
            ],
        "model_or_rule_assessed_parent_ids":
            sorted(
                rows_by_parent
            ),

        # Backward-compatible aliases used by the v1 report builder.
        "selected_candidate_count":
            len(
                universe
            ),
        "selected_assessed_count":
            len(
                assessed
            ),
        "selected_unassessed_candidate_ids":
            sorted(
                set(
                    unassessed
                )
            ),
        "selected_excluded_candidate_ids":
            sorted(
                set(
                    excluded
                )
            ),
        "selected_conditional_candidate_ids":
            sorted(
                set(
                    conditional
                )
            ),
    }



# ============================================================================
# RequirementCoverage
# ============================================================================




def evaluate_need_coverage(
    *,
    trace: dict,
    alignments: list[dict],
) -> dict:
    """Evaluate the CURRENT need at the level requested by the need itself.

    Initial Core v1 needs use CANDIDATE_DISCOVERY.  That level can be
    procedurally complete without being sufficient for ProofStandard.

    This function therefore separates:
      - procedure_complete: did the registered discovery procedure run?
      - candidate_disposition_complete: were generated candidates disposed?
      - proof_coverage_pass: is the achieved level sufficient for proof?
    """

    need_id = str(
        trace.get(
            "need_id",
            "",
        )
    )

    requirement_id = str(
        trace.get(
            "requirement_id",
            "",
        )
    )

    direction = str(
        trace.get(
            "direction",
            "",
        )
    )

    required_level = str(
        trace.get(
            "coverage_requirement",
            "CANDIDATE_DISCOVERY",
        )
    )

    lexical = lexical_channel(
        trace
    )

    typed = typed_channel(
        trace
    )

    structure = structure_channel(
        trace
    )

    channels = {
        item[
            "channel"
        ]: item
        for item in (
            lexical,
            typed,
            structure,
        )
    }

    expected_channels = list(
        trace.get(
            "expected_channels",
            REQUIRED_CHANNELS,
        )
        or REQUIRED_CHANNELS
    )

    disposition = (
        candidate_disposition(
            trace=
                trace,
            alignments=
                alignments,
        )
    )

    candidate_universe_preserved = bool(
        trace.get(
            "candidate_universe_persisted",
            False,
        )
    )

    executed_channels = [
        name
        for name in expected_channels
        if (
            name in channels
            and channels[
                name
            ][
                "executed"
            ]
        )
    ]

    failed_channels = []
    limiting_factors = []

    if (
        required_level
        == "CANDIDATE_DISCOVERY"
    ):
        for name in expected_channels:
            channel = channels.get(
                name
            )

            if channel is None:
                failed_channels.append(
                    name
                )
                limiting_factors.append(
                    f"{name}_CHANNEL_MISSING"
                )
                continue

            if not channel.get(
                "complete_for_candidate_discovery",
                False,
            ):
                failed_channels.append(
                    name
                )
                limiting_factors.append(
                    f"{name}_DISCOVERY_PROCEDURE_INCOMPLETE"
                )

        procedure_complete = bool(
            not failed_channels
            and candidate_universe_preserved
        )

        achieved_level = (
            "CANDIDATE_DISCOVERY"
            if procedure_complete
            else "NONE"
        )

    else:
        # Higher coverage levels require an explicit ProcedureObjective /
        # PopulationFrame contract.  Do not invent semantics here.
        procedure_complete = False
        achieved_level = (
            "CANDIDATE_DISCOVERY"
            if candidate_universe_preserved
            else "NONE"
        )

        limiting_factors.append(
            "HIGHER_COVERAGE_PROCEDURE_OBJECTIVE_NOT_IMPLEMENTED"
        )

    if not candidate_universe_preserved:
        limiting_factors.append(
            "CANDIDATE_UNIVERSE_NOT_PERSISTED"
        )

    unassessed = disposition[
        "universe_unassessed_candidate_ids"
    ]

    candidate_disposition_complete = (
        len(
            unassessed
        )
        == 0
    )

    if not candidate_disposition_complete:
        limiting_factors.append(
            "CANDIDATE_UNIVERSE_NOT_FULLY_ASSESSED"
        )

    universe_count = disposition[
        "candidate_universe_count"
    ]

    context_count = disposition[
        "context_selected_count"
    ]

    context_cap = int(
        trace.get(
            "model_context_cap",
            trace.get(
                "support_context_cap",
                trace.get(
                    "attack_context_cap",
                    trace.get(
                        "top_k",
                        0,
                    ),
                ),
            ),
        )
        or 0
    )

    context_packing_status = (
        "ALL_UNIVERSE_IN_CONTEXT"
        if universe_count
        <= context_count
        else "PARTIAL_CONTEXT_BATCH"
    )

    identity_gap_ids = (
        disposition[
            "universe_conditional_candidate_ids"
        ]
    )

    parse_gap_ids = list(
        trace.get(
            "parse_gap_ids",
            [],
        )
        or []
    )

    missing_required_track_types = list(
        trace.get(
            "missing_required_track_types",
            [],
        )
        or []
    )

    if parse_gap_ids:
        status = (
            "INCOMPLETE_PARSE"
        )

    elif missing_required_track_types:
        status = (
            "INCOMPLETE_MISSING_SOURCE"
        )

    elif failed_channels:
        status = (
            "INCOMPLETE_CHANNEL_FAILURE"
        )

    elif (
        not candidate_universe_preserved
    ):
        status = (
            "ERROR"
        )

    elif (
        not candidate_disposition_complete
    ):
        status = (
            "LIMITED_TOP_K"
        )

    else:
        has_relevant_alignment = any(
            (
                row.get(
                    "relation"
                )
                in {
                    "SUPPORT",
                    "ATTACK",
                }
            )
            and need_id
            in (
                row.get(
                    "retrieval_need_ids",
                    [],
                )
                or []
            )
            for row in alignments
        )

        status = (
            "COMPLETE"
            if has_relevant_alignment
            else "COMPLETE_NO_RELEVANT_HIT"
        )

    # CANDIDATE_DISCOVERY is intentionally not enough for D7.14 proof.
    proof_coverage_pass = False

    if (
        required_level
        != "CANDIDATE_DISCOVERY"
    ):
        limiting_factors.append(
            "REQUESTED_COVERAGE_LEVEL_NOT_YET_SATISFIED"
        )
    else:
        limiting_factors.append(
            "CANDIDATE_DISCOVERY_NOT_PROOF_COVERAGE"
        )

    report = {
        "coverage_id":
            stable_id(
                requirement_id,
                need_id,
                direction,
            ),
        "need_id":
            need_id,
        "requirement_id":
            requirement_id,
        "direction":
            direction,

        "required_level":
            required_level,
        "achieved_level":
            achieved_level,

        "procedure_complete":
            procedure_complete,
        "candidate_disposition_complete":
            candidate_disposition_complete,
        "proof_coverage_pass":
            proof_coverage_pass,

        # Backward-compatible alias: never let discovery-only coverage satisfy
        # ProofStandard accidentally.
        "coverage_pass":
            proof_coverage_pass,

        "conclusion_scope":
            "SOURCE_PACKAGE_ONLY",

        "indexed_record_count":
            int(
                trace.get(
                    "retrieval_scan_chunk_count",
                    0,
                )
                or 0
            ),
        "searchable_record_count":
            int(
                trace.get(
                    "retrieval_scan_chunk_count",
                    0,
                )
                or 0
            ),
        "scanned_record_count":
            max(
                int(
                    lexical.get(
                        "scanned_record_count",
                        0,
                    )
                    or 0
                ),
                int(
                    typed.get(
                        "scanned_record_count",
                        0,
                    )
                    or 0
                ),
                int(
                    structure.get(
                        "scanned_record_count",
                        0,
                    )
                    or 0
                ),
            ),

        "query_plan_mode":
            trace.get(
                "query_plan_mode"
            ),
        "expected_channels":
            expected_channels,
        "executed_channels":
            executed_channels,
        "failed_channels":
            failed_channels,
        "channel_reports":
            channels,

        "candidate_count":
            universe_count,
        "candidate_universe_preserved":
            candidate_universe_preserved,

        "model_context_count":
            context_count,
        "model_context_cap":
            context_cap,
        "context_packing_status":
            context_packing_status,

        "deterministically_assessed_count":
            len(
                disposition[
                    "universe_excluded_candidate_ids"
                ]
            ),
        "model_or_rule_assessed_count":
            len(
                disposition[
                    "model_or_rule_assessed_parent_ids"
                ]
            ),
        "unassessed_candidate_ids":
            unassessed,

        "direct_alignment_ids": [
            alignment_id(
                row
            )
            for row in alignments
            if (
                need_id
                in (
                    row.get(
                        "retrieval_need_ids",
                        [],
                    )
                    or []
                )
                and row.get(
                    "argument_admission_channel"
                )
                == "DIRECT"
            )
        ],

        "conditional_alignment_ids": [
            alignment_id(
                row
            )
            for row in alignments
            if (
                need_id
                in (
                    row.get(
                        "retrieval_need_ids",
                        [],
                    )
                    or []
                )
                and row.get(
                    "argument_admission_channel"
                )
                == "CONDITIONAL"
            )
        ],

        "excluded_hit_ids":
            disposition[
                "universe_excluded_candidate_ids"
            ],

        "parse_gap_ids":
            parse_gap_ids,
        "identity_gap_ids":
            identity_gap_ids,
        "missing_required_track_types":
            missing_required_track_types,

        "status":
            status,
        "limiting_factors":
            sorted(
                set(
                    limiting_factors
                )
            ),

        "policy_notes": [
            "coverage level comes from RetrievalNeed, not the evaluator",
            "full-case scan does not mean every positive BM25 score is a candidate",
            "candidate universe and model context remain separate",
            "CANDIDATE_DISCOVERY never satisfies ProofStandard coverage",
            "higher coverage requires an explicit ProcedureObjective/PopulationFrame",
        ],
    }

    report[
        "coverage_sha256"
    ] = sha256_json(
        report
    )

    return report





def evaluate_coverage_bundle(
    requirement_result: dict,
) -> dict:
    traces = (
        requirement_result.get(
            "retrieval_traces",
            []
        )
    )

    alignments = (
        requirement_result.get(
            "alignments",
            []
        )
    )

    reports = [
        evaluate_need_coverage(
            trace=
                trace,
            alignments=
                alignments,
        )
        for trace in traces
    ]

    by_requirement = {}

    for report in reports:
        by_requirement.setdefault(
            report[
                "requirement_id"
            ],
            [],
        ).append(
            report
        )

    requirement_summaries = []

    for rid, group in sorted(
        by_requirement.items()
    ):
        support = [
            report
            for report in group
            if report[
                "direction"
            ]
            == "SUPPORT"
        ]

        attack = [
            report
            for report in group
            if report[
                "direction"
            ]
            == "ATTACK"
        ]

        bidirectional_discovery_complete = bool(
            support
            and attack
            and all(
                report[
                    "procedure_complete"
                ]
                for report in (
                    support
                    + attack
                )
            )
        )

        proof_coverage_pass = bool(
            support
            and attack
            and all(
                report[
                    "proof_coverage_pass"
                ]
                for report in (
                    support
                    + attack
                )
            )
        )

        requirement_summaries.append(
            {
                "requirement_id":
                    rid,
                "support_need_present":
                    bool(
                        support
                    ),
                "attack_need_present":
                    bool(
                        attack
                    ),

                "requested_levels":
                    sorted(
                        {
                            report[
                                "required_level"
                            ]
                            for report in group
                        }
                    ),

                "support_statuses": [
                    report[
                        "status"
                    ]
                    for report in support
                ],
                "attack_statuses": [
                    report[
                        "status"
                    ]
                    for report in attack
                ],

                "bidirectional_discovery_complete":
                    bidirectional_discovery_complete,
                "proof_coverage_pass":
                    proof_coverage_pass,

                "coverage_status":
                    (
                        "PROOF_COVERAGE_COMPLETE"
                        if proof_coverage_pass
                        else (
                            "DISCOVERY_COMPLETE_PROOF_PENDING"
                            if bidirectional_discovery_complete
                            else "DISCOVERY_INCOMPLETE"
                        )
                    ),

                "limiting_factors":
                    sorted(
                        {
                            factor
                            for report in group
                            for factor in report[
                                "limiting_factors"
                            ]
                        }
                    ),
            }
        )

    discovery_complete = bool(
        requirement_summaries
        and all(
            item[
                "bidirectional_discovery_complete"
            ]
            for item in requirement_summaries
        )
    )

    proof_coverage_complete = bool(
        requirement_summaries
        and all(
            item[
                "proof_coverage_pass"
            ]
            for item in requirement_summaries
        )
    )

    bundle = {
        "schema":
            "freca-core-requirement-coverage-v1.1",
        "cp_id":
            requirement_result.get(
                "cp_id"
            ),
        "case_id":
            requirement_result.get(
                "case_id"
            ),

        "coverage_policy": {
            "initial_need_level":
                "CANDIDATE_DISCOVERY",
            "top_k_never_means_complete":
                True,
            "candidate_universe_is_generated_hits_not_all_positive_bm25_scores":
                True,
            "proof_requires_explicit_higher_coverage_objective":
                True,
        },

        "need_reports":
            reports,
        "requirement_summaries":
            requirement_summaries,

        "discovery_complete":
            discovery_complete,
        "proof_coverage_complete":
            proof_coverage_complete,

        # Backward-compatible aliases consumed by existing ProofStandard:
        # do not allow discovery-only completion to leak into proof.
        "coverage_complete":
            proof_coverage_complete,
        "coverage_pass":
            proof_coverage_complete,

        "adverse_inference_allowed":
            False,
        "burden_rule_applied":
            False,

        "next_required_artifact":
            (
                "TARGETED_COMPLETE_PROCEDURE_OBJECTIVE"
                if discovery_complete
                and not proof_coverage_complete
                else None
            ),

        "notes": [
            (
                "Initial Core retrieval needs are CANDIDATE_DISCOVERY; "
                "this artifact no longer upgrades them to FULL_CASE_SCAN."
            ),
            (
                "Full-case lexical scoring is recorded as procedure execution; "
                "only the frozen ranked candidate limit becomes RetrievalHits."
            ),
            (
                "A later explicit ProcedureObjective/PopulationFrame is required "
                "before TARGETED_COMPLETE or higher coverage can be claimed."
            ),
        ],
    }

    bundle[
        "bundle_sha256"
    ] = sha256_json(
        bundle
    )

    return bundle



# ============================================================================
# Self tests
# ============================================================================


def _trace(
    *,
    direction: str,
    full_universe: bool,
    typed: bool,
    structure_full: bool,
    candidates: list[dict] | None = None,
) -> dict:
    variants = [
        {
            "variant_id":
                "v1",
            "query":
                "fixture",
            "candidate_limit":
                40,
            "top_ids":
                ["e1"],
        }
    ]

    if full_universe:
        variants[
            0
        ][
            "full_ids"
        ] = [
            "e1"
        ]

    trace = {
        "need_id":
            f"ERX.{direction.lower()}",
        "requirement_id":
            "ERX",
        "direction":
            direction,
        "query":
            "fixture",
        "query_variants":
            variants,
        "retrieval_scan_chunk_count":
            10,
        "candidate_limit":
            40,
        "top_k":
            12,
        "candidates":
            candidates
            or [
                {
                    "evidence_id":
                        "e1",
                    "identity_use_decision":
                        "ADMIT_DIRECT",
                    "retrieval_methods":
                        [
                            "VARIANT_TOP"
                        ],
                }
            ],
    }

    if typed:
        trace[
            "typed_fact_scan"
        ] = {
            "scan_chunk_count":
                10,
            "matched_count":
                1,
        }

    if structure_full:
        trace[
            "structure_full_scan"
        ] = True

    if full_universe:
        trace[
            "candidate_universe_ids"
        ] = [
            "e1"
        ]

    return trace



def run_self_tests() -> None:
    def make_trace(
        *,
        unassessed: bool,
        missing_channel: bool = False,
        level: str = "CANDIDATE_DISCOVERY",
    ) -> dict:
        expected = [
            "RAW_LEXICAL",
            "TYPED_FACT",
            "STRUCTURE",
        ]

        trace = {
            "need_id":
                "ERX.support",
            "requirement_id":
                "ERX",
            "direction":
                "SUPPORT",
            "coverage_requirement":
                level,
            "expected_channels":
                expected,
            "retrieval_scan_chunk_count":
                10,
            "raw_lexical_scan": {
                "scan_chunk_count":
                    10,
                "scan_complete":
                    True,
                "candidate_generation_mode":
                    "TOP_K_PER_VARIANT",
                "candidate_limit_per_variant":
                    4,
                "generated_union_count":
                    1,
            },
            "query_variants": [
                {
                    "variant_id":
                        "v1",
                    "query":
                        "fixture",
                    "scan_complete":
                        True,
                }
            ],
            "typed_fact_scan": {
                "scan_chunk_count":
                    10,
                "matched_count":
                    1,
                "full_case_scan":
                    True,
            },
            "structure_scan": {
                "mode":
                    "SEED_NEIGHBOUR_RESCUE_ONLY",
                "scan_chunk_count":
                    10,
                "seed_count":
                    1,
                "generated_candidate_count":
                    0,
                "full_scan":
                    False,
                "executed":
                    not missing_channel,
            },
            "candidate_universe_persisted":
                True,
            "candidate_universe": [
                {
                    "evidence_id":
                        "e1",
                    "identity_use_decision":
                        "ADMIT_DIRECT",
                    "retrieval_methods":
                        [
                            "TYPED_FACT_SCAN",
                        ],
                }
            ],
            "candidate_universe_ids":
                [
                    "e1",
                ],
            "candidates": (
                []
                if unassessed
                else [
                    {
                        "evidence_id":
                            "e1",
                        "identity_use_decision":
                            "ADMIT_DIRECT",
                    }
                ]
            ),
            "model_context_cap":
                24,
        }

        return trace

    alignment = {
        "requirement_id":
            "ERX",
        "evidence_id":
            "e1",
        "alignment_evidence_id":
            "a1",
        "retrieval_need_ids":
            [
                "ERX.support",
            ],
        "argument_admission_channel":
            "DIRECT",
        "relation":
            "SUPPORT",
    }

    # Discovery procedure complete, candidate disposed.
    report = evaluate_need_coverage(
        trace=
            make_trace(
                unassessed=
                    False,
            ),
        alignments=[
            alignment
        ],
    )

    assert (
        report[
            "procedure_complete"
        ]
        is True
    )

    assert (
        report[
            "status"
        ]
        == "COMPLETE"
    )

    assert (
        report[
            "proof_coverage_pass"
        ]
        is False
    )

    # Discovery can be procedurally complete while candidate disposition remains
    # unfinished.
    report = evaluate_need_coverage(
        trace=
            make_trace(
                unassessed=
                    True,
            ),
        alignments=[],
    )

    assert (
        report[
            "procedure_complete"
        ]
        is True
    )

    assert (
        report[
            "status"
        ]
        == "LIMITED_TOP_K"
    )

    assert (
        report[
            "proof_coverage_pass"
        ]
        is False
    )

    # Missing expected channel genuinely blocks discovery.
    report = evaluate_need_coverage(
        trace=
            make_trace(
                unassessed=
                    False,
                missing_channel=
                    True,
            ),
        alignments=[
            alignment
        ],
    )

    assert (
        report[
            "procedure_complete"
        ]
        is False
    )

    assert (
        report[
            "status"
        ]
        == "INCOMPLETE_CHANNEL_FAILURE"
    )

    # Higher level is never guessed from discovery metadata.
    report = evaluate_need_coverage(
        trace=
            make_trace(
                unassessed=
                    False,
                level=
                    "TARGETED_COMPLETE",
            ),
        alignments=[
            alignment
        ],
    )

    assert (
        report[
            "procedure_complete"
        ]
        is False
    )

    assert (
        "HIGHER_COVERAGE_PROCEDURE_OBJECTIVE_NOT_IMPLEMENTED"
        in report[
            "limiting_factors"
        ]
    )

    print(
        "coverage_v1.1 self-tests: PASS"
    )
    print(
        "  CANDIDATE_DISCOVERY procedure complete -> does NOT satisfy proof"
    )
    print(
        "  unassessed generated candidate          -> LIMITED_TOP_K"
    )
    print(
        "  missing expected channel               -> channel failure"
    )
    print(
        "  TARGETED_COMPLETE without objective    -> not guessed"
    )



# ============================================================================
# CLI
# ============================================================================



def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
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

        if (
            args.requirement_result
            is None
        ):
            return

    if (
        args.requirement_result
        is None
    ):
        parser.error(
            "--requirement-result is required "
            "unless only --self-test is used"
        )

    result = load_json(
        args.requirement_result
    )

    coverage = evaluate_coverage_bundle(
        result
    )

    output = (
        args.output
        or args.requirement_result.with_name(
            args.requirement_result.stem
            + "_coverage_v1_1.json"
        )
    )

    save_json(
        coverage,
        output,
    )

    print(
        "=" * 72
    )
    print(
        "FRECA REQUIREMENT COVERAGE V1.1"
    )
    print(
        "=" * 72
    )

    for report in coverage[
        "need_reports"
    ]:
        print()
        print(
            report[
                "need_id"
            ],
            "requested=",
            report[
                "required_level"
            ],
            "achieved=",
            report[
                "achieved_level"
            ],
            "status=",
            report[
                "status"
            ],
        )

        print(
            "  procedure_complete:",
            report[
                "procedure_complete"
            ],
        )

        print(
            "  proof_coverage_pass:",
            report[
                "proof_coverage_pass"
            ],
        )

        print(
            "  candidate universe/context:",
            (
                report[
                    "candidate_count"
                ],
                report[
                    "model_context_count"
                ],
            ),
        )

        print(
            "  unassessed:",
            len(
                report[
                    "unassessed_candidate_ids"
                ]
            ),
        )

        print(
            "  channels:",
            {
                name:
                    report[
                        "channel_reports"
                    ][
                        name
                    ].get(
                        "mode"
                    )
                for name in report[
                    "expected_channels"
                ]
                if name in report[
                    "channel_reports"
                ]
            },
        )

        print(
            "  limiting_factors:",
            report[
                "limiting_factors"
            ],
        )

    print()
    print(
        "Requirement summaries:"
    )

    for item in coverage[
        "requirement_summaries"
    ]:
        print(
            " ",
            item[
                "requirement_id"
            ],
            item[
                "coverage_status"
            ],
            "requested=",
            item[
                "requested_levels"
            ],
        )

    print()
    print(
        "Discovery complete     :",
        coverage[
            "discovery_complete"
        ],
    )

    print(
        "Proof coverage complete:",
        coverage[
            "proof_coverage_complete"
        ],
    )

    print(
        "Next required artifact :",
        coverage[
            "next_required_artifact"
        ],
    )

    print(
        "Adverse inference      :",
        coverage[
            "adverse_inference_allowed"
        ],
    )

    print(
        "Saved                  :",
        output,
    )


if __name__ == "__main__":
    main()



