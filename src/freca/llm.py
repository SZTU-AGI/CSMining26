from __future__ import annotations

import base64
import json
import mimetypes
import os
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from freca.config import ModelEndpointConfig, ResponseFormatMode
from freca.state import atomic_write_json, build_cache_key, read_json


_LEDGER_LOCK = threading.Lock()


class ModelResponseError(RuntimeError):
    pass


class _RetryableStatus(Exception):
    """Internal: signals a 429/5xx with a suggested backoff wait in seconds."""

    def __init__(self, message: str, *, wait_seconds: float) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


def _retry_wait_seconds(
    attempt: int,
    *,
    retry_after: str | None = None,
    status: int | None = None,
) -> float:
    """Backoff schedule for retryable model responses.

    For 429 we honour a ``Retry-After`` header when present; otherwise we use a
    longer exponential schedule (5, 10, 20, 40, 60s capped) than the default
    2/4/8 because vendors such as MiniMax enforce aggressive per-minute quotas
    and the short backoff never lets the quota refill. Non-429 retryables use
    the gentler 2/4/8 schedule.
    """
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass
    if status == 429:
        schedule = (5.0, 10.0, 20.0, 40.0, 60.0)
        return schedule[min(attempt, len(schedule) - 1)]
    return float(min(2**attempt, 8))


class JsonChatClient(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


def _parse_json_object(value: Any) -> dict[str, Any]:
    """Parse a JSON object string, tolerating a bare top-level array of dicts.

    Some Anthropic-compatible vendors return ``[{...}, {...}]`` directly when the
    schema describes an array of items rather than wrapping it under a named key.
    We accept that shape and re-wrap it under ``{"verdicts": ...}`` so downstream
    validation can stay schema-stable.
    """
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise ModelResponseError(f"model response is not valid JSON object: type={type(value).__name__}")
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:200].replace("\n", " ")
        raise ModelResponseError(
            f"model response is not valid JSON: {exc.msg} at pos {exc.pos}: {preview!r}"
        ) from exc
    if isinstance(parsed, list):
        if all(isinstance(item, dict) for item in parsed):
            return {"verdicts": parsed}
        raise ModelResponseError(
            "model response is a list but does not contain only dict objects"
        )
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            f"model response is not a JSON object: got {type(parsed).__name__}"
        )
    return parsed


