#!/usr/bin/env python3
"""Provenance-separated Production V2 downstream execution path.

The CLI consumes an already-created requirement result.  It does not resume or
write into a V1 task directory.  Model-backed alignment actions are disabled by
default and require an explicit flag, which is reserved for the later approved
live gate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import core_outcome_adapter_v1 as outcome_adapter
import fold_policy_v3_core as fold_policy
import freca_core_v1 as core
import multi_atom_support_v1
import open_goal_v1
import procedure_objective_v1
import production_stop_gate_v1 as stop_gate
import proof_standard_v1_1 as proof_v1
import repair_feedback_v1_2 as feedback

import coverage_policy_v2
import proof_gate_applicability_v2
import production_repair_dispatcher_v2 as dispatcher


V2_RUNTIME_FILES = [
    "production_runner_v2.py",
    "production_repair_policy_v2.json",
    "coverage_policy_v2.py",
    "procedure_executor_v2.py",
    "proof_gate_applicability_v2.py",
    "production_repair_dispatcher_v2.py",
]


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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def runtime_hashes() -> dict[str, str]:
    return {
        name: sha256_file(Path(name))
        for name in V2_RUNTIME_FILES
        if Path(name).is_file()
    }


def expression_atom_ids(expression: Any) -> set[str]:
    if isinstance(expression, dict):
        values = {
            str(expression["atom_id"])
            if expression.get("op") == "ATOM" and expression.get("atom_id")
            else ""
        }
        for value in expression.values():
            values.update(expression_atom_ids(value))
        values.discard("")
        return values
    if isinstance(expression, list):
        values: set[str] = set()
        for value in expression:
            values.update(expression_atom_ids(value))
        return values
    return set()


def coverage_purpose_overrides(
    requirement_result: dict,
    contract: dict,
) -> dict[str, str]:
    contract_body = contract.get("contract", contract)
    nonapp_atoms = expression_atom_ids(contract_body.get("non_applicability"))
    nonapp_requirements = {
        str(row.get("requirement_id"))
        for row in requirement_result["evidence_requirement_plan"].get(
            "requirements", []
        )
        if str(row.get("atom_id")) in nonapp_atoms
    }
    return {
        str(trace["need_id"]): "NON_APPLICABILITY_COUNTERCHECK"
        for trace in requirement_result.get("retrieval_traces", [])
        if str(trace.get("requirement_id")) in nonapp_requirements
        and str(trace.get("direction")) == "ATTACK"
    }


def build_layer7_v2(
    *,
    requirement_result: dict,
    contract: dict,
    purpose_overrides: dict[str, str] | None = None,
) -> dict:
    rr, applicability = proof_gate_applicability_v2.apply_gate_assessments(
        requirement_result=requirement_result,
        contract_bundle=contract,
    )
    resolved_overrides = coverage_purpose_overrides(rr, contract)
    resolved_overrides.update(purpose_overrides or {})
    coverage = coverage_policy_v2.evaluate_coverage_bundle(
        rr,
        contract_bundle=contract,
        purpose_overrides=resolved_overrides,
    )
    proof = proof_gate_applicability_v2.evaluate_proof_standard_bundle(
        rr,
        coverage,
    )
    proof["coverage_source_sha256"] = coverage["bundle_sha256"]
    proof["proof_gate_applicability_sha256"] = applicability["audit_sha256"]
    proof["post_proof_argument"] = proof_v1.run_post_proof_argument(
        requirement_result=rr,
        contract_bundle=contract,
        proof_bundle=proof,
    )
    procedure = procedure_objective_v1.build_plan(rr, coverage)
    goals = open_goal_v1.build_open_goal_ledger(
        requirement_result=rr,
        coverage=coverage,
        procedure_plan=procedure,
        proof_standard=proof,
        contract_bundle=contract,
    )
    goals["schema"] = "freca-core-open-goal-ledger-v2"
    goals["v2_interface_extensions"] = [
        {
            "goal_type": "COMPLETE_TARGETED_COVERAGE",
            "action_type": "COMPLETE_TARGETED_COVERAGE",
            "source": "procedure_objective_v1.coverage_upgrade_requests",
            "goal_forces_semantic_result": False,
        }
    ]
    temporal_limitations = [
        {
            "limitation_id": "terminal-temporal-" + row["classification_id"],
            "requirement_id": row["requirement_id"],
            "blocker_code": "TEMPORAL_REQUIREMENT_UNRESOLVED",
            "state": "TERMINAL_LIMITATION",
            "reason_codes": list(row.get("reason_codes", [])),
            "required_external_change": (
                "ADD_TYPED_TEMPORAL_APPLICABILITY_BASIS_TO_FROZEN_CONTRACT_OR_"
                "EVIDENCE_REQUIREMENT_IN_A_SEPARATELY_APPROVED_CHANGE"
            ),
            "executable_in_current_v2_scope": False,
        }
        for row in applicability.get("temporal_classifications", [])
        if row.get("state") == "TEMPORAL_UNRESOLVED"
    ]
    goals["terminal_limitations"] = temporal_limitations
    goals["semantic_sha256"] = sha256_json(
        {
            "evaluation_bundle_id": goals.get("evaluation_bundle_id"),
            "goal_hashes": [row.get("goal_sha256") for row in goals.get("goals", [])],
            "interface_extensions": goals.get("interface_extensions", []),
            "v2_interface_extensions": goals["v2_interface_extensions"],
            "terminal_limitations": temporal_limitations,
        }
    )
    return {
        "requirement_result": rr,
        "coverage": coverage,
        "proof": proof,
        "procedure": procedure,
        "open_goals": goals,
        "gate_applicability": applicability,
    }


def merge_round_artifacts(
    requirement_result: dict,
    round_bundle: dict,
) -> tuple[dict, dict]:
    merged, diagnostics = feedback.merge_round_into_requirement_result(
        requirement_result,
        round_bundle,
    )
    existing = {
        str(row.get("procedure_artifact_id")): row
        for row in merged.get("targeted_coverage_procedure_artifacts", []) or []
        if row.get("procedure_artifact_id")
    }
    appended = []
    for execution in round_bundle.get("action_executions", []):
        for artifact in execution.get(
            "targeted_coverage_procedure_artifacts", []
        ) or []:
            artifact_id = str(artifact.get("procedure_artifact_id") or "")
            if not artifact_id or artifact_id in existing:
                continue
            existing[artifact_id] = copy.deepcopy(artifact)
            appended.append(artifact_id)
    merged["targeted_coverage_procedure_artifacts"] = list(existing.values())
    merged["schema"] = "freca-core-requirement-reasoning-v2-production-v2"
    diagnostics["appended_targeted_coverage_procedure_artifact_ids"] = appended
    diagnostics["v2_merge_sha256"] = sha256_json(diagnostics)
    return merged, diagnostics


def evaluate_hard_gates_v2(
    *,
    before: dict,
    after: dict,
    proof_before: dict,
    proof_after: dict,
    round_bundle: dict,
) -> dict:
    result = feedback.evaluate_hard_gates(
        requirement_result_before=before,
        requirement_result_after=after,
        proof_before=proof_before,
        proof_after=proof_after,
        round_bundle=round_bundle,
    )
    affected = set(result.get("affected_requirement_ids", []))
    for execution in round_bundle.get("action_executions", []):
        for artifact in execution.get(
            "targeted_coverage_procedure_artifacts", []
        ) or []:
            if artifact.get("requirement_id"):
                affected.add(str(artifact["requirement_id"]))
    violations = feedback.unsupported_propagation_violations(
        proof_before=proof_before,
        proof_after=proof_after,
        affected_rids=affected,
    )
    result["gates"]["unsupported_propagation"] = {
        "pass": not violations,
        "violation_count": len(violations),
        "violations": violations,
    }
    result["affected_requirement_ids"] = sorted(affected)
    result["all_hard_gates_pass"] = all(
        row["pass"] for row in result["gates"].values()
    )
    result["v2_targeted_procedure_affected_path_supported"] = True
    return result


def run_repair_round_v2(
    *,
    before: dict,
    contract: dict,
    policy: dict,
    round_index: int,
    allow_model_actions: bool,
) -> tuple[dict, dict, dict, dict, dict]:
    plan = dispatcher.build_repair_plan(
        root=before,
        policy=policy,
        round_index=round_index,
        allow_model_actions=allow_model_actions,
    )
    bundle = dispatcher.execute_repair_plan(plan=plan, root=before)
    bundle_valid, bundle_reasons = dispatcher.validate_repair_round_bundle(
        plan=plan, bundle=bundle
    )
    if not bundle_valid:
        raise ValueError("Invalid V2 repair round bundle: " + ", ".join(bundle_reasons))
    merged_rr, merge_diagnostics = merge_round_artifacts(
        before["requirement_result"], bundle
    )
    after = build_layer7_v2(requirement_result=merged_rr, contract=contract)
    hard_gates = evaluate_hard_gates_v2(
        before=before["requirement_result"],
        after=after["requirement_result"],
        proof_before=before["proof"],
        proof_after=after["proof"],
        round_bundle=bundle,
    )
    diff = feedback.build_evaluation_diff(
        before_rr=before["requirement_result"],
        after_rr=after["requirement_result"],
        coverage_before=before["coverage"],
        coverage_after=after["coverage"],
        proof_before=before["proof"],
        proof_after=after["proof"],
        open_goals_before=before["open_goals"],
        open_goals_after=after["open_goals"],
        round_bundle=bundle,
        hard_gates=hard_gates,
    )
    diff["v2_merge_diagnostics"] = merge_diagnostics
    return after, plan, bundle, hard_gates, diff


def build_outcome_and_fold(root: dict, contract: dict) -> tuple[dict, dict]:
    contract_body = contract.get("contract", contract)
    nonapp_atoms = expression_atom_ids(contract_body.get("non_applicability"))
    requirements = {
        str(row.get("requirement_id")): row
        for row in root["requirement_result"]["evidence_requirement_plan"].get(
            "requirements", []
        )
    }
    nonapp_requirement_ids = {
        rid
        for rid, requirement in requirements.items()
        if str(requirement.get("atom_id")) in nonapp_atoms
    }
    reports = {
        str(row.get("requirement_id")): row
        for row in root["proof"].get("requirement_reports", [])
    }
    positive_nonapp_evidence = bool(nonapp_requirement_ids) and all(
        (reports.get(rid, {}).get("support_proof") or {}).get(
            "accepted_direction"
        )
        is True
        for rid in nonapp_requirement_ids
    )
    nonapp_countercheck_reports = [
        row
        for row in root["coverage"].get("need_reports", [])
        if str(row.get("requirement_id")) in nonapp_requirement_ids
        and row.get("coverage_purpose") == "NON_APPLICABILITY_COUNTERCHECK"
    ]
    countercheck_complete = bool(nonapp_countercheck_reports) and all(
        row.get("proof_coverage_pass") is True
        for row in nonapp_countercheck_reports
    )
    activity_counterevidence = any(
        (reports.get(rid, {}).get("attack_proof") or {}).get(
            "accepted_direction"
        )
        is True
        for rid in nonapp_requirement_ids
    )
    na_countercheck = {
        "passed": bool(positive_nonapp_evidence and countercheck_complete),
        "activity_counterevidence_standing": activity_counterevidence,
        "basis_requirement_ids": sorted(nonapp_requirement_ids),
        "coverage_report_ids": [
            row.get("need_report_id") or row.get("need_id")
            for row in nonapp_countercheck_reports
        ],
    }
    evaluation_contract = copy.deepcopy(contract)
    nonapp_evaluation = {
        "expression": contract_body.get("non_applicability"),
        "positive": False,
        "negative": False,
        "reason_codes": [],
        "adapted_to_const_true": False,
    }
    nonapp_expression = contract_body.get("non_applicability")
    if isinstance(nonapp_expression, dict) and nonapp_expression.get("op") != "CONST":
        atom_states, atom_reasons = multi_atom_support_v1.atom_states(
            contract_body,
            root["requirement_result"],
            root["proof"],
        )
        positive, negative, expression_reasons = multi_atom_support_v1.eval_expr(
            nonapp_expression,
            atom_states,
        )
        nonapp_evaluation.update(
            {
                "positive": positive,
                "negative": negative,
                "reason_codes": sorted(set(atom_reasons + expression_reasons)),
            }
        )
        if positive:
            evaluation_contract_body = evaluation_contract.get("contract")
            if not isinstance(evaluation_contract_body, dict):
                evaluation_contract_body = evaluation_contract
            evaluation_contract_body["non_applicability"] = {
                "op": "CONST",
                "value": True,
            }
            nonapp_evaluation["adapted_to_const_true"] = True

    outcome = outcome_adapter.build_argument_evaluation_bundle(
        requirement_result=root["requirement_result"],
        contract_bundle=evaluation_contract,
        proof_bundle=root["proof"],
        na_countercheck=na_countercheck,
    )
    outcome["v2_non_applicability_evaluation"] = nonapp_evaluation
    unsigned_outcome = dict(outcome)
    unsigned_outcome.pop("bundle_sha256", None)
    outcome["bundle_sha256"] = sha256_json(unsigned_outcome)
    branches = [
        {
            "valid": True,
            "internal_outcome": row["internal_outcome"],
            "fold_gate_report": row["fold_gate_report"],
        }
        for row in outcome.get("evaluations", [])
    ]
    return outcome, fold_policy.fold_envelope(branches)


def save_layer(root: dict, directory: Path) -> None:
    for key, filename in {
        "requirement_result": "requirement_result_v2.json",
        "coverage": "coverage_v2.json",
        "proof": "proof_standard_v2.json",
        "procedure": "procedure_objective_v2.json",
        "open_goals": "open_goals_v2.json",
        "gate_applicability": "proof_gate_applicability_v2.json",
    }.items():
        save_json(root[key], directory / filename)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-requirement-result", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument(
        "--repair-policy", type=Path, default=Path("production_repair_policy_v2.json")
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--allow-model-actions", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    v1_root = Path("results_v2/production_run_v1_shards").resolve()
    if output_dir == v1_root or v1_root in output_dir.parents:
        raise ValueError("Production V2 may not write inside the V1 result tree")

    api_attempts = []
    original_api = core.deepseek_json

    def reject_api(*_args: Any, **kwargs: Any) -> dict:
        api_attempts.append({"model": kwargs.get("model")})
        raise RuntimeError("ZERO_API_GUARD: model/API action attempted")

    if not args.allow_model_actions:
        core.deepseek_json = reject_api

    try:
        initial_rr = load_json(args.initial_requirement_result.resolve())
        contract = load_json(args.contract.resolve())
        policy = load_json(args.repair_policy.resolve())
        current = build_layer7_v2(
            requirement_result=initial_rr,
            contract=contract,
        )
        save_layer(current, output_dir / "initial")

        history = []
        max_rounds = min(
            args.max_rounds,
            int((policy.get("repair_state_machine") or {}).get("max_rounds", 2)),
        )
        for round_index in range(1, max_rounds + 1):
            after, plan, bundle, hard_gates, diff = run_repair_round_v2(
                before=current,
                contract=contract,
                policy=policy,
                round_index=round_index,
                allow_model_actions=args.allow_model_actions,
            )
            stop = stop_gate.decide_after_round(
                evaluation_diff=diff,
                repair_round=bundle,
                round_index=round_index,
                max_rounds=max_rounds,
            )
            round_dir = output_dir / "repair" / f"round-{round_index}"
            save_json(plan, round_dir / "repair_plan_v2.json")
            save_json(bundle, round_dir / "round_bundle_v2.json")
            save_layer(after, round_dir / "after")
            save_json(hard_gates, round_dir / "hard_gates_v2.json")
            save_json(diff, round_dir / "evaluation_diff_v2.json")
            save_json(stop, round_dir / "stop_decision_v2.json")
            history.append(
                {
                    "round_index": round_index,
                    "plan_id": plan["plan_id"],
                    "executed_action_ids": bundle["executed_action_ids"],
                    "stop_decision": stop,
                }
            )
            if hard_gates.get("all_hard_gates_pass") is not True:
                break
            current = after
            if not stop.get("allow_next_repair_round", False):
                break

        outcome, fold = build_outcome_and_fold(current, contract)
        save_json(outcome, output_dir / "core_outcome_adapter_v2.json")
        save_json(fold, output_dir / "fold_decision_v3.json")
        run_report = {
            "schema": "freca-production-v2-run-report",
            "runner_version": "PRODUCTION_RUNNER_V2_1",
            "input_requirement_result_sha256": "sha256:"
            + sha256_file(args.initial_requirement_result.resolve()),
            "contract_sha256": "sha256:" + sha256_file(args.contract.resolve()),
            "repair_policy_sha256": "sha256:"
            + sha256_file(args.repair_policy.resolve()),
            "runtime_file_sha256": runtime_hashes(),
            "allow_model_actions": args.allow_model_actions,
            "repair_history": history,
            "common_internal_outcome": outcome.get("common_internal_outcome"),
            "fold_label": fold.get("label"),
            "fold_finality": fold.get("finality"),
            "api_call_audit": {
                "attempted_calls": api_attempts,
                "attempted_call_count": len(api_attempts),
                "api_call_count": 0 if not args.allow_model_actions else None,
                "zero_api_pass": not args.allow_model_actions and not api_attempts,
            },
            "v1_output_written": False,
            "answer_comparator_used": False,
            "human_or_historical_labels_used": False,
        }
        run_report["report_sha256"] = sha256_json(run_report)
        save_json(run_report, output_dir / "run_report_v2.json")
    finally:
        core.deepseek_json = original_api

    print("Production V2 downstream execution complete")
    print("Output:", output_dir)
    print("Outcome:", outcome.get("common_internal_outcome"))
    print("Fold:", fold.get("label"), fold.get("finality"))
    print("API calls:", 0 if not args.allow_model_actions else "live mode")


if __name__ == "__main__":
    main()
