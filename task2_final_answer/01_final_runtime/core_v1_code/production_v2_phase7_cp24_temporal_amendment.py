#!/usr/bin/env python3
"""Apply and audit the authorized CP24 typed-temporal amendment with zero API."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import freca_core_v1 as core
import production_runner_v2 as runner
import production_repair_dispatcher_v2 as dispatcher
import repair_feedback_v1_2 as feedback


FROZEN_V1_TREE_DIGEST = (
    "2e5c90ec718c4387941da347c31d69e260356ceef33072f165281704ae2dbad4"
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def save(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def v1_tree_digest(root: Path) -> str:
    found = subprocess.run(
        ["find", root.as_posix(), "-type", "f", "-print0"],
        check=True,
        stdout=subprocess.PIPE,
    )
    ordered = subprocess.run(
        ["sort", "-z"], check=True, input=found.stdout, stdout=subprocess.PIPE
    )
    manifest = subprocess.run(
        ["xargs", "-0", "sha256sum"],
        check=True,
        input=ordered.stdout,
        stdout=subprocess.PIPE,
    )
    return hashlib.sha256(manifest.stdout).hexdigest()


def contract_body(bundle: dict) -> dict:
    return bundle.get("contract", bundle)


def find_atom(bundle: dict, atom_id: str) -> dict:
    matches = [
        row
        for row in contract_body(bundle).get("atoms", []) or []
        if str(row.get("atom_id")) == atom_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one contract atom {atom_id}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-contract", type=Path, default=Path("contracts_v2/CP24.json"))
    parser.add_argument(
        "--amendment",
        type=Path,
        default=Path("contract_amendments_v2/CP24_A1_typed_temporal_required.json"),
    )
    parser.add_argument(
        "--live-coordinate",
        type=Path,
        default=Path("results_v2/production_run_v2_live_gate/case-003__CP24"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_v2/production_run_v2_phase7_cp24_temporal_amendment"),
    )
    args = parser.parse_args()

    api_call_count = 0

    def forbidden_api(*_args: Any, **_kwargs: Any) -> dict:
        nonlocal api_call_count
        api_call_count += 1
        raise RuntimeError("PHASE7_ZERO_API_GUARD")

    core.deepseek_json = forbidden_api

    base_bytes = args.base_contract.read_bytes()
    base = load(args.base_contract)
    amendment = load(args.amendment)
    expected_base_hash = str(amendment["base_contract_sha256"])
    observed_base_hash = sha256_bytes(base_bytes)
    if observed_base_hash != expected_base_hash:
        raise ValueError("Base CP24 contract hash differs from the frozen amendment")

    atom_id = str(amendment["atom_id"])
    base_atom = find_atom(base, atom_id)
    anchor_quotes = {
        str(row.get("quote")) for row in base_atom.get("anchors", []) or []
    }
    anchor_quotes.add(str(base_atom.get("criterion_quote")))
    missing_basis_quotes = [
        row["exact_quote"]
        for row in amendment.get("basis", [])
        if str(row.get("exact_quote")) not in anchor_quotes
    ]
    if missing_basis_quotes:
        raise ValueError("Amendment basis quote is not grounded in the frozen atom")
    if base_atom.get("temporal_required") is not None:
        raise ValueError("Base atom already has temporal_required")

    amended = copy.deepcopy(base)
    amended_atom = find_atom(amended, atom_id)
    amended_atom[str(amendment["field"])] = amendment["value"]

    restoration_check = copy.deepcopy(amended)
    find_atom(restoration_check, atom_id).pop(str(amendment["field"]), None)
    if canonical_json(restoration_check) != canonical_json(base):
        raise ValueError("Amendment changed fields outside the authorized atom field")

    amended_contract_path = args.output_dir / "amended_contracts" / "CP24.json"
    save(amended, amended_contract_path)

    arm_a_dir = args.live_coordinate / "arm_a"
    initial_rr = load(arm_a_dir / "requirement_result_v2.json")
    amended_arm_a = runner.build_layer7_v2(
        requirement_result=initial_rr,
        contract=amended,
    )

    saved_round = load(args.live_coordinate / "arm_b" / "round_bundle.json")
    merged_rr, merge_diagnostics = runner.merge_round_artifacts(
        amended_arm_a["requirement_result"], saved_round
    )
    amended_arm_b = runner.build_layer7_v2(
        requirement_result=merged_rr,
        contract=amended,
    )
    hard_gates = runner.evaluate_hard_gates_v2(
        before=amended_arm_a["requirement_result"],
        after=amended_arm_b["requirement_result"],
        proof_before=amended_arm_a["proof"],
        proof_after=amended_arm_b["proof"],
        round_bundle=saved_round,
    )
    diff = feedback.build_evaluation_diff(
        before_rr=amended_arm_a["requirement_result"],
        after_rr=amended_arm_b["requirement_result"],
        coverage_before=amended_arm_a["coverage"],
        coverage_after=amended_arm_b["coverage"],
        proof_before=amended_arm_a["proof"],
        proof_after=amended_arm_b["proof"],
        open_goals_before=amended_arm_a["open_goals"],
        open_goals_after=amended_arm_b["open_goals"],
        round_bundle=saved_round,
        hard_gates=hard_gates,
    )
    diff["v2_merge_diagnostics"] = merge_diagnostics
    amended_outcome, amended_fold = runner.build_outcome_and_fold(amended_arm_b, amended)

    policy = load(Path("production_repair_policy_v2_live_gate.json"))
    next_plan = dispatcher.build_repair_plan(
        root=amended_arm_b,
        policy=policy,
        round_index=2,
        allow_model_actions=False,
    )
    resolve_time_actions = [
        row for row in next_plan.get("actions", []) if row.get("action_type") == "RESOLVE_TIME"
    ]

    (
        diagnostic_after,
        executed_plan,
        diagnostic_bundle,
        diagnostic_hard_gates,
        diagnostic_diff,
    ) = runner.run_repair_round_v2(
        before=amended_arm_b,
        contract=amended,
        policy=policy,
        round_index=2,
        allow_model_actions=False,
    )
    diagnostic_outcome, diagnostic_fold = runner.build_outcome_and_fold(
        diagnostic_after, amended
    )
    diagnostic_executions = diagnostic_bundle.get("action_executions", []) or []
    temporal_assessments = [
        assessment
        for execution in diagnostic_executions
        for assessment in execution.get("temporal_assessments", []) or []
    ]

    classifications = amended_arm_b["gate_applicability"].get(
        "temporal_classifications", []
    )
    proof_failures = {
        str(report.get("requirement_id")): {
            direction: list((report.get(f"{direction.lower()}_proof") or {}).get("failure_codes", []))
            for direction in ("SUPPORT", "ATTACK")
        }
        for report in amended_arm_b["proof"].get("requirement_reports", [])
    }

    runner.save_layer(amended_arm_a, args.output_dir / "arm_a_amended_contract")
    runner.save_layer(amended_arm_b, args.output_dir / "arm_b_saved_round_replay")
    save(hard_gates, args.output_dir / "arm_b_saved_round_replay" / "hard_gates.json")
    save(diff, args.output_dir / "arm_b_saved_round_replay" / "evaluation_diff.json")
    save(amended_outcome, args.output_dir / "arm_b_saved_round_replay" / "outcome.json")
    save(amended_fold, args.output_dir / "arm_b_saved_round_replay" / "fold.json")
    save(next_plan, args.output_dir / "diagnostic_next_plan.json")
    diagnostic_dir = args.output_dir / "diagnostic_zero_api_temporal_round"
    runner.save_layer(diagnostic_after, diagnostic_dir / "after")
    save(executed_plan, diagnostic_dir / "repair_plan.json")
    save(diagnostic_bundle, diagnostic_dir / "round_bundle.json")
    save(diagnostic_hard_gates, diagnostic_dir / "hard_gates.json")
    save(diagnostic_diff, diagnostic_dir / "evaluation_diff.json")
    save(diagnostic_outcome, diagnostic_dir / "outcome.json")
    save(diagnostic_fold, diagnostic_dir / "fold.json")

    observed_v1_digest = v1_tree_digest(Path("results_v2/production_run_v1_shards"))
    report = {
        "schema": "freca-production-v2-phase7-cp24-temporal-amendment-report-v1",
        "coordinate": {"case_uid": "case-003", "cp_id": "CP24"},
        "authorization_scope": amendment["authorization_scope"],
        "zero_api_guard_enabled": True,
        "api_call_count": api_call_count,
        "base_contract_sha256": observed_base_hash,
        "base_contract_unchanged_after_run": sha256_bytes(args.base_contract.read_bytes())
        == observed_base_hash,
        "amended_contract_sha256": sha256_bytes(amended_contract_path.read_bytes()),
        "amended_field": {
            "atom_id": atom_id,
            "field": amendment["field"],
            "value": amendment["value"],
            "only_authorized_field_changed": True,
            "all_basis_quotes_grounded": not missing_basis_quotes,
        },
        "temporal_classifications": classifications,
        "proof_failure_codes": proof_failures,
        "resolve_time_action_count": len(resolve_time_actions),
        "next_plan_action_types": [row.get("action_type") for row in next_plan.get("actions", [])],
        "all_hard_gates_pass": hard_gates["all_hard_gates_pass"],
        "internal_outcome": amended_outcome.get("common_internal_outcome"),
        "fold_label": amended_fold.get("label"),
        "resolved_decisive_goal_count": diff["effect_vector"][
            "resolved_decisive_goal_count"
        ],
        "diagnostic_temporal_round": {
            "executed_action_types": [
                row.get("action_type") for row in diagnostic_executions
            ],
            "temporal_assessment_count": len(temporal_assessments),
            "temporal_statuses": [
                assessment.get("status") for assessment in temporal_assessments
            ],
            "temporal_relations": [
                assessment.get("temporal_relation") for assessment in temporal_assessments
            ],
            "resolved_decisive_goal_count": diagnostic_diff["effect_vector"][
                "resolved_decisive_goal_count"
            ],
            "internal_outcome": diagnostic_outcome.get("common_internal_outcome"),
            "all_hard_gates_pass": diagnostic_hard_gates["all_hard_gates_pass"],
        },
        "v1_preservation": {
            "expected_tree_digest": FROZEN_V1_TREE_DIGEST,
            "observed_tree_digest": observed_v1_digest,
            "unchanged": observed_v1_digest == FROZEN_V1_TREE_DIGEST,
        },
        "scale_recommendation": "NO-GO",
    }
    report["report_sha256"] = sha256_json(report)
    save(report, args.output_dir / "phase7_cp24_temporal_amendment_report.json")

    if api_call_count != 0:
        raise RuntimeError("Phase 7 attempted an API call")
    if not hard_gates["all_hard_gates_pass"]:
        raise RuntimeError("Phase 7 hard gate failure")
    if not diagnostic_hard_gates["all_hard_gates_pass"]:
        raise RuntimeError("Phase 7 diagnostic temporal hard gate failure")
    if not report["v1_preservation"]["unchanged"]:
        raise RuntimeError("V1 tree changed")

    print(
        "Phase 7 CP24 temporal amendment:",
        f"classification={[row.get('state') for row in classifications]}",
        f"resolve_time_actions={len(resolve_time_actions)}",
        f"outcome={report['internal_outcome']}",
        "api_calls=0",
        "recommendation=NO-GO",
    )


if __name__ == "__main__":
    main()
