from __future__ import annotations

import json

import httpx
import pytest

from freca.config import ModelEndpointConfig
from freca.index.rerankers import CrossEncoderApiReranker, LLMListwiseReranker
from freca.llm import ReplayJsonClient
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


def _endpoint() -> ModelEndpointConfig:
    return ModelEndpointConfig(
        base_url="https://rerank.example/v1",
        model="rerank-model",
        api_key_env="RERANK_KEY",
        max_retries=0,
    )


def test_cross_encoder_reranker_validates_and_maps_indices(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/rerank"
        assert [item["id"] for item in body["documents"]] == ["a", "b"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            },
        )

    reranker = CrossEncoderApiReranker(
        _endpoint(), transport=httpx.MockTransport(handler)
    )

    assert reranker.rerank("query", [_chunk("a", "A"), _chunk("b", "B")]) == {
        "a": 0.2,
        "b": 0.9,
    }


def test_cross_encoder_rejects_missing_candidate(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_KEY", "secret")
    reranker = CrossEncoderApiReranker(
        _endpoint(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
            )
        ),
    )

    with pytest.raises(ValueError, match="exactly once"):
        reranker.rerank("q", [_chunk("a", "A"), _chunk("b", "B")])


def test_cross_encoder_retries_rate_limit_then_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_KEY", "secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.7}]},
        )

    endpoint = _endpoint().model_copy(update={"max_retries": 1})
    reranker = CrossEncoderApiReranker(
        endpoint,
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )

    assert reranker.rerank("q", [_chunk("a", "A")]) == {"a": 0.7}
    assert calls == 2


def test_llm_listwise_rejects_unknown_or_duplicate_ids() -> None:
    client = ReplayJsonClient(
        [
            {
                "ranking": [
                    {"chunk_id": "a", "score": 0.8},
                    {"chunk_id": "unknown", "score": 0.2},
                ]
            }
        ]
    )
    reranker = LLMListwiseReranker(client)

    with pytest.raises(ValueError, match="exactly once"):
        reranker.rerank("q", [_chunk("a", "A"), _chunk("b", "B")])


def test_llm_listwise_returns_scores_for_every_candidate() -> None:
    client = ReplayJsonClient(
        [
            {
                "ranking": [
                    {"chunk_id": "b", "score": 0.95},
                    {"chunk_id": "a", "score": 0.3},
                ]
            }
        ]
    )

    scores = LLMListwiseReranker(client).rerank(
        "q", [_chunk("a", "A"), _chunk("b", "B")]
    )

    assert scores == {"a": 0.3, "b": 0.95}
