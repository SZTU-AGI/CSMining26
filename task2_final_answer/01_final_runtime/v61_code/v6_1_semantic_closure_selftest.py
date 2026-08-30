#!/usr/bin/env python3
"""Focused regression tests for the V6.1 semantic/proof-closure fixes."""

from __future__ import annotations

import copy

import coverage_policy_v2
import evidence_nature_v1 as nature
import evidence_reasoning_v2 as reasoning
import proof_gate_applicability_v2 as gate


def _req(text: str, rid: str = "ER1") -> dict:
    return {
        "requirement_id": rid,
        "atom_id": "A1",
        "decisiveness": "DECISIVE",
        "proposition_to_establish": text,
        "query_sources": [],
    }


def _pair(requirement: dict, quote: str, *, evidence_id: str = "doc:P1#fc-1") -> dict:
    parent = evidence_id.split("#", 1)[0]
    fact_id = evidence_id.split("#", 1)[1]
    return {
        "requirement": requirement,
        "evidence_id": evidence_id,
        "parent_evidence_id": parent,
        "fact_candidate_id": fact_id,
        "fact_candidate": {
            "fact_candidate_id": fact_id,
            "parent_evidence_id": parent,
            "source_id": "doc",
            "quote": quote,
            "quote_start": 0,
            "quote_end": len(quote),
            "grounding_valid": True,
        },
        "evidence_text": quote,
        "parent_evidence_text": quote,
        "retrieval_need_ids": [requirement["requirement_id"] + ".support"],
        "identity_relation_to_case": "CORE_SELF_EXACT",
        "identity_use_decision": "ADMIT_DIRECT",
        "identity_decisive_proof_eligible": True,
        "identity_reason_code": "CORE_SELF_EXACT",
    }


def _validated(requirement: dict, quote: str, relation: str = "SUPPORT") -> dict:
    pair = _pair(requirement, quote)
    row = reasoning.validate_alignment(
        {
            "requirement_id": requirement["requirement_id"],
            "evidence_id": pair["evidence_id"],
            "relation": relation,
            "exact_quote": quote,
            "reason_code": "SELFTEST",
            "reason": "semantic closure regression test",
        },
        pair,
    )
    # The row-level typed result is authoritative; sync the cached FactCandidate
    # so downstream deterministic reliability code sees the same typing.
    row["fact_candidate"]["evidence_nature"] = copy.deepcopy(row["evidence_nature"])
    assertion = row["evidence_nature"].get("assertion_mode") or {}
    row["fact_candidate"]["modality"] = assertion.get("modality", "UNKNOWN")
    row["fact_candidate"]["speech_act"] = assertion.get("speech_act", "UNKNOWN")
    return row


def _trace(need_id: str, direction: str, candidate_id: str) -> dict:
    return {
        "need_id": need_id,
        "requirement_id": "ER1",
        "atom_id": "A1",
        "direction": direction,
        "priority_class": "DECISIVE" if direction == "SUPPORT" else "COUNTEREVIDENCE",
        "query_facets": ["selftest"],
        "query_variants": [{"variant_id": need_id + ".v1", "query": "selftest"}],
        "coverage_requirement": "CANDIDATE_DISCOVERY",
        "raw_lexical_scan": {
            "scan_chunk_count": 1,
            "scan_complete": True,
            "generated_union_count": 1,
            "candidate_generation_mode": "TOP_K_PER_VARIANT",
        },
        "typed_fact_scan": {
            "scan_chunk_count": 1,
            "matched_count": 1,
            "full_case_scan": True,
            "target_kinds": ["EQUIPMENT_FITNESS"],
            "wanted_natures": ["EQUIPMENT_CONDITION"],
        },
        "structure_scan": {
            "mode": "SEED_NEIGHBOUR_RESCUE_ONLY",
            "scan_chunk_count": 1,
            "seed_count": 1,
            "generated_candidate_count": 0,
            "executed": True,
        },
        "candidate_universe_persisted": True,
        "candidate_universe_ids": [candidate_id],
        "candidate_universe_count": 1,
        "candidate_universe": [{"evidence_id": candidate_id, "retrieval_methods": ["RAW_LEXICAL"]}],
        "model_context_candidate_ids": [candidate_id],
        "model_context_count": 1,
        "candidate_count_checked_by_model": 1,
        "retrieval_scan_chunk_count": 1,
        "candidates": [{"evidence_id": candidate_id, "text": "selftest"}],
    }


