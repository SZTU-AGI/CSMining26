import json
from pathlib import Path

import httpx
import numpy as np

from freca.config import ModelEndpointConfig
from freca.llm import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionDescriber,
    ReplayJsonClient,
)
from freca.retrieval import LLMQueryRewriter
from freca.models import CheckpointDefinition


def _config(api_key_env: str) -> ModelEndpointConfig:
    return ModelEndpointConfig(
        base_url="https://models.example/v1",
        model="test-model",
        api_key_env=api_key_env,
        max_retries=0,
    )


def test_openai_embedding_provider_returns_ordered_vectors(monkeypatch) -> None:
    monkeypatch.setenv("FRECA_EMBED_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.content)
        assert body["input"] == ["alpha", "beta"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    provider = OpenAICompatibleEmbeddingProvider(
        _config("FRECA_EMBED_KEY"), transport=httpx.MockTransport(handler)
    )
    vectors = provider.embed(["alpha", "beta"])

    assert np.array_equal(vectors, np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))


def test_vision_describer_sends_image_and_returns_neutral_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FRECA_VISION_KEY", "secret")
    image = tmp_path / "image.png"
    image.write_bytes(b"png-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Three marked bait stations."}}]},
        )

    describer = OpenAICompatibleVisionDescriber(
        _config("FRECA_VISION_KEY"), transport=httpx.MockTransport(handler)
    )
    assert describer.describe(image, context="site plan") == "Three marked bait stations."


def test_llm_query_rewriter_returns_only_queries_for_the_stated_gap() -> None:
    client = ReplayJsonClient(
        [{"policy_query": "policy time exception", "evidence_query": "dated farm records"}]
    )
    checkpoint = CheckpointDefinition(
        cp_id="CP22",
        element_id=3,
        element_title="Element-3",
        section_title="3.3 Record keeping",
        text="Records demonstrate operation.",
        source_file="cp.xlsx",
        cell="V3",
    )

    result = LLMQueryRewriter(client).rewrite(
        checkpoint=checkpoint,
        gap="time_or_retention",
        policy_query="old policy",
        evidence_query="old evidence",
    )

    assert result == ("policy time exception", "dated farm records")
    assert "time_or_retention" in client.requests[0]["user"]
