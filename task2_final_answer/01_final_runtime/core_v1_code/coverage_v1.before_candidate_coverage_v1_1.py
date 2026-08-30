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
    variants = trace.get(
        "query_variants",
        [],
    )

    scan_count = int(
        trace.get(
            "retrieval_scan_chunk_count",
            0,
        )
        or 0
    )

    executed = bool(
        trace.get(
            "query"
        )
        or variants
    )

    full_universe_preserved = (
        _variant_full_ids_preserved(
            trace
        )
    )

    candidate_limit = int(
        trace.get(
            "candidate_limit",
            0,
        )
        or 0
    )

    per_variant_quota = int(
        trace.get(
            "per_variant_quota",
            0,
        )
        or 0
    )

    # BM25 may inspect all docs internally, but the current trace only stores a
    # bounded ranked prefix.  A scan count is therefore NOT sufficient to claim
    # the candidate universe was preserved.
    mode = (
        "FULL_UNIVERSE"
        if (
            executed
            and full_universe_preserved
        )
        else (
            "LIMITED_RANKED_PREFIX"
            if executed
            else "NOT_EXECUTED"
        )
    )

    return {
        "channel":
            "RAW_LEXICAL",
        "executed":
            executed,
        "mode":
            mode,
        "scanned_record_count":
            scan_count,
        "candidate_limit":
            candidate_limit,
        "per_variant_quota":
            per_variant_quota,
        "candidate_universe_preserved":
            full_universe_preserved,
        "complete_for_population_coverage":
            bool(
                executed
                and full_universe_preserved
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
            "candidate_universe_preserved":
                False,
            "complete_for_population_coverage":
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

    # The typed scan iterates the full docs list and records matched_count.
    # Current trace does not persist every typed ID separately, but the
    # implementation has a full-case deterministic scan.  Treat the channel
    # execution itself as full scan; candidate-universe conservation remains a
    # separate global gate below.
    full_scan = bool(
        scan_count > 0
        and (
            full_case_count == 0
            or scan_count
            == full_case_count
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
        "candidate_universe_preserved":
            False,
        "complete_for_population_coverage":
            full_scan,
    }



def structure_channel(
    trace: dict,
) -> dict:
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

    structure_scan = (
        trace.get(
            "structure_scan"
        )
        or {}
    )

    explicit_full = bool(
        trace.get(
            "structure_full_scan"
        )
        or trace.get(
            "structure_scan_complete"
        )
        or structure_scan.get(
            "full_scan"
        )
    )

    if explicit_full:
        mode = (
            "FULL_STRUCTURE_SCAN"
        )

    elif neighbour_hits:
        mode = (
            "SEED_NEIGHBOUR_RESCUE_ONLY"
        )

    else:
        mode = (
            "NOT_EXECUTED"
        )

    return {
        "channel":
            "STRUCTURE",
        "executed":
            bool(
                explicit_full
                or neighbour_hits
            ),
        "mode":
            mode,
        "neighbour_candidate_count":
            len(
                neighbour_hits
            ),
        "scan_chunk_count":
            int(
                structure_scan.get(
                    "scan_chunk_count",
                    0,
                )
                or 0
            ),
        "candidate_universe_preserved":
            bool(
                trace.get(
                    "candidate_universe_persisted",
                    False,
                )
            ),
        "complete_for_population_coverage":
            explicit_full,
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
        for item
        in (
            lexical,
            typed,
            structure,
        )
    }

    disposition = (
        candidate_disposition(
            trace=
                trace,
            alignments=
                alignments,
        )
    )

    limiting_factors = []
    failed_channels = []
    executed_channels = []

    for channel_name in (
        REQUIRED_CHANNELS
    ):
        channel = channels[
            channel_name
        ]

        if channel[
            "executed"
        ]:
            executed_channels.append(
                channel_name
            )

        if not channel[
            "complete_for_population_coverage"
        ]:
            failed_channels.append(
                channel_name
            )

            if (
                channel_name
                == "RAW_LEXICAL"
            ):
                limiting_factors.append(
                    "RAW_LEXICAL_CANDIDATE_UNIVERSE_NOT_PERSISTED"
                )

            elif (
                channel_name
                == "TYPED_FACT"
            ):
                limiting_factors.append(
                    "TYPED_FACT_REQUIRED_CHANNEL_NOT_COMPLETE"
                )

            elif (
                channel_name
                == "STRUCTURE"
            ):
                limiting_factors.append(
                    "STRUCTURE_FULL_SCAN_NOT_EXECUTED"
                )

    candidate_universe_preserved = bool(
        trace.get(
            "candidate_universe_persisted",
            False,
        )
    )

    if not candidate_universe_preserved:
        limiting_factors.append(
            "CANDIDATE_UNIVERSE_NOT_PERSISTED"
        )

    unassessed = disposition[
        "universe_unassessed_candidate_ids"
    ]

    if unassessed:
        limiting_factors.append(
            "CANDIDATE_UNIVERSE_NOT_FULLY_ASSESSED"
        )

    context_count = disposition[
        "context_selected_count"
    ]

    universe_count = disposition[
        "candidate_universe_count"
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
        if (
            universe_count
            <= context_count
        )
        else "PARTIAL_CONTEXT_BATCH"
    )

    identity_gap_ids = (
        disposition[
            "universe_conditional_candidate_ids"
        ]
    )

    identity_gap_decisiveness = (
        "UNRESOLVED"
        if identity_gap_ids
        else "NONE"
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
        or unassessed
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
            for row
            in alignments
        )

        status = (
            "COMPLETE"
            if has_relevant_alignment
            else "COMPLETE_NO_RELEVANT_HIT"
        )

    coverage_pass = status in {
        "COMPLETE",
        "COMPLETE_NO_RELEVANT_HIT",
    }

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
            "FULL_CASE_SCAN",
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
                        "scan_chunk_count",
                        0,
                    )
                    or 0
                ),
            ),

        "query_plan_mode":
            trace.get(
                "query_plan_mode"
            ),
        "query_variant_ids": [
            str(
                variant.get(
                    "variant_id",
                    "",
                )
            )
            for variant
            in trace.get(
                "query_variants",
                [],
            )
            if variant.get(
                "variant_id"
            )
        ],

        "required_channels":
            list(
                REQUIRED_CHANNELS
            ),
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
            for row
            in alignments
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
            for row
            in alignments
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
        "identity_gap_decisiveness":
            identity_gap_decisiveness,
        "missing_required_track_types":
            missing_required_track_types,

        "status":
            status,
        "coverage_pass":
            coverage_pass,
        "limiting_factors":
            sorted(
                set(
                    limiting_factors
                )
            ),

        "legacy_trace_coverage_status":
            trace.get(
                "coverage_status"
            ),

        "policy_notes": [
            "top-k/context cap never implies COMPLETE",
            "candidate universe and model context are separate",
            "universe candidates need deterministic or model disposition",
            "conditional identity is preserved as a gap signal",
            "no adverse inference is produced by this artifact",
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
        for trace
        in traces
    ]

    by_requirement: dict[
        str,
        list[dict],
    ] = {}

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
            for report
            in group
            if report[
                "direction"
            ]
            == "SUPPORT"
        ]

        attack = [
            report
            for report
            in group
            if report[
                "direction"
            ]
            == "ATTACK"
        ]

        coverage_pass = bool(
            support
            and attack
            and all(
                report[
                    "coverage_pass"
                ]
                for report
                in (
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
                "support_statuses": [
                    report[
                        "status"
                    ]
                    for report
                    in support
                ],
                "attack_statuses": [
                    report[
                        "status"
                    ]
                    for report
                    in attack
                ],
                "coverage_pass":
                    coverage_pass,
                "coverage_status":
                    (
                        "COMPLETE_BIDIRECTIONAL"
                        if coverage_pass
                        else "INCOMPLETE_BIDIRECTIONAL"
                    ),
                "limiting_factors":
                    sorted(
                        {
                            factor
                            for report
                            in group
                            for factor
                            in report[
                                "limiting_factors"
                            ]
                        }
                    ),
            }
        )

    overall = bool(
        requirement_summaries
        and all(
            item[
                "coverage_pass"
            ]
            for item
            in requirement_summaries
        )
    )

    bundle = {
        "schema":
            "freca-core-requirement-coverage-v1",
        "cp_id":
            requirement_result.get(
                "cp_id"
            ),
        "case_id":
            requirement_result.get(
                "case_id"
            ),

        "coverage_policy": {
            "default_scan_scope":
                "FULL_CASE_SCAN",
            "top_k_never_means_complete":
                True,
            "require_raw_lexical":
                True,
            "require_typed_fact":
                True,
            "require_structure":
                True,
            "semantic_vector_required_for_complete":
                False,
        },

        "need_reports":
            reports,
        "requirement_summaries":
            requirement_summaries,

        "coverage_complete":
            overall,
        "coverage_pass":
            overall,

        "adverse_inference_allowed":
            False,
        "burden_rule_applied":
            False,

        "notes": [
            (
                "Coverage is a property of the executed procedure and "
                "candidate disposition, not of retrieval score."
            ),
            (
                "Current Core traces that persist only a context-capped "
                "candidate prefix cannot claim COMPLETE."
            ),
            (
                "COMPLETE_NO_RELEVANT_HIT would still not itself prove "
                "violation or non-compliance."
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
                "ERX.attack",
            ],
        "argument_admission_channel":
            "DIRECT",
        "relation":
            "SUPPORT",
    }

    # Full procedure -> COMPLETE.
    report = (
        evaluate_need_coverage(
            trace=
                _trace(
                    direction=
                        "SUPPORT",
                    full_universe=
                        True,
                    typed=
                        True,
                    structure_full=
                        True,
                ),
            alignments=[
                alignment
            ],
        )
    )

    assert (
        report[
            "status"
        ]
        == "COMPLETE"
    )

    # Context-capped universe -> not complete.
    report = (
        evaluate_need_coverage(
            trace=
                _trace(
                    direction=
                        "SUPPORT",
                    full_universe=
                        False,
                    typed=
                        True,
                    structure_full=
                        True,
                ),
            alignments=[
                alignment
            ],
        )
    )

    assert (
        report[
            "coverage_pass"
        ]
        is False
    )

    # Missing typed channel -> channel failure.
    report = (
        evaluate_need_coverage(
            trace=
                _trace(
                    direction=
                        "ATTACK",
                    full_universe=
                        True,
                    typed=
                        False,
                    structure_full=
                        True,
                ),
            alignments=[
                alignment
            ],
        )
    )

    assert (
        report[
            "status"
        ]
        == "INCOMPLETE_CHANNEL_FAILURE"
    )

    # Neighbour rescue is not a full STRUCTURE scan.
    trace = _trace(
        direction=
            "SUPPORT",
        full_universe=
            True,
        typed=
            True,
        structure_full=
            False,
        candidates=[
            {
                "evidence_id":
                    "e1",
                "identity_use_decision":
                    "ADMIT_DIRECT",
                "retrieval_methods":
                    [
                        "STRUCTURE_NEIGHBOUR"
                    ],
            }
        ],
    )

    report = (
        evaluate_need_coverage(
            trace=
                trace,
            alignments=[
                alignment
            ],
        )
    )

    assert (
        "STRUCTURE"
        in report[
            "failed_channels"
        ]
    )

    print(
        "coverage_v1 self-tests: PASS"
    )
    print(
        "  full universe + required channels -> COMPLETE"
    )
    print(
        "  context-capped universe          -> INCOMPLETE"
    )
    print(
        "  missing TYPED_FACT               -> INCOMPLETE_CHANNEL_FAILURE"
    )
    print(
        "  STRUCTURE_NEIGHBOUR rescue       -> not full structure coverage"
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

    coverage = (
        evaluate_coverage_bundle(
            result
        )
    )

    output = (
        args.output
        or args.requirement_result.with_name(
            args.requirement_result.stem
            + "_coverage_v1.json"
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
        "FRECA REQUIREMENT COVERAGE V1"
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
            "status=",
            report[
                "status"
            ],
        )

        print(
            "  channels:",
            {
                name:
                    report[
                        "channel_reports"
                    ][
                        name
                    ][
                        "mode"
                    ]
                for name
                in REQUIRED_CHANNELS
            },
        )

        print(
            "  candidate_universe_preserved:",
            report[
                "candidate_universe_preserved"
            ],
        )

        print(
            "  selected candidates:",
            report[
                "candidate_count"
            ],
        )

        print(
            "  unassessed selected:",
            len(
                report[
                    "unassessed_candidate_ids"
                ]
            ),
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
            "support=",
            item[
                "support_statuses"
            ],
            "attack=",
            item[
                "attack_statuses"
            ],
        )

    print()
    print(
        "Coverage complete     :",
        coverage[
            "coverage_complete"
        ],
    )

    print(
        "Adverse inference     :",
        coverage[
            "adverse_inference_allowed"
        ],
    )

    print(
        "Saved                 :",
        output,
    )


if __name__ == "__main__":
    main()
