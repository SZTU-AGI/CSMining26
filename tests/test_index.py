from pathlib import Path

import pytest

from freca.index import HybridIndex, reciprocal_rank_fusion
from freca.models import (
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
)


def _chunk(
    chunk_id: str,
    content: str,
    *,
    case_id: int | None,
    source_id: str,
    track: int | None,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        re_number="RE-WA-2021-0077" if case_id else None,
        track=track,
        source_id=source_id,
        source_file=f"{source_id}.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )


def test_case_index_requires_filter_and_never_returns_another_case() -> None:
    chunks = [
        _chunk("35-a", "bait station inspected", case_id=35, source_id="35-t3", track=3),
        _chunk("100-a", "bait station damaged", case_id=100, source_id="100-t3", track=3),
    ]
    index = HybridIndex(chunks, scope="case")

    with pytest.raises(ValueError, match="case_id is mandatory"):
        index.search("bait station", limit=10)

    hits = index.search("bait station", case_id=35, limit=10)
    assert hits
    assert {hit.chunk.case_id for hit in hits} == {35}


def test_rrf_is_deterministic_and_rewards_agreement() -> None:
    fused = reciprocal_rank_fusion(
        {"bm25": ["a", "b", "c"], "vector": ["b", "a", "d"]},
        k=60,
    )
    assert [item_id for item_id, _ in fused[:2]] == ["a", "b"]
    assert fused[0][1] == fused[1][1]


def test_source_aware_mmr_selects_multiple_sources() -> None:
    chunks = [
        _chunk("a1", "pest control bait station inspection", case_id=1, source_id="t3", track=3),
        _chunk("a2", "pest control bait station inspections", case_id=1, source_id="t3", track=3),
        _chunk("b1", "bait station map near storage shed", case_id=1, source_id="t7", track=7),
    ]
    index = HybridIndex(chunks, scope="case")

    hits = index.search("pest control bait station", case_id=1, limit=2)

    assert len(hits) == 2
    assert len({hit.chunk.source_id for hit in hits}) == 2


def test_index_round_trip_preserves_search_and_provenance(tmp_path: Path) -> None:
    chunks = [
        _chunk("policy-1", "registration must not be suspended", case_id=None, source_id="policy", track=None),
        _chunk("policy-2", "records must be retained", case_id=None, source_id="policy", track=None),
    ]
    path = tmp_path / "policy-index.json"
    HybridIndex(chunks, scope="policy").save(path)

    loaded = HybridIndex.load(path)
    hits = loaded.search("registration suspended", limit=1)

    assert hits[0].chunk.chunk_id == "policy-1"
    assert hits[0].chunk.source_sha256 == "a" * 64
