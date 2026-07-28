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


class JsonChatClient(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise ModelResponseError("model response is not valid JSON object")
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
        raise ModelResponseError("model response is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelResponseError("model response is not a JSON object")
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
            response = self.inner.complete_json(system=system, user=user, schema=schema)
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
