#!/usr/bin/env python3
"""FRECA Core PopulationFrame + AuditProcedureObjective planner v1.

This is a PLANNING artifact only.

Inputs:
    - requirement_reasoning_v2.json
    - coverage_v1_1.json

Outputs:
    - PopulationFrame per EvidenceRequirement
    - AuditProcedureObjective per RetrievalNeed
    - TARGETED_COMPLETE coverage-upgrade requests

It does NOT:
    - call an LLM
    - execute retrieval
    - change alignments
    - change ProofStandard accepted_state
    - produce a final task label

The implementation is a minimal transplant of frozen D7.9a semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
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
            part
        )
        for part in parts
    )

    return (
        prefix
        + "-"
        + hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()[
            :20
        ]
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


def get_case_uid(
    requirement_result: dict,
) -> str:
    for key in (
        "case_uid",
        "case_id",
        "case_name",
        "case",
    ):
        value = requirement_result.get(
            key
        )

        if value:
            return str(
                value
            )

    return (
        "case-unknown"
    )


def requirement_map(
    requirement_result: dict,
) -> dict[str, dict]:
    plan = requirement_result[
        "evidence_requirement_plan"
    ]

    return {
        str(
            row[
                "requirement_id"
            ]
        ): row
        for row in plan[
            "requirements"
        ]
    }


def traces_by_requirement(
    requirement_result: dict,
) -> dict[str, list[dict]]:
    out = {}

    for trace in requirement_result.get(
        "retrieval_traces",
        [],
    ):
        rid = str(
            trace[
                "requirement_id"
            ]
        )

        out.setdefault(
            rid,
            [],
        ).append(
            trace
        )

    return out


def coverage_reports_by_need(
    coverage: dict,
) -> dict[str, dict]:
    return {
        str(
            row[
                "need_id"
            ]
        ): row
        for row in coverage.get(
            "need_reports",
            [],
        )
    }


def infer_assertion_dimension(
    direction: str,
) -> str:
    # Minimal non-financial adaptation:
    # SUPPORT generally asks whether the relevant state/feature exists;
    # ATTACK asks whether an adverse event/condition occurred.
    if direction == "ATTACK":
        return "OCCURRENCE"

    return "EXISTENCE"


def infer_intended_purpose(
    direction: str,
) -> str:
    if direction == "ATTACK":
        return (
            "CONTRADICTION_SEARCH"
        )

    return "DETAIL_TEST"


def build_population_frame(
    *,
    case_uid: str,
    requirement: dict,
    traces: list[dict],
) -> dict:
    rid = str(
        requirement[
            "requirement_id"
        ]
    )

    proposition_id = (
        f"prop-{rid.lower()}"
    )

    all_universe_ids = []
    seen = set()

    scan_counts = []

    for trace in traces:
        scan_count = int(
            trace.get(
                "retrieval_scan_chunk_count",
                0,
            )
            or 0
        )

        if scan_count:
            scan_counts.append(
                scan_count
            )

        for evidence_id in trace.get(
            "candidate_universe_ids",
            [],
        ):
            evidence_id = str(
                evidence_id
            )

            if (
                evidence_id
                not in seen
            ):
                seen.add(
                    evidence_id
                )

                all_universe_ids.append(
                    evidence_id
                )

    known_population_size = (
        max(
            scan_counts
        )
        if scan_counts
        else None
    )

    limitations = [
        "TASK_PACKAGE_ONLY_NO_REAL_WORLD_EXTRAPOLATION",
        "EVENT_POPULATION_NOT_ESTABLISHED",
        "REPRESENTATIVENESS_NOT_CLAIMED",
    ]

    frame = {
        "population_frame_id":
            stable_id(
                "pop",
                case_uid,
                rid,
                proposition_id,
            ),

        "case_uid":
            case_uid,

        "proposition_id":
            proposition_id,

        "universe_mode":
            "TASK_PACKAGE_CENSUS",

        "population_definition":
            (
                "All parsed searchable evidence records in the current "
                "competition task package form the scanned evidence universe. "
                "The selected_item_ids are the union of candidates generated "
                "for this EvidenceRequirement by the registered discovery "
                "channels. This frame does not claim to enumerate all real-world "
                "farm activities or all events across the regulated period."
            ),

        "period_start":
            None,

        "period_end":
            None,

        "known_population_size":
            known_population_size,

        "selected_item_ids":
            all_universe_ids,

        "selection_method":
            "SPECIFIC_ITEMS",

        "representativeness_claimed":
            False,

        "extrapolation_permitted":
            False,

        "source_basis_ids": [
            str(
                trace[
                    "need_id"
                ]
            )
            for trace in traces
        ],

        "limitation_codes":
            limitations,
    }

    frame[
        "frame_sha256"
    ] = sha256_json(
        frame
    )

    return frame


def build_objective(
    *,
    requirement: dict,
    trace: dict,
    coverage_report: dict,
    population_frame: dict,
) -> dict:
    rid = str(
        requirement[
            "requirement_id"
        ]
    )

    need_id = str(
        trace[
            "need_id"
        ]
    )

    direction = str(
        trace[
            "direction"
        ]
    )

    proposition_id = (
        population_frame[
            "proposition_id"
        ]
    )

    intended_purpose = (
        infer_intended_purpose(
            direction
        )
    )

    assertion_dimension = (
        infer_assertion_dimension(
            direction
        )
    )

    success_criteria = [
        (
            "All generated candidate-universe items for this need receive "
            "a deterministic or validated semantic disposition."
        ),
        (
            "All registered retrieval channels for the targeted procedure "
            "execute successfully with replayable trace metadata."
        ),
        (
            "No decisive parse gap or unresolved source-use gap remains for "
            "the selected items used by the proof argument."
        ),
        (
            "SUPPORT and ATTACK evidence remain separately preserved; "
            "contradictory evidence is not majority-voted away."
        ),
        (
            "The procedure result remains scoped to the task package / "
            "selected items unless a separate population or period basis "
            "permits broader inference."
        ),
    ]

    if direction == "ATTACK":
        success_criteria.append(
            (
                "An ATTACK conclusion requires an admissible explicit adverse "
                "fact or decisive counterevidence; ordinary non-hit is not a "
                "violation finding."
            )
        )

    objective = {
        "objective_id":
            stable_id(
                "obj",
                need_id,
                "TARGETED_COMPLETE",
            ),

        "proposition_id":
            proposition_id,

        "direction":
            direction,

        "intended_purpose":
            intended_purpose,

        "assertion_dimension":
            assertion_dimension,

        "required_disaggregation":
            "EVENT",

        "population_frame_id":
            population_frame[
                "population_frame_id"
            ],

        "success_criteria":
            success_criteria,

        "failure_consequence":
            "ADD_PROCEDURE",

        # Core extension fields outside the frozen object contract.
        "core_extension": {
            "requirement_id":
                rid,
            "need_id":
                need_id,
            "current_coverage_requirement":
                trace.get(
                    "coverage_requirement",
                    "CANDIDATE_DISCOVERY",
                ),
            "target_coverage_requirement":
                "TARGETED_COMPLETE",
            "current_coverage_status":
                coverage_report.get(
                    "status"
                ),
            "current_unassessed_candidate_ids":
                coverage_report.get(
                    "unassessed_candidate_ids",
                    [],
                ),
            "current_procedure_complete":
                bool(
                    coverage_report.get(
                        "procedure_complete",
                        False,
                    )
                ),
            "current_proof_coverage_pass":
                bool(
                    coverage_report.get(
                        "proof_coverage_pass",
                        False,
                    )
                ),
        },
    }

    objective[
        "objective_sha256"
    ] = sha256_json(
        objective
    )

    return objective


def build_plan(
    requirement_result: dict,
    coverage: dict,
) -> dict:
    reqs = requirement_map(
        requirement_result
    )

    traces = traces_by_requirement(
        requirement_result
    )

    coverage_by_need = (
        coverage_reports_by_need(
            coverage
        )
    )

    case_uid = get_case_uid(
        requirement_result
    )

    population_frames = []
    objectives = []
    upgrade_requests = []

    for rid, requirement in sorted(
        reqs.items()
    ):
        requirement_traces = (
            traces.get(
                rid,
                [],
            )
        )

        if not requirement_traces:
            continue

        frame = build_population_frame(
            case_uid=
                case_uid,
            requirement=
                requirement,
            traces=
                requirement_traces,
        )

        population_frames.append(
            frame
        )

        for trace in sorted(
            requirement_traces,
            key=lambda item: (
                str(
                    item.get(
                        "direction",
                        "",
                    )
                ),
                str(
                    item.get(
                        "need_id",
                        "",
                    )
                ),
            ),
        ):
            need_id = str(
                trace[
                    "need_id"
                ]
            )

            coverage_report = (
                coverage_by_need.get(
                    need_id
                )
            )

            if coverage_report is None:
                raise ValueError(
                    f"Coverage report missing for need {need_id}"
                )

            objective = build_objective(
                requirement=
                    requirement,
                trace=
                    trace,
                coverage_report=
                    coverage_report,
                population_frame=
                    frame,
            )

            objectives.append(
                objective
            )

            if not coverage_report.get(
                "proof_coverage_pass",
                False,
            ):
                upgrade = {
                    "upgrade_request_id":
                        stable_id(
                            "upgrade",
                            need_id,
                            "TARGETED_COMPLETE",
                        ),
                    "requirement_id":
                        rid,
                    "need_id":
                        need_id,
                    "objective_id":
                        objective[
                            "objective_id"
                        ],
                    "population_frame_id":
                        frame[
                            "population_frame_id"
                        ],
                    "from_level":
                        coverage_report.get(
                            "required_level",
                            "CANDIDATE_DISCOVERY",
                        ),
                    "to_level":
                        "TARGETED_COMPLETE",
                    "reason_codes": [
                        "PROOF_COVERAGE_PENDING",
                        *(
                            coverage_report.get(
                                "limiting_factors",
                                [],
                            )
                            or []
                        ),
                    ],
                    "allowed_next_action_types": [
                        "ASSESS_REMAINING_CANDIDATES",
                        "TARGETED_RETRIEVAL",
                        "RESOLVE_IDENTITY",
                        "RESOLVE_TIME",
                        "RUN_INFORMATION_RELIABILITY_TEST",
                    ],
                    "must_preserve_unknown_if_unresolved":
                        True,
                }

                upgrade[
                    "upgrade_sha256"
                ] = sha256_json(
                    upgrade
                )

                upgrade_requests.append(
                    upgrade
                )

    bundle = {
        "schema":
            "freca-core-procedure-objective-plan-v1",

        "case_uid":
            case_uid,

        "cp_id":
            requirement_result.get(
                "cp_id"
            )
            or requirement_result.get(
                "evidence_requirement_plan",
                {},
            ).get(
                "cp_id"
            ),

        "source_coverage_schema":
            coverage.get(
                "schema"
            ),

        "population_frames":
            population_frames,

        "audit_procedure_objectives":
            objectives,

        "coverage_upgrade_requests":
            upgrade_requests,

        "technology_assisted_procedure_records":
            [],

        "execution_status":
            "PLANNED_NOT_EXECUTED",

        "proof_state_modified":
            False,

        "final_label":
            None,

        "design_invariants": [
            "TASK_PACKAGE_CENSUS does not imply real-world population coverage",
            "SPECIFIC_ITEMS does not permit population extrapolation",
            "procedure objective is distinct from evidence",
            "failure preserves UNKNOWN or triggers another procedure",
            "no output here can directly satisfy ProofStandard",
        ],
    }

    bundle[
        "bundle_sha256"
    ] = sha256_json(
        bundle
    )

    return bundle


def run_self_tests() -> None:
    rr = {
        "case_id":
            "case-x",
        "cp_id":
            "CPX",
        "evidence_requirement_plan": {
            "cp_id":
                "CPX",
            "requirements": [
                {
                    "requirement_id":
                        "ER1",
                    "atom_id":
                        "A1",
                    "proposition_to_establish":
                        "fixture proposition",
                    "decisiveness":
                        "DECISIVE",
                }
            ],
        },
        "retrieval_traces": [
            {
                "need_id":
                    "ER1.support",
                "requirement_id":
                    "ER1",
                "direction":
                    "SUPPORT",
                "coverage_requirement":
                    "CANDIDATE_DISCOVERY",
                "retrieval_scan_chunk_count":
                    10,
                "candidate_universe_ids": [
                    "e1",
                    "e2",
                ],
            },
            {
                "need_id":
                    "ER1.attack",
                "requirement_id":
                    "ER1",
                "direction":
                    "ATTACK",
                "coverage_requirement":
                    "CANDIDATE_DISCOVERY",
                "retrieval_scan_chunk_count":
                    10,
                "candidate_universe_ids": [
                    "e2",
                    "e3",
                ],
            },
        ],
    }

    cov = {
        "schema":
            "freca-core-requirement-coverage-v1.1",
        "need_reports": [
            {
                "need_id":
                    "ER1.support",
                "required_level":
                    "CANDIDATE_DISCOVERY",
                "status":
                    "LIMITED_TOP_K",
                "procedure_complete":
                    True,
                "proof_coverage_pass":
                    False,
                "unassessed_candidate_ids": [
                    "e2",
                ],
                "limiting_factors": [
                    "CANDIDATE_DISCOVERY_NOT_PROOF_COVERAGE",
                ],
            },
            {
                "need_id":
                    "ER1.attack",
                "required_level":
                    "CANDIDATE_DISCOVERY",
                "status":
                    "LIMITED_TOP_K",
                "procedure_complete":
                    True,
                "proof_coverage_pass":
                    False,
                "unassessed_candidate_ids": [
                    "e3",
                ],
                "limiting_factors": [
                    "CANDIDATE_DISCOVERY_NOT_PROOF_COVERAGE",
                ],
            },
        ],
    }

    result = build_plan(
        rr,
        cov,
    )

    assert len(
        result[
            "population_frames"
        ]
    ) == 1

    frame = result[
        "population_frames"
    ][
        0
    ]

    assert (
        frame[
            "universe_mode"
        ]
        == "TASK_PACKAGE_CENSUS"
    )

    assert (
        frame[
            "selection_method"
        ]
        == "SPECIFIC_ITEMS"
    )

    assert (
        frame[
            "representativeness_claimed"
        ]
        is False
    )

    assert (
        frame[
            "extrapolation_permitted"
        ]
        is False
    )

    assert set(
        frame[
            "selected_item_ids"
        ]
    ) == {
        "e1",
        "e2",
        "e3",
    }

    assert len(
        result[
            "audit_procedure_objectives"
        ]
    ) == 2

    assert len(
        result[
            "coverage_upgrade_requests"
        ]
    ) == 2

    assert all(
        row[
            "to_level"
        ]
        == "TARGETED_COMPLETE"
        for row in result[
            "coverage_upgrade_requests"
        ]
    )

    assert (
        result[
            "proof_state_modified"
        ]
        is False
    )

    assert (
        result[
            "final_label"
        ]
        is None
    )

    print(
        "procedure_objective_v1 self-tests: PASS"
    )
    print(
        "  one PopulationFrame shared by SUPPORT/ATTACK"
    )
    print(
        "  TASK_PACKAGE_CENSUS + SPECIFIC_ITEMS"
    )
    print(
        "  no representativeness / extrapolation claim"
    )
    print(
        "  discovery gaps -> TARGETED_COMPLETE upgrade requests"
    )
    print(
        "  proof state remains unchanged"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--coverage",
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
            and args.coverage
            is None
        ):
            return

    if (
        args.requirement_result
        is None
        or args.coverage
        is None
    ):
        parser.error(
            "--requirement-result and --coverage are required "
            "unless only --self-test is used"
        )

    rr = load_json(
        args.requirement_result
    )

    coverage = load_json(
        args.coverage
    )

    plan = build_plan(
        rr,
        coverage,
    )

    output = (
        args.output
        or args.coverage.with_name(
            args.coverage.stem
            + "_procedure_objective_v1.json"
        )
    )

    save_json(
        plan,
        output,
    )

    print(
        "=" * 72
    )
    print(
        "FRECA PROCEDURE OBJECTIVE PLAN V1"
    )
    print(
        "=" * 72
    )

    print()
    print(
        "Population frames :",
        len(
            plan[
                "population_frames"
            ]
        ),
    )

    print(
        "Objectives        :",
        len(
            plan[
                "audit_procedure_objectives"
            ]
        ),
    )

    print(
        "Upgrade requests  :",
        len(
            plan[
                "coverage_upgrade_requests"
            ]
        ),
    )

    print()

    for objective in plan[
        "audit_procedure_objectives"
    ]:
        ext = objective[
            "core_extension"
        ]

        print(
            ext[
                "need_id"
            ],
            "purpose=",
            objective[
                "intended_purpose"
            ],
            "from=",
            ext[
                "current_coverage_requirement"
            ],
            "to=",
            ext[
                "target_coverage_requirement"
            ],
            "unassessed=",
            len(
                ext[
                    "current_unassessed_candidate_ids"
                ]
            ),
        )

    print()
    print(
        "Execution status :",
        plan[
            "execution_status"
        ],
    )

    print(
        "Proof modified   :",
        plan[
            "proof_state_modified"
        ],
    )

    print(
        "Final label      :",
        plan[
            "final_label"
        ],
    )

    print(
        "Saved            :",
        output,
    )


if __name__ == "__main__":
    main()