class ReplayJsonClient:
    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.requests.append({"system": system, "user": user, "schema": schema})
        if not self._responses:
            raise ModelResponseError("replay responses exhausted")
        return _parse_json_object(self._responses.pop(0))

    def complete_json_with_images(
        self,
        *,
        system: str,
        user: str,
        image_paths: Sequence[Path],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.requests.append(
            {
                "system": system,
                "user": user,
                "image_paths": list(image_paths),
                "schema": schema,
            }
        )
        if not self._responses:
            raise ModelResponseError("replay responses exhausted")
        return _parse_json_object(self._responses.pop(0))


class CachedJsonClient:
    def __init__(
        self,
        inner: JsonChatClient,
        *,
        cache_dir: Path,
        ledger_path: Path,
        client_name: str,
        model_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.ledger_path = ledger_path
        self.client_name = client_name
        self.model_metadata = {
            key: value
            for key, value in (model_metadata or {}).items()
            if "key" not in key.lower() and "token" not in key.lower()
        }

    def _write_ledger(self, payload: dict[str, Any]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with _LEDGER_LOCK, self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        request_hash = build_cache_key(
            {"client": self.client_name, "model": self.model_metadata},
            {"system": system, "user": user},
            {"schema": schema},
        )
        cache_path = self.cache_dir / f"{request_hash}.json"
        cache_hit = cache_path.exists()
        if cache_hit:
            response = read_json(cache_path)["response"]
        else:
            response = self.inner.complete_json(
                system=system, user=user, schema=schema, max_tokens=max_tokens
            )
            atomic_write_json(
                cache_path,
                {
                    "request_hash": request_hash,
                    "client": self.client_name,
                    "model": self.model_metadata,
                    "response": response,
                },
            )
        self._write_ledger(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "client": self.client_name,
                "model": self.model_metadata,
                "request_hash": request_hash,
                "cache_hit": cache_hit,
                "system": system,
                "user": user,
                "schema": schema,
                "response": response,
            }
        )
        return dict(response)


class OpenAICompatibleJsonClient:
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

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return self._complete_json(
            system=system,
            user=user,
            image_paths=(),
            schema=schema,
        )

    def complete_json_with_images(
        self,
        *,
        system: str,
        user: str,
        image_paths: Sequence[Path],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return self._complete_json(
            system=system,
            user=user,
            image_paths=image_paths,
            schema=schema,
        )

    def _complete_json(
        self,
        *,
        system: str,
        user: str,
        image_paths: Sequence[Path],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"required model credential environment variable is unset: "
                f"{self.config.api_key_env}"
            )
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        user_content: str | list[dict[str, Any]] = user
        if image_paths:
            user_content = [{"type": "text", "text": user}]
            for image_path in image_paths:
                mime = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
                encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    }
                )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
        }
        if self.config.response_format == ResponseFormatMode.JSON_SCHEMA:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "freca_structured_response",
                    "strict": True,
                    "schema": schema,
                },
            }
        elif self.config.response_format == ResponseFormatMode.JSON_OBJECT:
            payload["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(
                    transport=self.transport,
                    timeout=self.config.timeout_seconds,
                ) as client:
                    response = client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable model response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                return _parse_json_object(content)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.sleep(min(2**attempt, 8))
        raise ModelResponseError(
            f"model request failed after {self.config.max_retries + 1} attempts"
        ) from last_error


