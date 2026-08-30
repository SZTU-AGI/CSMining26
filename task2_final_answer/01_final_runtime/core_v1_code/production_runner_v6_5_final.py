#!/usr/bin/env python3
from __future__ import annotations

"""FRECA V6.5 final production runner.

This is the first batch runner in this branch whose persisted decision.json is
built directly from the corrected V2 proof semantics (rather than requiring a
post-hoc V2 replay). It adds V6.4.3 structural witnesses and V6.5 whole-CP
applicability/N/A evaluation before proof/fold.
"""

import copy
import os
from pathlib import Path

import applicability_v6_5
import core_outcome_adapter_v1 as adapter
import fold_policy_v3_core as foldmod
import production_runner_v1 as base
import production_runner_v2 as v2
import semantic_replay_v6_1
import structured_witness_v6_3 as structural

_ORIGINAL_INITIAL = base.run_initial_requirement_reasoning
_ORIGINAL_FP = base.task_input_fingerprint


def _fingerprint_v6_5(**kwargs):
    _old_hash, payload = _ORIGINAL_FP(**kwargs)
    payload = copy.deepcopy(payload)
    payload["v6_5_final_semantics"] = {
        "version": applicability_v6_5.VERSION,
        "proof_layer": "PRODUCTION_RUNNER_V2_BUILD_LAYER7_V2",
        "semantic_replay_before_structural": True,
        "structural_version": "V6.4.3",
        "applicability_model": os.environ.get("FRECA_APPLICABILITY_MODEL") or os.environ.get("FRECA_ALIGNMENT_MODEL", "deepseek-v4-flash"),
        "na_countercheck_enabled": True,
        "whole_cp_na_only": True,
    }
    return base.sha256_json(payload), payload


def run_initial_requirement_reasoning_v6_5(*, case, cp_id, chunks, plan, task_dir, retrieval_top_k):
    raw = _ORIGINAL_INITIAL(
        case=case,
        cp_id=cp_id,
        chunks=chunks,
        plan=plan,
        task_dir=task_dir,
        retrieval_top_k=retrieval_top_k,
    )
    # A task may have persisted its V6.5 requirement_result and then crashed
    # before decision.json was written.  Do not replay/enrich a second time;
    # this makes partial-task resume idempotent as well as decision-level resume.
    if raw.get("production_semantic_version") == "V6.5_FINAL_0_1_NA":
        return raw

    # Recompute all deterministic alignment typing under the current semantic
    # rules, then add structural aggregate witnesses.
    replayed, replay_audit = semantic_replay_v6_1.replay_requirement_result(raw)
    enriched, struct_audit = structural.enrich_requirement_result(replayed, chunks)

    app_path = Path(task_dir) / "initial" / "applicability_v6_5.json"
    app_eval = applicability_v6_5.evaluate(
        enriched,
        chunks,
        cache_path=app_path,
        model=os.environ.get("FRECA_APPLICABILITY_MODEL"),
    )
    enriched["applicability_evaluation_v6_5"] = app_eval
    enriched["production_semantic_version"] = "V6.5_FINAL_0_1_NA"
    enriched["semantic_replay_v6_1_audit"] = replay_audit
    enriched["structural_witness_v6_4_3_audit"] = struct_audit

    path = Path(task_dir) / "initial" / "requirement_result.json"
    base.save_json_atomic(enriched, path)
    if struct_audit.get("injected_count"):
        print(f"    structural witnesses: {struct_audit['injected_count']} {struct_audit.get('family_counts', {})}")
    if app_eval.get("scope") == "WHOLE_CP_CONDITIONAL":
        print(
            "    applicability:",
            app_eval.get("decision"),
            "method=", app_eval.get("method"),
            "model_calls=", app_eval.get("model_calls", 0),
        )
    return enriched


def build_outcome_and_fold_v6_5(*, root: dict, contract: dict):
    rr = root["requirement_result"]
    app_eval = rr.get("applicability_evaluation_v6_5") or {
        "scope": "NON_CONDITIONAL", "decision": "NON_CONDITIONAL"
    }
    effective_contract = applicability_v6_5.apply_to_contract(contract, app_eval)
    root_states = adapter.derive_root_states(
        contract_bundle=effective_contract,
        proof_bundle=root["proof"],
        requirement_result=rr,
    )

    countercheck = None
    if (
        app_eval.get("scope") == "WHOLE_CP_CONDITIONAL"
        and app_eval.get("decision") == "NOT_APPLICABLE"
        and (app_eval.get("countercheck") or {}).get("status") == "NOT_FOUND"
    ):
        countercheck = {
            "passed": bool(
                root_states.get("non_applicability_state") == "TRUE"
                and root_states.get("applicability_state") != "TRUE"
                and root_states.get("violation_state") != "TRUE"
            ),
            "activity_counterevidence_standing": bool(
                root_states.get("violation_state") == "TRUE"
            ),
            "countercheck_basis": "V6_5_INDEPENDENT_APPLICABILITY_COUNTERCHECK",
        }

    outcome = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=effective_contract,
        proof_bundle=root["proof"],
        na_countercheck=countercheck,
    )
    outcome["v6_5_applicability_evaluation"] = app_eval
    outcome["effective_applicability_roots"] = {
        "applicability_state": root_states.get("applicability_state"),
        "non_applicability_state": root_states.get("non_applicability_state"),
    }

    evaluations = outcome.get("evaluations") or []
    if not evaluations:
        raise RuntimeError("V6.5 outcome adapter produced no evaluations")
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
        raise RuntimeError(f"V6.5 fold emitted invalid label: {fold.get('label')!r}")
    fold["v6_5_applicability_decision"] = app_eval.get("decision")
    fold["v6_5_applicability_scope"] = app_eval.get("scope")
    fold["v6_5_final_semantic_version"] = "V6.5_FINAL_0_1_NA"
    return outcome, fold


# Patch the generic batch harness with the final semantics.
base.task_input_fingerprint = _fingerprint_v6_5
base.run_initial_requirement_reasoning = run_initial_requirement_reasoning_v6_5
base.build_layer7 = v2.build_layer7_v2
base.build_outcome_and_fold = build_outcome_and_fold_v6_5

for _name in (
    "semantic_replay_v6_1.py",
    "structured_witness_v6_3.py",
    "applicability_v6_5.py",
    "production_runner_v6_5_final.py",
):
    if _name not in base.RUNTIME_FILES:
        base.RUNTIME_FILES.append(_name)


if __name__ == "__main__":
    base.main()
