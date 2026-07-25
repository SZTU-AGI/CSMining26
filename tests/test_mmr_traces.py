from __future__ import annotations

import numpy as np

from freca.index.ranking import select_with_mmr_trace
from freca.models import ContentKind, EvidenceChunk, SourceLocation, SourceType


def _chunk(
    chunk_id: str,
    *,
    source_id: str,
    track: int,
    page: int,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=1,
        re_number="RE-X",
        track=track,
        source_id=source_id,
        source_file=f"{source_id}.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(page=page),
        content=chunk_id,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )


def test_source_aware_mmr_records_penalties_and_covers_second_source() -> None:
    candidates = [
        (_chunk("a1", source_id="t3", track=3, page=1), 1.0, {}, np.array([1.0, 0.0])),
        (_chunk("a2", source_id="t3", track=3, page=1), 0.99, {}, np.array([1.0, 0.0])),
        (_chunk("b1", source_id="t7", track=7, page=2), 0.7, {}, np.array([0.0, 1.0])),
    ]

    result = select_with_mmr_trace(
        candidates,
        limit=2,
        lambda_relevance=0.65,
        source_aware=True,
        min_unique_sources=2,
    )

    assert [item[0].chunk_id for item in result.selected] == ["a1", "b1"]
    a2_trace = next(item for item in result.candidate_traces if item["chunk_id"] == "a2")
    assert a2_trace["selected"] is False
    assert a2_trace["reason"] == "limit_reached"
    assert a2_trace["source_penalty"] > 0
    assert a2_trace["track_penalty"] > 0
    assert a2_trace["location_penalty"] > 0


def test_plain_mmr_has_no_source_track_or_location_penalties() -> None:
    candidates = [
        (_chunk("a1", source_id="t3", track=3, page=1), 1.0, {}, np.array([1.0, 0.0])),
        (_chunk("a2", source_id="t3", track=3, page=1), 0.9, {}, np.array([1.0, 0.0])),
    ]

    result = select_with_mmr_trace(
        candidates,
        limit=1,
        lambda_relevance=0.65,
        source_aware=False,
        min_unique_sources=1,
    )

    trace = next(item for item in result.candidate_traces if item["chunk_id"] == "a2")
    assert trace["source_penalty"] == 0
    assert trace["track_penalty"] == 0
    assert trace["location_penalty"] == 0
    assert "max_similarity" in trace


def test_mmr_selection_is_deterministic_on_equal_scores() -> None:
    candidates = [
        (_chunk("b", source_id="s1", track=1, page=1), 1.0, {}, np.array([1.0, 0.0])),
        (_chunk("a", source_id="s2", track=2, page=2), 1.0, {}, np.array([1.0, 0.0])),
    ]

    first = select_with_mmr_trace(candidates, limit=1)
    second = select_with_mmr_trace(list(reversed(candidates)), limit=1)

    assert first.selected[0][0].chunk_id == second.selected[0][0].chunk_id == "a"
