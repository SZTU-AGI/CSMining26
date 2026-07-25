from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np

from freca.config import ModelEndpointConfig
from freca.llm import (
    CachedJsonClient,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleJsonClient,
    OpenAICompatibleVisionDescriber,
    ReplayJsonClient,
)


def _endpoint(env: str, *, response_format: str = "json_schema", retries: int = 1):
    return ModelEndpointConfig(
        base_url="https://models.example/v1",
        model="test-model",
        api_key_env=env,
        max_retries=retries,
        response_format=response_format,
    )


def test_json_client_supports_json_object_response_format(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    client = OpenAICompatibleJsonClient(
        _endpoint("MODEL_KEY", response_format="json_object", retries=0),
        transport=httpx.MockTransport(handler),
    )

    assert client.complete_json(system="s", user="u", schema={}) == {"ok": True}


def test_embedding_retries_429_then_returns_normalized_vector(monkeypatch) -> None:
    monkeypatch.setenv("EMBED_KEY", "secret")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": "busy"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [3, 4]}]})

    provider = OpenAICompatibleEmbeddingProvider(
        _endpoint("EMBED_KEY"), transport=httpx.MockTransport(handler), sleep=lambda _: None
    )

    assert np.allclose(provider.embed(["a"]), [[0.6, 0.8]])
    assert attempts == 2


def test_vision_retries_server_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VISION_KEY", "secret")
    image = tmp_path / "map.png"
    image.write_bytes(b"png")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Two stations."}}]}
        )

    client = OpenAICompatibleVisionDescriber(
        _endpoint("VISION_KEY"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )

    assert client.describe(image, context="map") == "Two stations."
    assert attempts == 2


def test_cached_json_client_calls_model_once_and_writes_redacted_ledger(
    tmp_path: Path,
) -> None:
    replay = ReplayJsonClient([{"verdict": "1"}])
    client = CachedJsonClient(
        replay,
        cache_dir=tmp_path / "cache",
        ledger_path=tmp_path / "logs" / "model-calls.jsonl",
        client_name="audit",
        model_metadata={"model": "m", "api_key_env": "SECRET_ENV"},
    )

    first = client.complete_json(system="system", user="evidence", schema={"type": "object"})
    second = client.complete_json(system="system", user="evidence", schema={"type": "object"})

    assert first == second == {"verdict": "1"}
    assert len(replay.requests) == 1
    lines = [json.loads(line) for line in (tmp_path / "logs" / "model-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["cache_hit"] for line in lines] == [False, True]
    assert "secret" not in json.dumps(lines).lower()
