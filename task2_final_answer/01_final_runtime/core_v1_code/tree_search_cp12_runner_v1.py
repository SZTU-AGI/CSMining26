#!/usr/bin/env python3
"""Run one isolated CP12 D/T/M repair-routing experiment.

This runner DOES real action execution for the committed branch, then calls the
existing repair feedback/rerun pipeline before any second decision.

Planner search itself never executes branch rollouts.

Important isolation:
- production action_gate_v1_1 is NOT patched;
- production tree_search_allowed_now=False is NOT changed;
- experiment takes the already-generated legal ActionGate actions and chooses
  among the currently executable subset;
- no answer comparator or historical label is accepted as an input.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import action_gate_v1_1 as gate
import repair_round_executor_v1 as executor
import repair_feedback_v1_2 as feedback
import production_stop_gate_v1 as stop_gate

import tree_search_harness_v1 as search
import tree_search_planner_v1 as planner

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
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(str(p) for p in parts)
    return (
        prefix
        + "-"
        + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def experiment_subplan(
    *,
    source_plan: dict,
    selected_action: dict,
    arm: str,
    step_index: int,
) -> dict:
    goal_id = str(selected_action.get("goal_id") or "")

    ranking = [
        row
        for row in source_plan.get(
            "selected_goal_ranking",
            [],
        )
        if str(row.get("goal_id") or "") == goal_id
    ]

    out = {
        "schema":
            "freca-core-tree-search-experiment-repair-plan-v1",

        "plan_id":
            stable_id(
                "tree-exp-plan",
                str(source_plan.get("plan_id")),
                arm,
                str(step_index),
                str(selected_action["action_id"]),
            ),

        "source_action_gate_plan_id":
            source_plan.get("plan_id"),

        "case_uid":
            source_plan.get("case_uid"),

        "cp_id":
            source_plan.get("cp_id"),

        "base_evaluation_bundle_id":
            source_plan.get("base_evaluation_bundle_id"),

        "open_goal_ledger_id":
            source_plan.get("open_goal_ledger_id"),

        "round_index":
            step_index,

        "selected_goal_ids":
            [goal_id] if goal_id else [],

        "selected_goal_ranking":
            ranking,

        "actions":
            [copy.deepcopy(selected_action)],

        "duplicate_action_rejections":
            [],

        "budget": {
            "max_rounds": 2,
            "max_selected_goals_per_round": 1,
            "max_actions_per_round": 1,
            "alignment_batch_size": 24,
            "experiment_global_max_executed_actions": 2,
        },

        "selection_rule_version":
            f"TREE_SEARCH_EXPERIMENT_{arm}_V1",

        "production_tree_search_gate_overridden":
            True,

        "override_scope":
            "EXPERIMENT_ONLY",

        "execution_status":
            "PLANNED_NOT_EXECUTED",

        "proof_state_modified":
            False,

        "final_label":
            None,

        "answer_comparator_used":
            False,
    }

    out["plan_sha256"] = sha256_json(out)
    return out


def tree_specific_hard_gates(
    *,
    selected_action: dict,
    source_actions: list[dict],
    round_bundle: dict,
) -> dict:
    violations = {
        "catalog_external_action": [],
        "direct_proof_state_mutation": [],
        "fabricated_evidence_or_action_id": [],
    }

    if (
        str(selected_action.get("action_type") or "")
        not in search.LEGAL_ACTION_CATALOG
    ):
        violations["catalog_external_action"].append(
            str(selected_action.get("action_type"))
        )

    if (
        selected_action.get("proof_state_modified") is True
        or selected_action.get("final_label") is not None
        or round_bundle.get("proof_state_modified") is True
        or round_bundle.get("final_label") is not None
    ):
        violations["direct_proof_state_mutation"].append(
            str(selected_action.get("action_id"))
        )

    source_by_id = {
        str(row.get("action_id")): row
        for row in source_actions
    }

    source = source_by_id.get(
        str(selected_action.get("action_id"))
    )

    if source is None:
        violations["fabricated_evidence_or_action_id"].append(
            "ACTION_ID_NOT_FROM_ACTION_GATE"
        )
    else:
        if (
            str(source.get("action_signature"))
            != str(selected_action.get("action_signature"))
        ):
            violations[
                "fabricated_evidence_or_action_id"
            ].append(
                "ACTION_SIGNATURE_CHANGED_AFTER_GATE"
            )

        if (
            list(source.get("target_artifact_ids") or [])
            != list(selected_action.get("target_artifact_ids") or [])
        ):
            violations[
                "fabricated_evidence_or_action_id"
            ].append(
                "TARGET_IDS_CHANGED_AFTER_GATE"
            )

    all_pass = all(
        not rows
        for rows in violations.values()
    )

    return {
        "all_pass": all_pass,
        "violations": violations,
    }


def choose_action(
    *,
    arm: str,
    executable_actions: list[dict],
    open_goals: dict,
    proof: dict,
    coverage: dict,
    planner_model: str,
) -> tuple[dict, dict | None]:
    if arm == "D":
        return (
            search.deterministic_select(
                executable_actions,
                horizon=2,
            ),
            None,
        )

    state_summary = planner.build_state_summary(
        open_goals=open_goals,
        proof=proof,
        coverage=coverage,
    )

    scorer = planner.MemoizedDeepSeekPlanScorer(
        state_summary=state_summary,
        model=planner_model,
        thinking=False,
    )

    if arm == "T":
        decision = search.tot_select(
            executable_actions,
            scorer=scorer,
            depth=2,
            beam_width=3,
        )
    elif arm == "M":
        decision = search.mcts_select(
            executable_actions,
            scorer=scorer,
            depth=2,
            simulations=12,
        )
    else:
        raise ValueError(f"Unknown arm: {arm}")

    return decision, scorer.telemetry_summary()


def cumulative_round_bundle(
    *,
    arm: str,
    step_bundles: list[dict],
) -> dict:
    executions = []
    planned_ids = []
    executed_ids = []

    for bundle in step_bundles:
        executions.extend(
            copy.deepcopy(
                bundle.get("action_executions", [])
            )
        )
        planned_ids.extend(
            str(x)
            for x in bundle.get("planned_action_ids", [])
        )
        executed_ids.extend(
            str(x)
            for x in bundle.get("executed_action_ids", [])
        )

    out = {
        "schema":
            "freca-core-tree-search-cumulative-round-v1",
        "arm": arm,
        "planned_action_ids":
            sorted(set(planned_ids)),
        "executed_action_ids":
            sorted(set(executed_ids)),
        "missing_action_ids": [],
        "round_execution_complete": True,
        "action_executions": executions,
        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,
        "answer_comparator_used": False,
    }
    out["bundle_sha256"] = sha256_json(out)
    return out


def run_experiment(
    *,
    arm: str,
    requirement_result: dict,
    coverage_before: dict,
    proof_before: dict,
    open_goals_before: dict,
    contract: dict,
    base_dir: Path,
    output_dir: Path,
    planner_model: str = "deepseek-v4-pro",
    max_actions: int = 2,
) -> dict:
    if arm not in {"D", "T", "M"}:
        raise ValueError("arm must be D, T, or M")

    if max_actions < 1 or max_actions > 2:
        raise ValueError(
            "experiment v1 freezes max_actions to 1..2"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    initial_rr = copy.deepcopy(requirement_result)
    initial_cov = copy.deepcopy(coverage_before)
    initial_proof = copy.deepcopy(proof_before)
    initial_goals = copy.deepcopy(open_goals_before)

    state_rr = copy.deepcopy(requirement_result)
    state_cov = copy.deepcopy(coverage_before)
    state_proof = copy.deepcopy(proof_before)
    state_goals = copy.deepcopy(open_goals_before)

    prior_execution_paths: list[Path] = []
    step_bundles: list[dict] = []
    step_records: list[dict] = []
    planner_records: list[dict] = []

    stop_reason = None

    for step in range(1, max_actions + 1):
        source_plan = gate.build_repair_plan(
            open_goal_ledger=state_goals,
            requirement_result=state_rr,
            base_dir=base_dir,
            prior_plan_paths=prior_execution_paths,
            round_index=step,
            max_selected_goals_per_round=3,
            max_actions_per_round=6,
            alignment_batch_size=24,
        )

        executable_actions, rejected_actions = (
            search.legal_actions(
                source_plan,
                executable_only=True,
            )
        )

        save_json(
            source_plan,
            output_dir
            / f"step{step}_source_action_gate_plan.json",
        )

        save_json(
            {
                "accepted_action_ids": [
                    row["action_id"]
                    for row in executable_actions
                ],
                "rejected": rejected_actions,
            },
            output_dir
            / f"step{step}_action_filter.json",
        )

        if not executable_actions:
            stop_reason = "NO_EXECUTABLE_ACTION"
            break

        decision, planner_telemetry = choose_action(
            arm=arm,
            executable_actions=executable_actions,
            open_goals=state_goals,
            proof=state_proof,
            coverage=state_cov,
            planner_model=planner_model,
        )

        selected = decision["selected_first_action"]

        save_json(
            {
                "arm": arm,
                "decision": decision,
                "planner_telemetry": planner_telemetry,
            },
            output_dir
            / f"step{step}_search_decision.json",
        )

        if planner_telemetry:
            planner_records.append(
                planner_telemetry
            )

        subplan = experiment_subplan(
            source_plan=source_plan,
            selected_action=selected,
            arm=arm,
            step_index=step,
        )

        subplan_path = (
            output_dir
            / f"step{step}_committed_plan.json"
        )
        save_json(subplan, subplan_path)

        with capture_deepseek_telemetry() as events:
            round_bundle = (
                executor.execute_remaining_round_actions(
                    repair_plan=subplan,
                    requirement_result=state_rr,
                    existing_executions=[],
                    action_indices=[1],
                )
            )

        execution_cost = summarize_telemetry(events)
        round_bundle["cost_telemetry"] = execution_cost

        for execution in round_bundle.get(
            "action_executions",
            [],
        ):
            execution.setdefault(
                "cost_telemetry",
                execution_cost,
            )

        round_bundle["bundle_sha256"] = (
            executor.sha256_json(round_bundle)
        )

        round_path = (
            output_dir
            / f"step{step}_round_bundle.json"
        )
        save_json(round_bundle, round_path)

        tree_gates = tree_specific_hard_gates(
            selected_action=selected,
            source_actions=source_plan.get(
                "actions",
                [],
            ),
            round_bundle=round_bundle,
        )

        merged_rr, merge_diag = (
            feedback.merge_round_into_requirement_result(
                state_rr,
                round_bundle,
            )
        )

        rerun = feedback.rerun_layer7(
            merged_requirement_result=merged_rr,
            contract_bundle=contract,
        )

        rr_after = rerun["requirement_result"]
        cov_after = rerun["coverage"]
        proof_after = rerun["proof_standard"]
        goals_after = rerun["open_goals"]

        hard_gates = feedback.evaluate_hard_gates(
            requirement_result_before=state_rr,
            requirement_result_after=rr_after,
            proof_before=state_proof,
            proof_after=proof_after,
            round_bundle=round_bundle,
        )

        diff = feedback.build_evaluation_diff(
            before_rr=state_rr,
            after_rr=rr_after,
            coverage_before=state_cov,
            coverage_after=cov_after,
            proof_before=state_proof,
            proof_after=proof_after,
            open_goals_before=state_goals,
            open_goals_after=goals_after,
            round_bundle=round_bundle,
            hard_gates=hard_gates,
        )

        stop = stop_gate.decide_after_round(
            evaluation_diff=diff,
            repair_round=round_bundle,
            round_index=step,
            max_rounds=2,
        )

        save_json(
            rr_after,
            output_dir
            / f"step{step}_requirement_after.json",
        )
        save_json(
            cov_after,
            output_dir
            / f"step{step}_coverage_after.json",
        )
        save_json(
            proof_after,
            output_dir
            / f"step{step}_proof_after.json",
        )
        save_json(
            goals_after,
            output_dir
            / f"step{step}_open_goals_after.json",
        )
        save_json(
            diff,
            output_dir
            / f"step{step}_evaluation_diff.json",
        )
        save_json(
            {
                "core_hard_gates": hard_gates,
                "tree_specific_hard_gates": tree_gates,
                "stop_gate": stop,
                "merge_diagnostics": merge_diag,
            },
            output_dir
            / f"step{step}_diagnostics.json",
        )

        step_bundles.append(round_bundle)
        step_records.append({
            "step": step,
            "selected_action_id":
                selected["action_id"],
            "selected_action_type":
                selected["action_type"],
            "selected_action_signature":
                selected["action_signature"],
            "planner_value":
                decision.get("planner_value"),
            "execution_cost":
                execution_cost,
            "tree_hard_gates":
                tree_gates,
            "core_hard_gates":
                hard_gates,
            "evaluation_diff":
                diff,
            "stop_gate":
                stop,
        })

        # This round artifact is the executed-only novelty history source.
        prior_execution_paths.append(round_path)

        state_rr = rr_after
        state_cov = cov_after
        state_proof = proof_after
        state_goals = goals_after

        if not stop.get(
            "allow_next_repair_round",
            False,
        ):
            stop_reason = (
                ",".join(
                    stop.get("stop_reasons") or []
                )
                or "PRODUCTION_STOP_GATE"
            )
            break

    cumulative = cumulative_round_bundle(
        arm=arm,
        step_bundles=step_bundles,
    )

    final_hard = feedback.evaluate_hard_gates(
        requirement_result_before=initial_rr,
        requirement_result_after=state_rr,
        proof_before=initial_proof,
        proof_after=state_proof,
        round_bundle=cumulative,
    )

    final_diff = feedback.build_evaluation_diff(
        before_rr=initial_rr,
        after_rr=state_rr,
        coverage_before=initial_cov,
        coverage_after=state_cov,
        proof_before=initial_proof,
        proof_after=state_proof,
        open_goals_before=initial_goals,
        open_goals_after=state_goals,
        round_bundle=cumulative,
        hard_gates=final_hard,
    )

    tree_gate_all_pass = all(
        record["tree_hard_gates"]["all_pass"]
        for record in step_records
    )

    summary = {
        "schema":
            "freca-core-tree-search-experiment-result-v1",
        "arm": arm,
        "case_uid":
            open_goals_before.get("case_uid"),
        "cp_id":
            open_goals_before.get("cp_id"),
        "executed_action_count":
            len(step_records),
        "steps":
            step_records,
        "planner_telemetry":
            planner_records,
        "final_core_hard_gates":
            final_hard,
        "tree_specific_hard_gates_all_pass":
            tree_gate_all_pass,
        "final_evaluation_diff":
            final_diff,
        "stop_reason":
            stop_reason,
        "production_files_modified":
            False,
        "answer_comparator_used":
            False,
        "historical_labels_used":
            False,
        "final_label":
            None,
    }

    summary["result_sha256"] = sha256_json(
        summary
    )

    save_json(
        summary,
        output_dir / "experiment_result.json",
    )

    return summary


def run_self_tests() -> None:
    # Only check helper isolation; do not invoke real Core rerun or API.
    action = {
        "action_id": "a1",
        "goal_id": "g1",
        "action_type": "ALIGN_NEXT_CANDIDATE_BATCH",
        "action_signature": "sha256:a1",
        "target_artifact_ids": ["e1"],
        "execution_status": "PLANNED_NOT_EXECUTED",
    }

    source_plan = {
        "plan_id": "p1",
        "case_uid": "case-x",
        "cp_id": "CP12",
        "base_evaluation_bundle_id": "eval-1",
        "open_goal_ledger_id": "goal-ledger-1",
        "selected_goal_ranking": [
            {"goal_id": "g1"}
        ],
    }

    sub = experiment_subplan(
        source_plan=source_plan,
        selected_action=action,
        arm="T",
        step_index=1,
    )

    assert len(sub["actions"]) == 1
    assert sub["final_label"] is None
    assert (
        sub["production_tree_search_gate_overridden"]
        is True
    )

    round_bundle = {
        "proof_state_modified": False,
        "final_label": None,
    }

    gates = tree_specific_hard_gates(
        selected_action=action,
        source_actions=[action],
        round_bundle=round_bundle,
    )

    assert gates["all_pass"] is True

    forged = copy.deepcopy(action)
    forged["target_artifact_ids"] = ["fabricated"]

    bad = tree_specific_hard_gates(
        selected_action=forged,
        source_actions=[action],
        round_bundle=round_bundle,
    )
    assert bad["all_pass"] is False

    print("tree_search_cp12_runner_v1 self-tests: PASS")
    print("  experiment plan contains one committed ActionGate action")
    print("  production tree-search gate is not patched")
    print("  forged target/action mutation is detected")
    print("  no API under self-test")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--arm",
        choices=["D", "T", "M"],
    )
    parser.add_argument(
        "--requirement-result",
        type=Path,
    )
    parser.add_argument(
        "--coverage",
        type=Path,
    )
    parser.add_argument(
        "--proof",
        type=Path,
    )
    parser.add_argument(
        "--open-goals",
        type=Path,
    )
    parser.add_argument(
        "--contract",
        type=Path,
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("."),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
    )
    parser.add_argument(
        "--planner-model",
        default="deepseek-v4-pro",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        if args.arm is None:
            return

    required = {
        "--arm": args.arm,
        "--requirement-result":
            args.requirement_result,
        "--coverage":
            args.coverage,
        "--proof":
            args.proof,
        "--open-goals":
            args.open_goals,
        "--contract":
            args.contract,
        "--output-dir":
            args.output_dir,
    }

    missing = [
        key
        for key, value in required.items()
        if value is None
    ]

    if missing:
        parser.error(
            "missing: " + ", ".join(missing)
        )

    result = run_experiment(
        arm=args.arm,
        requirement_result=load_json(
            args.requirement_result
        ),
        coverage_before=load_json(
            args.coverage
        ),
        proof_before=load_json(
            args.proof
        ),
        open_goals_before=load_json(
            args.open_goals
        ),
        contract=load_json(
            args.contract
        ),
        base_dir=args.base_dir,
        output_dir=args.output_dir,
        planner_model=args.planner_model,
        max_actions=args.max_actions,
    )

    print("=" * 78)
    print("FRECA TREE-SEARCH EXPERIMENT V1")
    print("=" * 78)
    print("Arm:", result["arm"])
    print("Case:", result["case_uid"])
    print("CP:", result["cp_id"])
    print(
        "Executed actions:",
        result["executed_action_count"],
    )
    print(
        "Tree hard gates:",
        result[
            "tree_specific_hard_gates_all_pass"
        ],
    )

    effect = result["final_evaluation_diff"]

    for key in (
        "resolved_decisive_goal_count",
        "goal_aligned_verified_signal_gain",
        "proof_blocker_delta",
        "coverage_delta",
        "candidate_disposition_gain",
        "new_conflict_count",
        "verified_signal_gain",
    ):
        if key in effect:
            print(f"{key}:", effect[key])

    print("Stop:", result["stop_reason"])
    print(
        "Saved:",
        args.output_dir / "experiment_result.json",
    )


if __name__ == "__main__":
    main()
