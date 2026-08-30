#!/usr/bin/env python3
from __future__ import annotations
import copy
import structured_witness_v6_3 as sw

def fake_validate(raw, pair):
    return {
        **copy.deepcopy(raw),
        "fact_candidate_id": pair["fact_candidate_id"],
        "fact_candidate": copy.deepcopy(pair["fact_candidate"]),
        "parent_evidence_id": pair["parent_evidence_id"],
        "source_id": (pair.get("fact_candidate") or {}).get("source_id"),
        "evidence_nature": {"evidence_natures": ["ORIGINAL_NATURE"]},
        "information_reliability": {"status": "PASS", "reason_codes": ["PREEXISTING"]},
        "argument_truth_bearing": True,
        "argument_admission_channel": "DIRECT",
    }

orig = sw.evidence_reasoning_v2.validate_alignment
sw.evidence_reasoning_v2.validate_alignment = fake_validate
try:
    req={"requirement_id":"ER1","proposition_to_establish":"dummy"}
    ident={"identity_relation_to_case":"CORE_SELF_EXACT","identity_use_decision":"ADMIT_DIRECT","identity_decisive_proof_eligible":True,"identity_reason_code":"TEST"}
    def make(kind, rel="SUPPORT"):
        return sw._validated_synthetic_alignment(
            requirement=req,
            evidence_id="doc.docx:P1",
            exact_quote="grounded fact",
            semantic_context="grounded fact",
            relation=rel,
            identity=ident,
            reason_code="TEST",
            reason="test",
            derived_from=["doc.docx:P1"],
            witness_key=kind,
            aggregate_kind=kind,
        )
    cp12=make("HOLISTIC_MULTI_SECTION_SUPPORT")
    assert cp12["evidence_nature"]["evidence_natures"] == ["PHYSICAL_DESIGN_FEATURE"]
    assert "information_reliability" not in cp12
    assert cp12.get("structural_reliability_override") == "CP12_ER1_DESIGN_SUPPORT_ONLY"

    for kind, rel in [
        ("REGISTERED_EXPORT_SCOPE_ALL_IN_SCOPE", "SUPPORT"),
        ("REGISTERED_EXPORT_SCOPE_EXPLICIT_COUNTEREXAMPLE", "ATTACK"),
        ("STATION_TABLE_ALL_SATISFACTORY", "SUPPORT"),
        ("STATION_TABLE_EXPLICIT_DEFECT", "ATTACK"),
        ("HOLISTIC_CURRENT_STATE_SUPPORT", "SUPPORT"),
        ("CURRENT_RISK_OUTCOME_SUPPORT", "SUPPORT"),
    ]:
        row=make(kind, rel)
        assert row["evidence_nature"]["evidence_natures"] == ["ORIGINAL_NATURE"], (kind,row["evidence_nature"])
        assert row["information_reliability"]["status"] == "PASS", kind
        assert "structural_reliability_override" not in row, kind
    print("V6.4.3 scoped reliability self-test: PASS (7/7)")
finally:
    sw.evidence_reasoning_v2.validate_alignment = orig
