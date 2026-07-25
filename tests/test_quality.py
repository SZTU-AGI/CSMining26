from __future__ import annotations

from freca.llm import ReplayJsonClient
from freca.models import (
    Applicability,
    AuditDecision,
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    RetrievalBundle,
    RetrievalHit,
    SourceLocation,
    SourceType,
    VerificationResult,
    VerificationStatus,
    Verdict,
)
from freca.quality import (
    arbitrate_checkpoint,
    find_consistency_issues,
    should_arbitrate,
    validate_citations,
    verify_decision,
)


def _chunk(chunk_id: str, *, case_id: int | None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        re_number="RE-X" if case_id else None,
        track=1 if case_id else None,
        source_id="t1" if case_id else "policy",
        source_file="t1.docx" if case_id else "policy.pdf",
        source_type=SourceType.DOCX if case_id else SourceType.PDF,
        location=SourceLocation(paragraph_index=0) if case_id else SourceLocation(page=1),
        content="registration evidence" if case_id else "registration requirement",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="d" * 64,
    )


def _bundle() -> RetrievalBundle:
    return RetrievalBundle(
        case_id=1,
        cp_id="CP1",
        policy_hits=[RetrievalHit(chunk=_chunk("p1", case_id=None), score=1, rank=1)],
        evidence_hits=[RetrievalHit(chunk=_chunk("e1", case_id=1), score=1, rank=1)],
        rounds=[],
        complete=True,
        stop_reason="complete",
    )


def _checkpoint() -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id="CP1",
        element_id=1,
        element_title="Element-1",
        section_title="1.1 Export operations",
        text="The operation is within registration.",
        source_file="cp.xlsx",
        cell="A3",
    )


def _decision(**updates) -> AuditDecision:
    payload = {
        "case_id": 1,
        "cp_id": "CP1",
        "applicability": Applicability.APPLICABLE,
        "regulatory_requirement": "Registration covers operations.",
        "policy_citations": ["p1"],
        "supporting_evidence": ["e1"],
        "contrary_evidence": [],
        "contradictions": [],
        "verdict": Verdict.COMPLIANT,
        "reasoning_summary": "Supported.",
        "confidence": 0.9,
        "retrieval_complete": True,
        "review_flags": [],
        "shared_facts": {"registration_status": "current"},
    }
    payload.update(updates)
    return AuditDecision(**payload)


def test_citation_validation_blocks_missing_and_wrong_case_references() -> None:
    decision = _decision(policy_citations=["missing-policy"], supporting_evidence=["missing-evidence"])

    result = validate_citations(decision, _bundle())

    assert result.passed is False
    assert any("missing-policy" in error for error in result.errors)
    assert any("missing-evidence" in error for error in result.errors)


def test_verifier_returns_structured_status_and_checks_citations() -> None:
    client = ReplayJsonClient(
        [
            {
                "case_id": 1,
                "cp_id": "CP1",
                "status": "PASS",
                "issues": [],
                "checked_citations": ["p1", "e1"],
            }
        ]
    )

    result = verify_decision(client, _checkpoint(), _bundle(), _decision())

    assert result.status == VerificationStatus.PASS
    assert "p1" in client.requests[0]["user"]


def test_consistency_check_flags_conflicting_shared_fact_values() -> None:
    cp1 = _decision()
    cp2 = _decision(
        cp_id="CP2",
        shared_facts={"registration_status": "suspended"},
    )

    findings = find_consistency_issues([cp1, cp2])

    assert len(findings) == 1
    assert findings[0].fact_key == "registration_status"
    assert set(findings[0].cp_ids) == {"CP1", "CP2"}


def test_blind_arbitration_does_not_send_first_answer_to_second_model() -> None:
    first = _decision(reasoning_summary="UNIQUE_FIRST_MODEL_ANCHOR")
    second_payload = first.model_dump(mode="json")
    second_payload["reasoning_summary"] = "Independent conclusion."
    client = ReplayJsonClient([second_payload])

    result = arbitrate_checkpoint(client, _checkpoint(), _bundle(), first)

    assert result.agreement is True
    assert "UNIQUE_FIRST_MODEL_ANCHOR" not in client.requests[0]["user"]


def test_low_confidence_or_failed_verification_triggers_arbitration() -> None:
    verification = VerificationResult(
        case_id=1,
        cp_id="CP1",
        status=VerificationStatus.FAIL,
        issues=["unsupported"],
        checked_citations=["p1", "e1"],
    )
    validation = validate_citations(_decision(confidence=0.5), _bundle())

    assert should_arbitrate(
        _decision(confidence=0.5), validation, verification, consistency_findings=[]
    )
