#!/usr/bin/env python3
"""Phase 3 synthetic and mutation validation for Production V2.

Every semantic reachability test uses the live V2 Layer-7 builder and outcome
adapter.  The fixture starts from a saved real requirement-result schema and
only minimizes evidence/traces in memory.  No model entrypoint is permitted.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import coverage_policy_v2
import freca_core_v1 as core
import procedure_executor_v2
import production_repair_dispatcher_v2 as dispatcher
import production_runner_v2 as runner
import production_stop_gate_v1 as stop_gate
import proof_gate_applicability_v2 as applicability_v2


BASE_RR = Path(
    "results_v2/production_run_v1_shards/shard-03/tasks/"
    "case-004/CP4/initial/requirement_result.json"
)
BASE_CONTRACT = Path("contracts_v2/CP4.json")
POLICY = Path("production_repair_policy_v2.json")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def rehash_procedure(artifact: dict) -> None:
    unsigned = dict(artifact)
    unsigned.pop("procedure_artifact_sha256", None)
    artifact["procedure_artifact_sha256"] = procedure_executor_v2.sha256_json(
        unsigned
    )


def _base_alignment(relation: str, suffix: str) -> dict:
    rr = load(BASE_RR)
    source = next(
        row
        for row in rr["alignments"]
        if row.get("alignment_evidence_id")
        == "4_Farm-Management-Plan_Yorke_Peninsula_Agri_Pty_Ltd.docx:"
        "P33#fc-d407f390a1c18a2da301"
    )
    row = copy.deepcopy(source)
    evidence_id = f"phase3:{suffix}"
    alignment_id = f"{evidence_id}#fc-{suffix}"
    row.update(
        {
            "evidence_id": evidence_id,
            "alignment_evidence_id": alignment_id,
            "fact_candidate_id": f"fc-{suffix}",
            "relation": relation,
            "argument_admission_channel": "DIRECT",
            "argument_truth_bearing": True,
            "predicate_compatibility": "DIRECT",
            "identity_decisive_proof_eligible": True,
            "identity_use_decision": "ADMIT_DIRECT",
            "retrieval_need_ids": ["ER1.support", "ER1.attack"],
            "quote_match_mode": "EXACT_RAW",
        }
    )
    row.pop("temporal_assessment", None)
    row.pop("temporal_relation", None)
    row.pop("temporal_requirement_classification", None)
    row.pop("information_reliability", None)
    fact = copy.deepcopy(row.get("fact_candidate") or {})
    fact.update(
        {
            "fact_candidate_id": f"fc-{suffix}",
            "evidence_id": evidence_id,
            "source_id": "phase3-source.docx",
            "grounding_valid": True,
            "status": "SPAN_VALIDATED",
            "modality": "ACTUAL",
            "polarity": "ADVERSE" if relation == "ATTACK" else "POSITIVE",
        }
    )
    row["fact_candidate"] = fact
    natures = (
        ["ADVERSE_OPERATIONAL_FINDING"]
        if relation == "ATTACK"
        else ["OBSERVATION_RECORD"]
    )
    nature = {
        "evidence_natures": natures,
        "assertion_mode": {
            "actual_signal_present": True,
            "modality": "ACTUAL",
            "speech_act": "OBSERVATION",
        },
    }
    row["evidence_nature"] = nature
    row["fact_candidate"]["evidence_nature"] = copy.deepcopy(nature)
    return row


def _trace(trace: dict, evidence_ids: list[str], *, excluded_only: bool = False) -> dict:
    trace = copy.deepcopy(trace)
    universe = [
        {
            "evidence_id": evidence_id,
            "identity_use_decision": (
                "EXCLUDE_SUBSTANTIVE" if excluded_only else "ADMIT_DIRECT"
            ),
            "identity_reason_codes": [],
            "retrieval_methods": ["RAW_LEXICAL"],
        }
        for evidence_id in evidence_ids
    ]
    trace.update(
        {
            "candidate_universe": universe,
            "candidates": copy.deepcopy(universe),
            "candidate_universe_persisted": True,
            "expected_channels": ["RAW_LEXICAL"],
            "query_variants": [{"full_ids": evidence_ids}],
            "raw_lexical_scan": {
                "scan_chunk_count": max(1, len(evidence_ids)),
                "scan_complete": True,
                "generated_union_count": len(evidence_ids),
                "candidate_generation_mode": "PHASE3_MINIMAL_FULL_SCAN",
            },
            "parse_gap_ids": [],
            "readability_gap_ids": [],
            "missing_required_track_types": [],
            "retrieval_scan_chunk_count": max(1, len(evidence_ids)),
        }
    )
    return trace


def fixture(
    relations: list[str],
    *,
    temporal_required: bool = False,
    support_extra_unassessed: bool = False,
    nonapp_contract: bool = False,
    plan_statement: bool = False,
) -> tuple[dict, dict]:
    rr = load(BASE_RR)
    contract = load(BASE_CONTRACT)
    requirement = rr["evidence_requirement_plan"]["requirements"][0]
    requirement["temporal_required"] = temporal_required
    rr["targeted_coverage_procedure_artifacts"] = []

    rows = [_base_alignment(relation, f"{relation.lower()}-{index}") for index, relation in enumerate(relations)]
    if plan_statement and rows:
        nature = {
            "evidence_natures": ["PLAN_STATEMENT", "PROCEDURE_STATEMENT"],
            "assertion_mode": {
                "actual_signal_present": False,
                "modality": "PLANNED",
                "speech_act": "PROCEDURE",
            },
        }
        rows[0]["evidence_nature"] = nature
        rows[0]["fact_candidate"]["evidence_nature"] = copy.deepcopy(nature)
        rows[0]["fact_candidate"]["modality"] = "PLANNED"
    rr["alignments"] = rows

    row_ids = [str(row["evidence_id"]) for row in rows]
    if not row_ids:
        row_ids = ["phase3:deterministically-excluded"]
    traces = []
    for original in rr["retrieval_traces"]:
        ids = list(row_ids)
        if (
            support_extra_unassessed
            and original.get("direction") == "SUPPORT"
        ):
            ids.append("phase3:support-unassessed")
        traces.append(_trace(original, ids, excluded_only=not rows))
    rr["retrieval_traces"] = traces

    if nonapp_contract:
        body = contract["contract"]
        body["applicability"] = {"op": "CONST", "value": False}
        body["non_applicability"] = {"op": "ATOM", "atom_id": "A1"}
    return rr, contract


def add_completed_procedure(
    rr: dict,
    contract: dict,
    *,
    need_id: str,
    purpose: str,
) -> dict:
    before = runner.build_layer7_v2(requirement_result=rr, contract=contract)
    execution = procedure_executor_v2.execute_targeted_complete(
        requirement_result=before["requirement_result"],
        procedure_plan=before["procedure"],
        need_id=need_id,
        coverage_purpose=purpose,
        action_id=f"phase3-action-{need_id}",
        goal_id=f"phase3-goal-{need_id}",
    )
    artifact = execution["targeted_coverage_procedure_artifacts"][0]
    if artifact["completion_status"] != "COMPLETE":
        raise AssertionError((need_id, artifact.get("reason_codes")))
    rr.setdefault("targeted_coverage_procedure_artifacts", []).append(artifact)
    return artifact


def root_and_outcome(
    relations: list[str],
    *,
    complete_needs: list[tuple[str, str]] = [],
    **kwargs: Any,
) -> tuple[dict, dict, dict, dict]:
    rr, contract = fixture(relations, **kwargs)
    artifacts = []
    for need_id, purpose in complete_needs:
        artifacts.append(
            add_completed_procedure(
                rr, contract, need_id=need_id, purpose=purpose
            )
        )
    root = runner.build_layer7_v2(requirement_result=rr, contract=contract)
    outcome, fold = runner.build_outcome_and_fold(root, contract)
    return root, outcome, fold, {row["need_id"]: row for row in artifacts}


class Validation:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def run(self, name: str, category: str, fn: Callable[[], Any]) -> None:
        try:
            detail = fn()
            self.rows.append(
                {"name": name, "category": category, "status": "PASS", "detail": detail}
            )
        except Exception as exc:
            self.rows.append(
                {
                    "name": name,
                    "category": category,
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--v1-tree-digest", required=True)
    parser.add_argument("--expected-v1-tree-digest", required=True)
    args = parser.parse_args()

    attempted_calls: list[dict] = []
    original_api = core.deepseek_json

    def reject_api(*_args: Any, **kwargs: Any) -> dict:
        attempted_calls.append({"model": kwargs.get("model")})
        raise RuntimeError("PHASE3_ZERO_API_GUARD")

    core.deepseek_json = reject_api
    validation = Validation()

    def compliant() -> dict:
        root, outcome, _, _ = root_and_outcome(
            ["SUPPORT"],
            support_extra_unassessed=True,
            complete_needs=[("ER1.attack", "CONTRADICTION_COUNTERCHECK")],
        )
        report = root["proof"]["requirement_reports"][0]
        support_need = next(
            row for row in root["coverage"]["need_reports"] if row["need_id"] == "ER1.support"
        )
        require(outcome["common_internal_outcome"] == "PROVEN_COMPLIANT", "compliant not reachable")
        require(report["support_proof"]["accepted_direction"] is True, "support not accepted")
        require(support_need["targeted_procedure_complete"] is False, "own universe falsely exhausted")
        return {"outcome": outcome["common_internal_outcome"], "support_own_complete": False}

    def non_compliant() -> dict:
        _, outcome, _, _ = root_and_outcome(
            ["ATTACK"],
            complete_needs=[("ER1.support", "CONTRADICTION_COUNTERCHECK")],
        )
        require(outcome["common_internal_outcome"] == "PROVEN_NON_COMPLIANT", "non-compliant not reachable")
        return {"outcome": outcome["common_internal_outcome"]}

    def absence_no_violation() -> dict:
        _, outcome, _, _ = root_and_outcome(
            [], complete_needs=[("ER1.support", "CONTRADICTION_COUNTERCHECK")]
        )
        require(outcome["common_internal_outcome"] != "PROVEN_NON_COMPLIANT", "absence became violation")
        return {"outcome": outcome["common_internal_outcome"]}

    def missing_no_na() -> dict:
        _, outcome, _, _ = root_and_outcome(
            [], nonapp_contract=True,
            complete_needs=[("ER1.attack", "NON_APPLICABILITY_COUNTERCHECK")],
        )
        require(outcome["common_internal_outcome"] != "PROVEN_NOT_APPLICABLE", "missing became N/A")
        return {"outcome": outcome["common_internal_outcome"]}

    def na_without_countercheck() -> dict:
        _, outcome, _, _ = root_and_outcome(["SUPPORT"], nonapp_contract=True)
        require(outcome["common_internal_outcome"] != "PROVEN_NOT_APPLICABLE", "N/A lacked countercheck")
        return {"outcome": outcome["common_internal_outcome"]}

    def na_with_countercheck() -> dict:
        root, outcome, _, _ = root_and_outcome(
            ["SUPPORT"], nonapp_contract=True,
            complete_needs=[("ER1.attack", "NON_APPLICABILITY_COUNTERCHECK")],
        )
        need = next(row for row in root["coverage"]["need_reports"] if row["need_id"] == "ER1.attack")
        require(need["coverage_purpose"] == "NON_APPLICABILITY_COUNTERCHECK", "wrong N/A purpose")
        require(outcome["common_internal_outcome"] == "PROVEN_NOT_APPLICABLE", "N/A not reachable")
        return {"outcome": outcome["common_internal_outcome"]}

    def temporal_not_required() -> dict:
        root, _, _, _ = root_and_outcome(
            ["SUPPORT"],
            complete_needs=[("ER1.attack", "CONTRADICTION_COUNTERCHECK")],
        )
        classification = root["gate_applicability"]["temporal_classifications"][0]
        proof = root["proof"]["requirement_reports"][0]["support_proof"]
        require(classification["state"] == "TEMPORAL_NOT_REQUIRED", "not-required classification lost")
        require(proof["temporal_status"] == "NOT_REQUIRED", "not-required collapsed to PASS")
        return {"classification": classification["state"], "proof_status": proof["temporal_status"]}

    def temporal_required_unknown() -> dict:
        _, outcome, _, _ = root_and_outcome(
            ["SUPPORT"], temporal_required=True,
            complete_needs=[("ER1.attack", "CONTRADICTION_COUNTERCHECK")],
        )
        require(outcome["common_internal_outcome"] == "UNKNOWN", "missing temporal assessment passed")
        return {"outcome": outcome["common_internal_outcome"]}

    def plan_not_actual() -> dict:
        root, outcome, _, _ = root_and_outcome(
            ["SUPPORT"], plan_statement=True,
            complete_needs=[("ER1.attack", "CONTRADICTION_COUNTERCHECK")],
        )
        assessment = root["requirement_result"]["alignments"][0]["information_reliability"]
        require(assessment["status"] == "UNRESOLVED", "plan became reliable actual fact")
        require(outcome["common_internal_outcome"] == "UNKNOWN", "plan proved performance")
        return {"reliability": assessment["status"], "outcome": outcome["common_internal_outcome"]}

    def conflict() -> dict:
        root, outcome, _, _ = root_and_outcome(
            ["SUPPORT", "ATTACK"],
            complete_needs=[
                ("ER1.support", "CONTRADICTION_COUNTERCHECK"),
                ("ER1.attack", "CONTRADICTION_COUNTERCHECK"),
            ],
        )
        state = root["proof"]["requirement_reports"][0]["accepted_state"]
        require(state == "BOTH", "support/attack conflict suppressed")
        require(outcome["common_internal_outcome"] == "CONFLICTING", "conflict outcome lost")
        return {"accepted_state": state, "outcome": outcome["common_internal_outcome"]}

    def incomplete_no_coverage() -> dict:
        rr, contract = fixture(["SUPPORT"], support_extra_unassessed=True)
        initial = runner.build_layer7_v2(requirement_result=rr, contract=contract)
        execution = procedure_executor_v2.execute_targeted_complete(
            requirement_result=initial["requirement_result"], procedure_plan=initial["procedure"],
            need_id="ER1.support", coverage_purpose="POSITIVE_EXISTENCE_PROOF",
            action_id="phase3-incomplete", goal_id="phase3-incomplete-goal",
        )
        artifact = execution["targeted_coverage_procedure_artifacts"][0]
        require(artifact["completion_status"] == "INCOMPLETE", "incomplete procedure marked complete")
        rr["targeted_coverage_procedure_artifacts"] = [artifact]
        coverage = runner.build_layer7_v2(requirement_result=rr, contract=contract)["coverage"]
        own = next(row for row in coverage["need_reports"] if row["need_id"] == "ER1.support")
        require(own["targeted_procedure_complete"] is False, "incomplete artifact set coverage")
        return {"completion": artifact["completion_status"], "coverage": own["targeted_procedure_complete"]}

    def parse_gap_invalidates() -> dict:
        _, _, _, artifacts = root_and_outcome(
            ["SUPPORT"], complete_needs=[("ER1.attack", "CONTRADICTION_COUNTERCHECK")]
        )
        mutated = copy.deepcopy(artifacts["ER1.attack"])
        mutated["parse_readability_gaps"] = ["unreadable-page"]
        rehash_procedure(mutated)
        valid, reasons = procedure_executor_v2.validate_procedure_artifact(mutated)
        require(not valid and "COMPLETE_WITH_MATERIAL_PARSE_GAP" in reasons, "parse gap mutation accepted")
        return {"detected": reasons}

    def blocker_routes() -> dict:
        scenarios = [
            root_and_outcome(["SUPPORT"], temporal_required=True)[0],
            root_and_outcome([])[0],
            root_and_outcome(["SUPPORT"], plan_statement=True)[0],
        ]
        emitted = {
            code for root in scenarios for row in root["proof"]["requirement_reports"] for code in row["failure_codes"]
        }
        routes = {
            "COVERAGE_INCOMPLETE", "NO_DIRECT_SUPPORT_BASIS", "NO_EXPLICIT_VIOLATION_BASIS",
            "NO_TEMPORAL_BASIS_ROWS", "NO_RELIABILITY_BASIS_ROWS", "TEMPORAL_SCOPE_UNRESOLVED",
            "INFORMATION_RELIABILITY_UNRESOLVED",
        }
        terminal = {"TEMPORAL_REQUIREMENT_UNRESOLVED"}
        missing = sorted(emitted - routes - terminal)
        require(not missing, f"unrouted blockers: {missing}")
        return {"emitted": sorted(emitted), "terminal": sorted(emitted & terminal)}

    def no_goal_change_stop() -> dict:
        rr, contract = fixture(["SUPPORT"], support_extra_unassessed=True)
        attack_trace = next(
            row for row in rr["retrieval_traces"] if row.get("direction") == "ATTACK"
        )
        extra = {
            "evidence_id": "phase3:attack-unassessed",
            "identity_use_decision": "ADMIT_DIRECT",
            "identity_reason_codes": [],
            "retrieval_methods": ["RAW_LEXICAL"],
        }
        attack_trace["candidate_universe"].append(copy.deepcopy(extra))
        attack_trace["candidates"].append(copy.deepcopy(extra))
        before = runner.build_layer7_v2(requirement_result=rr, contract=contract)
        after, _, bundle, _, diff = runner.run_repair_round_v2(
            before=before, contract=contract, policy=load(POLICY), round_index=1,
            allow_model_actions=False,
        )
        decision = stop_gate.decide_after_round(
            evaluation_diff=diff, repair_round=bundle, round_index=1, max_rounds=1
        )
        require("NO_GOAL_STATE_CHANGE" in decision["stop_reasons"], "ineffective round did not stop")
        require(decision["allow_next_repair_round"] is False, "stop allowed another round")
        return {"stop_reasons": decision["stop_reasons"], "after": after["proof"]["internal_outcome"]}

    def v1_unchanged() -> dict:
        require(args.v1_tree_digest == args.expected_v1_tree_digest, "V1 tree changed")
        return {"digest": args.v1_tree_digest}

    required_tests = [
        ("explicit_positive_reaches_proven_compliant_without_own_exhaustion", compliant),
        ("explicit_adverse_reaches_proven_non_compliant", non_compliant),
        ("absence_only_cannot_reach_violation", absence_no_violation),
        ("missing_document_cannot_reach_na", missing_no_na),
        ("positive_na_without_countercheck_cannot_reach_na", na_without_countercheck),
        ("positive_na_with_countercheck_reaches_na", na_with_countercheck),
        ("non_temporal_is_not_required_not_pass", temporal_not_required),
        ("temporal_without_assessment_remains_unknown", temporal_required_unknown),
        ("source_provenance_does_not_make_plan_actual", plan_not_actual),
        ("support_and_attack_remain_conflicting", conflict),
        ("incomplete_targeted_procedure_cannot_set_coverage", incomplete_no_coverage),
        ("material_parse_gap_invalidates_complete_procedure", parse_gap_invalidates),
        ("every_emitted_blocker_has_action_or_terminal_route", blocker_routes),
        ("no_goal_state_change_stops_complete_ineffective_round", no_goal_change_stop),
        ("v1_tree_hash_unchanged", v1_unchanged),
    ]
    for name, fn in required_tests:
        validation.run(name, "REQUIRED", fn)

    def execution_mutation(action_type: str) -> dict:
        initial_dir = Path(
            "results_v2/production_run_v1_shards/shard-00/tasks/"
            "case-001/CP17/initial"
        )
        root = {
            "requirement_result": load(initial_dir / "requirement_result.json"),
            "coverage": load(initial_dir / "layer7/coverage_v1_1.json"),
            "procedure": load(initial_dir / "layer7/procedure_objective_v1.json"),
            "open_goals": load(initial_dir / "layer7/open_goals_v1.json"),
        }
        plan = dispatcher.build_repair_plan(
            root=root, policy=load(POLICY), round_index=1, allow_model_actions=False
        )
        bundle = dispatcher.execute_repair_plan(plan=plan, root=root)
        mutated = copy.deepcopy(bundle)
        mutated["action_executions"] = [
            row for row in mutated["action_executions"] if row.get("action_type") != action_type
        ]
        valid, reasons = dispatcher.validate_repair_round_bundle(plan=plan, bundle=mutated)
        require(not valid, f"removing {action_type} execution escaped detection")
        return {"detected": reasons}

    def forged_coverage() -> dict:
        rr, contract = fixture([])
        bundle = coverage_policy_v2.evaluate_coverage_bundle(rr, contract_bundle=contract)
        mutated = copy.deepcopy(bundle)
        mutated["coverage_complete"] = True
        mutated["proof_coverage_complete"] = True
        mutated["bundle_sha256"] = coverage_policy_v2.sha256_json(
            {k: v for k, v in mutated.items() if k != "bundle_sha256"}
        )
        valid, reasons = coverage_policy_v2.validate_coverage_bundle(
            rr, contract_bundle=contract, bundle=mutated
        )
        require(not valid, "coverage true without basis escaped detection")
        return {"detected": reasons}

    def temporal_state_mutation(new_state: Any, new_relation: str) -> dict:
        root, _, _, _ = root_and_outcome(["SUPPORT"])
        rr = copy.deepcopy(root["requirement_result"])
        rr["alignments"][0]["temporal_requirement_classification"]["state"] = new_state
        rr["alignments"][0]["temporal_relation"] = new_relation
        valid, reasons = applicability_v2.validate_gate_applicability(
            requirement_result=rr, contract_bundle=load(BASE_CONTRACT)
        )
        require(not valid, f"temporal mutation {new_state!r} escaped detection")
        return {"detected": reasons}

    def remove_counter_scan() -> dict:
        _, _, _, artifacts = root_and_outcome(
            ["SUPPORT"], complete_needs=[("ER1.attack", "CONTRADICTION_COUNTERCHECK")]
        )
        mutated = copy.deepcopy(artifacts["ER1.attack"])
        mutated.pop("counterevidence_scan_result", None)
        rehash_procedure(mutated)
        valid, reasons = procedure_executor_v2.validate_procedure_artifact(mutated)
        require(not valid, "missing counterevidence scan escaped detection")
        return {"detected": reasons}

    mutations = [
        ("remove_temporal_execution", lambda: execution_mutation("RESOLVE_TIME")),
        ("remove_reliability_execution", lambda: execution_mutation("ASSESS_INFORMATION_RELIABILITY")),
        ("set_coverage_true_without_basis", forged_coverage),
        ("convert_not_required_to_pass", lambda: temporal_state_mutation("TEMPORAL_NOT_REQUIRED", "PASS")),
        ("convert_unknown_to_false", lambda: temporal_state_mutation(False, "FAIL")),
        ("remove_counterevidence_scan", remove_counter_scan),
    ]
    for name, fn in mutations:
        validation.run(name, "MUTATION_DETECTION", fn)

    core.deepseek_json = original_api
    status = "PASS" if all(row["status"] == "PASS" for row in validation.rows) and not attempted_calls else "FAIL"
    report = {
        "schema": "freca-production-v2-phase3-validation-v1",
        "phase": "PHASE_3_SYNTHETIC_AND_MUTATION_VALIDATION",
        "phase4_replay_started": False,
        "fixture_basis": str(BASE_RR.resolve()),
        "required_test_count": sum(row["category"] == "REQUIRED" for row in validation.rows),
        "mutation_test_count": sum(row["category"] == "MUTATION_DETECTION" for row in validation.rows),
        "results": validation.rows,
        "api_call_audit": {
            "attempted_calls": attempted_calls,
            "attempted_call_count": len(attempted_calls),
            "api_call_count": 0,
            "zero_api_pass": not attempted_calls,
        },
        "v1_tree_digest": args.v1_tree_digest,
        "status": status,
    }
    report["report_sha256"] = sha256_json(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Phase 3 validation: {status}")
    for row in validation.rows:
        print(row["status"], row["category"], row["name"])
        if row["status"] == "FAIL":
            print(" ", row["error"])
    if status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