class AnthropicMessagesClient:
    """Anthropic Messages API client. Targets `/v1/messages` on the configured base_url.

    The base_url may already include the vendor prefix (e.g.
    ``https://api.minimaxi.com/anthropic``); we always append ``/v1/messages``.
    ``max_tokens`` is required by Anthropic and we derive a safe default from the
    schema size when none is configured.
    """

    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        max_tokens: int | None = None,
        per_checkpoint_tokens: int = 600,
    ) -> None:
        self.config = config
        self.transport = transport
        self.sleep = sleep
        self.max_tokens = max_tokens
        self.per_checkpoint_tokens = per_checkpoint_tokens
        self.last_usage: dict[str, int] | None = None

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return self._complete_json(
            system=system,
            user=user,
            image_paths=(),
            schema=schema,
            max_tokens=max_tokens,
        )

    def complete_json_with_images(
        self,
        *,
        system: str,
        user: str,
        image_paths: Sequence[Path],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return self._complete_json(
            system=system,
            user=user,
            image_paths=image_paths,
            schema=schema,
            max_tokens=max_tokens,
        )

    def _complete_json(
        self,
        *,
        system: str,
        user: str,
        image_paths: Sequence[Path],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"required model credential environment variable is unset: "
                f"{self.config.api_key_env}"
            )
        url = f"{self.config.base_url.rstrip('/')}/v1/messages"
        user_blocks: list[dict[str, Any]] = []
        if image_paths:
            user_blocks.append({"type": "text", "text": user})
            for image_path in image_paths:
                mime = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
                encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
                user_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": encoded,
                        },
                    }
                )
            user_payload: str | list[dict[str, Any]] = user_blocks
        else:
            user_payload = user
        payload: dict[str, Any] = {
            "model": self.config.model,
            "system": system,
            "messages": [{"role": "user", "content": user_payload}],
            "temperature": 0,
            "max_tokens": max_tokens or self.max_tokens or self._default_max_tokens(schema),
        }
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(
                    transport=self.transport,
                    timeout=self.config.timeout_seconds,
                ) as client:
                    response = client.post(
                        url,
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json=payload,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("retry-after")
                    wait_seconds = _retry_wait_seconds(
                        attempt, retry_after=retry_after, status=response.status_code
                    )
                    raise _RetryableStatus(
                        f"retryable model response {response.status_code}",
                        wait_seconds=wait_seconds,
                    )
                if response.status_code >= 400:
                    raise ModelResponseError(
                        f"model request rejected: {response.status_code} {response.text[:300]}"
                    )
                body = response.json()
                usage = body.get("usage")
                if isinstance(usage, Mapping):
                    self.last_usage = {key: int(value) for key, value in usage.items() if isinstance(value, (int, float))}
                else:
                    self.last_usage = None
                return _parse_json_object(_extract_anthropic_text(body))
            except ModelResponseError:
                raise
            except _RetryableStatus as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.sleep(exc.wait_seconds)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.sleep(_retry_wait_seconds(attempt))
        raise ModelResponseError(
            f"model request failed after {self.config.max_retries + 1} attempts"
        ) from last_error

    @staticmethod
    def _default_max_tokens(schema: dict[str, Any]) -> int:
        """Heuristic upper bound so callers don't have to set max_tokens explicitly."""
        return 4096


def _extract_anthropic_text(body: Mapping[str, Any]) -> str:
    try:
        blocks = body["content"]
    except KeyError as exc:
        raise ModelResponseError("anthropic response missing content blocks") from exc
    if not isinstance(blocks, list) or not blocks:
        raise ModelResponseError("anthropic response content is empty")
    for block in blocks:
        if isinstance(block, Mapping) and block.get("type") == "text":
            return str(block.get("text", ""))
    raise ModelResponseError("anthropic response contained no text block")


def build_audit_client(config: ModelEndpointConfig) -> JsonChatClient:
    """Pick the audit chat client based on the configured base_url shape.

    URLs whose path ends in ``/anthropic`` (e.g. ``https://api.minimaxi.com/anthropic``)
    route through :class:`AnthropicMessagesClient`; everything else uses the
    OpenAI-compatible ``/chat/completions`` protocol.
    """
    base = config.base_url.rstrip("/").lower()
    if base.endswith("/anthropic"):
        return AnthropicMessagesClient(config)
    if base.endswith("/v1") or "/v1/" in base:
        return OpenAICompatibleJsonClient(config)
    raise ValueError(
        f"unrecognised audit base_url: {config.base_url!r} "
        f"(expected /anthropic or /v1 suffix)"
    )


class OpenAICompatibleEmbeddingProvider:
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

    @property
    def name(self) -> str:
        return f"openai-compatible:{self.config.model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"required embedding credential environment variable is unset: "
                f"{self.config.api_key_env}"
            )
        url = f"{self.config.base_url.rstrip('/')}/embeddings"
        last_error: Exception | None = None
        data = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(
                    transport=self.transport,
                    timeout=self.config.timeout_seconds,
                ) as client:
                    response = client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": self.config.model, "input": texts},
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable embedding response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                break
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.sleep(min(2**attempt, 8))
        if data is None:
            raise ModelResponseError(
                f"embedding request failed after {self.config.max_retries + 1} attempts"
            ) from last_error
        vectors = np.asarray([item["embedding"] for item in data], dtype=np.float32)
        if vectors.shape[0] != len(texts):
            raise ModelResponseError("embedding response count does not match input count")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms


class OpenAICompatibleVisionDescriber:
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

    def describe(self, image_path, *, context: str) -> str:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"required vision credential environment variable is unset: "
                f"{self.config.api_key_env}"
            )
        mime = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Describe only visible content neutrally. Preserve labels, spatial "
                        "relationships and uncertainty. Do not decide compliance."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Source context: {context}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                },
            ],
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(
                    transport=self.transport,
                    timeout=self.config.timeout_seconds,
                ) as client:
                    response = client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable vision response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ModelResponseError("vision response is empty")
                return content.strip()
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.sleep(min(2**attempt, 8))
        raise ModelResponseError(
            f"vision request failed after {self.config.max_retries + 1} attempts"
        ) from last_error
