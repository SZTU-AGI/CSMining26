from __future__ import annotations

from freca.agent.escalation import escalated_arbitrate
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
    Verdict,
)


def _checkpoint() -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id="CP1",
        element_id=1,
        element_title="Element-1",
        section_title="1.1 Test",
        text="Test",
        source_file="cp.xlsx",
        cell="A3",
    )


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="p1",
        case_id=None,
        re_number=None,
        track=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=1),
        content="registration covers operations",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="d" * 64,
    )


def _bundle() -> RetrievalBundle:
    return RetrievalBundle(
        case_id=1,
        cp_id="CP1",
        policy_hits=[RetrievalHit(chunk=_chunk(), score=1, rank=1)],
        evidence_hits=[],
        rounds=[],
        complete=True,
        stop_reason="complete",
    )


def _decision(*, verdict: Verdict) -> AuditDecision:
    return AuditDecision(
        case_id=1,
        cp_id="CP1",
        applicability=Applicability.APPLICABLE,
        regulatory_requirement="Requirement",
        policy_citations=["p1"],
        supporting_evidence=[],
        contrary_evidence=[],
        contradictions=[],
        verdict=verdict,
        reasoning_summary="first",
        confidence=0.5,
        retrieval_complete=False,
        review_flags=["low_confidence"],
        shared_facts={},
    )


def test_escalated_returns_agreement_when_blind_matches() -> None:
    first = _decision(verdict=Verdict.COMPLIANT)
    # blind 与 first 一致
    blind_payload = first.model_dump(mode="json")
    blind_payload["reasoning_summary"] = "blind says compliant"
    blind = ReplayJsonClient([blind_payload])
    result = escalated_arbitrate(
        blind_client=blind,
        tiebreaker_client=None,
        checkpoint=_checkpoint(),
        retrieval=_bundle(),
        first_decision=first,
    )
    assert result.resolution == "ACCEPT_AGREEMENT"


def test_escalated_degrades_to_blind_when_tiebreaker_missing() -> None:
    first = _decision(verdict=Verdict.COMPLIANT)
    blind_payload = first.model_dump(mode="json")
    blind_payload["verdict"] = "0"  # 分歧
    blind_payload["applicability"] = "APPLICABLE"
    blind_payload["reasoning_summary"] = "blind disagrees"
    blind = ReplayJsonClient([blind_payload])
    result = escalated_arbitrate(
        blind_client=blind,
        tiebreaker_client=None,
        checkpoint=_checkpoint(),
        retrieval=_bundle(),
        first_decision=first,
    )
    # 没有 tiebreaker → 降级到盲式 → 分歧 → REVIEW
    assert result.resolution == "REVIEW_DISAGREEMENT"
    assert result.agreement is False


def test_escalated_three_way_majority_accepts() -> None:
    first = _decision(verdict=Verdict.COMPLIANT)
    blind_payload = first.model_dump(mode="json")
    blind_payload["verdict"] = "0"  # blind 分歧
    blind_payload["reasoning_summary"] = "blind says non-compliant"
    blind = ReplayJsonClient([blind_payload])
    # tiebreaker 与 first 一致 → 多数票
    tiebreaker_payload = first.model_dump(mode="json")
    tiebreaker_payload["reasoning_summary"] = "tiebreaker agrees"
    tiebreaker = ReplayJsonClient([tiebreaker_payload])

    result = escalated_arbitrate(
        blind_client=blind,
        tiebreaker_client=tiebreaker,
        checkpoint=_checkpoint(),
        retrieval=_bundle(),
        first_decision=first,
    )
    assert result.resolution == "ACCEPT_MAJORITY"


def test_escalated_three_way_tie_returns_review() -> None:
    first = _decision(verdict=Verdict.COMPLIANT)
    blind_payload = first.model_dump(mode="json")
    blind_payload["verdict"] = "0"
    blind_payload["reasoning_summary"] = "blind disagrees"
    blind = ReplayJsonClient([blind_payload])
    # tiebreaker 与 blind 一致 → first vs blind/tiebreaker
    tiebreaker_payload = first.model_dump(mode="json")
    tiebreaker_payload["verdict"] = "0"
    tiebreaker_payload["reasoning_summary"] = "tiebreaker agrees with blind"
    tiebreaker = ReplayJsonClient([tiebreaker_payload])

    result = escalated_arbitrate(
        blind_client=blind,
        tiebreaker_client=tiebreaker,
        checkpoint=_checkpoint(),
        retrieval=_bundle(),
        first_decision=first,
    )
    # first 一票,blind+third 两票 → ACCEPT_MAJORITY
    assert result.resolution == "ACCEPT_MAJORITY"


def test_escalated_three_way_full_tie_returns_review() -> None:
    first = _decision(verdict=Verdict.COMPLIANT)
    blind_payload = first.model_dump(mode="json")
    blind_payload["verdict"] = "0"
    blind_payload["applicability"] = "APPLICABLE"
    blind_payload["reasoning_summary"] = "blind says 0"
    blind = ReplayJsonClient([blind_payload])
    # tiebreaker 也返回 N/A (third)
    tiebreaker_payload = first.model_dump(mode="json")
    tiebreaker_payload["verdict"] = "N/A"
    tiebreaker_payload["applicability"] = "NOT_APPLICABLE"
    tiebreaker_payload["reasoning_summary"] = "tiebreaker says N/A"
    tiebreaker = ReplayJsonClient([tiebreaker_payload])

    result = escalated_arbitrate(
        blind_client=blind,
        tiebreaker_client=tiebreaker,
        checkpoint=_checkpoint(),
        retrieval=_bundle(),
        first_decision=first,
    )
    # 三个全部不同 → THREE_WAY_TIE
    assert result.resolution == "THREE_WAY_TIE"
    assert result.agreement is False