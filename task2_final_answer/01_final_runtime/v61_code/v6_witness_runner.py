#!/usr/bin/env python3
"""Fail-closed single-coordinate V6 witness runner.

`preflight` is always zero API.  `run` requires explicit repair mode and API
authorization, refuses a witness with non-executable terminal blockers, caps
logical/HTTP calls, and never writes into the frozen V1 tree.
"""

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
import production_repair_dispatcher_v2 as dispatcher
import production_runner_v2 as runner
from semantic_replay_v6_1 import replay_requirement_result
from witness_funnel_v6 import analyze_root


HERE = Path(__file__).resolve().parent
V6_ROOT = HERE.parent
DEFAULT_WITNESS = V6_ROOT / "config" / "witness_case_011_cp19.json"
DEFAULT_POLICY = V6_ROOT / "config" / "v6_repair_policy.json"
DEFAULT_DECISIONS = V6_ROOT / "config" / "v6_decisions.json"
DEFAULT_OUTPUT = V6_ROOT / "results" / "case-011__CP19"
SUBSTANTIVE_OUTCOMES = {
    "PROVEN_COMPLIANT",
    "PROVEN_NON_COMPLIANT",
    "PROVEN_NOT_APPLICABLE",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def save(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_output_isolated(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (V6_ROOT / "results").resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"Output must remain under {allowed}; got {resolved}")
    return resolved


def count_decisive_goals(root: dict) -> set[str]:
    return {
        str(row.get("goal_id"))
        for row in (root.get("open_goals") or {}).get("goals", []) or []
        if str(row.get("estimated_verdict_impact")) == "DECISIVE"
        and row.get("goal_id")
    }


def execution_metrics(bundle: dict) -> dict[str, int]:
    totals: collections.Counter[str] = collections.Counter()
    for execution in bundle.get("action_executions", []) or []:
        alignments = execution.get("new_alignments", []) or []
        truth = [
            row for row in alignments
            if row.get("relation") in {"SUPPORT", "ATTACK"}
            and row.get("argument_admission_channel") == "DIRECT"
            and row.get("argument_truth_bearing") is True
        ]
        telemetry = execution.get("cost_telemetry") or {}
        totals["new_alignment_count"] += len(alignments)
        totals["new_truth_bearing_count"] += len(truth)
        totals["goal_aligned_truth_bearing_count"] += int(
            execution.get("goal_aligned_truth_bearing_count", 0) or 0
        )
        for key in (
            "request_attempt_count", "successful_call_count", "failed_call_count",
            "prompt_tokens", "completion_tokens", "total_tokens", "wall_time_ms",
        ):
            totals[key] += int(telemetry.get(key, 0) or 0)
    return dict(totals)


def build_preflight(
    witness_path: Path,
    policy_path: Path,
    decisions_path: Path,
    repair_enabled: bool,
) -> tuple[dict, dict, dict, dict, dict]:
    witness = load(witness_path)
    policy = load(policy_path)
    decisions = load(decisions_path)
    if decisions.get("provider") != "deepseek":
        raise ValueError("V6 frozen provider must be deepseek")
    if (decisions.get("n_a_policy") or {}).get("enabled") is not False:
        raise ValueError("V6 witness trial requires the N/A switch frozen off")

    task_dir = Path(witness["task_dir"]).resolve()
    rr_path = task_dir / "initial" / "requirement_result.json"
    contract_path = Path(witness["contract_path"]).resolve()
    chunks_path = Path(witness["case_chunks_path"]).resolve()
    for path in (rr_path, contract_path, chunks_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    rr_original = load(rr_path)
    contract = load(contract_path)
    # V6.1 preflight revalidates persisted model relations through the current
    # deterministic semantic gate before proof/repair. This prevents an old
    # requirement_result from retaining legacy CORROBORATIVE->DIRECT admissions
    # after the semantic code has been fixed. No model/API call is made here.
    rr, semantic_replay = replay_requirement_result(rr_original)
    root = runner.build_layer7_v2(requirement_result=rr, contract=contract)
    outcome, fold = runner.build_outcome_and_fold(root, contract)
    plan = dispatcher.build_repair_plan(
        root=root,
        policy=policy,
        round_index=1,
        allow_model_actions=repair_enabled,
    )
    funnel = analyze_root(root, chunks_path=chunks_path)
    blockers = list(funnel["blockers"]["non_executable_terminal_codes"])
    if "TEMPORAL_REQUIREMENT_UNRESOLVED" in blockers:
        if not (
            witness.get("target_period_start")
            and witness.get("target_period_end")
            and witness.get("target_period_provenance")
        ):
            blockers.append("TARGET_PERIOD_NOT_FROZEN_WITH_PROVENANCE")

    api_admissible = bool(repair_enabled and plan.get("actions") and not blockers)
    report = {
        "schema": "freca-v6-witness-preflight-v1",
        "version_id": decisions.get("version_id"),
        "case_uid": witness.get("case_uid"),
        "cp_id": witness.get("cp_id"),
        "provider": decisions.get("provider"),
        "alignment_model": decisions.get("alignment_model"),
        "repair_enabled": repair_enabled,
        "na_enabled": False,
        "incumbent_relationship": decisions.get("incumbent_relationship"),
        "input_paths": {
            "witness": str(witness_path.resolve()),
            "requirement_result": str(rr_path),
            "contract": str(contract_path),
            "case_chunks": str(chunks_path),
        },
        "input_sha256": {
            "witness": sha256_file(witness_path),
            "requirement_result": sha256_file(rr_path),
            "contract": sha256_file(contract_path),
            "case_chunks": sha256_file(chunks_path),
            "policy": sha256_file(policy_path),
            "decisions": sha256_file(decisions_path),
        },
        "initial_internal_outcome": outcome.get("common_internal_outcome"),
        "initial_fold_label": fold.get("label"),
        "initial_fold_finality": fold.get("finality"),
        "decisive_open_goal_count": len(count_decisive_goals(root)),
        "planned_actions": [
            {
                "action_type": row.get("action_type"),
                "goal_type": row.get("goal_type"),
                "need_id": row.get("need_id"),
                "target_count": len(row.get("target_artifact_ids", []) or []),
            }
            for row in plan.get("actions", []) or []
        ],
        "blocking_codes": sorted(set(blockers)),
        "api_admissible": api_admissible,
        "api_calls_made": 0,
        "semantic_replay": semantic_replay,
        "funnel": funnel,
        "decision": "GO_FOR_SINGLE_WITNESS_API" if api_admissible else "NO_GO",
    }
    report["report_sha256"] = sha256_json(report)
    return report, witness, policy, contract, root


def run_api(
    *, preflight: dict, witness: dict, policy: dict, contract: dict,
    before: dict, decisions: dict, output_dir: Path,
) -> dict:
    if not preflight.get("api_admissible"):
        raise RuntimeError(
            "API_BLOCKED_BY_PREFLIGHT: " + ",".join(preflight.get("blocking_codes", []))
        )
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is not set; no API call attempted")

    limits = decisions["api_limits"]
    max_logical = int(limits["max_logical_model_calls"])
    max_http = int(limits["max_http_attempts"])
    logical_calls: list[dict] = []
    http_attempts: list[dict] = []
    violations: list[str] = []
    original_api = core.deepseek_json
    original_request = requests.sessions.Session.request
    api_host = urlparse(core.DEEPSEEK_BASE_URL).netloc

    def capped_api(*args: Any, **kwargs: Any) -> dict:
        if len(logical_calls) >= max_logical:
            violations.append("MAX_LOGICAL_MODEL_CALLS")
            raise RuntimeError("V6_LIMIT: max logical model calls")
        logical_calls.append({
            "index": len(logical_calls) + 1,
            "model": kwargs.get("model") or decisions.get("alignment_model"),
            "thinking": kwargs.get("thinking"),
            "max_tokens": kwargs.get("max_tokens"),
        })
        return original_api(*args, **kwargs)

    def capped_request(self: Any, method: str, url: str, *args: Any, **kwargs: Any):
        if urlparse(str(url)).netloc == api_host:
            if len(http_attempts) >= max_http:
                violations.append("MAX_HTTP_ATTEMPTS")
                raise RuntimeError("V6_LIMIT: max HTTP attempts")
            http_attempts.append({
                "index": len(http_attempts) + 1,
                "method": str(method).upper(),
                "host": api_host,
            })
        return original_request(self, method, url, *args, **kwargs)

    rr_path = Path(preflight["input_paths"]["requirement_result"])
    contract_path = Path(preflight["input_paths"]["contract"])
    input_hashes_before = {
        "requirement_result": sha256_file(rr_path),
        "contract": sha256_file(contract_path),
    }
    started = time.monotonic()
    try:
        core.deepseek_json = capped_api
        requests.sessions.Session.request = capped_request
        after, plan, bundle, hard_gates, diff = runner.run_repair_round_v2(
            before=before,
            contract=contract,
            policy=policy,
            round_index=1,
            allow_model_actions=True,
        )
    finally:
        core.deepseek_json = original_api
        requests.sessions.Session.request = original_request

    outcome_a, fold_a = runner.build_outcome_and_fold(before, contract)
    outcome_b, fold_b = runner.build_outcome_and_fold(after, contract)
    before_goals = count_decisive_goals(before)
    after_goals = count_decisive_goals(after)
    resolved = sorted(before_goals - after_goals)
    chunks_path = Path(preflight["input_paths"]["case_chunks"])
    funnel_a = analyze_root(before, chunks_path=chunks_path)
    funnel_b = analyze_root(after, chunks_path=chunks_path)
    metrics = execution_metrics(bundle)
    prompt_tokens = int(metrics.get("prompt_tokens", 0))
    completion_tokens = int(metrics.get("completion_tokens", 0))
    if prompt_tokens > int(limits["max_prompt_tokens"]):
        violations.append("MAX_PROMPT_TOKENS")
    if completion_tokens > int(limits["max_completion_tokens"]):
        violations.append("MAX_COMPLETION_TOKENS")

    input_hashes_after = {
        "requirement_result": sha256_file(rr_path),
        "contract": sha256_file(contract_path),
    }
    outcome_changed = (
        outcome_a.get("common_internal_outcome") != outcome_b.get("common_internal_outcome")
    )
    substantive = outcome_b.get("common_internal_outcome") in SUBSTANTIVE_OUTCOMES
    source_grounded = (
        funnel_b["accepted_decisive_basis_count"] > 0
        and funnel_b["decisive_requirements_with_accepted_direction_count"] > 0
    )
    success = all((
        not violations,
        hard_gates.get("all_hard_gates_pass") is True,
        bundle.get("round_execution_complete") is True,
        input_hashes_before == input_hashes_after,
        bool(resolved),
        outcome_changed,
        substantive,
        source_grounded,
        int(metrics.get("goal_aligned_truth_bearing_count", 0)) > 0,
    ))
    report = {
        "schema": "freca-v6-single-witness-result-v1",
        "preflight_sha256": preflight["report_sha256"],
        "repair_enabled": True,
        "na_enabled": False,
        "provider": decisions["provider"],
        "alignment_model": decisions["alignment_model"],
        "before_internal_outcome": outcome_a.get("common_internal_outcome"),
        "after_internal_outcome": outcome_b.get("common_internal_outcome"),
        "before_fold": fold_a,
        "after_fold": fold_b,
        "resolved_decisive_goal_ids": resolved,
        "resolved_decisive_goal_count": len(resolved),
        "funnel_before": funnel_a,
        "funnel_after": funnel_b,
        "execution_metrics": metrics,
        "logical_calls": logical_calls,
        "http_attempts": http_attempts,
        "limit_violations": sorted(set(violations)),
        "hard_gates_pass": hard_gates.get("all_hard_gates_pass"),
        "round_execution_complete": bundle.get("round_execution_complete"),
        "frozen_inputs_unchanged": input_hashes_before == input_hashes_after,
        "substantive_internal_outcome_change": bool(outcome_changed and substantive),
        "source_grounded_decisive_witness": source_grounded,
        "primary_success_gate_pass": success,
        "recommendation": "GO_FOR_SEPARATELY_REVIEWED_NEXT_SAMPLE" if success else "NO_GO",
        "wall_time_ms": int((time.monotonic() - started) * 1000),
        "answer_comparator_used": False,
        "human_or_historical_labels_used": False,
    }
    report["report_sha256"] = sha256_json(report)
    runner.save_layer(before, output_dir / "arm_a")
    runner.save_layer(after, output_dir / "arm_b" / "after")
    save(plan, output_dir / "arm_b" / "repair_plan.json")
    save(bundle, output_dir / "arm_b" / "round_bundle.json")
    save(hard_gates, output_dir / "arm_b" / "hard_gates.json")
    save(diff, output_dir / "arm_b" / "evaluation_diff.json")
    save(report, output_dir / "witness_result.json")
    return report


def run_self_tests() -> None:
    assert ensure_output_isolated(DEFAULT_OUTPUT) == DEFAULT_OUTPUT.resolve()
    try:
        ensure_output_isolated(Path("/tmp/not-v6"))
    except ValueError:
        pass
    else:
        raise AssertionError("output isolation failed open")
    assert SUBSTANTIVE_OUTCOMES == {
        "PROVEN_COMPLIANT", "PROVEN_NON_COMPLIANT", "PROVEN_NOT_APPLICABLE"
    }
    print("v6_witness_runner self-tests: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "run", "self-test"))
    parser.add_argument("--witness", type=Path, default=DEFAULT_WITNESS)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repair-enabled", choices=("on", "off"), required=False)
    parser.add_argument("--allow-api", choices=("YES",), required=False)
    args = parser.parse_args()
    if args.mode == "self-test":
        run_self_tests()
        return 0
    if args.repair_enabled is None:
        parser.error("--repair-enabled on|off is required for preflight and run")
    repair_enabled = args.repair_enabled == "on"
    os.environ["FRECA_ENABLE_NA_COUNTERCHECK"] = "0"
    preflight, witness, policy, contract, root = build_preflight(
        args.witness, args.policy, args.decisions, repair_enabled
    )
    output_dir = ensure_output_isolated(args.output_dir)
    save(preflight, output_dir / "preflight.json")
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    if args.mode == "preflight":
        return 0
    if not repair_enabled:
        raise SystemExit("RUN_BLOCKED: --repair-enabled must be on for a witness trial")
    if args.allow_api != "YES":
        raise SystemExit("RUN_BLOCKED: add --allow-api YES after reviewing preflight.json")
    if not preflight.get("api_admissible"):
        print(
            "RUN_BLOCKED_BY_PREFLIGHT: "
            + ",".join(preflight.get("blocking_codes", [])),
        )
        return 3
    decisions = load(args.decisions)
    report = run_api(
        preflight=preflight,
        witness=witness,
        policy=policy,
        contract=contract,
        before=root,
        decisions=decisions,
        output_dir=output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["primary_success_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