def test_requirement_profiles() -> None:
    cases = {
        "registration": (
            "The establishment is operating within its registered operations and, where applicable, "
            "its registered functions to prepare plants or plant products for export.",
            nature.TARGET_REGISTRATION_SCOPE,
        ),
        "equipment": (
            "Where applicable, pest control stations and traps are fit for purpose and in good working order.",
            nature.TARGET_EQUIPMENT_FITNESS,
        ),
        "risk": (
            "The risk of contamination or infestation is maintained at an acceptable level through "
            "phytosanitary requirements.",
            nature.TARGET_RISK_CONTROL_STATE,
        ),
    }
    for name, (text, expected) in cases.items():
        profile = nature.infer_requirement_predicate_profile(_req(text))
        assert profile["target_kinds"] == [expected], (name, profile)
        assert profile["profile_source"] == "PROPOSITION", (name, profile)


def test_registration_scope_typing() -> None:
    requirement = _req(
        "The establishment is operating within its registered operations and registered functions."
    )
    direct = (
        "This establishment is registered to conduct export operations for wheat. "
        "All export activities carried out at this premises are within the registered scope as approved by DAFF."
    )
    auth_only = (
        "The establishment is a registered export establishment and is registered to handle and export wheat."
    )
    assert nature.assess_alignment_compatibility(
        requirement, direct, "SUPPORT", "CORROBORATION_ONLY"
    )["compatibility_decision"] == nature.DIRECT
    assert nature.assess_alignment_compatibility(
        requirement, auth_only, "SUPPORT", "CORROBORATION_ONLY"
    )["compatibility_decision"] == nature.CORROBORATIVE



def test_typed_retrieval_uses_requirement_proposition() -> None:
    requirement = _req(
        "The establishment is operating within its registered operations and registered functions."
    )
    requirement["query_sources"] = [
        {"source": "CP", "candidate_id": None, "quote": requirement["proposition_to_establish"]},
        {
            "source": "RULES",
            "candidate_id": "rule:last",
            "quote": "An assessment must be carried out at an establishment registered for export operations.",
        },
    ]
    plan = {"cp_id": "CP1", "requirements": [requirement]}
    needs = reasoning.build_retrieval_needs(plan)
    assert needs[0]["proposition_to_establish"] == requirement["proposition_to_establish"]
    chunks = [
        {
            "evidence_id": "doc:P1",
            "text": (
                "All export activities carried out at this premises are within the registered scope "
                "as approved by DAFF."
            ),
        }
    ]
    traces = reasoning.retrieve_requirement_candidates(chunks, needs, top_k=4)
    for trace in traces:
        assert trace["typed_fact_scan"]["target_kinds"] == [nature.TARGET_REGISTRATION_SCOPE], trace


def test_corroborative_never_truth_bearing() -> None:
    requirement = _req(
        "Where applicable, pest control stations and traps are fit for purpose and in good working order."
    )
    row = _validated(requirement, "The pest-control program requires monthly inspection of all bait stations.")
    assert row["predicate_compatibility"] == nature.CORROBORATIVE, row
    assert row["argument_admission_channel"] == "CONDITIONAL", row
    assert row["argument_truth_bearing"] is False, row


