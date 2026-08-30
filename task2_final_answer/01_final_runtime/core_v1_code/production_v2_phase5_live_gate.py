#!/usr/bin/env python3
"""Pre-registered DeepSeek Arm A/B quality-cost gate for Production V2."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

import freca_core_v1 as core
import production_runner_v2 as runner


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


def count_goals(root: dict) -> tuple[int, set[str]]:
    rows = root["open_goals"].get("goals", []) or []
    decisive = {
        str(row.get("goal_id"))
        for row in rows
        if str(row.get("estimated_verdict_impact")) == "DECISIVE"
    }
    return len(rows), decisive


def execution_metrics(bundle: dict) -> dict:
    executions = bundle.get("action_executions", []) or []
    alignments = [
        row for execution in executions for row in execution.get("new_alignments", []) or []
    ]
    truth = [
        row
        for row in alignments
        if row.get("relation") in {"SUPPORT", "ATTACK"}
        and row.get("argument_admission_channel") == "DIRECT"
        and row.get("argument_truth_bearing") is True
    ]
    costs = [
        execution.get("cost_telemetry") or {}
        for execution in executions
        if execution.get("action_type") == "ALIGN_NEXT_CANDIDATE_BATCH"
    ]
    return {
        "executed_action_types": [row.get("action_type") for row in executions],
        "new_alignment_count": len(alignments),
        "new_truth_bearing_evidence_count": len(truth),
        "goal_aligned_truth_bearing_count": sum(
            int(execution.get("goal_aligned_truth_bearing_count", 0) or 0)
            for execution in executions
        ),
        "off_goal_truth_bearing_count": sum(
            int(execution.get("off_goal_truth_bearing_count", 0) or 0)
            for execution in executions
        ),
        "request_attempt_count": sum(int(row.get("request_attempt_count", 0) or 0) for row in costs),
        "successful_call_count": sum(int(row.get("successful_call_count", 0) or 0) for row in costs),
        "failed_call_count": sum(int(row.get("failed_call_count", 0) or 0) for row in costs),
        "prompt_tokens": sum(int(row.get("prompt_tokens", 0) or 0) for row in costs),
        "completion_tokens": sum(int(row.get("completion_tokens", 0) or 0) for row in costs),
        "total_tokens": sum(int(row.get("total_tokens", 0) or 0) for row in costs),
        "prompt_cache_hit_tokens": sum(int(row.get("prompt_cache_hit_tokens", 0) or 0) for row in costs),
        "prompt_cache_miss_tokens": sum(int(row.get("prompt_cache_miss_tokens", 0) or 0) for row in costs),
        "api_wall_time_ms": sum(int(row.get("wall_time_ms", 0) or 0) for row in costs),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preregistration", type=Path, default=Path("phase5_live_gate_preregistration.json")
    )
    parser.add_argument(
        "--policy", type=Path, default=Path("production_repair_policy_v2_live_gate.json")
    )
    parser.add_argument("--contracts-dir", type=Path, default=Path("contracts_v2"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results_v2/production_run_v2_live_gate")
    )
    args = parser.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit(
            "PRECHECK_BLOCK: DEEPSEEK_API_KEY is not set in this process; no API call attempted."
        )

    prereg_path = args.preregistration.resolve()
    policy_path = args.policy.resolve()
    prereg = load(prereg_path)
    policy = load(policy_path)
    limits = policy["live_gate_limits"]
    sample = prereg.get("sample", [])
    if len(sample) != int(limits["sample_coordinate_count"]):
        raise ValueError("Preregistered sample count differs from frozen policy")

    output_dir = args.output_dir.resolve()
    v1_root = Path("results_v2/production_run_v1_shards").resolve()
    if output_dir == v1_root or v1_root in output_dir.parents:
        raise ValueError("Live gate may not write inside the V1 result tree")

    max_logical = int(limits["max_logical_model_calls"])
    max_http = int(limits["max_http_attempts"])
    max_prompt = int(limits["max_prompt_tokens"])
    max_completion = int(limits["max_completion_tokens"])
    logical_calls: list[dict] = []
    http_attempts: list[dict] = []
    limit_violations: list[str] = []

    original_api = core.deepseek_json
    original_request = requests.sessions.Session.request
    deepseek_host = urlparse(core.DEEPSEEK_BASE_URL).netloc

    def capped_api(*args: Any, **kwargs: Any) -> dict:
        if len(logical_calls) >= max_logical:
            limit_violations.append("MAX_LOGICAL_MODEL_CALLS")
            raise RuntimeError("LIVE_GATE_LIMIT: max logical model calls")
        logical_calls.append(
            {
                "logical_call_index": len(logical_calls) + 1,
                "model": kwargs.get("model"),
                "thinking": kwargs.get("thinking"),
                "max_tokens": kwargs.get("max_tokens"),
            }
        )
        return original_api(*args, **kwargs)

    def capped_request(self: Any, method: str, url: str, *args: Any, **kwargs: Any):
        if urlparse(str(url)).netloc == deepseek_host:
            if len(http_attempts) >= max_http:
                limit_violations.append("MAX_HTTP_ATTEMPTS")
                raise RuntimeError("LIVE_GATE_LIMIT: max HTTP attempts")
            http_attempts.append(
                {
                    "http_attempt_index": len(http_attempts) + 1,
                    "method": str(method).upper(),
                    "host": deepseek_host,
                    "model": (kwargs.get("json") or {}).get("model")
                    if isinstance(kwargs.get("json"), dict)
                    else None,
                }
            )
        return original_request(self, method, url, *args, **kwargs)

    rows = []
    started = time.monotonic()
    contracts_dir = args.contracts_dir.resolve()
    try:
        for index, item in enumerate(sample, 1):
            task_dir = Path(item["task_dir"]).resolve()
            rr_path = task_dir / "initial" / "requirement_result.json"
            contract_path = contracts_dir / f"{item['cp_id']}.json"
            rr = load(rr_path)
            contract = load(contract_path)

            arm_a_root = runner.build_layer7_v2(requirement_result=rr, contract=contract)
            arm_a_outcome, arm_a_fold = runner.build_outcome_and_fold(arm_a_root, contract)
            arm_a_goal_count, arm_a_decisive_ids = count_goals(arm_a_root)
            coordinate_dir = output_dir / f"{item['case_uid']}__{item['cp_id']}"
            runner.save_layer(arm_a_root, coordinate_dir / "arm_a")
            save(arm_a_outcome, coordinate_dir / "arm_a" / "outcome.json")
            save(arm_a_fold, coordinate_dir / "arm_a" / "fold.json")

            core.deepseek_json = capped_api
            requests.sessions.Session.request = capped_request
            coordinate_started = time.monotonic()
            after, plan, bundle, hard_gates, diff = runner.run_repair_round_v2(
                before=arm_a_root,
                contract=contract,
                policy=policy,
                round_index=1,
                allow_model_actions=True,
            )
            arm_b_outcome, arm_b_fold = runner.build_outcome_and_fold(after, contract)
            elapsed_ms = int((time.monotonic() - coordinate_started) * 1000)
            arm_b_goal_count, arm_b_decisive_ids = count_goals(after)
            metrics = execution_metrics(bundle)
            resolved_decisive_ids = sorted(arm_a_decisive_ids - arm_b_decisive_ids)
            substantive_change = (
                arm_a_outcome.get("common_internal_outcome")
                != arm_b_outcome.get("common_internal_outcome")
            )
            row = {
                **item,
                "input_requirement_result_sha256": "sha256:" + sha256_file(rr_path),
                "contract_sha256": "sha256:" + sha256_file(contract_path),
                "arm_a_internal_outcome": arm_a_outcome.get("common_internal_outcome"),
                "arm_b_internal_outcome": arm_b_outcome.get("common_internal_outcome"),
                "arm_a_fold_label": arm_a_fold.get("label"),
                "arm_b_fold_label": arm_b_fold.get("label"),
                "arm_a_open_goal_count": arm_a_goal_count,
                "arm_b_open_goal_count": arm_b_goal_count,
                "resolved_decisive_goal_ids": resolved_decisive_ids,
                "resolved_decisive_goal_count": len(resolved_decisive_ids),
                "substantive_internal_outcome_change": substantive_change,
                "hard_gates_pass": hard_gates.get("all_hard_gates_pass"),
                "round_execution_complete": bundle.get("round_execution_complete"),
                "coordinate_wall_time_ms": elapsed_ms,
                **metrics,
            }
            rows.append(row)
            runner.save_layer(after, coordinate_dir / "arm_b" / "after")
            save(plan, coordinate_dir / "arm_b" / "repair_plan.json")
            save(bundle, coordinate_dir / "arm_b" / "round_bundle.json")
            save(hard_gates, coordinate_dir / "arm_b" / "hard_gates.json")
            save(diff, coordinate_dir / "arm_b" / "evaluation_diff.json")
            save(arm_b_outcome, coordinate_dir / "arm_b" / "outcome.json")
            save(arm_b_fold, coordinate_dir / "arm_b" / "fold.json")

            total_prompt = sum(int(row.get("prompt_tokens", 0) or 0) for row in rows)
            total_completion = sum(int(row.get("completion_tokens", 0) or 0) for row in rows)
            if total_prompt > max_prompt:
                limit_violations.append("MAX_PROMPT_TOKENS")
            if total_completion > max_completion:
                limit_violations.append("MAX_COMPLETION_TOKENS")
            print(
                f"live gate {index}/{len(sample)} {item['case_uid']} {item['cp_id']} "
                f"logical={len(logical_calls)} http={len(http_attempts)} "
                f"outcome={row['arm_a_internal_outcome']}->{row['arm_b_internal_outcome']}",
                flush=True,
            )
            if limit_violations:
                break
    finally:
        core.deepseek_json = original_api
        requests.sessions.Session.request = original_request

    totals = collections.Counter()
    for row in rows:
        for key in (
            "resolved_decisive_goal_count",
            "new_alignment_count",
            "new_truth_bearing_evidence_count",
            "goal_aligned_truth_bearing_count",
            "off_goal_truth_bearing_count",
            "request_attempt_count",
            "successful_call_count",
            "failed_call_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "api_wall_time_ms",
        ):
            totals[key] += int(row.get(key, 0) or 0)
    substantive = sum(row["substantive_internal_outcome_change"] for row in rows)
    primary_pass = bool(
        totals["resolved_decisive_goal_count"] > 0 or substantive > 0
    )
    report = {
        "schema": "freca-production-v2-phase5-live-gate-report-v1",
        "gate_id": prereg.get("gate_id"),
        "preregistration_sha256": "sha256:" + sha256_file(prereg_path),
        "policy_sha256": "sha256:" + sha256_file(policy_path),
        "sample_planned_count": len(sample),
        "sample_completed_count": len(rows),
        "coordinates": rows,
        "totals": dict(totals),
        "substantive_internal_outcome_change_count": substantive,
        "logical_model_call_count": len(logical_calls),
        "http_attempt_count": len(http_attempts),
        "logical_calls": logical_calls,
        "http_attempts": http_attempts,
        "limit_violations": sorted(set(limit_violations)),
        "wall_time_ms": int((time.monotonic() - started) * 1000),
        "monetary_cost_usd": None,
        "monetary_cost_interpretation": (
            "No price is inferred for the configured model identifier. Token and call "
            "telemetry are authoritative; a price table must be separately frozen."
        ),
        "cost_per_resolved_decisive_goal_usd": None,
        "primary_success_gate_pass": primary_pass,
        "all_hard_gates_pass": all(row["hard_gates_pass"] is True for row in rows),
        "all_rounds_complete": all(row["round_execution_complete"] is True for row in rows),
        "answer_comparator_used": False,
        "human_or_historical_labels_used": False,
        "larger_paid_run_recommendation": "GO" if primary_pass and not limit_violations else "NO-GO",
    }
    report["report_sha256"] = sha256_json(report)
    save(report, output_dir / "phase5_live_gate_report.json")
    print("Phase 5 recommendation:", report["larger_paid_run_recommendation"])


if __name__ == "__main__":
    main()
