#!/usr/bin/env python3
"""FRECA Core Repair Executor v1 — RESOLVE_TIME only.

This is the first real repair-action executor.

Supported action:
    RESOLVE_TIME

Frozen return path:
    Layer 8 RepairAction
      -> Layer 5 typed temporal assessment artifact
      -> later Layer 6/7 recomputation

This module deliberately does NOT:
    - mutate the original requirement result
    - change EvidenceAlignment relation
    - change ProofStandard accepted_state
    - run ArgumentGraph
    - produce a final label
    - execute any other action in the RepairPlan

Temporal resolution is deterministic and conservative:
    - exact source text is the only source for event dates;
    - PopulationFrame period_start / period_end define the target interval;
    - if either side is insufficient, relation remains UNKNOWN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


# ============================================================================
# Helpers
# ============================================================================


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
            canonical_json(
                value
            ).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def stable_id(
    prefix: str,
    *parts: str,
) -> str:
    raw = "\n".join(
        str(
            item
        )
        for item in parts
    )

    return (
        prefix
        + "-"
        + hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()[:20]
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


# ============================================================================
# Date parsing
# ============================================================================


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _safe_date(
    year: int,
    month: int,
    day: int,
) -> date | None:
    try:
        return date(
            year,
            month,
            day,
        )
    except ValueError:
        return None


def _iso(
    value: date | None,
) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def parse_exact_dates(
    text: str,
) -> list[dict]:
    """Extract exact day-level dates from source text.

    Supported deterministic formats:
      YYYY-MM-DD
      DD/MM/YYYY
      DD-MM-YYYY
      D Month YYYY
      Month D, YYYY

    Year-only / month-only expressions are intentionally not upgraded to
    day-level events in v1.
    """

    matches = []

    occupied = []

    def add(
        start: int,
        end: int,
        raw: str,
        parsed: date,
        fmt: str,
    ) -> None:
        for a, b in occupied:
            if not (
                end <= a
                or start >= b
            ):
                return

        occupied.append(
            (
                start,
                end,
            )
        )

        matches.append(
            {
                "raw":
                    raw,
                "date":
                    parsed.isoformat(),
                "format":
                    fmt,
                "start":
                    start,
                "end":
                    end,
            }
        )

    # YYYY-MM-DD
    for m in re.finditer(
        r"\b(20\d{2})-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])\b",
        text,
    ):
        parsed = _safe_date(
            int(
                m.group(
                    1
                )
            ),
            int(
                m.group(
                    2
                )
            ),
            int(
                m.group(
                    3
                )
            ),
        )

        if parsed:
            add(
                m.start(),
                m.end(),
                m.group(
                    0
                ),
                parsed,
                "YYYY-MM-DD",
            )

    # DD/MM/YYYY or DD-MM-YYYY
    for m in re.finditer(
        r"\b(0?[1-9]|[12]\d|3[01])([/-])(0?[1-9]|1[0-2])\2(20\d{2})\b",
        text,
    ):
        parsed = _safe_date(
            int(
                m.group(
                    4
                )
            ),
            int(
                m.group(
                    3
                )
            ),
            int(
                m.group(
                    1
                )
            ),
        )

        if parsed:
            add(
                m.start(),
                m.end(),
                m.group(
                    0
                ),
                parsed,
                "DD/MM/YYYY",
            )

    month_pattern = (
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December|Jan|Feb|Mar|Apr|"
        r"Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    )

    # D Month YYYY
    for m in re.finditer(
        rf"\b(0?[1-9]|[12]\d|3[01])\s+{month_pattern}\s+(20\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    ):
        month = MONTHS[
            m.group(
                2
            ).lower()
        ]

        parsed = _safe_date(
            int(
                m.group(
                    3
                )
            ),
            month,
            int(
                m.group(
                    1
                )
            ),
        )

        if parsed:
            add(
                m.start(),
                m.end(),
                m.group(
                    0
                ),
                parsed,
                "D_MONTH_YYYY",
            )

    # Month D, YYYY
    for m in re.finditer(
        rf"\b{month_pattern}\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    ):
        month = MONTHS[
            m.group(
                1
            ).lower()
        ]

        parsed = _safe_date(
            int(
                m.group(
                    3
                )
            ),
            month,
            int(
                m.group(
                    2
                )
            ),
        )

        if parsed:
            add(
                m.start(),
                m.end(),
                m.group(
                    0
                ),
                parsed,
                "MONTH_D_YYYY",
            )

    return sorted(
        matches,
        key=lambda row: (
            row[
                "start"
            ],
            row[
                "end"
            ],
        ),
    )


def parse_iso_optional(
    value: str | None,
) -> date | None:
    if value is None:
        return None

    value = str(
        value
    ).strip()

    if not value:
        return None

    try:
        return date.fromisoformat(
            value
        )
    except ValueError:
        return None


# ============================================================================
# Cross-artifact lookup
# ============================================================================


def alignment_lookup(
    requirement_result: dict,
) -> dict[str, dict]:
    lookup = {}

    for row in requirement_result.get(
        "alignments",
        [],
    ):
        ids = [
            row.get(
                "alignment_evidence_id"
            ),
            row.get(
                "fact_candidate_id"
            ),
            row.get(
                "evidence_id"
            ),
        ]

        fact = (
            row.get(
                "fact_candidate"
            )
            or {}
        )

        ids.append(
            fact.get(
                "fact_candidate_id"
            )
        )

        for value in ids:
            if value:
                lookup[
                    str(
                        value
                    )
                ] = row

    return lookup


def goals_by_id(
    open_goal_ledger: dict | None,
) -> dict[str, dict]:
    if not open_goal_ledger:
        return {}

    return {
        str(
            row[
                "goal_id"
            ]
        ): row
        for row in open_goal_ledger.get(
            "goals",
            [],
        )
    }


def population_by_id(
    procedure_plan: dict,
) -> dict[str, dict]:
    return {
        str(
            row[
                "population_frame_id"
            ]
        ): row
        for row in procedure_plan.get(
            "population_frames",
            [],
        )
    }


def objectives_by_need(
    procedure_plan: dict,
) -> dict[str, dict]:
    out = {}

    for row in procedure_plan.get(
        "audit_procedure_objectives",
        [],
    ):
        ext = (
            row.get(
                "core_extension"
            )
            or {}
        )

        need_id = ext.get(
            "need_id"
        )

        if need_id:
            out[
                str(
                    need_id
                )
            ] = row

    return out


# ============================================================================
# Temporal resolver
# ============================================================================


def temporal_relation(
    *,
    event_dates: list[date],
    period_start: date,
    period_end: date,
) -> str:
    """Compare exact event-date envelope against target interval."""

    event_start = min(
        event_dates
    )

    event_end = max(
        event_dates
    )

    if (
        event_end
        < period_start
        or event_start
        > period_end
    ):
        return "OUT_OF_SCOPE"

    if (
        event_start
        >= period_start
        and event_end
        <= period_end
    ):
        return "IN_SCOPE"

    return "OVERLAPS"


def resolve_one(
    *,
    action: dict,
    target_artifact_id: str,
    alignment: dict | None,
    period_start: date | None,
    period_end: date | None,
    population_frame_id: str | None,
) -> dict:
    reason_codes = []

    if alignment is None:
        assessment = {
            "assessment_id":
                stable_id(
                    "temporal",
                    action[
                        "action_id"
                    ],
                    target_artifact_id,
                ),

            "action_id":
                action[
                    "action_id"
                ],

            "target_artifact_id":
                target_artifact_id,

            "population_frame_id":
                population_frame_id,

            "alignment_evidence_id":
                None,

            "fact_candidate_id":
                None,

            "evidence_id":
                None,

            "source_quote":
                None,

            "source_temporal_expressions":
                [],

            "target_period_start":
                _iso(
                    period_start
                ),

            "target_period_end":
                _iso(
                    period_end
                ),

            "event_start":
                None,

            "event_end":
                None,

            "temporal_relation":
                "UNKNOWN",

            "status":
                "UNKNOWN",

            "reason_codes": [
                "TARGET_ARTIFACT_NOT_RESOLVED_TO_ALIGNMENT",
            ],

            "basis_artifact_ids": [
                target_artifact_id
            ],
        }

        assessment[
            "assessment_sha256"
        ] = sha256_json(
            assessment
        )

        return assessment

    quote = str(
        alignment.get(
            "exact_quote",
            "",
        )
    ).strip()

    if not quote:
        fact = (
            alignment.get(
                "fact_candidate"
            )
            or {}
        )

        quote = str(
            fact.get(
                "quote",
                "",
            )
        ).strip()

    expressions = parse_exact_dates(
        quote
    )

    event_dates = [
        date.fromisoformat(
            row[
                "date"
            ]
        )
        for row in expressions
    ]

    if not quote:
        reason_codes.append(
            "SOURCE_QUOTE_MISSING"
        )

    if not event_dates:
        reason_codes.append(
            "SOURCE_EXPLICIT_DAY_LEVEL_DATE_MISSING"
        )

    if (
        period_start
        is None
        or period_end
        is None
    ):
        reason_codes.append(
            "TARGET_PERIOD_UNSPECIFIED"
        )

    if (
        event_dates
        and period_start is not None
        and period_end is not None
    ):
        relation = temporal_relation(
            event_dates=
                event_dates,
            period_start=
                period_start,
            period_end=
                period_end,
        )

        status = (
            "RESOLVED"
        )

        event_start = min(
            event_dates
        )

        event_end = max(
            event_dates
        )

    else:
        relation = (
            "UNKNOWN"
        )

        status = (
            "UNKNOWN"
        )

        event_start = (
            min(
                event_dates
            )
            if event_dates
            else None
        )

        event_end = (
            max(
                event_dates
            )
            if event_dates
            else None
        )

    fact = (
        alignment.get(
            "fact_candidate"
        )
        or {}
    )

    assessment = {
        "assessment_id":
            stable_id(
                "temporal",
                action[
                    "action_id"
                ],
                target_artifact_id,
                relation,
            ),

        "action_id":
            action[
                "action_id"
            ],

        "target_artifact_id":
            target_artifact_id,

        "population_frame_id":
            population_frame_id,

        "requirement_id":
            alignment.get(
                "requirement_id"
            ),

        "alignment_evidence_id":
            alignment.get(
                "alignment_evidence_id"
            ),

        "fact_candidate_id":
            (
                alignment.get(
                    "fact_candidate_id"
                )
                or fact.get(
                    "fact_candidate_id"
                )
            ),

        "evidence_id":
            alignment.get(
                "evidence_id"
            ),

        "source_quote":
            quote,

        "source_temporal_expressions":
            expressions,

        "target_period_start":
            _iso(
                period_start
            ),

        "target_period_end":
            _iso(
                period_end
            ),

        "event_start":
            _iso(
                event_start
            ),

        "event_end":
            _iso(
                event_end
            ),

        "temporal_relation":
            relation,

        "status":
            status,

        "reason_codes":
            reason_codes,

        "basis_artifact_ids": [
            target_artifact_id,
            *(
                [
                    str(
                        alignment.get(
                            "alignment_evidence_id"
                        )
                    )
                ]
                if alignment.get(
                    "alignment_evidence_id"
                )
                else []
            ),
        ],
    }

    assessment[
        "assessment_sha256"
    ] = sha256_json(
        assessment
    )

    return assessment


# ============================================================================
# Execute one RepairAction
# ============================================================================


def choose_action(
    repair_plan: dict,
    *,
    action_id: str | None,
    action_index: int | None,
) -> dict:
    actions = repair_plan.get(
        "actions",
        [],
    )

    if not actions:
        raise ValueError(
            "RepairPlan has no actions"
        )

    if action_id:
        matches = [
            row
            for row in actions
            if str(
                row.get(
                    "action_id"
                )
            )
            == action_id
        ]

        if len(
            matches
        ) != 1:
            raise ValueError(
                f"Expected exactly one action_id={action_id}; "
                f"found {len(matches)}"
            )

        return matches[
            0
        ]

    if action_index is None:
        action_index = 1

    if (
        action_index
        < 1
        or action_index
        > len(
            actions
        )
    ):
        raise ValueError(
            "action-index out of range"
        )

    return actions[
        action_index - 1
    ]


def build_execution(
    *,
    repair_plan: dict,
    requirement_result: dict,
    procedure_plan: dict,
    open_goal_ledger: dict | None,
    action_id: str | None,
    action_index: int | None,
) -> dict:
    action = choose_action(
        repair_plan,
        action_id=
            action_id,
        action_index=
            action_index,
    )

    if (
        action.get(
            "action_type"
        )
        != "RESOLVE_TIME"
    ):
        raise ValueError(
            "repair_executor_v1 currently supports only RESOLVE_TIME; "
            f"selected {action.get('action_type')}"
        )

    alignment_by_id = alignment_lookup(
        requirement_result
    )

    goals = goals_by_id(
        open_goal_ledger
    )

    goal = goals.get(
        str(
            action.get(
                "goal_id"
            )
        ),
        {},
    )

    need_ids = [
        str(
            item
        )
        for item in (
            goal.get(
                "prior_need_ids",
                [],
            )
            or []
        )
    ]

    if not need_ids:
        query_plan_id = action.get(
            "query_plan_id"
        )

        if query_plan_id:
            need_ids = [
                str(
                    query_plan_id
                )
            ]

    need_id = (
        need_ids[
            0
        ]
        if need_ids
        else None
    )

    objectives = objectives_by_need(
        procedure_plan
    )

    objective = (
        objectives.get(
            need_id
        )
        if need_id
        else None
    )

    populations = population_by_id(
        procedure_plan
    )

    population_frame_id = (
        str(
            objective.get(
                "population_frame_id"
            )
        )
        if objective
        and objective.get(
            "population_frame_id"
        )
        else None
    )

    population = (
        populations.get(
            population_frame_id
        )
        if population_frame_id
        else None
    )

    period_start = parse_iso_optional(
        (
            population.get(
                "period_start"
            )
            if population
            else None
        )
    )

    period_end = parse_iso_optional(
        (
            population.get(
                "period_end"
            )
            if population
            else None
        )
    )

    assessments = []

    for target in action.get(
        "target_artifact_ids",
        [],
    ):
        target = str(
            target
        )

        assessment = resolve_one(
            action=
                action,
            target_artifact_id=
                target,
            alignment=
                alignment_by_id.get(
                    target
                ),
            period_start=
                period_start,
            period_end=
                period_end,
            population_frame_id=
                population_frame_id,
        )

        assessments.append(
            assessment
        )

    resolved_count = sum(
        row[
            "status"
        ]
        == "RESOLVED"
        for row in assessments
    )

    unknown_count = sum(
        row[
            "status"
        ]
        == "UNKNOWN"
        for row in assessments
    )

    if (
        resolved_count
        and unknown_count
    ):
        signal_status = (
            "PARTIAL_NEW_SIGNAL"
        )

    elif resolved_count:
        signal_status = (
            "NEW_VALIDATED_SIGNAL"
        )

    else:
        signal_status = (
            "NO_NEW_VALIDATED_SIGNAL"
        )

    execution = {
        "schema":
            "freca-core-repair-execution-v1",

        "execution_id":
            stable_id(
                "repair-exec",
                repair_plan[
                    "plan_id"
                ],
                action[
                    "action_id"
                ],
            ),

        "repair_plan_id":
            repair_plan[
                "plan_id"
            ],

        "round_index":
            repair_plan[
                "round_index"
            ],

        "action_id":
            action[
                "action_id"
            ],

        "goal_id":
            action[
                "goal_id"
            ],

        "action_type":
            action[
                "action_type"
            ],

        "action_signature":
            action[
                "action_signature"
            ],

        "need_id":
            need_id,

        "target_artifact_ids":
            [
                str(
                    item
                )
                for item in action.get(
                    "target_artifact_ids",
                    [],
                )
            ],

        "population_frame_id":
            population_frame_id,

        "population_period": {
            "period_start":
                _iso(
                    period_start
                ),
            "period_end":
                _iso(
                    period_end
                ),
        },

        "temporal_assessments":
            assessments,

        "resolved_count":
            resolved_count,

        "unknown_count":
            unknown_count,

        "signal_status":
            signal_status,

        "action_execution_status":
            "EXECUTED",

        "required_return_path": [
            "LAYER5_TEMPORAL_RELATION",
            "LAYER6_BINDING",
            "LAYER7_PROOF_COVERAGE_ARGUMENT_REEVALUATION",
        ],

        "upstream_artifacts_mutated":
            False,

        "proof_state_modified":
            False,

        "final_label":
            None,

        "reason_codes":
            sorted(
                {
                    code
                    for row in assessments
                    for code in row[
                        "reason_codes"
                    ]
                }
            ),

        "next_step":
            (
                "APPLY_VALIDATED_TEMPORAL_ARTIFACTS_AND_REEVALUATE"
                if resolved_count
                else "REROUTE_OPEN_GOALS_AFTER_NO_SIGNAL"
            ),
    }

    execution[
        "execution_sha256"
    ] = sha256_json(
        execution
    )

    return execution


# ============================================================================
# Self-tests
# ============================================================================


def _alignment(
    quote: str,
) -> dict:
    return {
        "requirement_id":
            "ER2",
        "alignment_evidence_id":
            "align-1",
        "fact_candidate_id":
            "fc-1",
        "evidence_id":
            "doc:P1",
        "exact_quote":
            quote,
        "fact_candidate": {
            "fact_candidate_id":
                "fc-1",
            "quote":
                quote,
        },
    }


def _base_plan() -> dict:
    return {
        "plan_id":
            "plan-1",
        "round_index":
            1,
        "actions": [
            {
                "action_id":
                    "action-time",
                "goal_id":
                    "goal-time",
                "action_type":
                    "RESOLVE_TIME",
                "action_signature":
                    "sha256:action",
                "target_artifact_ids": [
                    "align-1"
                ],
                "query_plan_id":
                    "ER2.attack",
            }
        ],
    }


def _procedure(
    start: str | None,
    end: str | None,
) -> dict:
    return {
        "population_frames": [
            {
                "population_frame_id":
                    "pop-1",
                "period_start":
                    start,
                "period_end":
                    end,
            }
        ],
        "audit_procedure_objectives": [
            {
                "objective_id":
                    "obj-1",
                "population_frame_id":
                    "pop-1",
                "core_extension": {
                    "need_id":
                        "ER2.attack"
                },
            }
        ],
    }


def run_self_tests() -> None:
    # Exact date inside target period.
    rr = {
        "alignments": [
            _alignment(
                "Rodent activity observed on 20 March 2025."
            )
        ]
    }

    result = build_execution(
        repair_plan=
            _base_plan(),
        requirement_result=
            rr,
        procedure_plan=
            _procedure(
                "2025-01-01",
                "2025-12-31",
            ),
        open_goal_ledger=
            None,
        action_id=
            None,
        action_index=
            1,
    )

    assert (
        result[
            "temporal_assessments"
        ][
            0
        ][
            "temporal_relation"
        ]
        == "IN_SCOPE"
    )

    assert (
        result[
            "signal_status"
        ]
        == "NEW_VALIDATED_SIGNAL"
    )

    # Exact date before period.
    rr = {
        "alignments": [
            _alignment(
                "Rodent activity observed on 20 March 2024."
            )
        ]
    }

    result = build_execution(
        repair_plan=
            _base_plan(),
        requirement_result=
            rr,
        procedure_plan=
            _procedure(
                "2025-01-01",
                "2025-12-31",
            ),
        open_goal_ledger=
            None,
        action_id=
            None,
        action_index=
            1,
    )

    assert (
        result[
            "temporal_assessments"
        ][
            0
        ][
            "temporal_relation"
        ]
        == "OUT_OF_SCOPE"
    )

    # Source date exists but target period missing -> UNKNOWN.
    rr = {
        "alignments": [
            _alignment(
                "Rodent activity observed on 20 March 2025."
            )
        ]
    }

    result = build_execution(
        repair_plan=
            _base_plan(),
        requirement_result=
            rr,
        procedure_plan=
            _procedure(
                None,
                None,
            ),
        open_goal_ledger=
            None,
        action_id=
            None,
        action_index=
            1,
    )

    assert (
        result[
            "temporal_assessments"
        ][
            0
        ][
            "temporal_relation"
        ]
        == "UNKNOWN"
    )

    assert (
        "TARGET_PERIOD_UNSPECIFIED"
        in result[
            "temporal_assessments"
        ][
            0
        ][
            "reason_codes"
        ]
    )

    # No day-level source date -> UNKNOWN.
    rr = {
        "alignments": [
            _alignment(
                "Trend review identifies repeated rodent activity."
            )
        ]
    }

    result = build_execution(
        repair_plan=
            _base_plan(),
        requirement_result=
            rr,
        procedure_plan=
            _procedure(
                "2025-01-01",
                "2025-12-31",
            ),
        open_goal_ledger=
            None,
        action_id=
            None,
        action_index=
            1,
    )

    assert (
        result[
            "temporal_assessments"
        ][
            0
        ][
            "temporal_relation"
        ]
        == "UNKNOWN"
    )

    assert (
        result[
            "signal_status"
        ]
        == "NO_NEW_VALIDATED_SIGNAL"
    )

    print(
        "repair_executor_v1 self-tests: PASS"
    )
    print(
        "  exact source date + explicit target period -> IN_SCOPE/OUT_OF_SCOPE"
    )
    print(
        "  missing target period                     -> UNKNOWN"
    )
    print(
        "  missing explicit source date              -> UNKNOWN"
    )
    print(
        "  upstream proof/result not mutated"
    )


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--repair-plan",
        type=Path,
    )

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--procedure-plan",
        type=Path,
    )

    parser.add_argument(
        "--open-goals",
        type=Path,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--action-id",
        type=str,
    )

    parser.add_argument(
        "--action-index",
        type=int,
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if (
            args.repair_plan
            is None
            and args.requirement_result
            is None
            and args.procedure_plan
            is None
        ):
            return

    required = {
        "--repair-plan":
            args.repair_plan,
        "--requirement-result":
            args.requirement_result,
        "--procedure-plan":
            args.procedure_plan,
    }

    missing = [
        key
        for key, value in required.items()
        if value is None
    ]

    if missing:
        parser.error(
            "missing required arguments: "
            + ", ".join(
                missing
            )
        )

    repair_plan = load_json(
        args.repair_plan
    )

    requirement_result = load_json(
        args.requirement_result
    )

    procedure_plan = load_json(
        args.procedure_plan
    )

    open_goals = (
        load_json(
            args.open_goals
        )
        if args.open_goals
        else None
    )

    execution = build_execution(
        repair_plan=
            repair_plan,
        requirement_result=
            requirement_result,
        procedure_plan=
            procedure_plan,
        open_goal_ledger=
            open_goals,
        action_id=
            args.action_id,
        action_index=
            args.action_index,
    )

    output = (
        args.output
        or args.repair_plan.with_name(
            args.repair_plan.stem
            + "_execution_v1.json"
        )
    )

    save_json(
        execution,
        output,
    )

    print(
        "=" * 72
    )
    print(
        "FRECA REPAIR EXECUTOR V1 — RESOLVE_TIME"
    )
    print(
        "=" * 72
    )

    print()
    print(
        "Action:",
        execution[
            "action_id"
        ],
    )

    print(
        "Goal:",
        execution[
            "goal_id"
        ],
    )

    print(
        "Need:",
        execution[
            "need_id"
        ],
    )

    print(
        "Target period:",
        execution[
            "population_period"
        ],
    )

    print()

    for index, row in enumerate(
        execution[
            "temporal_assessments"
        ],
        start=1,
    ):
        print(
            f"{index:02d}.",
            row[
                "target_artifact_id"
            ],
        )

        print(
            "    evidence:",
            row.get(
                "evidence_id"
            ),
        )

        print(
            "    source dates:",
            [
                x[
                    "date"
                ]
                for x in row[
                    "source_temporal_expressions"
                ]
            ],
        )

        print(
            "    relation:",
            row[
                "temporal_relation"
            ],
        )

        print(
            "    status:",
            row[
                "status"
            ],
        )

        print(
            "    reasons:",
            row[
                "reason_codes"
            ],
        )

    print()
    print(
        "Signal status    :",
        execution[
            "signal_status"
        ],
    )

    print(
        "Action executed  :",
        execution[
            "action_execution_status"
        ],
    )

    print(
        "Proof modified   :",
        execution[
            "proof_state_modified"
        ],
    )

    print(
        "Final label      :",
        execution[
            "final_label"
        ],
    )

    print(
        "Next step        :",
        execution[
            "next_step"
        ],
    )

    print(
        "Saved            :",
        output,
    )


if __name__ == "__main__":
    main()
