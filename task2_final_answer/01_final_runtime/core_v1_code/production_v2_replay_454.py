#!/usr/bin/env python3
"""Phase 4 zero-API replay over completed Production V1 coordinates."""

from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import freca_core_v1 as core
import production_runner_v2 as runner


OUTCOMES = [
    "PROVEN_COMPLIANT",
    "PROVEN_NON_COMPLIANT",
    "PROVEN_NOT_APPLICABLE",
    "NOT_DEMONSTRATED",
    "CONFLICTING",
    "UNKNOWN",
    "REPLAY_INCOMPATIBLE",
    "SYSTEM_BLOCK",
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def save(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def blocker_rows(proof: dict) -> dict[str, list[str]]:
    result = {}
    for row in proof.get("requirement_reports", []):
        rid = str(row.get("requirement_id"))
        for key, direction in (("support_proof", "SUPPORT"), ("attack_proof", "ATTACK")):
            directional = row.get(key) or {}
            result[f"{rid}.{direction}"] = sorted(set(directional.get("failure_codes", []) or []))
    return result


def blocker_set(rows: dict[str, list[str]]) -> list[str]:
    return sorted({code for codes in rows.values() for code in codes})


def final_v1_proof_path(task_dir: Path) -> Path:
    after = sorted(task_dir.glob("repair/round-*/after/proof_standard_v1_1.json"))
    if after:
        return after[-1]
    return task_dir / "initial" / "layer7" / "proof_standard_v1_1.json"


def compatibility(
    *, task_dir: Path, decision: dict, contracts_dir: Path
) -> tuple[bool, list[str], dict[str, Path]]:
    cp_id = str(decision.get("cp_id") or task_dir.name)
    paths = {
        "task_meta": task_dir / "task_meta.json",
        "initial_requirement_result": task_dir / "initial" / "requirement_result.json",
        "v1_final_proof": final_v1_proof_path(task_dir),
        "v1_outcome": task_dir / "core_outcome_adapter_v1.json",
        "v1_fold": task_dir / "fold_decision_v3.json",
        "contract": contracts_dir / f"{cp_id}.json",
    }
    reasons = [f"MISSING:{name}" for name, path in paths.items() if not path.is_file()]
    if decision.get("status") != "COMPLETE":
        reasons.append("DECISION_NOT_COMPLETE")
    if reasons:
        return False, reasons, paths
    try:
        meta = load(paths["task_meta"])
        rr = load(paths["initial_requirement_result"])
        contract = load(paths["contract"])
    except Exception as exc:
        return False, [f"JSON_LOAD_FAILED:{type(exc).__name__}:{exc}"], paths
    if str(rr.get("case_uid")) != str(decision.get("case_uid")):
        reasons.append("CASE_UID_MISMATCH")
    if str(rr.get("cp_id")) != cp_id:
        reasons.append("CP_ID_MISMATCH")
    if str((contract.get("contract") or {}).get("cp_id")) != cp_id:
        reasons.append("CONTRACT_CP_ID_MISMATCH")
    if meta.get("input_fingerprint") != decision.get("input_fingerprint"):
        reasons.append("INPUT_FINGERPRINT_MISMATCH")
    return not reasons, reasons, paths


def replay_one(
    *, task_dir: Path, decision: dict, paths: dict[str, Path], policy: dict
) -> dict:
    rr = load(paths["initial_requirement_result"])
    contract = load(paths["contract"])
    v1_proof = load(paths["v1_final_proof"])
    v1_outcome = load(paths["v1_outcome"])
    v1_fold = load(paths["v1_fold"])

    initial = runner.build_layer7_v2(requirement_result=rr, contract=contract)
    final, plan, bundle, hard_gates, diff = runner.run_repair_round_v2(
        before=initial,
        contract=contract,
        policy=policy,
        round_index=1,
        allow_model_actions=False,
    )
    outcome, fold = runner.build_outcome_and_fold(final, contract)
    v1_blockers_by_direction = blocker_rows(v1_proof)
    v2_initial_by_direction = blocker_rows(initial["proof"])
    v2_final_by_direction = blocker_rows(final["proof"])
    v1_blockers = blocker_set(v1_blockers_by_direction)
    v2_initial_blockers = blocker_set(v2_initial_by_direction)
    v2_final_blockers = blocker_set(v2_final_by_direction)

    executions = bundle.get("action_executions", [])
    procedure_artifacts = [
        artifact
        for execution in executions
        for artifact in execution.get("targeted_coverage_procedure_artifacts", [])
    ]
    temporal_assessments = [
        artifact
        for execution in executions
        for artifact in execution.get("temporal_assessments", [])
    ]
    reliability_assessments = [
        artifact
        for execution in executions
        for artifact in execution.get("information_reliability_assessments", [])
    ]
    v1_semantic_fingerprint = sha256_json(
        {"proof": v1_proof, "outcome": v1_outcome, "fold": v1_fold}
    )
    v2_semantic_fingerprint = sha256_json(
        {
            "runtime": runner.runtime_hashes(),
            "input_requirement_result_sha256": sha256_file(
                paths["initial_requirement_result"]
            ),
            "contract_sha256": sha256_file(paths["contract"]),
            "proof": final["proof"],
            "outcome": outcome,
            "fold": fold,
        }
    )
    terminal_limitations = plan.get("terminal_limitations", [])
    return {
        "schema": "freca-production-v2-coordinate-replay-summary-v1",
        "case_uid": decision.get("case_uid"),
        "cp_id": decision.get("cp_id"),
        "task_dir": str(task_dir),
        "input_fingerprint": decision.get("input_fingerprint"),
        "input_requirement_result_sha256": "sha256:"
        + sha256_file(paths["initial_requirement_result"]),
        "contract_sha256": "sha256:" + sha256_file(paths["contract"]),
        "compatibility_status": "COMPATIBLE",
        "v1_internal_outcome": v1_outcome.get("common_internal_outcome"),
        "v2_internal_outcome": outcome.get("common_internal_outcome"),
        "v1_fold_label": v1_fold.get("label"),
        "v2_fold_label": fold.get("label"),
        "v1_fold_finality": v1_fold.get("finality"),
        "v2_fold_finality": fold.get("finality"),
        "v1_blockers_by_direction": v1_blockers_by_direction,
        "v2_initial_blockers_by_direction": v2_initial_by_direction,
        "v2_final_blockers_by_direction": v2_final_by_direction,
        "v1_blockers": v1_blockers,
        "v2_initial_blockers": v2_initial_blockers,
        "v2_final_blockers": v2_final_blockers,
        "executed_action_types": [row.get("action_type") for row in executions],
        "terminal_limitations": terminal_limitations,
        "targeted_procedure_statuses": [
            {
                "need_id": row.get("need_id"),
                "coverage_purpose": row.get("coverage_purpose"),
                "completion_status": row.get("completion_status"),
                "reason_codes": row.get("reason_codes", []),
                "procedure_artifact_sha256": row.get("procedure_artifact_sha256"),
            }
            for row in procedure_artifacts
        ],
        "temporal_assessment_count": len(temporal_assessments),
        "reliability_assessment_count": len(reliability_assessments),
        "hard_gates_pass": hard_gates.get("all_hard_gates_pass"),
        "round_execution_complete": bundle.get("round_execution_complete"),
        "goal_state_changed": diff.get("effect_vector", {}).get(
            "resolved_decisive_goal_count", 0
        )
        > 0,
        "v1_semantic_fingerprint": v1_semantic_fingerprint,
        "v2_semantic_fingerprint": v2_semantic_fingerprint,
        "semantic_fingerprint_changed": v1_semantic_fingerprint
        != v2_semantic_fingerprint,
    }


def matrix(rows: list[dict], before_key: str, after_key: str) -> dict:
    counts = {before: {after: 0 for after in OUTCOMES} for before in OUTCOMES}
    for row in rows:
        before = str(row.get(before_key) or "SYSTEM_BLOCK")
        after = str(row.get(after_key) or "SYSTEM_BLOCK")
        if before not in counts:
            before = "SYSTEM_BLOCK"
        if after not in counts[before]:
            after = "SYSTEM_BLOCK"
        counts[before][after] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--v1-root", type=Path, default=Path("results_v2/production_run_v1_shards")
    )
    parser.add_argument("--contracts-dir", type=Path, default=Path("contracts_v2"))
    parser.add_argument(
        "--policy", type=Path, default=Path("production_repair_policy_v2.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results_v2/production_run_v2_replay")
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--v1-tree-digest", required=True)
    parser.add_argument("--expected-v1-tree-digest", required=True)
    args = parser.parse_args()

    v1_root = args.v1_root.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir == v1_root or v1_root in output_dir.parents:
        raise ValueError("Phase 4 output may not be inside the V1 result tree")
    contracts_dir = args.contracts_dir.resolve()
    policy = load(args.policy.resolve())
    decisions = sorted(v1_root.glob("shard-*/tasks/case-*/CP*/decision.json"))
    all_initial_artifacts = sorted(
        v1_root.glob("shard-*/tasks/case-*/CP*/initial/requirement_result.json")
    )
    completed_task_dirs = {path.parent for path in decisions}
    initial_without_completed_decision = [
        path
        for path in all_initial_artifacts
        if path.parent.parent not in completed_task_dirs
    ]
    if args.limit is not None:
        decisions = decisions[: args.limit]

    attempted_calls: list[dict] = []
    original_api = core.deepseek_json

    def reject_api(*_args: Any, **kwargs: Any) -> dict:
        attempted_calls.append({"model": kwargs.get("model")})
        raise RuntimeError("PHASE4_ZERO_API_GUARD")

    core.deepseek_json = reject_api
    started = time.monotonic()
    inventory_rows = []
    replay_rows = []
    suppressed_model_actions = 0
    try:
        for index, decision_path in enumerate(decisions, 1):
            task_dir = decision_path.parent
            decision = load(decision_path)
            compatible, reasons, paths = compatibility(
                task_dir=task_dir, decision=decision, contracts_dir=contracts_dir
            )
            inventory_row = {
                "case_uid": decision.get("case_uid"),
                "cp_id": decision.get("cp_id"),
                "task_dir": str(task_dir),
                "decision_sha256": "sha256:" + sha256_file(decision_path),
                "input_fingerprint": decision.get("input_fingerprint"),
                "compatible": compatible,
                "incompatibility_reasons": reasons,
            }
            inventory_rows.append(inventory_row)
            if not compatible:
                replay_rows.append(
                    {
                        **inventory_row,
                        "compatibility_status": "REPLAY_INCOMPATIBLE",
                        "v1_internal_outcome": decision.get("common_internal_outcome"),
                        "v2_internal_outcome": "REPLAY_INCOMPATIBLE",
                    }
                )
                continue
            try:
                row = replay_one(
                    task_dir=task_dir,
                    decision=decision,
                    paths=paths,
                    policy=policy,
                )
                suppressed_model_actions += sum(
                    item.get("reason_code")
                    == "MODEL_ACTION_NOT_ADMITTED_IN_ZERO_API_MODE"
                    for item in row.get("terminal_limitations", [])
                )
                replay_rows.append(row)
                coordinate_path = (
                    output_dir
                    / "coordinates"
                    / f"{row['case_uid']}__{row['cp_id']}"
                    / "replay_summary.json"
                )
                coordinate = copy.deepcopy(row)
                coordinate["summary_sha256"] = sha256_json(coordinate)
                save(coordinate, coordinate_path)
            except Exception as exc:
                replay_rows.append(
                    {
                        **inventory_row,
                        "compatibility_status": "SYSTEM_BLOCK",
                        "v1_internal_outcome": decision.get("common_internal_outcome"),
                        "v2_internal_outcome": "SYSTEM_BLOCK",
                        "system_block": f"{type(exc).__name__}: {exc}",
                    }
                )
            if index % 25 == 0 or index == len(decisions):
                elapsed = time.monotonic() - started
                print(
                    f"replay progress {index}/{len(decisions)} "
                    f"elapsed={elapsed:.1f}s api_attempts={len(attempted_calls)}",
                    flush=True,
                )
    finally:
        core.deepseek_json = original_api

    compatible_rows = [
        row for row in replay_rows if row.get("compatibility_status") == "COMPATIBLE"
    ]
    incompatible_rows = [
        row
        for row in replay_rows
        if row.get("compatibility_status") == "REPLAY_INCOMPATIBLE"
    ]
    system_blocks = [
        row for row in replay_rows if row.get("compatibility_status") == "SYSTEM_BLOCK"
    ]
    inventory = {
        "schema": "freca-production-v2-replay-inventory-v1",
        "decision_inventory_count": len(decisions),
        "initial_requirement_result_count": len(all_initial_artifacts),
        "initial_without_completed_decision_count": len(
            initial_without_completed_decision
        ),
        "initial_without_completed_decision": [
            str(path.parent.parent) for path in initial_without_completed_decision
        ],
        "compatible_count": len(compatible_rows),
        "replay_incompatible_count": len(incompatible_rows),
        "system_block_count": len(system_blocks),
        "coordinates": inventory_rows,
    }
    inventory["inventory_sha256"] = sha256_json(inventory)

    exact_transitions = collections.Counter()
    per_blocker: dict[str, collections.Counter] = {}
    for row in compatible_rows:
        before = tuple(row.get("v1_blockers", []))
        after = tuple(row.get("v2_final_blockers", []))
        exact_transitions[(before, after)] += 1
        for blocker in set(before) | set(after):
            per_blocker.setdefault(blocker, collections.Counter())[
                (blocker in before, blocker in after)
            ] += 1
    blocker_report = {
        "schema": "freca-production-v2-blocker-transition-matrix-v1",
        "coordinate_count": len(compatible_rows),
        "exact_blocker_set_transitions": [
            {
                "v1_blockers": list(before),
                "v2_blockers": list(after),
                "count": count,
            }
            for (before, after), count in sorted(
                exact_transitions.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "per_blocker_presence_transitions": {
            blocker: {
                f"v1_{str(before).lower()}__v2_{str(after).lower()}": count
                for (before, after), count in sorted(counts.items())
            }
            for blocker, counts in sorted(per_blocker.items())
        },
    }
    blocker_report["matrix_sha256"] = sha256_json(blocker_report)

    outcome_report = {
        "schema": "freca-production-v2-outcome-transition-matrix-v1",
        "coordinate_count": len(replay_rows),
        "outcome_vocabulary": OUTCOMES,
        "matrix": matrix(replay_rows, "v1_internal_outcome", "v2_internal_outcome"),
        "v2_outcome_counts": dict(
            sorted(collections.Counter(row.get("v2_internal_outcome") for row in replay_rows).items())
        ),
        "fold_transition_counts": dict(
            sorted(
                collections.Counter(
                    f"{row.get('v1_fold_label')}->{row.get('v2_fold_label')}"
                    for row in compatible_rows
                ).items()
            )
        ),
    }
    outcome_report["matrix_sha256"] = sha256_json(outcome_report)

    all_unknown = bool(compatible_rows) and all(
        row.get("v2_internal_outcome") == "UNKNOWN" for row in compatible_rows
    )
    remaining = collections.Counter(
        code for row in compatible_rows for code in row.get("v2_final_blockers", [])
    )
    reachability = {
        "schema": "freca-production-v2-semantic-reachability-report-v1",
        "phase": "PHASE_4_454_COORDINATE_ZERO_API_REPLAY",
        "coordinate_count": len(replay_rows),
        "compatible_count": len(compatible_rows),
        "replay_incompatible_count": len(incompatible_rows),
        "system_block_count": len(system_blocks),
        "all_compatible_real_coordinates_unknown": all_unknown,
        "synthetic_reachability_report": str(
            (output_dir / "phase3" / "phase3_validation_report.json").resolve()
        ),
        "synthetic_reachability_status": (
            load(output_dir / "phase3" / "phase3_validation_report.json").get("status")
            if (output_dir / "phase3" / "phase3_validation_report.json").is_file()
            else "MISSING"
        ),
        "remaining_blocker_counts": dict(sorted(remaining.items())),
        "next_concrete_missing_procedures": [
            "COMPLETE_CANDIDATE_DISPOSITION_FOR_TARGETED_COUNTERCHECK"
            if remaining.get("COVERAGE_INCOMPLETE")
            else None,
            "ADD_TYPED_TEMPORAL_APPLICABILITY_BASIS_IN_SEPARATELY_APPROVED_CONTRACT_CHANGE"
            if remaining.get("TEMPORAL_REQUIREMENT_UNRESOLVED")
            else None,
            "OBTAIN_OR_VALIDATE_TRUTH_BEARING_RELIABILITY_BASIS"
            if remaining.get("INFORMATION_RELIABILITY_UNRESOLVED")
            else None,
        ],
        "coordinate_summaries": replay_rows,
        "input_hash_compatibility_checked_per_coordinate": True,
        "semantic_fingerprints_changed_count": sum(
            row.get("semantic_fingerprint_changed") is True for row in compatible_rows
        ),
        "all_compatible_semantic_fingerprints_changed": all(
            row.get("semantic_fingerprint_changed") is True
            for row in compatible_rows
        ),
        "all_coordinate_hard_gates_pass": all(
            row.get("hard_gates_pass") is True for row in compatible_rows
        ),
        "all_round_executions_complete": all(
            row.get("round_execution_complete") is True for row in compatible_rows
        ),
        "v1_preservation": {
            "expected_tree_digest": args.expected_v1_tree_digest,
            "observed_tree_digest": args.v1_tree_digest,
            "unchanged": args.v1_tree_digest == args.expected_v1_tree_digest,
            "tree_digest_method": (
                "find production_run_v1_shards -type f -print0 | sort -z | "
                "xargs -0 sha256sum | sha256sum"
            ),
        },
        "answer_comparator_used": False,
        "historical_labels_used": False,
    }
    reachability["next_concrete_missing_procedures"] = [
        row for row in reachability["next_concrete_missing_procedures"] if row
    ]
    reachability["report_sha256"] = sha256_json(reachability)

    api_audit = {
        "schema": "freca-production-v2-phase4-zero-api-audit-v1",
        "guarded_entrypoint": "freca_core_v1.deepseek_json",
        "guard_installed": True,
        "attempted_calls": attempted_calls,
        "attempted_call_count": len(attempted_calls),
        "api_call_count": len(attempted_calls),
        "zero_api_pass": not attempted_calls,
        "coordinate_count": len(replay_rows),
    }
    api_audit["report_sha256"] = sha256_json(api_audit)
    cost = {
        "schema": "freca-production-v2-phase4-cost-avoided-v1",
        "coordinate_count": len(replay_rows),
        "actual_api_calls": len(attempted_calls),
        "actual_model_cost": 0,
        "currency": "USD",
        "model_actions_suppressed_by_zero_api_gate": suppressed_model_actions,
        "avoided_token_count": None,
        "avoided_cost": None,
        "interpretation": (
            "Suppressed model actions are counted, but tokens and monetary cost are "
            "not inferred because no Phase 4 request was sent and no frozen price/input "
            "size exists for those hypothetical calls."
        ),
    }
    cost["report_sha256"] = sha256_json(cost)

    save(inventory, output_dir / "replay_inventory.json")
    save(reachability, output_dir / "semantic_reachability_report.json")
    save(blocker_report, output_dir / "blocker_transition_matrix.json")
    save(outcome_report, output_dir / "outcome_transition_matrix.json")
    save(api_audit, output_dir / "api_call_audit.json")
    save(cost, output_dir / "cost_avoided_report.json")

    status = (
        "PASS"
        if not incompatible_rows
        and not system_blocks
        and not attempted_calls
        and args.v1_tree_digest == args.expected_v1_tree_digest
        and all(row.get("semantic_fingerprint_changed") is True for row in compatible_rows)
        else "FAIL"
    )
    print(
        f"Phase 4 replay {status}: total={len(replay_rows)} "
        f"compatible={len(compatible_rows)} incompatible={len(incompatible_rows)} "
        f"system_blocks={len(system_blocks)} api_calls={len(attempted_calls)}"
    )
    print("outcomes", outcome_report["v2_outcome_counts"])
    if status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
