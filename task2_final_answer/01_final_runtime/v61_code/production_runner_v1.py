#!/usr/bin/env python3
"""FRECA manifest-aware resume-safe production runner v1.

This is the P5 runner.

Core properties
---------------
1. The task plane is exactly:
       logical case serial 1..100 x CP1..CP41 = 4100 coordinates.
2. Evidence is consumed from logical_case_manifest_v1.json, never by blindly
   reading a physical RE directory.
3. Shared physical container cases (case-035/case-100) are isolated by exact
   manifest file assignments.
4. A case is parsed once and cached; all 41 CP tasks reuse the parsed chunks.
5. Evidence reasoning receives the physical RE/output identifier only for the
   existing body-first identity countercheck.  The unique orchestration key is
   case_uid (case-001..case-100).
6. Current frozen EvidenceRequirement plans are reused; no plan recompilation.
7. Initial Layer-7 is followed by Coverage -> ProofStandard v1.1 ->
   post-proof Argument -> OpenGoal.
8. Bounded production repair executes ONLY the frozen admitted primitive:
       TOP_K_NEXT_BATCH_EXPANSION / ALIGN_NEXT_CANDIDATE_BATCH
   max 2 rounds, max 3 selected goals/round, parent budget 24.
   CHANNEL_COMPLETION_ONLY and tree search are not executed.
9. Every repair round passes through repair hard gates and the frozen
   production stop gate. NO_GOAL_STATE_CHANGE is a hard stop.
10. Core outcome adapter emits six-state semantics; FOLD-POLICY-v3 is the
    unique 1/0/N/A boundary.
11. Resume is artifact/fingerprint based. Input/code mismatch never silently
    reuses an old task.

No answer comparator or human/historical labels are consumed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

import freca_core_v1 as core
import evidence_reasoning_v2 as er
import coverage_v1
import proof_standard_v1_1 as proofmod
import procedure_objective_v1
import open_goal_v1
import core_outcome_adapter_v1 as adapter
from na_countercheck_v1 import derive_na_countercheck
import fold_policy_v3_core as foldmod
import action_gate_v1_1 as action_gate
import production_stop_gate_v1 as stop_gate

try:
    import repair_feedback_v1_2 as feedback
    FEEDBACK_MODULE = "repair_feedback_v1_2"
except ImportError:
    import repair_feedback_v1_1 as feedback
    FEEDBACK_MODULE = "repair_feedback_v1_1"

try:
    import strategy_a_next_batch_v1 as strategy_a
except ImportError as exc:
    raise SystemExit(
        "Missing strategy_a_next_batch_v1.py. "
        "The frozen production repair policy admits its "
        "TOP_K_NEXT_BATCH_EXPANSION primitive."
    ) from exc


CP_IDS = [f"CP{i}" for i in range(1, 42)]

DEFAULT_MANIFEST = Path("results_v2/logical_case_manifest_v1.json")
DEFAULT_CONTRACT_DIR = Path("contracts_v2")
DEFAULT_RUN_DIR = Path("results_v2/production_run_v1")

POLICY_CANDIDATES = [
    Path("production_repair_policy_v1.json"),
    Path("production_adopted/policies/production_repair_policy_v1.json"),
    Path("policies/production_repair_policy_v1.json"),
]

RUNTIME_FILES = [
    "freca_core_v1.py",
    "evidence_reasoning_v2.py",
    "coverage_v1.py",
    "proof_standard_v1_1.py",
    "procedure_objective_v1.py",
    "open_goal_v1.py",
    "core_outcome_adapter_v1.py",
    "fold_policy_v3_core.py",
    "action_gate_v1_1.py",
    "production_stop_gate_v1.py",
    "strategy_a_next_batch_v1.py",
    "repair_feedback_v1_2.py",
    "repair_feedback_v1_1.py",
    "multi_atom_support_v1.py",
    "multi_atom_argument_support_v1.py",
    "argument_core_v1.py",
    "na_countercheck_v1.py",
]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return "sha256:" + sha256_bytes(
        canonical_json(value).encode("utf-8")
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    return value


def save_json_atomic(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def file_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.exists() else None


def runtime_hashes() -> dict[str, str]:
    out = {}
    for name in RUNTIME_FILES:
        path = Path(name)
        if path.exists():
            out[name] = sha256_file(path)
    return dict(sorted(out.items()))


def resolve_policy(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit

    existing = [p for p in POLICY_CANDIDATES if p.exists()]
    if len(existing) != 1:
        raise RuntimeError(
            "Expected exactly one production_repair_policy_v1.json "
            f"candidate; found {[str(x) for x in existing]}. "
            "Pass --repair-policy explicitly."
        )
    return existing[0]


def validate_policy(policy: dict) -> None:
    if policy.get("schema") != "freca-core-production-repair-policy-v1":
        raise ValueError("Unexpected production repair policy schema")
    if policy.get("frozen_before_full_4100_run") is not True:
        raise ValueError("Repair policy is not frozen before full 4100")
    if policy.get("answer_comparator_used") is not False:
        raise ValueError("Repair policy comparator isolation failed")
    if policy.get("human_or_historical_labels_used") is not False:
        raise ValueError("Repair policy historical-label isolation failed")

    primitive = policy.get("selected_repair_primitive") or {}
    if primitive.get("name") != "TOP_K_NEXT_BATCH_EXPANSION":
        raise ValueError("Unexpected admitted production repair primitive")
    if primitive.get("action_type") != "ALIGN_NEXT_CANDIDATE_BATCH":
        raise ValueError("Unexpected admitted production repair action")
    if int(primitive.get("parent_alignment_budget", -1)) != 24:
        raise ValueError("Unexpected parent alignment budget")

    disabled = policy.get("disabled_experimental_arm") or {}
    if (
        disabled.get("name") != "CHANNEL_COMPLETION_ONLY"
        or disabled.get("production_enabled") is not False
    ):
        raise ValueError("CHANNEL_COMPLETION_ONLY is not explicitly disabled")

    machine = policy.get("repair_state_machine") or {}
    if int(machine.get("max_rounds", -1)) != 2:
        raise ValueError("Unexpected production repair max_rounds")
    if machine.get("substantive_change_does_not_override_no_goal_state_change") is not True:
        raise ValueError("NO_GOAL_STATE_CHANGE hard-stop invariant missing")


def normalize_case_selector(value: str) -> str:
    raw = str(value).strip()
    if raw.startswith("case-"):
        n = int(raw.split("-", 1)[1])
    else:
        n = int(raw)
    if not 1 <= n <= 100:
        raise ValueError(f"case serial outside 1..100: {value}")
    return f"case-{n:03d}"


def normalize_cp_selector(value: str) -> str:
    raw = str(value).strip().upper()
    if not raw.startswith("CP"):
        raw = "CP" + raw
    n = int(raw[2:])
    if not 1 <= n <= 41:
        raise ValueError(f"CP outside 1..41: {value}")
    return f"CP{n}"


def manifest_case_map(manifest: dict) -> dict[str, dict]:
    return {
        str(row["case_uid"]): row
        for row in manifest.get("cases", [])
    }


def validate_manifest(manifest: dict) -> None:
    if manifest.get("schema") != "freca-logical-case-manifest-v1":
        raise ValueError("Unexpected logical-case manifest schema")
    if manifest.get("all_pass") is not True:
        raise ValueError("Logical-case manifest gate is not PASS")
    if int(manifest.get("fatal_anomaly_count", -1)) != 0:
        raise ValueError("Logical-case manifest has fatal anomalies")
    if manifest.get("all_sources_assigned_exactly_once") is not True:
        raise ValueError("Logical-case source conservation failed")
    if int(manifest.get("expected_decision_count", -1)) != 4100:
        raise ValueError("Logical-case manifest does not define 4100 decisions")

    cases = manifest.get("cases") or []
    if len(cases) != 100:
        raise ValueError(f"Expected 100 logical cases; found {len(cases)}")

    serials = sorted(int(row["serial"]) for row in cases)
    if serials != list(range(1, 101)):
        raise ValueError("Logical case serials are not exactly 1..100")


def current_plan_and_contract(
    cp_id: str,
    contract_dir: Path,
) -> tuple[dict, dict]:
    cp = core.get_cp(cp_id)
    contract_path = contract_dir / f"{cp_id}.json"
    ledger_path = contract_dir / f"{cp_id}_candidate_ledger.json"
    relation_path = contract_dir / f"{cp_id}_rule_set_relation.json"
    plan_path = contract_dir / f"{cp_id}_evidence_requirements.json"

    contract = load_json(contract_path)
    ledger = load_json(ledger_path)
    relation = load_json(relation_path)
    plan = load_json(plan_path)

    validated = er.validate_evidence_requirements(
        plan,
        cp,
        contract,
        ledger,
        relation,
    )

    # Current canonical files were already frozen through this validator.
    if canonical_json(validated) != canonical_json(plan):
        raise RuntimeError(
            f"{cp_id}: EvidenceRequirement plan validates to different "
            "canonical content"
        )

    return contract, plan


def case_source_fingerprint(case: dict) -> dict:
    rows = []
    for track_name, assignment in sorted(
        (case.get("track_assignments") or {}).items()
    ):
        rows.append({
            "track": track_name,
            "relative_path": assignment["relative_path"],
            "size_bytes": int(assignment["size_bytes"]),
            "sha256": assignment["sha256"],
        })
    return {
        "case_uid": case["case_uid"],
        "serial": int(case["serial"]),
        "physical_case_dir": case["physical_case_dir"],
        "missing_tracks": list(case.get("missing_tracks") or []),
        "sources": rows,
    }


def verify_case_sources(
    *,
    case: dict,
    case_root: Path,
) -> list[tuple[Path, dict]]:
    out = []
    seen_names = set()

    for track_name, assignment in sorted(
        (case.get("track_assignments") or {}).items()
    ):
        source = case_root / assignment["relative_path"]

        if not source.is_file():
            raise FileNotFoundError(source)

        if source.name in seen_names:
            raise RuntimeError(
                f"{case['case_uid']}: duplicate basename in logical case: "
                f"{source.name}"
            )
        seen_names.add(source.name)

        actual_size = source.stat().st_size
        if actual_size != int(assignment["size_bytes"]):
            raise RuntimeError(
                f"{source}: size mismatch {actual_size} != "
                f"{assignment['size_bytes']}"
            )

        actual_hash = sha256_file(source)
        if actual_hash != assignment["sha256"]:
            raise RuntimeError(
                f"{source}: sha256 mismatch {actual_hash} != "
                f"{assignment['sha256']}"
            )

        out.append((source, assignment))

    expected_file_count = 9 - len(case.get("missing_tracks") or [])
    if len(out) != expected_file_count:
        raise RuntimeError(
            f"{case['case_uid']}: assigned source count {len(out)} != "
            f"expected {expected_file_count}"
        )

    return out


def stage_case(
    *,
    case: dict,
    case_root: Path,
    run_dir: Path,
) -> Path:
    sources = verify_case_sources(case=case, case_root=case_root)

    stage_dir = run_dir / "staged_cases" / case["case_uid"]
    stage_meta = stage_dir / "_manifest.json"

    desired = {
        "case_source_fingerprint": case_source_fingerprint(case),
    }
    desired["sha256"] = sha256_json(desired["case_source_fingerprint"])

    if stage_meta.exists():
        old = load_json(stage_meta)
        if old == desired:
            # Recheck symlinks so a stale target is not silently reused.
            names = {
                source.name
                for source, _assignment in sources
            }
            current = {
                p.name
                for p in stage_dir.iterdir()
                if p.name != "_manifest.json"
            }
            if names == current:
                return stage_dir

    if stage_dir.exists():
        for child in stage_dir.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    stage_dir.mkdir(parents=True, exist_ok=True)

    for source, _assignment in sources:
        target = stage_dir / source.name
        os.symlink(source, target)

    save_json_atomic(desired, stage_meta)
    return stage_dir


def parse_case_cached(
    *,
    case: dict,
    stage_dir: Path,
    run_dir: Path,
    parser_hash: str,
) -> tuple[list[dict], bool]:
    case_dir = run_dir / "cases" / case["case_uid"]
    chunks_path = case_dir / "evidence_chunks.json"
    meta_path = case_dir / "parse_meta.json"

    fingerprint = {
        "case_source_fingerprint": case_source_fingerprint(case),
        "parser_sha256": parser_hash,
    }
    fingerprint_sha = sha256_json(fingerprint)

    if chunks_path.exists() and meta_path.exists():
        meta = load_json(meta_path)
        if meta.get("input_fingerprint") == fingerprint_sha:
            value = json.loads(chunks_path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise RuntimeError(f"{chunks_path}: expected list")
            return value, True

    print(
        f"    parsing logical evidence once: {case['case_uid']} "
        f"files={len(case.get('track_assignments') or {})}"
    )
    chunks = core.load_case_evidence(stage_dir)

    case_dir.mkdir(parents=True, exist_ok=True)
    tmp = chunks_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(chunks_path)

    save_json_atomic(
        {
            "schema": "freca-production-case-parse-meta-v1",
            "case_uid": case["case_uid"],
            "input_fingerprint": fingerprint_sha,
            "chunk_count": len(chunks),
            "answer_comparator_used": False,
        },
        meta_path,
    )
    return chunks, False


def inject_coverage(rr: dict, coverage: dict) -> dict:
    patched = copy.deepcopy(rr)
    gate = patched.setdefault("proof_gate", {})

    proof_complete = bool(
        coverage.get(
            "proof_coverage_complete",
            coverage.get("coverage_complete", False),
        )
    )

    gate["coverage_complete"] = proof_complete
    gate["coverage_source_schema"] = coverage.get("schema")
    gate["coverage_source_sha256"] = coverage.get("bundle_sha256")

    summaries = {
        str(row["requirement_id"]): row
        for row in coverage.get("requirement_summaries", [])
    }

    reports = gate.setdefault("requirement_reports", [])
    if not reports:
        reports.extend(
            {
                "requirement_id": str(row["requirement_id"]),
                "coverage_pass": False,
            }
            for row in rr["evidence_requirement_plan"].get(
                "requirements", []
            )
        )

    for row in reports:
        rid = str(row["requirement_id"])
        summary = summaries.get(rid, {})
        row["coverage_pass"] = bool(
            summary.get("proof_coverage_pass", False)
        )
        row["coverage_status_v1_1"] = summary.get(
            "coverage_status"
        )

    return patched


def build_layer7(
    *,
    requirement_result: dict,
    contract: dict,
) -> dict:
    coverage = coverage_v1.evaluate_coverage_bundle(
        requirement_result
    )
    rr_for_proof = inject_coverage(
        requirement_result,
        coverage,
    )

    proof = proofmod.evaluate_proof_standard_bundle(
        rr_for_proof
    )
    proof["coverage_source_sha256"] = coverage.get(
        "bundle_sha256"
    )
    proof["post_proof_argument"] = (
        proofmod.run_post_proof_argument(
            requirement_result=rr_for_proof,
            contract_bundle=contract,
            proof_bundle=proof,
        )
    )

    procedure = procedure_objective_v1.build_plan(
        rr_for_proof,
        coverage,
    )

    goals = open_goal_v1.build_open_goal_ledger(
        requirement_result=rr_for_proof,
        coverage=coverage,
        procedure_plan=procedure,
        proof_standard=proof,
        contract_bundle=contract,
    )

    return {
        "requirement_result": rr_for_proof,
        "coverage": coverage,
        "proof": proof,
        "procedure": procedure,
        "open_goals": goals,
    }


def save_layer7(root: dict, directory: Path) -> None:
    save_json_atomic(
        root["requirement_result"],
        directory / "requirement_for_proof.json",
    )
    save_json_atomic(
        root["coverage"],
        directory / "coverage_v1_1.json",
    )
    save_json_atomic(
        root["proof"],
        directory / "proof_standard_v1_1.json",
    )
    save_json_atomic(
        root["procedure"],
        directory / "procedure_objective_v1.json",
    )
    save_json_atomic(
        root["open_goals"],
        directory / "open_goals_v1.json",
    )


def load_layer7(directory: Path) -> dict | None:
    paths = {
        "requirement_result": directory / "requirement_for_proof.json",
        "coverage": directory / "coverage_v1_1.json",
        "proof": directory / "proof_standard_v1_1.json",
        "procedure": directory / "procedure_objective_v1.json",
        "open_goals": directory / "open_goals_v1.json",
    }
    if not all(path.exists() for path in paths.values()):
        return None
    return {
        key: load_json(path)
        for key, path in paths.items()
    }


def alignable_goals(root: dict) -> list[dict]:
    rows = []
    for goal in root["open_goals"].get("goals", []):
        available = set(
            str(x)
            for x in (goal.get("available_action_types") or [])
        )
        if "ALIGN_NEXT_CANDIDATE_BATCH" not in available:
            continue

        need_ids = [
            str(x)
            for x in (goal.get("prior_need_ids") or [])
        ]
        if not need_ids:
            continue

        # Current action gate additionally requires unassessed candidates.
        try:
            executable = action_gate.executable_action_types(goal)
        except Exception:
            executable = []

        if "ALIGN_NEXT_CANDIDATE_BATCH" not in executable:
            continue

        rows.append(goal)

    rows.sort(key=action_gate.goal_sort_key)
    return rows[:3]


def sum_costs(action_bundles: list[dict]) -> dict:
    fields = [
        "request_attempt_count",
        "successful_call_count",
        "failed_call_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "wall_time_ms",
    ]
    out = {
        "schema": "freca-core-cost-telemetry-v1",
        "status": "AGGREGATED_FROM_ACTIONS",
        "semantic_configuration_modified": False,
        "answer_comparator_used": False,
    }
    for field in fields:
        values = []
        for bundle in action_bundles:
            cost = bundle.get("cost_telemetry") or {}
            value = cost.get(field)
            if isinstance(value, (int, float)):
                values.append(value)
        out[field] = sum(values) if values else None
    return out


def build_production_round(
    *,
    root: dict,
    round_index: int,
    parent_budget: int,
) -> tuple[dict | None, list[str]]:
    goals = alignable_goals(root)
    if not goals:
        return None, []

    action_bundles = []
    executions = []
    selected_goal_ids = []

    for goal in goals:
        need_ids = [
            str(x)
            for x in (goal.get("prior_need_ids") or [])
        ]
        if not need_ids:
            continue

        need_id = need_ids[0]

        selected, _mini_trace, _diag = strategy_a.select_next_batch(
            requirement_result=root["requirement_result"],
            coverage_before=root["coverage"],
            need_id=need_id,
            parent_budget=parent_budget,
        )
        if not selected:
            continue

        bundle = strategy_a.execute_arm(
            requirement_result=root["requirement_result"],
            coverage_before=root["coverage"],
            need_id=need_id,
            parent_budget=parent_budget,
        )

        execution = copy.deepcopy(
            bundle["action_executions"][0]
        )
        execution["goal_id"] = str(goal["goal_id"])
        execution["production_policy_id"] = (
            "PRODUCTION_REPAIR_POLICY_V1"
        )
        execution["production_admitted_primitive"] = (
            "TOP_K_NEXT_BATCH_EXPANSION"
        )
        execution["execution_sha256"] = feedback.sha256_json(
            execution
        )

        executions.append(execution)
        action_bundles.append(bundle)
        selected_goal_ids.append(str(goal["goal_id"]))

    if not executions:
        return None, selected_goal_ids

    planned_ids = [
        str(row["action_id"])
        for row in executions
    ]
    new_alignment_ids = sorted({
        str(
            alignment.get("alignment_evidence_id")
            or alignment.get("fact_candidate_id")
        )
        for execution in executions
        for alignment in execution.get("new_alignments", [])
        if (
            alignment.get("alignment_evidence_id")
            or alignment.get("fact_candidate_id")
        )
    })

    round_bundle = {
        "schema": "freca-core-production-repair-round-v1",
        "round_index": round_index,
        "production_policy_id": "PRODUCTION_REPAIR_POLICY_V1",
        "repair_primitive": "TOP_K_NEXT_BATCH_EXPANSION",
        "action_type": "ALIGN_NEXT_CANDIDATE_BATCH",
        "selected_goal_ids": selected_goal_ids,
        "planned_action_ids": planned_ids,
        "executed_action_ids": planned_ids,
        "missing_action_ids": [],
        "round_execution_complete": True,
        "action_executions": executions,
        "new_alignment_ids": new_alignment_ids,
        "new_alignment_count": sum(
            int(row.get("new_alignment_count", 0))
            for row in executions
        ),
        "any_new_validated_signal": any(
            bool(row.get("new_alignment_count"))
            for row in executions
        ),
        "parent_alignment_budget_per_action": parent_budget,
        "max_selected_goals_per_round": 3,
        "max_actions_per_round": 6,
        "cost_telemetry": sum_costs(action_bundles),
        "channel_completion_executed": False,
        "tree_search_executed": False,
        "answer_comparator_used": False,
        "human_or_historical_labels_used": False,
        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,
    }

    round_bundle["round_artifact_bundle_id"] = (
        feedback.stable_id(
            "production-repair-round",
            sha256_json(root["requirement_result"]),
            str(round_index),
            ",".join(planned_ids),
        )
    )
    round_bundle["bundle_sha256"] = feedback.sha256_json(
        round_bundle
    )
    return round_bundle, selected_goal_ids


def run_repair_round(
    *,
    before: dict,
    contract: dict,
    round_bundle: dict,
) -> tuple[dict, dict, dict]:
    merged_rr, merge_diag = (
        feedback.merge_round_into_requirement_result(
            before["requirement_result"],
            round_bundle,
        )
    )

    after = build_layer7(
        requirement_result=merged_rr,
        contract=contract,
    )

    hard_gates = feedback.evaluate_hard_gates(
        requirement_result_before=before["requirement_result"],
        requirement_result_after=after["requirement_result"],
        proof_before=before["proof"],
        proof_after=after["proof"],
        round_bundle=round_bundle,
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
        round_bundle=round_bundle,
        hard_gates=hard_gates,
    )

    return after, hard_gates, diff


def build_outcome_and_fold(
    *,
    root: dict,
    contract: dict,
) -> tuple[dict, dict]:
    root_states = adapter.derive_root_states(
        contract_bundle=contract,
        proof_bundle=root["proof"],
        requirement_result=root["requirement_result"],
    )
    outcome = adapter.build_argument_evaluation_bundle(
        requirement_result=root["requirement_result"],
        contract_bundle=contract,
        proof_bundle=root["proof"],
        na_countercheck=derive_na_countercheck(root_states),
    )

    evaluations = outcome.get("evaluations") or []
    if not evaluations:
        raise RuntimeError(
            "Core outcome adapter produced no interpretation evaluations"
        )

    branches = [
        {
            "valid": True,
            "internal_outcome": row["internal_outcome"],
            "fold_gate_report": row["fold_gate_report"],
        }
        for row in evaluations
    ]

    fold = foldmod.fold_envelope(branches)
    if str(fold.get("label")) not in {"1", "0", "N/A"}:
        raise RuntimeError(
            f"Fold emitted invalid label: {fold.get('label')!r}"
        )

    return outcome, fold


def task_input_fingerprint(
    *,
    case: dict,
    cp_id: str,
    manifest: dict,
    contract_dir: Path,
    policy_path: Path,
    runtime: dict,
    retrieval_top_k: int,
) -> tuple[str, dict]:
    files = {
        "contract": contract_dir / f"{cp_id}.json",
        "candidate_ledger":
            contract_dir / f"{cp_id}_candidate_ledger.json",
        "rule_set_relation":
            contract_dir / f"{cp_id}_rule_set_relation.json",
        "evidence_requirements":
            contract_dir / f"{cp_id}_evidence_requirements.json",
    }

    payload = {
        "case_uid": case["case_uid"],
        "case_source_fingerprint": case_source_fingerprint(case),
        "cp_id": cp_id,
        "logical_case_manifest_semantic_sha256":
            manifest.get("semantic_sha256"),
        "normative_file_sha256": {
            name: sha256_file(path)
            for name, path in files.items()
        },
        "production_repair_policy_sha256":
            sha256_file(policy_path),
        "runtime_file_sha256": runtime,
        "retrieval_top_k": retrieval_top_k,
        "feedback_module": FEEDBACK_MODULE,
        "na_countercheck_enabled":
            os.environ.get("FRECA_ENABLE_NA_COUNTERCHECK") == "1",
        "answer_comparator_used": False,
    }
    return sha256_json(payload), payload


def prepare_task_meta(
    *,
    task_dir: Path,
    fingerprint: str,
    payload: dict,
) -> None:
    meta_path = task_dir / "task_meta.json"

    if meta_path.exists():
        old = load_json(meta_path)
        if old.get("input_fingerprint") != fingerprint:
            raise RuntimeError(
                "L3_RESUME_INPUT_MISMATCH: existing task directory "
                "was created from different inputs/code. "
                f"Use a new --run-dir. Task: {task_dir}"
            )
        return

    save_json_atomic(
        {
            "schema": "freca-production-task-meta-v1",
            "input_fingerprint": fingerprint,
            "inputs": payload,
        },
        meta_path,
    )


def run_initial_requirement_reasoning(
    *,
    case: dict,
    cp_id: str,
    chunks: list[dict],
    plan: dict,
    task_dir: Path,
    retrieval_top_k: int,
) -> dict:
    path = task_dir / "initial" / "requirement_result.json"
    if path.exists():
        return load_json(path)

    output_identifier = str(case["re_number_candidate"])

    old_result_dir = er.RESULT_DIR
    er_output_dir = task_dir / "initial" / "_er_output"
    er_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        er.RESULT_DIR = er_output_dir
        rr = er.run_requirement_reasoning(
            cp_id=cp_id,
            case_id=output_identifier,
            evidence_chunks=chunks,
            retrieval_top_k=retrieval_top_k,
            force_plan_recompile=False,
        )
    finally:
        er.RESULT_DIR = old_result_dir

    embedded = rr.get("evidence_requirement_plan")
    if not isinstance(embedded, dict):
        raise RuntimeError("Initial result contains no EvidenceRequirement plan")

    if canonical_json(embedded) != canonical_json(plan):
        raise RuntimeError(
            "Initial evidence evaluation did not consume the current "
            "frozen EvidenceRequirement plan"
        )

    # Add orchestration identity AFTER the evidence/identity/alignment stage.
    # This field is not used to define body identity.
    rr["case_uid"] = str(case["case_uid"])
    rr["logical_case_manifest_ref"] = {
        "case_uid": case["case_uid"],
        "physical_case_dir": case["physical_case_dir"],
        "source_count": len(case.get("track_assignments") or {}),
        "missing_tracks": list(case.get("missing_tracks") or []),
    }
    rr.pop("saved_path", None)

    save_json_atomic(rr, path)
    return rr


def save_round(
    *,
    directory: Path,
    round_bundle: dict,
    after: dict,
    hard_gates: dict,
    diff: dict,
    stop: dict,
    admitted: bool,
) -> None:
    save_json_atomic(round_bundle, directory / "round_bundle.json")
    save_layer7(after, directory / "after")
    save_json_atomic(hard_gates, directory / "hard_gates.json")
    save_json_atomic(diff, directory / "evaluation_diff.json")
    save_json_atomic(stop, directory / "stop_decision.json")
    save_json_atomic(
        {
            "schema": "freca-production-repair-round-admission-v1",
            "admitted_to_production_state": admitted,
            "answer_comparator_used": False,
        },
        directory / "admission.json",
    )


def load_completed_round(directory: Path) -> tuple[dict, dict] | None:
    required = [
        directory / "round_bundle.json",
        directory / "after" / "requirement_for_proof.json",
        directory / "after" / "coverage_v1_1.json",
        directory / "after" / "proof_standard_v1_1.json",
        directory / "after" / "procedure_objective_v1.json",
        directory / "after" / "open_goals_v1.json",
        directory / "hard_gates.json",
        directory / "evaluation_diff.json",
        directory / "stop_decision.json",
        directory / "admission.json",
    ]
    if not all(path.exists() for path in required):
        return None

    admission = load_json(directory / "admission.json")
    after = load_layer7(directory / "after")
    stop = load_json(directory / "stop_decision.json")

    if after is None:
        return None

    if admission.get("admitted_to_production_state") is True:
        return after, stop

    # A rejected repair round must not alter current production state.
    return {}, stop


def run_task(
    *,
    case: dict,
    cp_id: str,
    chunks: list[dict],
    manifest: dict,
    contract_dir: Path,
    policy: dict,
    policy_path: Path,
    run_dir: Path,
    runtime: dict,
    retrieval_top_k: int,
    repair_enabled: bool,
) -> dict:
    task_dir = (
        run_dir
        / "tasks"
        / case["case_uid"]
        / cp_id
    )
    task_dir.mkdir(parents=True, exist_ok=True)

    fingerprint, payload = task_input_fingerprint(
        case=case,
        cp_id=cp_id,
        manifest=manifest,
        contract_dir=contract_dir,
        policy_path=policy_path,
        runtime=runtime,
        retrieval_top_k=retrieval_top_k,
    )

    prepare_task_meta(
        task_dir=task_dir,
        fingerprint=fingerprint,
        payload=payload,
    )

    final_path = task_dir / "decision.json"
    if final_path.exists():
        final = load_json(final_path)
        if final.get("input_fingerprint") != fingerprint:
            raise RuntimeError(
                f"{task_dir}: final decision fingerprint mismatch"
            )
        return {
            "status": "SKIPPED_COMPLETE",
            "decision": final,
        }

    contract, plan = current_plan_and_contract(
        cp_id,
        contract_dir,
    )

    initial_rr = run_initial_requirement_reasoning(
        case=case,
        cp_id=cp_id,
        chunks=chunks,
        plan=plan,
        task_dir=task_dir,
        retrieval_top_k=retrieval_top_k,
    )

    initial_dir = task_dir / "initial" / "layer7"
    initial_root = load_layer7(initial_dir)
    if initial_root is None:
        initial_root = build_layer7(
            requirement_result=initial_rr,
            contract=contract,
        )
        save_layer7(initial_root, initial_dir)

    current = initial_root
    repair_history = []
    repair_status = (
        "DISABLED_BY_RUN_ARGUMENT"
        if not repair_enabled
        else "NO_ADMITTED_ACTION_EXECUTED"
    )

    max_rounds = int(
        (policy.get("repair_state_machine") or {}).get(
            "max_rounds",
            2,
        )
    )
    parent_budget = int(
        (policy.get("selected_repair_primitive") or {}).get(
            "parent_alignment_budget",
            24,
        )
    )

    if repair_enabled:
        for round_index in range(1, max_rounds + 1):
            round_dir = task_dir / "repair" / f"round-{round_index}"

            completed = load_completed_round(round_dir)
            if completed is not None:
                resumed_after, resumed_stop = completed
                if resumed_after:
                    current = resumed_after
                repair_history.append({
                    "round_index": round_index,
                    "status": "RESUMED_COMPLETE_ROUND",
                    "stop_decision": resumed_stop,
                })
                repair_status = resumed_stop.get(
                    "decision",
                    "DEFER",
                )
                if not resumed_stop.get("allow_next_repair_round", False):
                    break
                continue

            round_bundle, selected_goal_ids = build_production_round(
                root=current,
                round_index=round_index,
                parent_budget=parent_budget,
            )

            if round_bundle is None:
                repair_status = "NO_ADMITTED_EXECUTABLE_ACTION"
                repair_history.append({
                    "round_index": round_index,
                    "status": repair_status,
                    "selected_goal_ids": selected_goal_ids,
                })
                break

            after, hard_gates, diff = run_repair_round(
                before=current,
                contract=contract,
                round_bundle=round_bundle,
            )

            if hard_gates.get("all_hard_gates_pass") is not True:
                stop = {
                    "decision": "DEFER",
                    "allow_next_repair_round": False,
                    "stop_reasons": ["REPAIR_HARD_GATE_FAILURE"],
                    "policy": "PRODUCTION_REPAIR_HARD_GATE",
                }
                admitted = False
                repair_status = "REPAIR_HARD_GATE_FAILURE"
            else:
                stop = stop_gate.decide_after_round(
                    evaluation_diff=diff,
                    repair_round=round_bundle,
                    round_index=round_index,
                    max_rounds=max_rounds,
                )
                admitted = True
                current = after
                repair_status = stop["decision"]

            save_round(
                directory=round_dir,
                round_bundle=round_bundle,
                after=after,
                hard_gates=hard_gates,
                diff=diff,
                stop=stop,
                admitted=admitted,
            )

            repair_history.append({
                "round_index": round_index,
                "status": "EXECUTED",
                "selected_goal_ids": selected_goal_ids,
                "hard_gates_pass": hard_gates.get(
                    "all_hard_gates_pass"
                ),
                "stop_decision": stop,
                "effect_vector": diff.get("effect_vector"),
            })

            if not stop.get("allow_next_repair_round", False):
                break

    outcome, fold = build_outcome_and_fold(
        root=current,
        contract=contract,
    )

    save_json_atomic(
        outcome,
        task_dir / "core_outcome_adapter_v1.json",
    )
    save_json_atomic(
        fold,
        task_dir / "fold_decision_v3.json",
    )

    evaluations = outcome.get("evaluations") or []

    decision = {
        "schema": "freca-production-decision-v1",
        "input_fingerprint": fingerprint,
        "case_uid": case["case_uid"],
        "serial": int(case["serial"]),
        "output_identifier": case["re_number_candidate"],
        "physical_case_dir": case["physical_case_dir"],
        "cp_id": cp_id,
        "source_count": len(case.get("track_assignments") or {}),
        "missing_tracks": list(case.get("missing_tracks") or []),
        "shared_physical_container": bool(
            case.get("shared_physical_container")
        ),
        "initial_requirement_result":
            str(task_dir / "initial" / "requirement_result.json"),
        "repair_enabled": repair_enabled,
        "repair_policy_id": policy.get("policy_id"),
        "repair_status": repair_status,
        "repair_history": repair_history,
        "interpretation_count": len(evaluations),
        "internal_outcomes": [
            row.get("internal_outcome")
            for row in evaluations
        ],
        "common_internal_outcome": outcome.get(
            "common_internal_outcome"
        ),
        "fold_label": str(fold["label"]),
        "fold_finality": fold.get("finality"),
        "benchmark_fallback": bool(
            fold.get("benchmark_fallback", False)
        ),
        "fold_decision": fold,
        "answer_comparator_used": False,
        "human_or_historical_labels_used": False,
        "case_serial_used_for_evidence_reasoning": False,
        "final_label_emitted_only_by_fold": True,
        "status": "COMPLETE",
    }

    save_json_atomic(decision, final_path)

    # Read-back fingerprint and label boundary.
    replay = load_json(final_path)
    if replay.get("input_fingerprint") != fingerprint:
        raise RuntimeError("Decision write/read fingerprint mismatch")
    if replay.get("fold_label") not in {"1", "0", "N/A"}:
        raise RuntimeError("Decision write/read fold label invalid")

    return {
        "status": "COMPLETED",
        "decision": replay,
    }


def write_run_report(
    *,
    path: Path,
    manifest: dict,
    policy_path: Path,
    runtime: dict,
    selected_cases: list[str],
    selected_cps: list[str],
    task_rows: list[dict],
    dry_run: bool,
) -> None:
    counts = {}
    for row in task_rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1

    report = {
        "schema": "freca-production-run-report-v1",
        "logical_case_manifest_semantic_sha256":
            manifest.get("semantic_sha256"),
        "repair_policy_path": str(policy_path),
        "repair_policy_sha256": sha256_file(policy_path),
        "runtime_file_sha256": runtime,
        "selected_cases": selected_cases,
        "selected_cps": selected_cps,
        "selected_task_count":
            len(selected_cases) * len(selected_cps),
        "dry_run": dry_run,
        "status_counts": dict(sorted(counts.items())),
        "tasks": task_rows,
        "answer_comparator_used": False,
        "human_or_historical_labels_used": False,
    }
    save_json_atomic(report, path)


def main() -> None:
    p = argparse.ArgumentParser()

    p.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
    )
    p.add_argument(
        "--contract-dir",
        type=Path,
        default=DEFAULT_CONTRACT_DIR,
    )
    p.add_argument(
        "--repair-policy",
        type=Path,
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_RUN_DIR,
    )

    p.add_argument("--case", action="append")
    p.add_argument("--cp", action="append")
    p.add_argument(
        "--all",
        action="store_true",
        help="Select all 100 cases and all 41 CPs.",
    )
    p.add_argument(
        "--max-tasks",
        type=int,
        help="Execute only the first N selected coordinates; useful for smoke.",
    )
    p.add_argument(
        "--retrieval-top-k",
        type=int,
        default=12,
    )
    p.add_argument(
        "--no-repair",
        action="store_true",
        help="Diagnostic only: stop after initial Layer-7 before Fold.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Zero-API task/input/staging preflight.",
    )
    p.add_argument(
        "--stop-on-error",
        action="store_true",
    )

    args = p.parse_args()

    manifest = load_json(args.manifest)
    validate_manifest(manifest)
    case_map = manifest_case_map(manifest)

    policy_path = resolve_policy(args.repair_policy)
    policy = load_json(policy_path)
    validate_policy(policy)

    runtime = runtime_hashes()
    parser_hash = runtime.get("freca_core_v1.py")
    if not parser_hash:
        raise RuntimeError("Cannot hash freca_core_v1.py")

    case_root = Path(
        manifest["dataset_structure_profile"]["case_root"]
    )
    if not case_root.is_dir():
        raise FileNotFoundError(case_root)

    if args.all:
        selected_cases = [
            f"case-{i:03d}"
            for i in range(1, 101)
        ]
        selected_cps = list(CP_IDS)
    else:
        selected_cases = (
            [
                normalize_case_selector(value)
                for value in args.case
            ]
            if args.case
            else []
        )
        selected_cps = (
            [
                normalize_cp_selector(value)
                for value in args.cp
            ]
            if args.cp
            else []
        )

        if not selected_cases or not selected_cps:
            p.error(
                "Use --all, or provide at least one --case and one --cp."
            )

    selected_cases = sorted(
        set(selected_cases),
        key=lambda x: int(x.split("-")[1]),
    )
    selected_cps = sorted(
        set(selected_cps),
        key=lambda x: int(x[2:]),
    )

    for case_uid in selected_cases:
        if case_uid not in case_map:
            raise ValueError(f"Manifest has no {case_uid}")

    # Validate every selected normative input before first task/API call.
    for cp_id in selected_cps:
        current_plan_and_contract(
            cp_id,
            args.contract_dir,
        )

    coordinates = [
        (case_uid, cp_id)
        for case_uid in selected_cases
        for cp_id in selected_cps
    ]
    if args.max_tasks is not None:
        if args.max_tasks < 1:
            p.error("--max-tasks must be >= 1")
        coordinates = coordinates[:args.max_tasks]

    args.run_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 92)
    print("FRECA MANIFEST-AWARE RESUME-SAFE PRODUCTION RUNNER V1")
    print("=" * 92)
    print("Manifest:", args.manifest)
    print("Manifest ALL PASS:", manifest["all_pass"])
    print("Logical cases selected:", len(selected_cases))
    print("CPs selected:", len(selected_cps))
    print("Coordinates selected:", len(coordinates))
    print("Repair policy:", policy_path)
    print("Repair primitive: TOP_K_NEXT_BATCH_EXPANSION")
    print("Repair max rounds: 2")
    print("CHANNEL_COMPLETION_ONLY: DISABLED")
    print("Tree search: DISABLED")
    print("Answer comparator: False")
    print("Dry run:", args.dry_run)
    print()

    # Stage all cases in the selected coordinate set.
    staged = {}
    for case_uid in sorted({c for c, _ in coordinates}):
        case = case_map[case_uid]
        staged[case_uid] = stage_case(
            case=case,
            case_root=case_root,
            run_dir=args.run_dir,
        )
        print(
            f"STAGED {case_uid}: "
            f"{len(case.get('track_assignments') or {})} files "
            f"from {case['physical_case_dir']}"
        )

    task_rows = []
    report_path = args.run_dir / "run_report.json"

    if args.dry_run:
        for case_uid, cp_id in coordinates:
            case = case_map[case_uid]
            fingerprint, _payload = task_input_fingerprint(
                case=case,
                cp_id=cp_id,
                manifest=manifest,
                contract_dir=args.contract_dir,
                policy_path=policy_path,
                runtime=runtime,
                retrieval_top_k=args.retrieval_top_k,
            )
            task_rows.append({
                "case_uid": case_uid,
                "cp_id": cp_id,
                "status": "DRY_RUN_READY",
                "input_fingerprint": fingerprint,
            })

        write_run_report(
            path=report_path,
            manifest=manifest,
            policy_path=policy_path,
            runtime=runtime,
            selected_cases=selected_cases,
            selected_cps=selected_cps,
            task_rows=task_rows,
            dry_run=True,
        )

        print()
        print("DRY RUN READY:", len(task_rows), "/", len(coordinates))
        print("API called: False")
        print("Saved:", report_path)
        return

    chunk_cache = {}

    for index, (case_uid, cp_id) in enumerate(coordinates, start=1):
        case = case_map[case_uid]

        print()
        print("#" * 92)
        print(
            f"[{index}/{len(coordinates)}] "
            f"{case_uid} / {cp_id}"
        )
        print("#" * 92)

        try:
            if case_uid not in chunk_cache:
                chunks, cache_hit = parse_case_cached(
                    case=case,
                    stage_dir=staged[case_uid],
                    run_dir=args.run_dir,
                    parser_hash=parser_hash,
                )
                chunk_cache[case_uid] = chunks
                print(
                    "    evidence chunks:",
                    len(chunks),
                    "(cache)"
                    if cache_hit
                    else "(parsed)",
                )
            else:
                chunks = chunk_cache[case_uid]
                print(
                    "    evidence chunks:",
                    len(chunks),
                    "(memory reuse)",
                )

            result = run_task(
                case=case,
                cp_id=cp_id,
                chunks=chunks,
                manifest=manifest,
                contract_dir=args.contract_dir,
                policy=policy,
                policy_path=policy_path,
                run_dir=args.run_dir,
                runtime=runtime,
                retrieval_top_k=args.retrieval_top_k,
                repair_enabled=not args.no_repair,
            )

            decision = result["decision"]
            row = {
                "case_uid": case_uid,
                "serial": int(case["serial"]),
                "cp_id": cp_id,
                "status": result["status"],
                "fold_label": decision["fold_label"],
                "fold_finality": decision["fold_finality"],
                "benchmark_fallback":
                    decision["benchmark_fallback"],
                "repair_status": decision["repair_status"],
            }
            task_rows.append(row)

            print("    status:", result["status"])
            print("    repair:", decision["repair_status"])
            print(
                "    outcomes:",
                decision["internal_outcomes"],
            )
            print(
                "    fold:",
                decision["fold_label"],
                "|",
                decision["fold_finality"],
            )

        except Exception as exc:
            task_dir = (
                args.run_dir
                / "tasks"
                / case_uid
                / cp_id
            )
            task_dir.mkdir(parents=True, exist_ok=True)

            error = {
                "schema": "freca-production-task-error-v1",
                "case_uid": case_uid,
                "cp_id": cp_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "answer_comparator_used": False,
            }
            save_json_atomic(
                error,
                task_dir / "error.json",
            )

            task_rows.append({
                "case_uid": case_uid,
                "serial": int(case["serial"]),
                "cp_id": cp_id,
                "status": "FAILED",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })

            print(
                "    FAILED:",
                type(exc).__name__,
                str(exc),
            )

            if args.stop_on_error:
                write_run_report(
                    path=report_path,
                    manifest=manifest,
                    policy_path=policy_path,
                    runtime=runtime,
                    selected_cases=selected_cases,
                    selected_cps=selected_cps,
                    task_rows=task_rows,
                    dry_run=False,
                )
                raise

        write_run_report(
            path=report_path,
            manifest=manifest,
            policy_path=policy_path,
            runtime=runtime,
            selected_cases=selected_cases,
            selected_cps=selected_cps,
            task_rows=task_rows,
            dry_run=False,
        )

    counts = {}
    for row in task_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    print()
    print("=" * 92)
    print("RUN SUMMARY")
    print("=" * 92)
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print("Processed:", len(task_rows), "/", len(coordinates))
    print("Answer comparator used: False")
    print("Saved:", report_path)

    if any(row["status"] == "FAILED" for row in task_rows):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
