#!/usr/bin/env python3
"""FRECA Core OpenGoal ledger v1.

Builds actionable Layer-7 OpenGoals from:
  - current requirement reasoning
  - current RequirementCoverage v1.1
  - current ProcedureObjective plan v1
  - current ProofStandard v1
  - frozen current contract

This module PLANS only. It does not execute repair actions.

Minimal interface completion:
  Frozen architecture already defines INFORMATION_RELIABILITY_TEST and
  InformationReliabilityAssessment, but its OpenGoal/RepairAction enums omit a
  reliability-specific bridge.  Core v1 therefore adds:
      goal_type   = RESOLVE_RELIABILITY
      action_type = ASSESS_INFORMATION_RELIABILITY
  as an interface completion, not a new proof mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


FROZEN_REPAIR_ACTION_TYPES = {
    "REQUERY_NEW_FACET",
    "EXECUTE_MISSING_CHANNEL",
    "ALIGN_NEXT_CANDIDATE_BATCH",
    "REPARSE_TARGET_SPAN",
    "RESOLVE_IDENTITY",
    "RESOLVE_COMMODITY",
    "RESOLVE_TIME",
    "VALIDATE_CITATION",
    "CHECK_EXCEPTION",
    "CHECK_REBUTTAL",
    "REEXECUTE_ARGUMENT_GRAPH",
}

CORE_INTERFACE_COMPLETION_ACTION_TYPES = {
    "ASSESS_INFORMATION_RELIABILITY",
}

ALLOWED_ACTION_TYPES = (
    FROZEN_REPAIR_ACTION_TYPES
    | CORE_INTERFACE_COMPLETION_ACTION_TYPES
)

FROZEN_GOAL_TYPES = {
    "FIND_SUPPORT",
    "FIND_ATTACK",
    "CHECK_EXCEPTION",
    "CHECK_REBUTTAL",
    "RESOLVE_IDENTITY",
    "RESOLVE_TIME",
    "RESOLVE_COMMODITY",
    "REPARSE_SOURCE",
    "VALIDATE_CITATION",
    "RESOLVE_INTERPRETATION",
}

CORE_INTERFACE_COMPLETION_GOAL_TYPES = {
    "RESOLVE_RELIABILITY",
}

ALLOWED_GOAL_TYPES = (
    FROZEN_GOAL_TYPES
    | CORE_INTERFACE_COMPLETION_GOAL_TYPES
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
            canonical_json(
                value
            ).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def sha256_text(
    value: str,
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            value.encode(
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


def get_case_uid(
    requirement_result: dict,
    procedure_plan: dict,
) -> str:
    for source in (
        procedure_plan,
        requirement_result,
    ):
        for key in (
            "case_uid",
            "case_id",
            "case_name",
            "case",
        ):
            value = source.get(
                key
            )

            if value:
                return str(
                    value
                )

    return "case-unknown"


def contract_interpretation_id(
    contract_bundle: dict,
) -> str:
    contract = (
        contract_bundle.get(
            "contract"
        )
        or contract_bundle
    )

    return stable_id(
        "interp",
        canonical_json(
            contract
        ),
    )


def requirement_map(
    requirement_result: dict,
) -> dict[str, dict]:
    return {
        str(
            row[
                "requirement_id"
            ]
        ): row
        for row in requirement_result[
            "evidence_requirement_plan"
        ][
            "requirements"
        ]
    }


def coverage_by_need(
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


def traces_by_need(
    requirement_result: dict,
) -> dict[str, dict]:
    return {
        str(
            row[
                "need_id"
            ]
        ): row
        for row in requirement_result.get(
            "retrieval_traces",
            [],
        )
    }


def proof_by_requirement(
    proof_standard: dict,
) -> dict[str, dict]:
    return {
        str(
            row[
                "requirement_id"
            ]
        ): row
        for row in proof_standard.get(
            "requirement_reports",
            [],
        )
    }


def objective_by_need(
    procedure_plan: dict,
) -> dict[str, dict]:
    out = {}

    for objective in procedure_plan.get(
        "audit_procedure_objectives",
        [],
    ):
        ext = (
            objective.get(
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
            ] = objective

    return out


def upgrade_by_need(
    procedure_plan: dict,
) -> dict[str, dict]:
    return {
        str(
            row[
                "need_id"
            ]
        ): row
        for row in procedure_plan.get(
            "coverage_upgrade_requests",
            [],
        )
    }


def current_statement_state(
    rid: str,
    proof_report: dict | None,
    requirement_result: dict,
) -> str:
    if proof_report:
        state = proof_report.get(
            "accepted_state"
        )

        if state in {
            "TRUE",
            "FALSE",
            "BOTH",
            "UNKNOWN",
        }:
            return state

    for row in (
        requirement_result.get(
            "proof_gate",
            {}
        ).get(
            "requirement_reports",
            [],
        )
    ):
        if str(
            row.get(
                "requirement_id"
            )
        ) == rid:
            state = row.get(
                "accepted_state"
            )

            if state in {
                "TRUE",
                "FALSE",
                "BOTH",
                "UNKNOWN",
            }:
                return state

    return "UNKNOWN"


def directional_proof(
    requirement_proof: dict | None,
    direction: str,
) -> dict:
    if not requirement_proof:
        return {}

    if direction == "SUPPORT":
        return (
            requirement_proof.get(
                "support_proof"
            )
            or {}
        )

    return (
        requirement_proof.get(
            "attack_proof"
        )
        or {}
    )


def previous_query_hashes(
    trace: dict | None,
) -> list[str]:
    if not trace:
        return []

    hashes = []

    for variant in trace.get(
        "query_variants",
        [],
    ):
        query = str(
            variant.get(
                "query",
                ""
            )
        ).strip()

        if not query:
            continue

        digest = sha256_text(
            query
        )

        if digest not in hashes:
            hashes.append(
                digest
            )

    return hashes


def impact_for_requirement(
    requirement: dict,
) -> str:
    decisiveness = str(
        requirement.get(
            "decisiveness",
            ""
        )
    )

    if decisiveness == "DECISIVE":
        return "DECISIVE"

    if decisiveness in {
        "POTENTIALLY_DECISIVE",
        "CORROBORATIVE",
    }:
        return "POTENTIALLY_DECISIVE"

    return "NON_DECISIVE"


def make_goal(
    *,
    case_uid: str,
    cp_id: str,
    interpretation_id: str,
    rid: str,
    goal_type: str,
    current_state: str,
    blocking_reason_codes: list[str],
    prior_need_ids: list[str],
    prohibited_query_hashes: list[str],
    available_action_types: list[str],
    impact: str,
    core_extension: dict,
) -> dict:
    if goal_type not in ALLOWED_GOAL_TYPES:
        raise ValueError(
            f"Unsupported goal type: {goal_type}"
        )

    invalid_actions = [
        item
        for item in available_action_types
        if item not in ALLOWED_ACTION_TYPES
    ]

    if invalid_actions:
        raise ValueError(
            "Unsupported action types: "
            + repr(
                invalid_actions
            )
        )

    target_statement_id = (
        f"stmt-{rid.lower()}"
    )

    target_proposition_id = (
        f"prop-{rid.lower()}"
    )

    goal_id = stable_id(
        "goal",
        case_uid,
        cp_id,
        rid,
        goal_type,
        "|".join(
            sorted(
                prior_need_ids
            )
        ),
        "|".join(
            sorted(
                blocking_reason_codes
            )
        ),
    )

    goal = {
        "goal_id":
            goal_id,

        "case_uid":
            case_uid,

        "cp_id":
            cp_id,

        "interpretation_id":
            interpretation_id,

        "target_statement_id":
            target_statement_id,

        "target_proposition_id":
            target_proposition_id,

        "goal_type":
            goal_type,

        "current_state":
            current_state,

        "blocking_reason_codes":
            sorted(
                set(
                    blocking_reason_codes
                )
            ),

        "prior_need_ids":
            sorted(
                set(
                    prior_need_ids
                )
            ),

        "prohibited_query_hashes":
            sorted(
                set(
                    prohibited_query_hashes
                )
            ),

        "available_action_types":
            sorted(
                set(
                    available_action_types
                )
            ),

        "estimated_verdict_impact":
            impact,

        "core_extension":
            core_extension,
    }

    goal[
        "goal_sha256"
    ] = sha256_json(
        goal
    )

    return goal


def build_coverage_goal(
    *,
    case_uid: str,
    cp_id: str,
    interpretation_id: str,
    requirement: dict,
    trace: dict,
    coverage_report: dict,
    objective: dict,
    upgrade: dict,
    requirement_proof: dict | None,
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

    proof = directional_proof(
        requirement_proof,
        direction,
    )

    reasons = [
        *(
            upgrade.get(
                "reason_codes",
                []
            )
            or []
        ),
        *(
            proof.get(
                "failure_codes",
                []
            )
            or []
        ),
    ]

    actions = []

    if coverage_report.get(
        "unassessed_candidate_ids"
    ):
        actions.append(
            "ALIGN_NEXT_CANDIDATE_BATCH"
        )

    if coverage_report.get(
        "failed_channels"
    ):
        actions.append(
            "EXECUTE_MISSING_CHANNEL"
        )

    # Novel query facet generation is deterministic and later novelty-gated.
    actions.append(
        "REQUERY_NEW_FACET"
    )

    if coverage_report.get(
        "identity_gap_ids"
    ):
        actions.append(
            "RESOLVE_IDENTITY"
        )

    if any(
        code
        in {
            "TEMPORAL_ASSESSMENT_MISSING",
            "TEMPORAL_SCOPE_NOT_PASSED",
        }
        for code in reasons
    ):
        actions.append(
            "RESOLVE_TIME"
        )

    goal_type = (
        "FIND_SUPPORT"
        if direction
        == "SUPPORT"
        else "FIND_ATTACK"
    )

    return make_goal(
        case_uid=
            case_uid,
        cp_id=
            cp_id,
        interpretation_id=
            interpretation_id,
        rid=
            rid,
        goal_type=
            goal_type,
        current_state=
            current_statement_state(
                rid,
                requirement_proof,
                {},
            ),
        blocking_reason_codes=
            reasons,
        prior_need_ids=[
            need_id
        ],
        prohibited_query_hashes=
            previous_query_hashes(
                trace
            ),
        available_action_types=
            actions,
        impact=
            impact_for_requirement(
                requirement
            ),
        core_extension={
            "goal_origin":
                "TARGETED_COMPLETE_COVERAGE_UPGRADE",
            "objective_id":
                objective.get(
                    "objective_id"
                ),
            "population_frame_id":
                objective.get(
                    "population_frame_id"
                ),
            "upgrade_request_id":
                upgrade.get(
                    "upgrade_request_id"
                ),
            "direction":
                direction,
            "current_coverage_level":
                coverage_report.get(
                    "required_level"
                ),
            "target_coverage_level":
                "TARGETED_COMPLETE",
            "unassessed_candidate_ids":
                coverage_report.get(
                    "unassessed_candidate_ids",
                    [],
                ),
            "directional_basis_artifact_ids":
                proof.get(
                    "basis_artifact_ids",
                    [],
                ),
        },
    )


def build_resolution_goals(
    *,
    case_uid: str,
    cp_id: str,
    interpretation_id: str,
    requirement: dict,
    trace: dict,
    requirement_proof: dict | None,
) -> list[dict]:
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

    proof = directional_proof(
        requirement_proof,
        direction,
    )

    basis_ids = list(
        proof.get(
            "basis_artifact_ids",
            []
        )
        or []
    )

    failures = list(
        proof.get(
            "failure_codes",
            []
        )
        or []
    )

    # If there is no directional basis yet, FIND_SUPPORT/FIND_ATTACK is the
    # correct first goal; there is nothing concrete to time/reliability-test.
    if not basis_ids:
        return []

    goals = []

    state = current_statement_state(
        rid,
        requirement_proof,
        {},
    )

    impact = impact_for_requirement(
        requirement
    )

    qhashes = previous_query_hashes(
        trace
    )

    if any(
        code
        in {
            "TEMPORAL_ASSESSMENT_MISSING",
            "TEMPORAL_SCOPE_NOT_PASSED",
        }
        for code in failures
    ):
        goals.append(
            make_goal(
                case_uid=
                    case_uid,
                cp_id=
                    cp_id,
                interpretation_id=
                    interpretation_id,
                rid=
                    rid,
                goal_type=
                    "RESOLVE_TIME",
                current_state=
                    state,
                blocking_reason_codes=[
                    code
                    for code in failures
                    if code.startswith(
                        "TEMPORAL_"
                    )
                ],
                prior_need_ids=[
                    need_id
                ],
                prohibited_query_hashes=
                    qhashes,
                available_action_types=[
                    "RESOLVE_TIME"
                ],
                impact=
                    impact,
                core_extension={
                    "goal_origin":
                        "PROOF_STANDARD_TEMPORAL_GATE",
                    "direction":
                        direction,
                    "target_artifact_ids":
                        basis_ids,
                },
            )
        )

    if any(
        code
        in {
            "INFORMATION_RELIABILITY_MISSING",
            "INFORMATION_RELIABILITY_UNRESOLVED",
            "INFORMATION_RELIABILITY_FAILED",
            "EVIDENCE_QUALITY_RELIABILITY_UNRESOLVED",
            "EVIDENCE_QUALITY_RELIABILITY_FAILED",
        }
        for code in failures
    ):
        goals.append(
            make_goal(
                case_uid=
                    case_uid,
                cp_id=
                    cp_id,
                interpretation_id=
                    interpretation_id,
                rid=
                    rid,
                goal_type=
                    "RESOLVE_RELIABILITY",
                current_state=
                    state,
                blocking_reason_codes=[
                    code
                    for code in failures
                    if (
                        "RELIABILITY"
                        in code
                    )
                ],
                prior_need_ids=[
                    need_id
                ],
                prohibited_query_hashes=
                    qhashes,
                available_action_types=[
                    "ASSESS_INFORMATION_RELIABILITY"
                ],
                impact=
                    impact,
                core_extension={
                    "goal_origin":
                        "PROOF_STANDARD_RELIABILITY_GATE",
                    "direction":
                        direction,
                    "target_artifact_ids":
                        basis_ids,
                    "architecture_interface_extension":
                        "RESOLVE_RELIABILITY_V1",
                },
            )
        )

    return goals


def build_open_goal_ledger(
    *,
    requirement_result: dict,
    coverage: dict,
    procedure_plan: dict,
    proof_standard: dict,
    contract_bundle: dict,
) -> dict:
    case_uid = get_case_uid(
        requirement_result,
        procedure_plan,
    )

    cp_id = str(
        procedure_plan.get(
            "cp_id"
        )
        or requirement_result.get(
            "cp_id"
        )
        or requirement_result.get(
            "evidence_requirement_plan",
            {},
        ).get(
            "cp_id"
        )
        or ""
    )

    if not cp_id:
        raise ValueError(
            "Missing cp_id"
        )

    interpretation_id = (
        contract_interpretation_id(
            contract_bundle
        )
    )

    reqs = requirement_map(
        requirement_result
    )

    cov = coverage_by_need(
        coverage
    )

    traces = traces_by_need(
        requirement_result
    )

    proofs = proof_by_requirement(
        proof_standard
    )

    objectives = objective_by_need(
        procedure_plan
    )

    upgrades = upgrade_by_need(
        procedure_plan
    )

    goals = []

    for need_id, upgrade in sorted(
        upgrades.items()
    ):
        trace = traces.get(
            need_id
        )

        coverage_report = cov.get(
            need_id
        )

        objective = objectives.get(
            need_id
        )

        if (
            trace is None
            or coverage_report is None
            or objective is None
        ):
            raise ValueError(
                "Cross-artifact reference missing for "
                + need_id
            )

        rid = str(
            trace[
                "requirement_id"
            ]
        )

        requirement = reqs[
            rid
        ]

        requirement_proof = (
            proofs.get(
                rid
            )
        )

        goals.append(
            build_coverage_goal(
                case_uid=
                    case_uid,
                cp_id=
                    cp_id,
                interpretation_id=
                    interpretation_id,
                requirement=
                    requirement,
                trace=
                    trace,
                coverage_report=
                    coverage_report,
                objective=
                    objective,
                upgrade=
                    upgrade,
                requirement_proof=
                    requirement_proof,
            )
        )

        goals.extend(
            build_resolution_goals(
                case_uid=
                    case_uid,
                cp_id=
                    cp_id,
                interpretation_id=
                    interpretation_id,
                requirement=
                    requirement,
                trace=
                    trace,
                requirement_proof=
                    requirement_proof,
            )
        )

    # Deduplicate exact semantic goals by goal_id.
    dedup = {}

    for goal in goals:
        dedup[
            goal[
                "goal_id"
            ]
        ] = goal

    goals = sorted(
        dedup.values(),
        key=lambda row: (
            {
                "DECISIVE": 0,
                "POTENTIALLY_DECISIVE": 1,
                "NON_DECISIVE": 2,
            }[
                row[
                    "estimated_verdict_impact"
                ]
            ],
            row[
                "goal_type"
            ],
            row[
                "goal_id"
            ],
        ),
    )

    ledger_basis = {
        "case_uid":
            case_uid,
        "cp_id":
            cp_id,
        "interpretation_id":
            interpretation_id,
        "procedure_plan_sha256":
            procedure_plan.get(
                "bundle_sha256"
            ),
        "coverage_sha256":
            coverage.get(
                "bundle_sha256"
            ),
        "proof_standard_sha256":
            proof_standard.get(
                "bundle_sha256"
            ),
        "contract_sha256":
            sha256_json(
                contract_bundle
            ),
    }

    evaluation_bundle_id = stable_id(
        "evalbundle-adapter",
        canonical_json(
            ledger_basis
        ),
    )

    ledger = {
        "schema":
            "freca-core-open-goal-ledger-v1",

        "ledger_id":
            stable_id(
                "goal-ledger",
                evaluation_bundle_id,
            ),

        "evaluation_bundle_id":
            evaluation_bundle_id,

        "case_uid":
            case_uid,

        "cp_id":
            cp_id,

        "interpretation_id":
            interpretation_id,

        "goals":
            goals,

        "fully_resolved":
            len(
                goals
            )
            == 0,

        "interface_extensions": [
            {
                "extension_id":
                    "RESOLVE_RELIABILITY_V1",
                "reason":
                    (
                        "Frozen architecture defines "
                        "INFORMATION_RELIABILITY_TEST and "
                        "InformationReliabilityAssessment but omits a "
                        "reliability-specific OpenGoal/RepairAction bridge."
                    ),
                "goal_type":
                    "RESOLVE_RELIABILITY",
                "action_type":
                    "ASSESS_INFORMATION_RELIABILITY",
                "proof_semantics_changed":
                    False,
            }
        ],

        "execution_status":
            "OPEN_GOALS_ONLY_NOT_EXECUTED",

        "repair_actions_executed":
            [],

        "proof_state_modified":
            False,

        "final_label":
            None,
    }

    ledger[
        "semantic_sha256"
    ] = sha256_json(
        {
            "evaluation_bundle_id":
                evaluation_bundle_id,
            "goals": [
                goal[
                    "goal_sha256"
                ]
                for goal in goals
            ],
            "interface_extensions":
                ledger[
                    "interface_extensions"
                ],
        }
    )

    return ledger


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
                "query_variants": [
                    {
                        "query":
                            "fixture support"
                    }
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
                "query_variants": [
                    {
                        "query":
                            "fixture attack"
                    }
                ],
            },
        ],
    }

    cov = {
        "bundle_sha256":
            "sha256:coverage",
        "need_reports": [
            {
                "need_id":
                    "ER1.support",
                "required_level":
                    "CANDIDATE_DISCOVERY",
                "status":
                    "LIMITED_TOP_K",
                "unassessed_candidate_ids": [
                    "e1"
                ],
                "failed_channels":
                    [],
                "identity_gap_ids":
                    [],
            },
            {
                "need_id":
                    "ER1.attack",
                "required_level":
                    "CANDIDATE_DISCOVERY",
                "status":
                    "COMPLETE",
                "unassessed_candidate_ids":
                    [],
                "failed_channels":
                    [],
                "identity_gap_ids":
                    [],
            },
        ],
    }

    proc = {
        "bundle_sha256":
            "sha256:procedure",
        "case_uid":
            "case-x",
        "cp_id":
            "CPX",
        "audit_procedure_objectives": [
            {
                "objective_id":
                    "obj-s",
                "population_frame_id":
                    "pop-1",
                "core_extension": {
                    "need_id":
                        "ER1.support"
                },
            },
            {
                "objective_id":
                    "obj-a",
                "population_frame_id":
                    "pop-1",
                "core_extension": {
                    "need_id":
                        "ER1.attack"
                },
            },
        ],
        "coverage_upgrade_requests": [
            {
                "upgrade_request_id":
                    "up-s",
                "need_id":
                    "ER1.support",
                "reason_codes": [
                    "PROOF_COVERAGE_PENDING"
                ],
            },
            {
                "upgrade_request_id":
                    "up-a",
                "need_id":
                    "ER1.attack",
                "reason_codes": [
                    "PROOF_COVERAGE_PENDING"
                ],
            },
        ],
    }

    proof = {
        "bundle_sha256":
            "sha256:proof",
        "requirement_reports": [
            {
                "requirement_id":
                    "ER1",
                "accepted_state":
                    "UNKNOWN",
                "support_proof": {
                    "basis_artifact_ids": [
                        "a1"
                    ],
                    "failure_codes": [
                        "TEMPORAL_ASSESSMENT_MISSING",
                        "INFORMATION_RELIABILITY_MISSING",
                        "COVERAGE_INCOMPLETE",
                    ],
                },
                "attack_proof": {
                    "basis_artifact_ids":
                        [],
                    "failure_codes": [
                        "NO_EXPLICIT_VIOLATION_BASIS",
                        "COVERAGE_INCOMPLETE",
                    ],
                },
            }
        ],
    }

    contract = {
        "contract": {
            "cp_id":
                "CPX",
            "atoms": [
                {
                    "atom_id":
                        "A1",
                }
            ],
        }
    }

    ledger = build_open_goal_ledger(
        requirement_result=
            rr,
        coverage=
            cov,
        procedure_plan=
            proc,
        proof_standard=
            proof,
        contract_bundle=
            contract,
    )

    goal_types = {
        goal[
            "goal_type"
        ]
        for goal in ledger[
            "goals"
        ]
    }

    assert "FIND_SUPPORT" in goal_types
    assert "FIND_ATTACK" in goal_types
    assert "RESOLVE_TIME" in goal_types
    assert "RESOLVE_RELIABILITY" in goal_types

    support_goal = next(
        goal
        for goal in ledger[
            "goals"
        ]
        if goal[
            "goal_type"
        ]
        == "FIND_SUPPORT"
    )

    assert (
        "ALIGN_NEXT_CANDIDATE_BATCH"
        in support_goal[
            "available_action_types"
        ]
    )

    reliability_goal = next(
        goal
        for goal in ledger[
            "goals"
        ]
        if goal[
            "goal_type"
        ]
        == "RESOLVE_RELIABILITY"
    )

    assert reliability_goal[
        "available_action_types"
    ] == [
        "ASSESS_INFORMATION_RELIABILITY"
    ]

    attack_goal = next(
        goal
        for goal in ledger[
            "goals"
        ]
        if goal[
            "goal_type"
        ]
        == "FIND_ATTACK"
    )

    assert (
        "RESOLVE_TIME"
        not in attack_goal[
            "available_action_types"
        ]
    )

    assert (
        ledger[
            "fully_resolved"
        ]
        is False
    )

    assert (
        ledger[
            "proof_state_modified"
        ]
        is False
    )

    assert (
        ledger[
            "final_label"
        ]
        is None
    )

    print(
        "open_goal_v1 self-tests: PASS"
    )
    print(
        "  coverage upgrade -> FIND_SUPPORT/FIND_ATTACK"
    )
    print(
        "  basis + temporal gap -> RESOLVE_TIME"
    )
    print(
        "  basis + reliability gap -> RESOLVE_RELIABILITY"
    )
    print(
        "  no directional basis -> no fake time/reliability goal"
    )
    print(
        "  no repair action executed"
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
        "--procedure-plan",
        type=Path,
    )

    parser.add_argument(
        "--proof-standard",
        type=Path,
    )

    parser.add_argument(
        "--contract",
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

        required = (
            args.requirement_result,
            args.coverage,
            args.procedure_plan,
            args.proof_standard,
            args.contract,
        )

        if all(
            item is None
            for item in required
        ):
            return

    required = {
        "--requirement-result":
            args.requirement_result,
        "--coverage":
            args.coverage,
        "--procedure-plan":
            args.procedure_plan,
        "--proof-standard":
            args.proof_standard,
        "--contract":
            args.contract,
    }

    missing = [
        name
        for name, value in required.items()
        if value is None
    ]

    if missing:
        parser.error(
            "missing required arguments: "
            + ", ".join(
                missing
            )
        )

    rr = load_json(
        args.requirement_result
    )

    coverage = load_json(
        args.coverage
    )

    procedure_plan = load_json(
        args.procedure_plan
    )

    proof_standard = load_json(
        args.proof_standard
    )

    contract = load_json(
        args.contract
    )

    ledger = build_open_goal_ledger(
        requirement_result=
            rr,
        coverage=
            coverage,
        procedure_plan=
            procedure_plan,
        proof_standard=
            proof_standard,
        contract_bundle=
            contract,
    )

    output = (
        args.output
        or args.procedure_plan.with_name(
            args.procedure_plan.stem
            + "_open_goals_v1.json"
        )
    )

    save_json(
        ledger,
        output,
    )

    print(
        "=" * 72
    )
    print(
        "FRECA OPEN GOAL LEDGER V1"
    )
    print(
        "=" * 72
    )

    print()
    print(
        "Goals:",
        len(
            ledger[
                "goals"
            ]
        ),
    )

    print()

    for index, goal in enumerate(
        ledger[
            "goals"
        ],
        start=1,
    ):
        ext = (
            goal.get(
                "core_extension"
            )
            or {}
        )

        print(
            f"{index:02d}.",
            goal[
                "goal_type"
            ],
            goal[
                "target_statement_id"
            ],
            "impact=",
            goal[
                "estimated_verdict_impact"
            ],
            "state=",
            goal[
                "current_state"
            ],
        )

        if ext.get(
            "direction"
        ):
            print(
                "    direction:",
                ext[
                    "direction"
                ],
            )

        if ext.get(
            "unassessed_candidate_ids"
        ) is not None:
            print(
                "    unassessed:",
                len(
                    ext.get(
                        "unassessed_candidate_ids",
                        [],
                    )
                ),
            )

        if ext.get(
            "target_artifact_ids"
        ) is not None:
            print(
                "    target artifacts:",
                len(
                    ext.get(
                        "target_artifact_ids",
                        [],
                    )
                ),
            )

        print(
            "    actions:",
            goal[
                "available_action_types"
            ],
        )

        print(
            "    blockers:",
            goal[
                "blocking_reason_codes"
            ],
        )

    print()
    print(
        "Fully resolved   :",
        ledger[
            "fully_resolved"
        ],
    )

    print(
        "Execution status :",
        ledger[
            "execution_status"
        ],
    )

    print(
        "Proof modified   :",
        ledger[
            "proof_state_modified"
        ],
    )

    print(
        "Final label      :",
        ledger[
            "final_label"
        ],
    )

    print(
        "Saved            :",
        output,
    )


if __name__ == "__main__":
    main()
