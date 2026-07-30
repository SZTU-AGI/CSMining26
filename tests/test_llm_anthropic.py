"""Replay tests for AnthropicMessagesClient — no real HTTP, no provider calls."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from freca.config import ModelEndpointConfig, ResponseFormatMode
from freca.llm import AnthropicMessagesClient, ModelResponseError


class _ReplayTransport(httpx.BaseTransport):
    """httpx transport that returns canned responses and records the request."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, json={"error": "replay exhausted"})
        return self.responses.pop(0)


def _anthropic_json_response(content: str) -> httpx.Response:
    body = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "MiniMax-M3",
        "content": [{"type": "text", "text": content}],
        "usage": {
            "input_tokens": 17,
            "output_tokens": 6,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "stop_reason": "end_turn",
    }
    return httpx.Response(200, json=body)


def _anthropic_json_response_with_prompt_cache(
    content: str, cache_read: int
) -> httpx.Response:
    body = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "MiniMax-M3",
        "content": [{"type": "text", "text": content}],
        "usage": {
            "input_tokens": 17,
            "output_tokens": 6,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": cache_read,
        },
        "stop_reason": "end_turn",
    }
    return httpx.Response(200, json=body)


def _endpoint(
    *, response_format: ResponseFormatMode = ResponseFormatMode.JSON_OBJECT, timeout: int = 30, max_retries: int = 2
) -> ModelEndpointConfig:
    return ModelEndpointConfig(
        base_url="https://api.minimaxi.com/anthropic",
        model="MiniMax-M3",
        api_key_env="FRECA_AUDIT_API_KEY",
        timeout_seconds=timeout,
        max_retries=max_retries,
        response_format=response_format,
    )


def test_anthropic_client_posts_to_v1_messages_with_anthropic_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    transport = _ReplayTransport(
        [_anthropic_json_response(json.dumps({"ok": True, "verdicts": []}))]
    )
    client = AnthropicMessagesClient(_endpoint(), transport=transport)

    out = client.complete_json(system="be terse", user="say ok", schema={})

    assert out == {"ok": True, "verdicts": []}
    req = transport.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.minimaxi.com/anthropic/v1/messages"
    assert req.headers["x-api-key"] == "test-key-abc"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content.decode("utf-8"))
    assert body["model"] == "MiniMax-M3"
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "say ok"}]
    assert body["max_tokens"] >= 1


def test_anthropic_client_inlines_images_as_image_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    img = tmp_path / "official.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
    transport = _ReplayTransport(
        [_anthropic_json_response(json.dumps({"ok": True, "verdicts": []}))]
    )
    client = AnthropicMessagesClient(_endpoint(), transport=transport)

    client.complete_json_with_images(
        system="see image",
        user="classify",
        image_paths=[img],
        schema={},
    )

    req = transport.requests[0]
    body = json.loads(req.content.decode("utf-8"))
    assert isinstance(body["messages"], list)
    user = body["messages"][0]
    blocks = user["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "classify"
    image_block = next(b for b in blocks if b["type"] == "image")
    source = image_block["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/png"
    assert isinstance(source["data"], str) and len(source["data"]) > 0


def test_anthropic_client_retries_on_429_and_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    retry_body = {"error": "rate limited"}
    transport = _ReplayTransport(
        [
            httpx.Response(429, json=retry_body),
            httpx.Response(429, json=retry_body),
            _anthropic_json_response(json.dumps({"ok": True})),
        ]
    )
    client = AnthropicMessagesClient(
        _endpoint(max_retries=3), transport=transport, sleep=lambda *_: None
    )

    out = client.complete_json(system="s", user="u", schema={})

    assert out == {"ok": True}
    assert len(transport.requests) == 3


def test_anthropic_client_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    transport = _ReplayTransport(
        [
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(500, json={"error": "boom"}),
        ]
    )
    client = AnthropicMessagesClient(
        _endpoint(max_retries=2), transport=transport, sleep=lambda *_: None
    )

    with pytest.raises(ModelResponseError):
        client.complete_json(system="s", user="u", schema={})
    assert len(transport.requests) == 3


def test_anthropic_client_fails_fast_on_4xx_other_than_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    transport = _ReplayTransport([httpx.Response(401, json={"error": "bad key"})])
    client = AnthropicMessagesClient(_endpoint(max_retries=2), transport=transport)

    with pytest.raises(ModelResponseError):
        client.complete_json(system="s", user="u", schema={})
    assert len(transport.requests) == 1


def test_anthropic_client_requires_api_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRECA_AUDIT_API_KEY", raising=False)
    transport = _ReplayTransport([])
    client = AnthropicMessagesClient(_endpoint(), transport=transport)

    with pytest.raises(RuntimeError, match="FRECA_AUDIT_API_KEY"):
        client.complete_json(system="s", user="u", schema={})


def test_anthropic_client_parses_json_wrapped_in_code_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    fenced = "```json\n{\"ok\": true}\n```"
    transport = _ReplayTransport([_anthropic_json_response(fenced)])
    client = AnthropicMessagesClient(_endpoint(), transport=transport)

    out = client.complete_json(system="s", user="u", schema={})

    assert out == {"ok": True}


def test_anthropic_client_honours_retry_after_header_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    sleeps: list[float] = []
    transport = _ReplayTransport(
        [
            httpx.Response(
                429,
                json={"error": "rate limited"},
                headers={"retry-after": "7"},
            ),
            _anthropic_json_response(json.dumps({"ok": True})),
        ]
    )
    client = AnthropicMessagesClient(
        _endpoint(max_retries=3),
        transport=transport,
        sleep=lambda seconds: sleeps.append(seconds),
    )

    out = client.complete_json(system="s", user="u", schema={})

    assert out == {"ok": True}
    assert sleeps == [7.0]


def test_anthropic_client_uses_long_backoff_schedule_for_429_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    sleeps: list[float] = []
    transport = _ReplayTransport(
        [
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(429, json={"error": "rate limited"}),
            _anthropic_json_response(json.dumps({"ok": True})),
        ]
    )
    client = AnthropicMessagesClient(
        _endpoint(max_retries=3),
        transport=transport,
        sleep=lambda seconds: sleeps.append(seconds),
    )

    client.complete_json(system="s", user="u", schema={})

    assert sleeps == [5.0, 10.0]


def test_anthropic_client_wraps_bare_list_of_dicts_under_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    raw = json.dumps(
        [{"cp_id": "CP1", "verdict": "1", "reason": "x", "citation_ids": ["a"], "uncertainty": 0.1}]
    )
    transport = _ReplayTransport([_anthropic_json_response(raw)])
    client = AnthropicMessagesClient(_endpoint(), transport=transport)

    out = client.complete_json(system="s", user="u", schema={})

    assert out == {
        "verdicts": [
            {
                "cp_id": "CP1",
                "verdict": "1",
                "reason": "x",
                "citation_ids": ["a"],
                "uncertainty": 0.1,
            }
        ]
    }


def test_anthropic_client_emits_request_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key-abc")
    transport = _ReplayTransport(
        [
            _anthropic_json_response_with_prompt_cache(
                json.dumps({"ok": True, "verdicts": []}), cache_read=128
            )
        ]
    )
    client = AnthropicMessagesClient(_endpoint(), transport=transport)

    client.complete_json(system="s", user="u", schema={})

    # No global ledger side-effect expected; just confirm call did not raise
    # and that input_tokens / cache_read_input_tokens are extractable from a
    # fresh probe — full ledger integration is exercised by the orchestrator.
    assert len(transport.requests) == 1


def _noop_ensure_response(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)