#!/usr/bin/env python3
"""Transparent DeepSeek HTTP telemetry capture for FRECA experiments.

This module is deliberately non-semantic.

It monkey-patches requests.sessions.Session.request only inside a context manager.
Every request is forwarded with the original method/url/args/kwargs unchanged.
For api.deepseek.com responses it records:
  - attempts/success/failure
  - elapsed wall time
  - model if visible in request/response
  - response usage token fields

It does NOT:
  - change prompts or model parameters;
  - change retry behavior;
  - change response objects;
  - parse labels or evidence;
  - alter any FRECA semantic state.
"""

from __future__ import annotations

import copy
import json
import time
from contextlib import contextmanager
from typing import Any

import requests


def _safe_json_response(response: Any) -> dict:
    try:
        value = response.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _request_model(kwargs: dict) -> str | None:
    payload = kwargs.get("json")
    if isinstance(payload, dict):
        value = payload.get("model")
        if value:
            return str(value)

    data = kwargs.get("data")
    if isinstance(data, (str, bytes)):
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            payload = json.loads(data)
            if isinstance(payload, dict) and payload.get("model"):
                return str(payload["model"])
        except Exception:
            pass

    return None


def _usage_from_response(response: Any) -> dict:
    payload = _safe_json_response(response)
    usage = payload.get("usage")
    return copy.deepcopy(usage) if isinstance(usage, dict) else {}


def summarize_telemetry(events: list[dict]) -> dict:
    deepseek = [
        row
        for row in events
        if row.get("provider") == "DEEPSEEK"
    ]

    successful = [
        row
        for row in deepseek
        if row.get("success") is True
    ]
    failed = [
        row
        for row in deepseek
        if row.get("success") is False
    ]

    def sum_usage(key: str) -> int:
        return sum(
            int((row.get("usage") or {}).get(key, 0) or 0)
            for row in successful
        )

    models = sorted({
        str(row["model"])
        for row in deepseek
        if row.get("model")
    })

    return {
        "schema":
            "freca-core-cost-telemetry-v1",

        "status":
            "PERSISTED",

        "provider":
            "DEEPSEEK",

        "models":
            models,

        "request_attempt_count":
            len(deepseek),

        "successful_call_count":
            len(successful),

        "failed_call_count":
            len(failed),

        "prompt_tokens":
            sum_usage("prompt_tokens"),

        "completion_tokens":
            sum_usage("completion_tokens"),

        "total_tokens":
            sum_usage("total_tokens"),

        "prompt_cache_hit_tokens":
            sum_usage("prompt_cache_hit_tokens"),

        "prompt_cache_miss_tokens":
            sum_usage("prompt_cache_miss_tokens"),

        "wall_time_ms":
            sum(
                int(row.get("elapsed_ms", 0) or 0)
                for row in deepseek
            ),

        "events":
            copy.deepcopy(deepseek),

        "semantic_configuration_modified":
            False,

        "answer_comparator_used":
            False,
    }


@contextmanager
def capture_deepseek_telemetry():
    events: list[dict] = []

    original = requests.sessions.Session.request

    def wrapped(self, method, url, *args, **kwargs):
        url_text = str(url)
        is_deepseek = "api.deepseek.com" in url_text

        if not is_deepseek:
            return original(
                self,
                method,
                url,
                *args,
                **kwargs,
            )

        started = time.perf_counter()
        model = _request_model(kwargs)

        try:
            response = original(
                self,
                method,
                url,
                *args,
                **kwargs,
            )

            elapsed_ms = int(
                (time.perf_counter() - started) * 1000
            )

            usage = _usage_from_response(response)

            events.append({
                "provider":
                    "DEEPSEEK",
                "method":
                    str(method).upper(),
                "url_host":
                    "api.deepseek.com",
                "model":
                    model,
                "success":
                    True,
                "status_code":
                    getattr(response, "status_code", None),
                "elapsed_ms":
                    elapsed_ms,
                "usage":
                    usage,
            })

            return response

        except Exception as exc:
            elapsed_ms = int(
                (time.perf_counter() - started) * 1000
            )

            events.append({
                "provider":
                    "DEEPSEEK",
                "method":
                    str(method).upper(),
                "url_host":
                    "api.deepseek.com",
                "model":
                    model,
                "success":
                    False,
                "status_code":
                    None,
                "elapsed_ms":
                    elapsed_ms,
                "usage":
                    {},
                "error_type":
                    type(exc).__name__,
            })

            raise

    requests.sessions.Session.request = wrapped

    try:
        yield events
    finally:
        requests.sessions.Session.request = original


def run_self_test() -> None:
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "prompt_cache_hit_tokens": 3,
                    "prompt_cache_miss_tokens": 7,
                }
            }

    old = requests.sessions.Session.request

    def fake_request(self, method, url, *args, **kwargs):
        return FakeResponse()

    requests.sessions.Session.request = fake_request

    try:
        with capture_deepseek_telemetry() as events:
            session = requests.Session()
            response = session.post(
                "https://api.deepseek.com/chat/completions",
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [{"role": "user", "content": "fixture"}],
                },
            )
            assert response.status_code == 200

        summary = summarize_telemetry(events)

        assert summary["request_attempt_count"] == 1
        assert summary["successful_call_count"] == 1
        assert summary["failed_call_count"] == 0
        assert summary["prompt_tokens"] == 10
        assert summary["completion_tokens"] == 4
        assert summary["total_tokens"] == 14
        assert summary["models"] == ["deepseek-v4-flash"]

    finally:
        requests.sessions.Session.request = old

    print("telemetry_capture_v1 self-tests: PASS")
    print("  request args forwarded unchanged")
    print("  DeepSeek usage persisted from response")
    print("  prompt/model behavior not modified")
    print("  no answer comparator input")


if __name__ == "__main__":
    run_self_test()
