from __future__ import annotations

from dataclasses import dataclass, field

from freca.index import HybridIndex
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
    RetrievalHit,
)
from freca.retrieval import build_initial_queries, retrieve_for_checkpoint


def _chunk(chunk_id: str, content: str, *, case_id: int | None, source_id: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        re_number="RE-X" if case_id else None,
        track=3 if case_id else None,
        source_id=source_id,
        source_file=f"{source_id}.txt",
        source_type=SourceType.DOCX if case_id else SourceType.PDF,
        location=SourceLocation(page=1) if case_id is None else SourceLocation(paragraph_index=0),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
    )


def _checkpoint() -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id="CP22",
        element_id=3,
        element_title="Element-3",
        section_title="3.3 Record keeping",
        text="Records demonstrate the operation of the pest control system.",
        source_file="cp.xlsx",
        cell="V3",
    )


@dataclass
class SequenceAssessor:
    gaps: list[list[str]]
    calls: int = 0

    def assess(self, checkpoint, policy_hits, evidence_hits):
        result = self.gaps[min(self.calls, len(self.gaps) - 1)]
        self.calls += 1
        return result


@dataclass
class RecordingRewriter:
    calls: list[str] = field(default_factory=list)

    def rewrite(self, *, checkpoint, gap, policy_query, evidence_query):
        self.calls.append(gap)
        return f"{policy_query} {gap}", f"{evidence_query} {gap}"


@dataclass
class SequenceIndex:
    responses: list[list[EvidenceChunk]]
    calls: int = 0

    def search(self, query, *, case_id=None, limit=8):
        chunks = self.responses[min(self.calls, len(self.responses) - 1)][:limit]
        self.calls += 1
        return [
            RetrievalHit(chunk=chunk, score=1.0 / rank, rank=rank)
            for rank, chunk in enumerate(chunks, start=1)
        ]


def test_initial_queries_are_derived_from_official_cp_text() -> None:
    policy_query, evidence_query = build_initial_queries(_checkpoint())

    assert _checkpoint().text in policy_query
    assert _checkpoint().text in evidence_query
    assert "applicability" in policy_query
    assert "contradictory evidence" in evidence_query


def test_retrieval_repairs_at_most_twice_and_keeps_separate_budgets() -> None:
    p1 = _chunk("p1", "record keeping requirement applies", case_id=None, source_id="policy")
    p2 = _chunk("p2", "time requirement", case_id=None, source_id="policy")
    e1 = _chunk("e1", "pest control records 2025", case_id=1, source_id="t3")
    e2 = _chunk("e2", "farm plan contradicts records", case_id=1, source_id="t4")
    e3 = _chunk("e3", "additional dated inspection", case_id=1, source_id="t6")
    policy_index = SequenceIndex(
        [[p1], [p1, p2], [p1, p2]],
    )
    case_index = SequenceIndex(
        [[e1], [e1, e2], [e1, e2, e3]],
    )
    assessor = SequenceAssessor([["time"], ["contradiction"], []])
    rewriter = RecordingRewriter()

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=policy_index,
        case_index=case_index,
        assessor=assessor,
        rewriter=rewriter,
        max_repairs=2,
        policy_limit=2,
        evidence_limit=3,
    )

    assert len(result.rounds) == 3
    assert rewriter.calls == ["time", "contradiction"]
    assert len(result.policy_hits) <= 2
    assert len(result.evidence_hits) <= 3
    assert result.complete is True


def test_retrieval_stops_when_a_repair_adds_no_new_chunks() -> None:
    policy_index = HybridIndex(
        [_chunk("p1", "record requirement", case_id=None, source_id="policy")],
        scope="policy",
    )
    case_index = HybridIndex(
        [_chunk("e1", "record evidence", case_id=1, source_id="t3")],
        scope="case",
    )

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=policy_index,
        case_index=case_index,
        assessor=SequenceAssessor([["time"]]),
        rewriter=RecordingRewriter(),
        max_repairs=2,
    )

    assert len(result.rounds) == 2
    assert result.stop_reason == "no_new_chunks"
    assert result.complete is False
