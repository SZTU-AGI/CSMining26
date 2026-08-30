#!/usr/bin/env python3
from __future__ import annotations

import copy

import coverage_policy_v2 as coverage
import evidence_nature_v1 as nature
import evidence_reasoning_v2 as reasoning
import proof_standard_v1_1 as proof
from fact_candidate_v1 import build_fact_candidates


def _pair(requirement: dict, text: str, relation: str, exact_quote: str):
    fact = build_fact_candidates("source.xlsx:Sheet:R1", text)[0]
    need = "ER1.support" if relation == "SUPPORT" else "ER1.attack"
    pair = {
        "requirement": requirement,
        "evidence_id": "source.xlsx:Sheet:R1#" + fact["fact_candidate_id"],
        "parent_evidence_id": "source.xlsx:Sheet:R1",
        "fact_candidate_id": fact["fact_candidate_id"],
        "fact_candidate": fact,
        "evidence_text": fact["quote"],
        "parent_evidence_text": text,
        "retrieval_need_ids": [need],
        "identity_relation_to_case": "CORE_SELF_EXACT",
        "identity_use_decision": "ADMIT_DIRECT",
        "identity_decisive_proof_eligible": True,
        "identity_reason_code": "SELFTEST",
    }
    raw = {
        "requirement_id": "ER1",
        "evidence_id": pair["evidence_id"],
        "relation": relation,
        "exact_quote": exact_quote,
        "reason_code": "SELFTEST",
        "reason": "selftest",
    }
    return reasoning.validate_alignment(raw, pair)


def main() -> int:
    cp1 = {
        "requirement_id": "ER1",
        "atom_id": "A1",
        "decisiveness": "DECISIVE",
        "proposition_to_establish": (
            "The establishment is operating within its registered operations and, "
            "where applicable, its registered functions to prepare plants or plant "
            "products for export."
        ),
    }
    cp26 = {
        "requirement_id": "ER1",
        "atom_id": "A1",
        "decisiveness": "DECISIVE",
        "proposition_to_establish": (
            "Pest control stations and traps are fit for purpose and in good working order."
        ),
    }
    cp15 = {
        "requirement_id": "ER1",
        "atom_id": "A1",
        "decisiveness": "DECISIVE",
        "proposition_to_establish": (
            "Screening of plants or plant products, if carried out at the establishment, "
            "is conducted in a way that is appropriate to manage the risk of contamination "
            "by large contaminants."
        ),
    }

    # 1) The exact quote may omit the equipment subject; FactCandidate context restores it.
    row = _pair(
        cp26,
        "10 | Perimeter | Enclosed bait station | 2025-03-14 | Good — bait present, lid secure",
        "SUPPORT",
        "Good — bait present, lid secure",
    )
    assert row["predicate_compatibility"] == "DIRECT", row
    assert row["argument_truth_bearing"] is True, row
    assert row["evidence_nature"]["assertion_mode"]["actual_signal_present"] is True, row

    # 2) A domestic-only 'Not registered' row must NOT attack export-registration scope.
    row = _pair(
        cp1,
        "Drying | High-temperature drying for domestic-grade product | Not registered",
        "ATTACK",
        "Not registered",
    )
    assert row["predicate_compatibility"] == "INCOMPATIBLE", row
    assert row["argument_truth_bearing"] is False, row
    assert row["predicate_compatibility_reason"] == "DOMESTIC_ONLY_OPERATION_NOT_EXPORT_SCOPE_VIOLATION", row

    # 3) The same terse quote is direct when its grounded fact has an explicit export nexus.
    row = _pair(
        cp1,
        "Export fumigation | Treatment of wheat for export | Not registered",
        "ATTACK",
        "Not registered",
    )
    assert row["predicate_compatibility"] == "DIRECT", row
    assert row["argument_truth_bearing"] is True, row
    assert row["fact_candidate"]["polarity"] == "ADVERSE", row
    assert row["fact_candidate"]["modality"] == "ACTUAL", row
    assert proof._is_explicit_adverse_fact(row) is True, row

    # 4) Explicit adverse proof is existential: it must not require exhausting the
    # entire ATTACK candidate universe before admitting the counterexample.
    rr = {
        "cp_id": "CP1",
        "case_id": "case-test",
        "evidence_requirement_plan": {"requirements": [cp1]},
        "retrieval_traces": [
            {"need_id": "ER1.support", "requirement_id": "ER1", "direction": "SUPPORT"},
            {"need_id": "ER1.attack", "requirement_id": "ER1", "direction": "ATTACK"},
        ],
        "alignments": [row],
        "targeted_coverage_procedure_artifacts": [],
    }
    original = coverage.coverage_v1.evaluate_coverage_bundle
    try:
        coverage.coverage_v1.evaluate_coverage_bundle = lambda _rr: {
            "discovery_complete": False,
            "need_reports": [
                {"need_id": "ER1.support", "procedure_complete": False, "limiting_factors": []},
                {"need_id": "ER1.attack", "procedure_complete": False, "limiting_factors": []},
            ],
        }
        bundle = coverage.evaluate_coverage_bundle(rr, contract_bundle={})
    finally:
        coverage.coverage_v1.evaluate_coverage_bundle = original
    assert bundle["directional_proof_coverage"]["ER1"]["ATTACK"] is True, bundle
    assert bundle["directional_proof_coverage"]["ER1"]["SUPPORT"] is False, bundle

    # 5) CP15: procedure text is still not an actual screening event.  V6.2 must
    # not manufacture applicability or vacuous compliance from missing evidence.
    row = _pair(
        cp15,
        "If required, screening will be carried out using the scalper before loading.",
        "SUPPORT",
        "screening will be carried out using the scalper",
    )
    assert row["argument_truth_bearing"] is False, row
    assert row["predicate_compatibility"] in {"CORROBORATIVE", "UNRESOLVED"}, row

    print("V6.2 fact-context / explicit-adverse self-tests: PASS (5/5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
