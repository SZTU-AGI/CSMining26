from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from freca.index.bm25 import tokenize
from freca.models import EvidenceChunk


@dataclass(frozen=True)
class MMRSelectionResult:
    selected: list[tuple[EvidenceChunk, float, dict[str, Any]]]
    candidate_traces: list[dict[str, Any]]


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    counter = 0
    for ranking in rankings.values():
        for rank, item_id in enumerate(ranking, start=1):
            if item_id not in first_seen:
                first_seen[item_id] = counter
                counter += 1
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))


def lexical_rerank_score(query: str, content: str) -> float:
    query_tokens = set(tokenize(query))
    content_tokens = set(tokenize(content))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens) / len(query_tokens)
    phrase_bonus = 0.15 if query.casefold() in content.casefold() else 0.0
    return min(1.0, overlap + phrase_bonus)


def _same_location(first: EvidenceChunk, second: EvidenceChunk) -> bool:
    for field in ("sheet", "section", "page", "object_id"):
        first_value = getattr(first.location, field)
        second_value = getattr(second.location, field)
        if first_value is not None and first_value == second_value:
            return True
    return False


def select_with_mmr_trace(
    candidates: list[tuple[EvidenceChunk, float, dict[str, float], np.ndarray]],
    *,
    limit: int,
    lambda_relevance: float = 0.65,
    source_aware: bool = True,
    same_source_penalty: float = 0.5,
    same_track_penalty: float = 0.15,
    same_location_penalty: float = 0.1,
    min_unique_sources: int = 2,
) -> MMRSelectionResult:
    selected: list[tuple[EvidenceChunk, float, dict[str, float], np.ndarray]] = []
    remaining = sorted(candidates, key=lambda item: item[0].chunk_id)
    latest: dict[str, dict[str, Any]] = {}
    while remaining and len(selected) < limit:
        best_index = 0
        best_score = float("-inf")
        selected_sources = {item[0].source_id for item in selected}
        alternative_source_exists = any(
            item[0].source_id not in selected_sources for item in remaining
        )
        for index, candidate in enumerate(remaining):
            chunk, relevance, trace, vector = candidate
            max_similarity = 0.0
            source_penalty = 0.0
            track_penalty = 0.0
            location_penalty = 0.0
            for selected_chunk, _, _, selected_vector in selected:
                similarity = float(np.dot(vector, selected_vector))
                max_similarity = max(max_similarity, similarity)
                if source_aware and chunk.source_id == selected_chunk.source_id:
                    source_penalty = max(source_penalty, same_source_penalty)
                if (
                    source_aware
                    and chunk.track is not None
                    and chunk.track == selected_chunk.track
                ):
                    track_penalty = max(track_penalty, same_track_penalty)
                if source_aware and _same_location(chunk, selected_chunk):
                    location_penalty = max(location_penalty, same_location_penalty)
            coverage_penalty = (
                1.0
                if source_aware
                and selected
                and len(selected_sources) < min_unique_sources
                and alternative_source_exists
                and chunk.source_id in selected_sources
                else 0.0
            )
            diversity = (
                max_similarity
                + source_penalty
                + track_penalty
                + location_penalty
                + coverage_penalty
            )
            mmr = lambda_relevance * relevance - (1.0 - lambda_relevance) * diversity
            latest[chunk.chunk_id] = {
                "chunk_id": chunk.chunk_id,
                "relevance": float(relevance),
                "max_similarity": float(max_similarity),
                "source_penalty": float(source_penalty),
                "track_penalty": float(track_penalty),
                "location_penalty": float(location_penalty),
                "coverage_penalty": float(coverage_penalty),
                "mmr": float(mmr),
                "selected": False,
                "reason": "lower_mmr_score",
            }
            if (mmr, relevance) > (best_score, remaining[best_index][1]):
                best_index = index
                best_score = mmr
        chosen = remaining.pop(best_index)
        latest[chosen[0].chunk_id]["selected"] = True
        latest[chosen[0].chunk_id]["reason"] = "selected"
        chosen[2].update(
            {
                key: value
                for key, value in latest[chosen[0].chunk_id].items()
                if key not in {"chunk_id", "selected", "reason"}
            }
        )
        selected.append(chosen)
    selected_ids = {item[0].chunk_id for item in selected}
    for chunk_id, trace in latest.items():
        if chunk_id not in selected_ids:
            trace["reason"] = "limit_reached" if len(selected) >= limit else "lower_mmr_score"
    return MMRSelectionResult(
        selected=[(chunk, score, trace) for chunk, score, trace, _ in selected],
        candidate_traces=[latest[key] for key in sorted(latest)],
    )


def source_aware_mmr(
    candidates: list[tuple[EvidenceChunk, float, dict[str, float], np.ndarray]],
    *,
    limit: int,
    lambda_relevance: float = 0.65,
) -> list[tuple[EvidenceChunk, float, dict[str, float]]]:
    return select_with_mmr_trace(
        candidates,
        limit=limit,
        lambda_relevance=lambda_relevance,
        source_aware=True,
    ).selected
