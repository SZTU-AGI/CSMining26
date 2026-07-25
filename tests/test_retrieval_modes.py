from __future__ import annotations

import numpy as np

from freca.config import RetrievalConfig
from freca.index import HybridIndex
from freca.models import ContentKind, EvidenceChunk, SourceLocation, SourceType


def _chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=1),
        content=text,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )


class CountingEmbeddings:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        return np.asarray(
            [[1.0, 0.0] if "alpha" in text else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )


def test_bm25_only_topk_never_calls_embedding_provider() -> None:
    embeddings = CountingEmbeddings()
    index = HybridIndex(
        [_chunk("a", "alpha rule"), _chunk("b", "beta rule")],
        scope="policy",
        embedding_provider=embeddings,
    )
    config = RetrievalConfig(
        recall_mode="bm25",
        fusion_mode="none",
        reranker_mode="none",
        selector_mode="top_k",
    )

    hits = index.search("alpha", limit=1, config=config)

    assert hits[0].chunk.chunk_id == "a"
    assert embeddings.calls == 0
    assert "vector" not in hits[0].score_trace


def test_vector_only_trace_omits_bm25() -> None:
    index = HybridIndex(
        [_chunk("a", "alpha rule"), _chunk("b", "beta rule")],
        scope="policy",
        embedding_provider=CountingEmbeddings(),
    )
    config = RetrievalConfig(
        recall_mode="vector",
        fusion_mode="none",
        reranker_mode="none",
        selector_mode="top_k",
    )

    hit = index.search("alpha", limit=1, config=config)[0]

    assert hit.chunk.chunk_id == "a"
    assert "bm25" not in hit.score_trace
    assert "vector" in hit.score_trace


def test_disabled_selector_returns_relevance_order() -> None:
    index = HybridIndex(
        [_chunk("a", "alpha alpha"), _chunk("b", "alpha")],
        scope="policy",
    )
    config = RetrievalConfig(
        recall_mode="bm25",
        fusion_mode="none",
        reranker_mode="none",
        selector_mode="top_k",
    )

    hits = index.search("alpha alpha", limit=2, config=config)

    assert [hit.score for hit in hits] == sorted(
        [hit.score for hit in hits], reverse=True
    )
    assert all("mmr" not in hit.score_trace for hit in hits)


def test_weighted_hybrid_records_weighted_fusion_not_rrf() -> None:
    index = HybridIndex(
        [_chunk("a", "alpha rule"), _chunk("b", "beta alpha")],
        scope="policy",
    )
    config = RetrievalConfig(
        recall_mode="hybrid",
        fusion_mode="weighted",
        reranker_mode="none",
        selector_mode="top_k",
    )

    hits = index.search("alpha", limit=2, config=config)

    assert all("weighted_fusion" in hit.score_trace for hit in hits)
    assert all("rrf" not in hit.score_trace for hit in hits)
