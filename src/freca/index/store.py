from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np

from freca.config import FusionMode, RecallMode, RerankerMode, RetrievalConfig, SelectorMode
from freca.index.bm25 import bm25_scores
from freca.index.ranking import (
    lexical_rerank_score,
    reciprocal_rank_fusion,
    select_with_mmr_trace,
)
from freca.index.vector import EmbeddingProvider, HashingEmbeddingProvider
from freca.models import ContentKind, EvidenceChunk, RetrievalHit


class HybridIndex:
    def __init__(
        self,
        chunks: list[EvidenceChunk],
        *,
        scope: Literal["policy", "case"],
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        if scope == "case" and any(chunk.case_id is None for chunk in chunks):
            raise ValueError("case index cannot contain policy chunks")
        if scope == "policy" and any(chunk.case_id is not None for chunk in chunks):
            raise ValueError("policy index cannot contain case chunks")
        self.chunks = list(chunks)
        self.scope = scope
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()

    def _subset(self, case_id: int | None) -> list[EvidenceChunk]:
        if self.scope == "case":
            if case_id is None:
                raise ValueError("case_id is mandatory for case index search")
            return [chunk for chunk in self.chunks if chunk.case_id == case_id]
        if case_id is not None:
            raise ValueError("case_id must not be supplied to policy index")
        return list(self.chunks)

    def search(
        self,
        query: str,
        *,
        case_id: int | None = None,
        limit: int = 8,
        candidate_limit: int = 40,
        config: RetrievalConfig | None = None,
        reranker=None,
        trace_sink: list[dict] | None = None,
        allowed_tracks: list[int] | None = None,
        content_kinds: list[ContentKind] | None = None,
        include_excluded_evidence: bool = False,
    ) -> list[RetrievalHit]:
        if limit < 1:
            raise ValueError("limit must be positive")
        retrieval = config or RetrievalConfig(candidate_limit=candidate_limit)
        candidate_limit = retrieval.candidate_limit if config is not None else candidate_limit
        subset = self._subset(case_id)
        if allowed_tracks:
            allowed = set(allowed_tracks)
            subset = [chunk for chunk in subset if chunk.track in allowed]
        if content_kinds:
            allowed_kinds = set(content_kinds)
            subset = [chunk for chunk in subset if chunk.content_kind in allowed_kinds]
        if not subset:
            return []
        contaminated_subset = [c for c in subset if "exclude_from_compliance_evidence" in c.flags]
        if trace_sink is not None:
            for chunk in contaminated_subset:
                trace_sink.append({"chunk_id": chunk.chunk_id, "selected": False, "reason": "contaminated_excluded_evidence"})
        eligible_subset = [c for c in subset if "exclude_from_compliance_evidence" not in c.flags]
        if not eligible_subset:
            return []
        texts = [chunk.content for chunk in eligible_subset]
        needs_bm25 = retrieval.recall_mode in {RecallMode.BM25, RecallMode.HYBRID}
        needs_vector_recall = retrieval.recall_mode in {RecallMode.VECTOR, RecallMode.HYBRID}
        needs_vectors = needs_vector_recall or retrieval.selector_mode != SelectorMode.TOP_K
        bm25 = bm25_scores(texts, query) if needs_bm25 else None
        vectors = self.embedding_provider.embed(texts) if needs_vectors else None
        query_vector = self.embedding_provider.embed([query])[0] if needs_vector_recall else None
        dense = vectors @ query_vector if needs_vector_recall else None

        def normalize(values: np.ndarray) -> np.ndarray:
            low = float(np.min(values))
            high = float(np.max(values))
            if high == low:
                return np.ones_like(values, dtype=np.float64)
            return (values - low) / (high - low)

        rankings: dict[str, list[str]] = {}
        if bm25 is not None:
            bm25_order = sorted(
                range(len(eligible_subset)), key=lambda i: (-bm25[i], eligible_subset[i].chunk_id)
            )
            rankings["bm25"] = [eligible_subset[i].chunk_id for i in bm25_order]
        if dense is not None:
            dense_order = sorted(
                range(len(eligible_subset)), key=lambda i: (-dense[i], eligible_subset[i].chunk_id)
            )
            rankings["vector"] = [eligible_subset[i].chunk_id for i in dense_order]

        by_id = {chunk.chunk_id: index for index, chunk in enumerate(eligible_subset)}
        fusion_scores: dict[str, float]
        fusion_key: str
        if retrieval.fusion_mode == FusionMode.RRF:
            fused = reciprocal_rank_fusion(rankings, k=retrieval.rrf_k)
            max_score = fused[0][1] if fused else 1.0
            fusion_scores = {item_id: score / max_score for item_id, score in fused}
            fusion_key = "rrf"
        elif retrieval.fusion_mode == FusionMode.WEIGHTED:
            bm25_normalized = normalize(bm25) if bm25 is not None else np.zeros(len(eligible_subset))
            dense_normalized = normalize(dense) if dense is not None else np.zeros(len(eligible_subset))
            fusion_scores = {
                chunk.chunk_id: float(
                    retrieval.bm25_weight * bm25_normalized[index]
                    + retrieval.vector_weight * dense_normalized[index]
                )
                for index, chunk in enumerate(eligible_subset)
            }
            fusion_key = "weighted_fusion"
        else:
            raw = bm25 if bm25 is not None else dense
            assert raw is not None
            normalized = normalize(raw)
            fusion_scores = {
                chunk.chunk_id: float(normalized[index])
                for index, chunk in enumerate(eligible_subset)
            }
            fusion_key = "recall"
        fused = sorted(fusion_scores.items(), key=lambda item: (-item[1], item[0]))
        ranked_candidates = fused[:candidate_limit]
        candidate_chunks = [eligible_subset[by_id[chunk_id]] for chunk_id, _ in ranked_candidates]
        if retrieval.reranker_mode == RerankerMode.LEXICAL:
            reranker_scores = {
                chunk.chunk_id: lexical_rerank_score(query, chunk.content)
                for chunk in candidate_chunks
            }
        elif retrieval.reranker_mode == RerankerMode.NONE:
            reranker_scores = {}
        else:
            if reranker is None:
                raise RuntimeError(
                    f"reranker backend is required for {retrieval.reranker_mode.value}"
                )
            reranker_scores = reranker.rerank(query, candidate_chunks)
        candidates = []
        for chunk_id, fusion_score in ranked_candidates:
            index = by_id[chunk_id]
            if retrieval.reranker_mode != RerankerMode.NONE:
                rerank_score = reranker_scores[chunk_id]
                relevance = (
                    retrieval.fusion_weight * fusion_score
                    + retrieval.reranker_weight * rerank_score
                )
            else:
                rerank_score = None
                relevance = fusion_score
            trace: dict[str, float] = {fusion_key: float(fusion_score)}
            if bm25 is not None:
                trace["bm25"] = float(bm25[index])
            if dense is not None:
                trace["vector"] = float(dense[index])
            if rerank_score is not None:
                trace["reranker"] = float(rerank_score)
            trace["relevance"] = float(relevance)
            vector = (
                vectors[index]
                if vectors is not None
                else np.zeros(1, dtype=np.float32)
            )
            candidates.append((eligible_subset[index], float(relevance), trace, vector))
        candidates.sort(key=lambda item: (-item[1], item[0].chunk_id))
        if retrieval.selector_mode == SelectorMode.TOP_K:
            selected = [
                (chunk, score, trace)
                for chunk, score, trace, _ in candidates[:limit]
            ]
            selection_trace = [
                {
                    "chunk_id": chunk.chunk_id,
                    "relevance": score,
                    "selected": index < limit,
                    "reason": "selected" if index < limit else "limit_reached",
                }
                for index, (chunk, score, _, _) in enumerate(candidates)
            ]
        else:
            selection = select_with_mmr_trace(
                candidates,
                limit=min(limit, len(candidates)),
                lambda_relevance=retrieval.mmr_lambda,
                source_aware=retrieval.selector_mode == SelectorMode.SOURCE_AWARE_MMR,
                same_source_penalty=retrieval.same_source_penalty,
                same_track_penalty=retrieval.same_track_penalty,
                same_location_penalty=retrieval.same_location_penalty,
                min_unique_sources=retrieval.min_unique_sources,
            )
            selected = selection.selected
            selection_trace = selection.candidate_traces
        if trace_sink is not None:
            trace_sink.extend(selection_trace)
        return [
            RetrievalHit(chunk=chunk, score=score, rank=rank, score_trace=trace)
            for rank, (chunk, score, trace) in enumerate(selected, start=1)
        ]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scope": self.scope,
            "embedding_provider": self.embedding_provider.name,
            "chunks": [chunk.model_dump(mode="json") for chunk in self.chunks],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> HybridIndex:
        payload = json.loads(path.read_text(encoding="utf-8"))
        chunks = [EvidenceChunk.model_validate(item) for item in payload["chunks"]]
        return cls(
            chunks,
            scope=payload["scope"],
            embedding_provider=embedding_provider,
        )
