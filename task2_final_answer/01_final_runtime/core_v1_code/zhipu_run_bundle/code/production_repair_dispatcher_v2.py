#!/usr/bin/env python3
"""Validated Production V2 action planner and dispatcher."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import repair_executor_v1
import strategy_a_next_batch_v1 as strategy_a
from procedure_executor_v2 import execute_targeted_complete
from proof_gate_applicability_v2 import execute_information_reliability_action


ALLOWED_ACTION_TYPES = {
    "ALIGN_NEXT_CANDIDATE_BATCH",
    "RESOLVE_TIME",
    "ASSESS_INFORMATION_RELIABILITY",
    "COMPLETE_TARGETED_COVERAGE",
}


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


def _target_ids(goal: dict, requirement_result: dict, batch_size: int) -> list[str]:
    ext = goal.get("core_extension") or {}
    direct = list(ext.get("target_artifact_ids", []) or [])
    if direct:
        return list(map(str, direct))
    if goal.get("goal_type") in {"FIND_SUPPORT", "FIND_ATTACK"}:
        remaining = list(ext.get("unassessed_candidate_ids", []) or [])
        if not remaining:
            return []
        need_ids = list(goal.get("prior_need_ids", []) or [])
        if need_ids:
            trace = next(
                (
                    row
                    for row in requirement_result.get("retrieval_traces", [])
                    if str(row.get("need_id")) == str(need_ids[0])
                ),
                None,
            )
            if trace:
                ordered = [
                    str(row.get("evidence_id"))
                    for row in trace.get("candidate_universe", []) or []
                    if str(row.get("evidence_id")) in set(map(str, remaining))
                ]
                return ordered[:batch_size]
        return sorted(map(str, remaining))[:batch_size]
    return []


def _action(
    *,
    goal_id: str,
    goal_type: str,
    action_type: str,
    need_id: str | None,
    target_ids: list[str],
    round_index: int,
    coverage_purpose: str | None = None,
) -> dict:
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ValueError(f"Unsupported V2 action: {action_type}")
    signature_payload = {
        "goal_id": goal_id,
        "goal_type": goal_type,
        "action_type": action_type,
        "need_id": need_id,
        "target_artifact_ids": target_ids,
        "coverage_purpose": coverage_purpose,
        "round_index": round_index,
    }
    signature = sha256_json(signature_payload)
    return {
        "schema": "freca-core-repair-action-v2",
        "action_id": stable_id("action-v2", goal_id, action_type, signature),
        "round_index": round_index,
        "goal_id": goal_id,
        "goal_type": goal_type,
        "action_type": action_type,
        "target_artifact_ids": target_ids,
        "query_plan_id": need_id,
        "need_id": need_id,
        "coverage_purpose": coverage_purpose,
        "action_signature": signature,
        "expected_signal_types": {
            "ALIGN_NEXT_CANDIDATE_BATCH": ["EvidenceAlignment", "CandidateUseDisposition"],
            "RESOLVE_TIME": ["TemporalAssessment"],
            "ASSESS_INFORMATION_RELIABILITY": ["InformationReliabilityAssessment"],
            "COMPLETE_TARGETED_COVERAGE": ["TargetedCoverageProcedureArtifact"],
        }[action_type],
    }


def build_repair_plan(
    *,
    root: dict,
    policy: dict,
    round_index: int,
    allow_model_actions: bool,
) -> dict:
    state = policy.get("repair_state_machine") or {}
    batch_size = int(state.get("parent_alignment_budget", 24))
    actions = []
    terminal_limitations = copy.deepcopy(
        root["open_goals"].get("terminal_limitations", []) or []
    )

    for goal in root["open_goals"].get("goals", []):
        goal_type = str(goal.get("goal_type"))
        if goal_type in {"FIND_SUPPORT", "FIND_ATTACK"}:
            action_type = "ALIGN_NEXT_CANDIDATE_BATCH"
        elif goal_type == "RESOLVE_TIME":
            action_type = "RESOLVE_TIME"
        elif goal_type == "RESOLVE_RELIABILITY":
            action_type = "ASSESS_INFORMATION_RELIABILITY"
        else:
            continue
        need_ids = list(goal.get("prior_need_ids", []) or [])
        need_id = str(need_ids[0]) if need_ids else None
        targets = _target_ids(goal, root["requirement_result"], batch_size)
        if not targets:
            terminal_limitations.append(
                {
                    "goal_id": goal.get("goal_id"),
                    "action_type": action_type,
                    "reason_code": "NO_CONCRETE_TARGET_ARTIFACTS",
                }
            )
            continue
        if action_type == "ALIGN_NEXT_CANDIDATE_BATCH" and not allow_model_actions:
            terminal_limitations.append(
                {
                    "goal_id": goal.get("goal_id"),
                    "action_type": action_type,
                    "reason_code": "MODEL_ACTION_NOT_ADMITTED_IN_ZERO_API_MODE",
                }
            )
            continue
        actions.append(
            _action(
                goal_id=str(goal["goal_id"]),
                goal_type=goal_type,
                action_type=action_type,
                need_id=need_id,
                target_ids=targets,
                round_index=round_index,
            )
        )

    for request in root["procedure"].get("coverage_upgrade_requests", []) or []:
        need_id = str(request.get("need_id") or "")
        if not need_id:
            continue
        direction = next(
            (
                str(row.get("direction"))
                for row in root["requirement_result"].get("retrieval_traces", [])
                if str(row.get("need_id")) == need_id
            ),
            "",
        )
        coverage_report = next(
            (
                row
                for row in root["coverage"].get("need_reports", [])
                if str(row.get("need_id")) == need_id
            ),
            {},
        )
        purpose = str(
            coverage_report.get("coverage_purpose")
            or (
                "POSITIVE_EXISTENCE_PROOF"
                if direction == "SUPPORT"
                else "EXPLICIT_ADVERSE_PROOF"
            )
        )
        goal_id = stable_id("coverage-goal-v2", need_id, purpose)
        actions.append(
            _action(
                goal_id=goal_id,
                goal_type="COMPLETE_TARGETED_COVERAGE",
                action_type="COMPLETE_TARGETED_COVERAGE",
                need_id=need_id,
                target_ids=[need_id],
                round_index=round_index,
                coverage_purpose=purpose,
            )
        )

    max_actions = int(state.get("max_actions_per_round", 8))
    actions = actions[:max_actions]
    plan = {
        "schema": "freca-core-repair-plan-v2",
        "plan_id": stable_id(
            "repair-plan-v2",
            str(root["open_goals"].get("ledger_id")),
            str(round_index),
            ",".join(row["action_signature"] for row in actions),
        ),
        "policy_id": policy.get("policy_id"),
        "round_index": round_index,
        "allow_model_actions": allow_model_actions,
        "selected_goal_ids": sorted({row["goal_id"] for row in actions}),
        "actions": actions,
        "terminal_limitations": terminal_limitations,
        "all_admitted_actions_have_executor": all(
            row["action_type"] in ALLOWED_ACTION_TYPES for row in actions
        ),
    }
    plan["plan_sha256"] = sha256_json(plan)
    return plan


def execute_repair_plan(
    *,
    plan: dict,
    root: dict,
) -> dict:
    executions = []
    for action in plan.get("actions", []):
        action_type = action["action_type"]
        if action_type == "RESOLVE_TIME":
            pseudo_plan = {
                "plan_id": plan["plan_id"],
                "round_index": plan["round_index"],
                "actions": plan["actions"],
            }
            execution = repair_executor_v1.build_execution(
                repair_plan=pseudo_plan,
                requirement_result=root["requirement_result"],
                procedure_plan=root["procedure"],
                open_goal_ledger=root["open_goals"],
                action_id=action["action_id"],
                action_index=None,
            )
        elif action_type == "ASSESS_INFORMATION_RELIABILITY":
            execution = execute_information_reliability_action(
                action=action,
                requirement_result=root["requirement_result"],
            )
        elif action_type == "COMPLETE_TARGETED_COVERAGE":
            execution = execute_targeted_complete(
                requirement_result=root["requirement_result"],
                procedure_plan=root["procedure"],
                need_id=str(action["need_id"]),
                coverage_purpose=str(action["coverage_purpose"]),
                action_id=action["action_id"],
                goal_id=action["goal_id"],
            )
        elif action_type == "ALIGN_NEXT_CANDIDATE_BATCH":
            bundle = strategy_a.execute_arm(
                requirement_result=root["requirement_result"],
                coverage_before=root["coverage"],
                need_id=str(action["need_id"]),
                parent_budget=len(action["target_artifact_ids"]),
            )
            execution = copy.deepcopy(bundle["action_executions"][0])
            execution.update(
                {
                    "action_id": action["action_id"],
                    "goal_id": action["goal_id"],
                    "action_type": action_type,
                    "action_signature": action["action_signature"],
                }
            )
        else:  # pragma: no cover - plan validation guards this
            raise ValueError(action_type)
        executions.append(execution)

    planned_ids = [row["action_id"] for row in plan.get("actions", [])]
    executed_ids = [row["action_id"] for row in executions]
    bundle = {
        "schema": "freca-core-repair-round-artifacts-v2",
        "round_artifact_bundle_id": stable_id(
            "repair-round-v2", plan["plan_id"], *sorted(executed_ids)
        ),
        "repair_plan_id": plan["plan_id"],
        "round_index": plan["round_index"],
        "planned_action_ids": planned_ids,
        "executed_action_ids": executed_ids,
        "missing_action_ids": sorted(set(planned_ids) - set(executed_ids)),
        "round_execution_complete": set(planned_ids) == set(executed_ids),
        "action_executions": executions,
        "terminal_limitations": plan.get("terminal_limitations", []),
        "proof_state_modified": False,
        "upstream_artifacts_mutated": False,
        "final_label": None,
    }
    bundle["bundle_sha256"] = sha256_json(bundle)
    return bundle


def validate_repair_round_bundle(
    *,
    plan: dict,
    bundle: dict,
) -> tuple[bool, list[str]]:
    reasons = []
    planned = [str(row.get("action_id")) for row in plan.get("actions", [])]
    execution_rows = bundle.get("action_executions", []) or []
    executed = [str(row.get("action_id")) for row in execution_rows]
    if executed != list(bundle.get("executed_action_ids", []) or []):
        reasons.append("EXECUTED_ACTION_INDEX_MISMATCH")
    if planned != list(bundle.get("planned_action_ids", []) or []):
        reasons.append("PLANNED_ACTION_INDEX_MISMATCH")
    missing = sorted(set(planned) - set(executed))
    if missing != sorted(bundle.get("missing_action_ids", []) or []):
        reasons.append("MISSING_ACTION_INDEX_MISMATCH")
    if bool(not missing and len(planned) == len(executed)) != bool(
        bundle.get("round_execution_complete")
    ):
        reasons.append("ROUND_EXECUTION_COMPLETENESS_MISMATCH")
    action_types = {str(row.get("action_id")): row.get("action_type") for row in plan.get("actions", [])}
    for row in execution_rows:
        if action_types.get(str(row.get("action_id"))) != row.get("action_type"):
            reasons.append("EXECUTED_ACTION_TYPE_MISMATCH")
    unsigned = dict(bundle)
    unsigned.pop("bundle_sha256", None)
    if bundle.get("bundle_sha256") != sha256_json(unsigned):
        reasons.append("ROUND_BUNDLE_HASH_MISMATCH")
    return not reasons, sorted(set(reasons))