def test_subject_and_activity_anchors() -> None:
    equipment = _req(
        "Where applicable, pest control stations and traps are fit for purpose and in good working order."
    )
    genuine = _validated(
        equipment,
        "All bait stations were checked and found serviceable and in good working order.",
    )
    assert genuine["predicate_compatibility"] == nature.DIRECT, genuine
    assert genuine["argument_truth_bearing"] is True, genuine

    screening = _req(
        "If screening of prescribed plants or plant products is carried out, the screening is appropriate "
        "to manage the risk of large contaminants."
    )
    screened = _validated(
        screening,
        "Screening records show the 5 mm screen was used for this lot.",
    )
    assert screened["predicate_compatibility"] == nature.DIRECT, screened
    unrelated = _validated(
        screening,
        "The cleaning inspection was performed on 12 May 2025.",
    )
    assert unrelated["predicate_compatibility"] != nature.DIRECT, unrelated
    assert unrelated["argument_truth_bearing"] is False, unrelated



def test_current_state_witness_can_support_ongoing_condition() -> None:
    requirement = _req(
        "The establishment is maintained to minimise contamination, infestation and pest harbourage during export operations."
    )
    current = _validated(
        requirement,
        "Current pest status: all monitoring indicators within acceptable range. No evidence of bird entry.",
    )
    assert current["predicate_compatibility"] == nature.DIRECT, current
    assert current["argument_truth_bearing"] is True, current

    event_only = _validated(
        requirement,
        "Cleaning was performed on 14 May 2025.",
    )
    assert event_only["predicate_compatibility"] == nature.CORROBORATIVE, event_only
    assert event_only["argument_truth_bearing"] is False, event_only


def test_directional_existence_not_blocked_by_countercheck_closure() -> None:
    requirement = _req(
        "Where applicable, pest control stations and traps are fit for purpose and in good working order."
    )
    quote = "All bait stations were checked and found serviceable and in good working order."
    row = _validated(requirement, quote)
    rr = {
        "schema": "selftest",
        "cp_id": "CP26",
        "case_id": "case-selftest",
        "evidence_requirement_plan": {"cp_id": "CP26", "requirements": [requirement]},
        "retrieval_traces": [
            _trace("ER1.support", "SUPPORT", row["evidence_id"]),
            _trace("ER1.attack", "ATTACK", "doc:P2"),
        ],
        "alignments": [row],
    }
    contract = {"contract": {"atoms": [], "burden_rules": []}}
    assessed, applicability = gate.apply_gate_assessments(
        requirement_result=rr,
        contract_bundle=contract,
    )
    assert applicability["temporal_classifications"][0]["state"] == "TEMPORAL_REQUIRED"
    direct = next(x for x in assessed["alignments"] if x["argument_truth_bearing"])
    assert direct["temporal_relation"] == "IN_SCOPE", direct
    assert (direct.get("information_reliability") or {}).get("status") == "PASS", direct

    coverage = coverage_policy_v2.evaluate_coverage_bundle(
        assessed,
        contract_bundle=contract,
    )
    support_report = next(x for x in coverage["need_reports"] if x["need_id"] == "ER1.support")
    assert support_report["directional_discovery_complete"] is True, support_report
    assert support_report["countercheck_complete"] is False, support_report
    assert support_report["proof_coverage_pass"] is True, support_report
    assert "COUNTEREVIDENCE_CLOSURE_OPEN_NONBLOCKING" in support_report["limiting_factors"], support_report

    proof = gate.evaluate_proof_standard_bundle(assessed, coverage)
    req_report = proof["requirement_reports"][0]
    assert req_report["accepted_state"] == "TRUE", req_report
    assert req_report["support_proof"]["accepted_direction"] is True, req_report


def main() -> int:
    tests = [
        test_requirement_profiles,
        test_registration_scope_typing,
        test_typed_retrieval_uses_requirement_proposition,
        test_corroborative_never_truth_bearing,
        test_subject_and_activity_anchors,
        test_current_state_witness_can_support_ongoing_condition,
        test_directional_existence_not_blocked_by_countercheck_closure,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"PASS V6.1 semantic closure self-test ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
