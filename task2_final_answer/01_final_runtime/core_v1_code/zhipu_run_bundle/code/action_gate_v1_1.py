#!/usr/bin/env python3
"""FRECA Core Action Gate / RepairPlan v1.

This is the first deterministic Layer-8 plan selector.

Inputs:
  - OpenGoalLedger v1
  - requirement_reasoning_v2.json

Outputs:
  - reference-core SearchGateDecision
  - stable selected goals
  - typed RepairAction records
  - RepairPlan

This module DOES NOT execute any action.

Selection order follows frozen D8.7:
  1. verdict impact: DECISIVE > POTENTIALLY_DECISIVE > NON_DECISIVE
  2. goals with executable external-signal actions first
  3. exception/rebuttal/counterevidence path first
  4. lower estimated operational cost first
  5. goal_id stable tie-break

Reference-core search gate is reused:
  deterministic repair available -> TARGETED_REPAIR

Tree search is therefore NOT entered while a legal deterministic repair action
exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


# ============================================================================
# Reference-core reuse
# ============================================================================


def _install_reference_core_path() -> Path:
    candidates = []

    env = os.environ.get(
        "FRECA_REFERENCE_CORE_SRC"
    )

    if env:
        candidates.append(
            Path(env)
        )

    here = Path(
        __file__
    ).resolve().parent

    candidates.extend(
        [
            Path(
                "/home/MeggieYu/freca/reference_core/"
                "freca_reference_core_20260828/src"
            ),
            here.parent
            / "reference_core"
            / "freca_reference_core_20260828"
            / "src",
            Path(
                "/mnt/data/freca_ref/"
                "freca_reference_core_20260828/src"
            ),
        ]
    )

    for candidate in candidates:
        if (
            candidate
            / "freca"
            / "search"
            / "gate.py"
        ).exists():
            sys.path.insert(
                0,
                str(
                    candidate
                ),
            )

            return candidate

    raise RuntimeError(
        "Could not locate freca_reference_core_20260828/src. "
        "Set FRECA_REFERENCE_CORE_SRC."
    )


REFERENCE_CORE_SRC = (
    _install_reference_core_path()
)

from freca.search.gate import (  # noqa: E402
    decide_search_route,
)


# ============================================================================
# Generic helpers
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


def sha256_bytes(
    value: bytes,
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            value
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


def file_sha256(
    path: Path,
) -> str | None:
    if not path.exists():
        return None

    return sha256_bytes(
        path.read_bytes()
    )


# ============================================================================
# Index/config identities for typed action novelty
# ============================================================================


def traces_by_need(
    requirement_result: dict,
) -> dict[str, dict]:
    return {
        str(
            trace[
                "need_id"
            ]
        ): trace
        for trace in requirement_result.get(
            "retrieval_traces",
            [],
        )
    }


def evidence_index_sha256(
    trace: dict | None,
) -> str:
    if trace is None:
        return sha256_json(
            {
                "missing_trace":
                    True
            }
        )

    payload = {
        "need_id":
            trace.get(
                "need_id"
            ),
        "candidate_universe_ids":
            trace.get(
                "candidate_universe_ids",
                [],
            ),
        "retrieval_scan_chunk_count":
            trace.get(
                "retrieval_scan_chunk_count"
            ),
        "query_plan_mode":
            trace.get(
                "query_plan_mode"
            ),
        "typed_fact_scan":
            trace.get(
                "typed_fact_scan"
            ),
        "raw_lexical_scan":
            trace.get(
                "raw_lexical_scan"
            ),
        "structure_scan":
            trace.get(
                "structure_scan"
            ),
    }

    return sha256_json(
        payload
    )


def parser_alignment_config_sha256(
    base_dir: Path,
    requirement_result: dict,
) -> str:
    component_files = [
        "evidence_reasoning_v2.py",
        "fact_candidate_v1.py",
        "evidence_nature_v1.py",
        "identity_admissibility_v1.py",
    ]

    components = {}

    for name in component_files:
        digest = file_sha256(
            base_dir
            / name
        )

        components[
            name
        ] = digest

    payload = {
        "components":
            components,
        "result_schema":
            requirement_result.get(
                "schema"
            ),
        "contract_model":
            requirement_result.get(
                "model"
            ),
    }

    return sha256_json(
        payload
    )


# ============================================================================
# Actionability and stable goal ordering
# ============================================================================


IMPACT_RANK = {
    "DECISIVE": 0,
    "POTENTIALLY_DECISIVE": 1,
    "NON_DECISIVE": 2,
}


# Ordinal only.  These are NOT learned cost weights and do not affect proof.
ACTION_COST_RANK = {
    "RESOLVE_TIME": 1,
    "RESOLVE_IDENTITY": 1,
    "VALIDATE_CITATION": 1,

    "ALIGN_NEXT_CANDIDATE_BATCH": 2,
    "ASSESS_INFORMATION_RELIABILITY": 2,
    "RESOLVE_COMMODITY": 2,
    "CHECK_EXCEPTION": 2,
    "CHECK_REBUTTAL": 2,

    "EXECUTE_MISSING_CHANNEL": 3,
    "REQUERY_NEW_FACET": 3,
    "REPARSE_TARGET_SPAN": 3,

    "REEXECUTE_ARGUMENT_GRAPH": 4,
}


def goal_direction(
    goal: dict,
) -> str | None:
    ext = (
        goal.get(
            "core_extension"
        )
        or {}
    )

    direction = ext.get(
        "direction"
    )

    if direction:
        return str(
            direction
        )

    return None


def target_artifacts(
    goal: dict,
) -> list[str]:
    ext = (
        goal.get(
            "core_extension"
        )
        or {}
    )

    return [
        str(
            item
        )
        for item in (
            ext.get(
                "target_artifact_ids",
                [],
            )
            or []
        )
    ]


def unassessed_candidates(
    goal: dict,
) -> list[str]:
    ext = (
        goal.get(
            "core_extension"
        )
        or {}
    )

    return [
        str(
            item
        )
        for item in (
            ext.get(
                "unassessed_candidate_ids",
                [],
            )
            or []
        )
    ]


def executable_action_types(
    goal: dict,
) -> list[str]:
    """Return actions executable with CURRENT artifacts.

    Layer 8 is not allowed to fabricate a new QueryPlan.  Therefore
    REQUERY_NEW_FACET is not directly executable here; it needs a later Layer-7
    deterministic QueryPlan builder.

    RESOLVE_IDENTITY also requires concrete target identity-gap artifacts; the
    current OpenGoal adapter does not yet carry them.
    """

    available = set(
        str(
            item
        )
        for item in goal.get(
            "available_action_types",
            [],
        )
    )

    executable = []

    if (
        "ALIGN_NEXT_CANDIDATE_BATCH"
        in available
        and unassessed_candidates(
            goal
        )
    ):
        executable.append(
            "ALIGN_NEXT_CANDIDATE_BATCH"
        )

    if (
        "RESOLVE_TIME"
        in available
        and target_artifacts(
            goal
        )
    ):
        executable.append(
            "RESOLVE_TIME"
        )

    if (
        "ASSESS_INFORMATION_RELIABILITY"
        in available
        and target_artifacts(
            goal
        )
    ):
        executable.append(
            "ASSESS_INFORMATION_RELIABILITY"
        )

    if (
        "VALIDATE_CITATION"
        in available
        and target_artifacts(
            goal
        )
    ):
        executable.append(
            "VALIDATE_CITATION"
        )

    if (
        "CHECK_EXCEPTION"
        in available
        and target_artifacts(
            goal
        )
    ):
        executable.append(
            "CHECK_EXCEPTION"
        )

    if (
        "CHECK_REBUTTAL"
        in available
        and target_artifacts(
            goal
        )
    ):
        executable.append(
            "CHECK_REBUTTAL"
        )

    return sorted(
        executable,
        key=lambda action: (
            ACTION_COST_RANK.get(
                action,
                99,
            ),
            action,
        ),
    )


def counterevidence_priority(
    goal: dict,
) -> bool:
    if goal.get(
        "goal_type"
    ) in {
        "FIND_ATTACK",
        "CHECK_EXCEPTION",
        "CHECK_REBUTTAL",
    }:
        return True

    # Resolution of an already-found ATTACK basis remains on the
    # counterevidence path.
    return (
        goal_direction(
            goal
        )
        == "ATTACK"
    )


def minimum_action_cost(
    goal: dict,
) -> int:
    actions = executable_action_types(
        goal
    )

    if not actions:
        return 999

    return min(
        ACTION_COST_RANK.get(
            action,
            99,
        )
        for action in actions
    )


def goal_sort_key(
    goal: dict,
) -> tuple:
    actions = executable_action_types(
        goal
    )

    return (
        IMPACT_RANK.get(
            str(
                goal.get(
                    "estimated_verdict_impact",
                    "NON_DECISIVE",
                )
            ),
            99,
        ),

        # Executable external-signal actions first.
        0 if actions else 1,

        # Counterevidence / rebuttal / exception path first.
        0 if counterevidence_priority(
            goal
        ) else 1,

        minimum_action_cost(
            goal
        ),

        str(
            goal.get(
                "goal_id",
                "",
            )
        ),
    )


# ============================================================================
# Action construction
# ============================================================================


def ordered_unassessed_batch(
    *,
    goal: dict,
    trace: dict | None,
    batch_size: int,
) -> list[str]:
    remaining = set(
        unassessed_candidates(
            goal
        )
    )

    if not remaining:
        return []

    if trace:
        ordered = [
            str(
                row.get(
                    "evidence_id"
                )
            )
            for row in trace.get(
                "candidate_universe",
                [],
            )
            if str(
                row.get(
                    "evidence_id"
                )
            )
            in remaining
        ]

        if ordered:
            return ordered[
                :batch_size
            ]

    return sorted(
        remaining
    )[
        :batch_size
    ]


def expected_signal_types(
    action_type: str,
) -> list[str]:
    mapping = {
        "ALIGN_NEXT_CANDIDATE_BATCH": [
            "EvidenceAlignment",
            "CandidateUseDisposition",
        ],
        "RESOLVE_TIME": [
            "TemporalAssessment",
        ],
        "ASSESS_INFORMATION_RELIABILITY": [
            "InformationReliabilityAssessment",
        ],
        "VALIDATE_CITATION": [
            "CitationValidation",
        ],
        "CHECK_EXCEPTION": [
            "ExceptionAlignment",
        ],
        "CHECK_REBUTTAL": [
            "RebuttalAlignment",
        ],
    }

    return mapping.get(
        action_type,
        [
            "ValidatedArtifact",
        ],
    )


def channel_set(
    action_type: str,
) -> list[str]:
    mapping = {
        "ALIGN_NEXT_CANDIDATE_BATCH": [
            "EXISTING_CANDIDATE_UNIVERSE",
        ],
        "RESOLVE_TIME": [
            "DETERMINISTIC_TEMPORAL_CHECK",
        ],
        "ASSESS_INFORMATION_RELIABILITY": [
            "INFORMATION_RELIABILITY_TEST",
        ],
        "VALIDATE_CITATION": [
            "CITATION_VALIDATOR",
        ],
        "CHECK_EXCEPTION": [
            "EXCEPTION_CHECK",
        ],
        "CHECK_REBUTTAL": [
            "REBUTTAL_CHECK",
        ],
    }

    return mapping.get(
        action_type,
        [],
    )


def build_action(
    *,
    goal: dict,
    action_type: str,
    round_index: int,
    trace: dict | None,
    evidence_index_hash: str,
    config_hash: str,
    prior_action_ids: list[str],
    alignment_batch_size: int,
) -> dict:
    goal_id = str(
        goal[
            "goal_id"
        ]
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

    if (
        action_type
        == "ALIGN_NEXT_CANDIDATE_BATCH"
    ):
        targets = ordered_unassessed_batch(
            goal=
                goal,
            trace=
                trace,
            batch_size=
                alignment_batch_size,
        )

    else:
        targets = target_artifacts(
            goal
        )

    if not targets:
        raise ValueError(
            f"Action {action_type} for {goal_id} has no target artifacts"
        )

    direction = goal_direction(
        goal
    )

    constraint_delta = {
        "goal_type": [
            str(
                goal.get(
                    "goal_type"
                )
            )
        ],
        "target_statement_id": [
            str(
                goal.get(
                    "target_statement_id"
                )
            )
        ],
    }

    if direction:
        constraint_delta[
            "direction"
        ] = [
            direction
        ]

    if (
        action_type
        == "ALIGN_NEXT_CANDIDATE_BATCH"
    ):
        constraint_delta[
            "coverage_target"
        ] = [
            "TARGETED_COMPLETE"
        ]

    query_plan_id = (
        need_ids[
            0
        ]
        if need_ids
        else None
    )

    query_hash = None

    channels = channel_set(
        action_type
    )

    signature_payload = {
        "goal_id":
            goal_id,
        "action_type":
            action_type,
        "normalized_query_hash":
            query_hash,
        "channel_set":
            channels,
        "constraint_delta":
            constraint_delta,
        "target_artifact_ids":
            targets,
        "evidence_index_sha256":
            evidence_index_hash,
        "parser_alignment_config_sha256":
            config_hash,
    }

    signature = sha256_json(
        signature_payload
    )

    action_id = stable_id(
        "action",
        goal_id,
        action_type,
        signature,
    )

    return {
        "action_id":
            action_id,

        "round_index":
            round_index,

        "goal_id":
            goal_id,

        "action_type":
            action_type,

        "target_artifact_ids":
            targets,

        "query_plan_id":
            query_plan_id,

        "query_hash":
            query_hash,

        "channel_set":
            channels,

        "constraint_delta":
            constraint_delta,

        "expected_signal_types":
            expected_signal_types(
                action_type
            ),

        "prior_action_ids":
            sorted(
                set(
                    prior_action_ids
                )
            ),

        "action_signature":
            signature,

        "action_signature_components":
            {
                "evidence_index_sha256":
                    evidence_index_hash,
                "parser_alignment_config_sha256":
                    config_hash,
            },

        "execution_status":
            "PLANNED_NOT_EXECUTED",
    }


# ============================================================================
# Prior action history / novelty
# ============================================================================



def load_prior_actions(
    paths: list[Path],
) -> tuple[
    set[str],
    dict[str, list[str]],
]:
    """Load novelty history from EXECUTED actions only.

    Accepted history artifacts:
      - repair round bundles with ``action_executions``;
      - action lists only when an action explicitly carries
        execution_status/action_execution_status == EXECUTED;
      - artifacts with ``executed_action_ids`` may authorize matching action IDs.

    A RepairPlan whose actions are merely PLANNED_NOT_EXECUTED contributes
    nothing to novelty history.
    """

    signatures: set[str] = set()
    action_ids_by_goal: dict[str, list[str]] = {}

    for path in paths:
        artifact = load_json(path)

        executed_ids = {
            str(x)
            for x in artifact.get("executed_action_ids", [])
            if x
        }

        candidates = []

        # Preferred production history source.
        for action in artifact.get("action_executions", []) or []:
            candidates.append(action)

        # Backward-compatible action container, but only explicit execution
        # may enter history.
        for action in artifact.get("actions", []) or []:
            candidates.append(action)

        for action in candidates:
            action_id = str(action.get("action_id") or "")

            status = str(
                action.get("action_execution_status")
                or action.get("execution_status")
                or ""
            ).upper()

            executed = (
                status == "EXECUTED"
                or (
                    bool(action_id)
                    and action_id in executed_ids
                )
            )

            if not executed:
                continue

            signature = action.get("action_signature")

            if signature:
                signatures.add(str(signature))

            goal_id = action.get("goal_id")

            if goal_id and action_id:
                action_ids_by_goal.setdefault(
                    str(goal_id),
                    [],
                ).append(action_id)

    for goal_id in list(action_ids_by_goal):
        action_ids_by_goal[goal_id] = sorted(
            set(action_ids_by_goal[goal_id])
        )

    return signatures, action_ids_by_goal



# ============================================================================
# RepairPlan
# ============================================================================


def build_repair_plan(
    *,
    open_goal_ledger: dict,
    requirement_result: dict,
    base_dir: Path,
    prior_plan_paths: list[Path],
    round_index: int = 1,
    max_selected_goals_per_round: int = 3,
    max_actions_per_round: int = 6,
    alignment_batch_size: int = 24,
) -> dict:
    if round_index < 1:
        raise ValueError(
            "round_index must be >= 1"
        )

    if round_index > 2:
        raise ValueError(
            "frozen RepairBudget allows at most 2 rounds"
        )

    goals = list(
        open_goal_ledger.get(
            "goals",
            [],
        )
    )

    traces = traces_by_need(
        requirement_result
    )

    config_hash = (
        parser_alignment_config_sha256(
            base_dir,
            requirement_result,
        )
    )

    (
        prior_signatures,
        prior_action_ids_by_goal,
    ) = load_prior_actions(
        prior_plan_paths
    )

    ranked_goals = sorted(
        goals,
        key=
            goal_sort_key,
    )

    selected_goals = []

    for goal in ranked_goals:
        if not executable_action_types(
            goal
        ):
            continue

        selected_goals.append(
            goal
        )

        if len(
            selected_goals
        ) >= max_selected_goals_per_round:
            break

    actions = []
    duplicate_rejections = []

    for goal in selected_goals:
        if len(
            actions
        ) >= max_actions_per_round:
            break

        executable = executable_action_types(
            goal
        )

        if not executable:
            continue

        # D8.7 chooses concrete actions; v1 selects the cheapest legal,
        # executable action for each selected goal.  Alternative actions remain
        # in the OpenGoal ledger for later rounds/search.
        action_type = executable[
            0
        ]

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

        trace = (
            traces.get(
                need_ids[
                    0
                ]
            )
            if need_ids
            else None
        )

        index_hash = (
            evidence_index_sha256(
                trace
            )
        )

        action = build_action(
            goal=
                goal,
            action_type=
                action_type,
            round_index=
                round_index,
            trace=
                trace,
            evidence_index_hash=
                index_hash,
            config_hash=
                config_hash,
            prior_action_ids=
                prior_action_ids_by_goal.get(
                    str(
                        goal[
                            "goal_id"
                        ]
                    ),
                    [],
                ),
            alignment_batch_size=
                alignment_batch_size,
        )

        if (
            action[
                "action_signature"
            ]
            in prior_signatures
        ):
            duplicate_rejections.append(
                {
                    "goal_id":
                        goal[
                            "goal_id"
                        ],
                    "action_type":
                        action_type,
                    "action_signature":
                        action[
                            "action_signature"
                        ],
                    "reason_code":
                        "L8_REPAIR_REPEATED_SIGNATURE",
                }
            )

            continue

        actions.append(
            action
        )

    deterministic_repair_available = bool(
        actions
    )

    task_requires_multistep_planning = (
        len(
            goals
        )
        > 1
    )

    external_signal_environment_available = (
        any(
            executable_action_types(
                goal
            )
            for goal in goals
        )
    )

    # There is currently no in-domain trained discriminator for production
    # pruning.  This value is intentionally False.
    discriminator_validated_in_domain = False

    route = decide_search_route(
        deterministic_repair_available=
            deterministic_repair_available,
        task_requires_multistep_planning=
            task_requires_multistep_planning,
        external_signal_environment_available=
            external_signal_environment_available,
        discriminator_validated_in_domain=
            discriminator_validated_in_domain,
    )

    if (
        deterministic_repair_available
        and route.route
        != "TARGETED_REPAIR"
    ):
        raise RuntimeError(
            "reference search gate failed deterministic repair invariant"
        )

    case_uid = str(
        open_goal_ledger.get(
            "case_uid",
            "case-unknown",
        )
    )

    cp_id = str(
        open_goal_ledger.get(
            "cp_id",
            "",
        )
    )

    base_evaluation_bundle_id = str(
        open_goal_ledger[
            "evaluation_bundle_id"
        ]
    )

    budget = {
        "max_rounds":
            2,
        "max_selected_goals_per_round":
            max_selected_goals_per_round,
        "max_actions_per_round":
            max_actions_per_round,

        # Runtime/cost limits remain unbound in Core v1 because Layer-0 budget
        # config has not yet been implemented.  The plan is valid for selection
        # but must not be executed beyond the explicit per-action batch cap.
        "alignment_batch_size":
            alignment_batch_size,
        "max_alignment_batches_per_round":
            None,
        "max_reparse_sources_per_round":
            None,
        "max_model_calls_total":
            None,
        "max_generated_tokens_total":
            None,
        "max_wall_time_ms":
            None,
        "runtime_budget_binding_status":
            "PARTIALLY_BOUND_CORE_V1",
    }

    plan = {
        "schema":
            "freca-core-repair-plan-v1",

        "plan_id":
            stable_id(
                "repair-plan",
                case_uid,
                cp_id,
                str(
                    round_index
                ),
                open_goal_ledger[
                    "semantic_sha256"
                ],
            ),

        "case_uid":
            case_uid,

        "cp_id":
            cp_id,

        "base_evaluation_bundle_id":
            base_evaluation_bundle_id,

        "open_goal_ledger_id":
            open_goal_ledger[
                "ledger_id"
            ],

        "round_index":
            round_index,

        "selected_goal_ids": [
            str(
                goal[
                    "goal_id"
                ]
            )
            for goal in selected_goals
        ],

        "selected_goal_ranking": [
            {
                "goal_id":
                    goal[
                        "goal_id"
                    ],
                "goal_type":
                    goal[
                        "goal_type"
                    ],
                "direction":
                    goal_direction(
                        goal
                    ),
                "impact":
                    goal[
                        "estimated_verdict_impact"
                    ],
                "executable_action_types":
                    executable_action_types(
                        goal
                    ),
                "counterevidence_priority":
                    counterevidence_priority(
                        goal
                    ),
                "minimum_action_cost_rank":
                    minimum_action_cost(
                        goal
                    ),
            }
            for goal in selected_goals
        ],

        "actions":
            actions,

        "duplicate_action_rejections":
            duplicate_rejections,

        "budget":
            budget,

        "selection_rule_version":
            "freca-core-d8.7-stable-selector-v1",

        "search_gate_decision": {
            "route":
                route.route,
            "reason_codes":
                list(
                    route.reason_codes
                ),
            "permanent_pruning_allowed":
                bool(
                    route.permanent_pruning_allowed
                ),
            "reference_core_src":
                str(
                    REFERENCE_CORE_SRC
                ),
        },

        "tree_search_allowed_now":
            False,

        "tree_search_block_reason":
            (
                "DETERMINISTIC_TARGETED_REPAIR_AVAILABLE"
                if deterministic_repair_available
                else None
            ),

        "execution_status":
            "PLANNED_NOT_EXECUTED",

        "proof_state_modified":
            False,

        "final_label":
            None,

        "planner_invariants": [
            "no model router used",
            "no human/gold comparator consumed",
            "only OpenGoal-declared actions considered",
            "new QueryPlan not fabricated by Layer 8",
            "duplicate action signatures rejected",
            "tree search blocked while deterministic repair is available",
        ],
    }

    plan[
        "plan_sha256"
    ] = sha256_json(
        plan
    )

    return plan


# ============================================================================
# Label-free self-tests
# ============================================================================


def _run_self_tests_v1() -> None:
    ledger = {
        "ledger_id":
            "ledger-x",
        "semantic_sha256":
            "sha256:ledger",
        "evaluation_bundle_id":
            "eval-x",
        "case_uid":
            "case-x",
        "cp_id":
            "CPX",
        "goals": [
            {
                "goal_id":
                    "g-support",
                "goal_type":
                    "FIND_SUPPORT",
                "estimated_verdict_impact":
                    "DECISIVE",
                "target_statement_id":
                    "stmt-er1",
                "prior_need_ids":
                    [
                        "ER1.support"
                    ],
                "available_action_types": [
                    "ALIGN_NEXT_CANDIDATE_BATCH",
                    "REQUERY_NEW_FACET",
                ],
                "core_extension": {
                    "direction":
                        "SUPPORT",
                    "unassessed_candidate_ids": [
                        "e2",
                        "e3",
                    ],
                },
            },
            {
                "goal_id":
                    "g-attack",
                "goal_type":
                    "FIND_ATTACK",
                "estimated_verdict_impact":
                    "DECISIVE",
                "target_statement_id":
                    "stmt-er1",
                "prior_need_ids":
                    [
                        "ER1.attack"
                    ],
                "available_action_types": [
                    "ALIGN_NEXT_CANDIDATE_BATCH",
                    "REQUERY_NEW_FACET",
                ],
                "core_extension": {
                    "direction":
                        "ATTACK",
                    "unassessed_candidate_ids": [
                        "e4",
                    ],
                },
            },
            {
                "goal_id":
                    "g-time-attack",
                "goal_type":
                    "RESOLVE_TIME",
                "estimated_verdict_impact":
                    "DECISIVE",
                "target_statement_id":
                    "stmt-er1",
                "prior_need_ids":
                    [
                        "ER1.attack"
                    ],
                "available_action_types": [
                    "RESOLVE_TIME"
                ],
                "core_extension": {
                    "direction":
                        "ATTACK",
                    "target_artifact_ids": [
                        "a1",
                    ],
                },
            },
            {
                "goal_id":
                    "g-reliability-support",
                "goal_type":
                    "RESOLVE_RELIABILITY",
                "estimated_verdict_impact":
                    "DECISIVE",
                "target_statement_id":
                    "stmt-er1",
                "prior_need_ids":
                    [
                        "ER1.support"
                    ],
                "available_action_types": [
                    "ASSESS_INFORMATION_RELIABILITY"
                ],
                "core_extension": {
                    "direction":
                        "SUPPORT",
                    "target_artifact_ids": [
                        "a2",
                    ],
                },
            },
        ],
    }

    rr = {
        "schema":
            "fixture",
        "retrieval_traces": [
            {
                "need_id":
                    "ER1.support",
                "candidate_universe_ids": [
                    "e1",
                    "e2",
                    "e3",
                ],
                "candidate_universe": [
                    {
                        "evidence_id":
                            "e1"
                    },
                    {
                        "evidence_id":
                            "e2"
                    },
                    {
                        "evidence_id":
                            "e3"
                    },
                ],
            },
            {
                "need_id":
                    "ER1.attack",
                "candidate_universe_ids": [
                    "e4",
                ],
                "candidate_universe": [
                    {
                        "evidence_id":
                            "e4"
                    }
                ],
            },
        ],
    }

    with tempfile.TemporaryDirectory() as td:
        plan = build_repair_plan(
            open_goal_ledger=
                ledger,
            requirement_result=
                rr,
            base_dir=
                Path(
                    td
                ),
            prior_plan_paths=
                [],
            round_index=
                1,
            max_selected_goals_per_round=
                3,
            max_actions_per_round=
                6,
            alignment_batch_size=
                24,
        )

    assert (
        plan[
            "search_gate_decision"
        ][
            "route"
        ]
        == "TARGETED_REPAIR"
    )

    assert (
        plan[
            "tree_search_allowed_now"
        ]
        is False
    )

    # Counterevidence path comes before support path; cheap ATTACK time
    # resolution is selected within the first 3.
    selected_types = [
        row[
            "goal_type"
        ]
        for row in plan[
            "selected_goal_ranking"
        ]
    ]

    assert (
        "FIND_ATTACK"
        in selected_types
    )

    assert (
        "RESOLVE_TIME"
        in selected_types
    )

    action_types = {
        row[
            "action_type"
        ]
        for row in plan[
            "actions"
        ]
    }

    assert (
        "ALIGN_NEXT_CANDIDATE_BATCH"
        in action_types
    )

    assert (
        "RESOLVE_TIME"
        in action_types
    )

    assert all(
        row[
            "execution_status"
        ]
        == "PLANNED_NOT_EXECUTED"
        for row in plan[
            "actions"
        ]
    )

    assert (
        plan[
            "proof_state_modified"
        ]
        is False
    )

    assert (
        plan[
            "final_label"
        ]
        is None
    )

    print(
        "action_gate_v1 self-tests: PASS"
    )
    print(
        "  reference search gate -> TARGETED_REPAIR"
    )
    print(
        "  deterministic repair blocks tree search"
    )
    print(
        "  DECISIVE/counterevidence/cost stable ordering works"
    )
    print(
        "  typed action signatures generated"
    )
    print(
        "  no action executed"
    )


# ============================================================================
# CLI
# ============================================================================



def run_self_tests() -> None:
    _run_self_tests_v1()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        planned = td / "planned.json"
        executed = td / "executed.json"

        save_json(
            {
                "actions": [
                    {
                        "action_id": "a-planned",
                        "goal_id": "g1",
                        "action_signature": "sha256:planned",
                        "execution_status": "PLANNED_NOT_EXECUTED",
                    }
                ]
            },
            planned,
        )

        save_json(
            {
                "executed_action_ids": ["a-exec"],
                "action_executions": [
                    {
                        "action_id": "a-exec",
                        "goal_id": "g1",
                        "action_signature": "sha256:exec",
                        "action_execution_status": "EXECUTED",
                    }
                ],
            },
            executed,
        )

        signatures, by_goal = load_prior_actions(
            [planned, executed]
        )

        assert "sha256:planned" not in signatures
        assert "sha256:exec" in signatures
        assert by_goal == {"g1": ["a-exec"]}

    print("action_gate_v1_1 production-freeze history tests: PASS")
    print("  PLANNED_NOT_EXECUTED is not novelty history")
    print("  EXECUTED signatures are preserved")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--open-goals",
        type=Path,
    )

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--prior-plan",
        type=Path,
        action="append",
        default=[],
    )

    parser.add_argument(
        "--round-index",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--alignment-batch-size",
        type=int,
        default=24,
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if (
            args.open_goals
            is None
            and args.requirement_result
            is None
        ):
            return

    if (
        args.open_goals
        is None
        or args.requirement_result
        is None
    ):
        parser.error(
            "--open-goals and --requirement-result are required "
            "unless only --self-test is used"
        )

    ledger = load_json(
        args.open_goals
    )

    rr = load_json(
        args.requirement_result
    )

    plan = build_repair_plan(
        open_goal_ledger=
            ledger,
        requirement_result=
            rr,
        base_dir=
            Path.cwd(),
        prior_plan_paths=
            args.prior_plan,
        round_index=
            args.round_index,
        alignment_batch_size=
            args.alignment_batch_size,
    )

    output = (
        args.output
        or args.open_goals.with_name(
            args.open_goals.stem
            + "_repair_plan_v1.json"
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
        "FRECA ACTION GATE / REPAIR PLAN V1"
    )
    print(
        "=" * 72
    )

    route = plan[
        "search_gate_decision"
    ]

    print()
    print(
        "Route:",
        route[
            "route"
        ],
    )

    print(
        "Reason:",
        route[
            "reason_codes"
        ],
    )

    print(
        "Tree search allowed now:",
        plan[
            "tree_search_allowed_now"
        ],
    )

    print()
    print(
        "Selected goals:"
    )

    for index, row in enumerate(
        plan[
            "selected_goal_ranking"
        ],
        start=1,
    ):
        print(
            f"  {index}.",
            row[
                "goal_type"
            ],
            row[
                "goal_id"
            ],
            "direction=",
            row[
                "direction"
            ],
            "actions=",
            row[
                "executable_action_types"
            ],
            "cost_rank=",
            row[
                "minimum_action_cost_rank"
            ],
        )

    print()
    print(
        "Planned actions:"
    )

    for index, action in enumerate(
        plan[
            "actions"
        ],
        start=1,
    ):
        print(
            f"  {index}.",
            action[
                "action_type"
            ],
            "goal=",
            action[
                "goal_id"
            ],
            "targets=",
            len(
                action[
                    "target_artifact_ids"
                ]
            ),
        )

        print(
            "     expected:",
            action[
                "expected_signal_types"
            ],
        )

        print(
            "     signature:",
            action[
                "action_signature"
            ],
        )

    if plan[
        "duplicate_action_rejections"
    ]:
        print()
        print(
            "Duplicate action rejections:",
            len(
                plan[
                    "duplicate_action_rejections"
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
