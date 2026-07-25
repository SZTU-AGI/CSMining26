from __future__ import annotations

import json
import os
import time
from typing import Protocol

import httpx

from freca.config import ModelEndpointConfig
from freca.index.ranking import lexical_rerank_score
from freca.llm import JsonChatClient
from freca.models import EvidenceChunk


class Reranker(Protocol):
    def rerank(self, query: str, chunks: list[EvidenceChunk]) -> dict[str, float]: ...


def _validate_scores(
    chunks: list[EvidenceChunk], scores: dict[str, float]
) -> dict[str, float]:
    expected = [chunk.chunk_id for chunk in chunks]
    if set(scores) != set(expected) or len(scores) != len(expected):
        raise ValueError("reranker must return every candidate exactly once")
    if any(not 0.0 <= score <= 1.0 for score in scores.values()):
        raise ValueError("reranker scores must be between 0 and 1")
    return {chunk_id: float(scores[chunk_id]) for chunk_id in expected}


class LexicalReranker:
    def rerank(self, query: str, chunks: list[EvidenceChunk]) -> dict[str, float]:
        return {
            chunk.chunk_id: lexical_rerank_score(query, chunk.content)
            for chunk in chunks
        }


class CrossEncoderApiReranker:
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.config = config
        self.transport = transport
        self.sleep = sleep

    def rerank(self, query: str, chunks: list[EvidenceChunk]) -> dict[str, float]:
        if not chunks:
            return {}
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"required reranker credential environment variable is unset: "
                f"{self.config.api_key_env}"
            )
        payload = {
            "model": self.config.model,
            "query": query,
            "documents": [
                {"id": chunk.chunk_id, "text": chunk.content} for chunk in chunks
            ],
            "top_n": len(chunks),
        }
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(
                    transport=self.transport, timeout=self.config.timeout_seconds
                ) as client:
                    response = client.post(
                        f"{self.config.base_url.rstrip('/')}/rerank",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable reranker response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                results = response.json()["results"]
                scores: dict[str, float] = {}
                for item in results:
                    if "index" in item:
                        index = int(item["index"])
                        if not 0 <= index < len(chunks):
                            raise ValueError("reranker returned an out-of-range index")
                        chunk_id = chunks[index].chunk_id
                    else:
                        document = item.get("document", {})
                        chunk_id = str(document.get("id") or item.get("id") or "")
                    if chunk_id in scores:
                        raise ValueError("reranker must return every candidate exactly once")
                    score = item.get("relevance_score", item.get("score"))
                    scores[chunk_id] = float(score)
                return _validate_scores(chunks, scores)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if isinstance(exc, ValueError) or attempt >= self.config.max_retries:
                    break
                self.sleep(min(2**attempt, 8))
        if isinstance(last_error, ValueError):
            raise last_error
        raise RuntimeError(
            f"reranker request failed after {self.config.max_retries + 1} attempts"
        ) from last_error


class LLMListwiseReranker:
    def __init__(self, client: JsonChatClient) -> None:
        self.client = client

    def rerank(self, query: str, chunks: list[EvidenceChunk]) -> dict[str, float]:
        if not chunks:
            return {}
        payload = self.client.complete_json(
            system=(
                "Rank only the supplied candidate chunks by relevance to the query. "
                "Return every chunk_id exactly once. Do not decide compliance."
            ),
            user=json.dumps(
                {
                    "query": query,
                    "candidates": [
                        {
                            "chunk_id": chunk.chunk_id,
                            "source_id": chunk.source_id,
                            "track": chunk.track,
                            "content": chunk.content,
                        }
                        for chunk in chunks
                    ],
                },
                ensure_ascii=False,
            ),
            schema={
                "type": "object",
                "properties": {
                    "ranking": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "chunk_id": {"type": "string"},
                                "score": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "required": ["chunk_id", "score"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["ranking"],
                "additionalProperties": False,
            },
        )
        scores: dict[str, float] = {}
        for item in payload.get("ranking", []):
            chunk_id = str(item["chunk_id"])
            if chunk_id in scores:
                raise ValueError("reranker must return every candidate exactly once")
            scores[chunk_id] = float(item["score"])
        return _validate_scores(chunks, scores)
