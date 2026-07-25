from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from freca.config import (
    MinerUMode,
    PipelineConfig,
    RerankerMode,
    RetrievalAgentMode,
)


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _endpoint_is_placeholder(base_url: str, model: str) -> bool:
    lowered = f"{base_url} {model}".lower()
    return ".invalid" in lowered or "configure-" in lowered


def check_readiness(config: PipelineConfig, *, stage: str = "pilot") -> dict:
    if stage not in {"prepare", "pilot", "full"}:
        raise ValueError(f"unsupported readiness stage: {stage}")
    checks: list[dict[str, str]] = []
    for name in (
        "cases_root",
        "policy_pdf",
        "checkpoints_xlsx",
        "submission_template",
    ):
        path = getattr(config.paths, name)
        checks.append(
            _check(
                f"path:{name}",
                "PASS" if path.exists() else "ERROR",
                str(path),
            )
        )

    if config.mineru.mode == MinerUMode.DISABLED:
        checks.append(
            _check(
                "mineru",
                "WARNING",
                "disabled: prepare will use the explicit PyMuPDF fallback",
            )
        )
    elif config.mineru.mode == MinerUMode.CLOUD_SDK:
        installed = importlib.util.find_spec("mineru") is not None
        checks.append(
            _check(
                "mineru:sdk",
                "PASS" if installed else "ERROR",
                "mineru-open-sdk installed" if installed else "install Task2[mineru]",
            )
        )
        token_set = bool(
            config.mineru.token_env and os.environ.get(config.mineru.token_env)
        )
        checks.append(
            _check(
                "mineru:token",
                "PASS" if token_set else "ERROR",
                (
                    f"environment variable {config.mineru.token_env} is set"
                    if token_set
                    else f"environment variable {config.mineru.token_env} is unset"
                ),
            )
        )
    else:
        placeholder = _endpoint_is_placeholder(config.mineru.base_url, config.mineru.model)
        checks.append(
            _check(
                "mineru:remote_api",
                "ERROR" if placeholder else "PASS",
                config.mineru.base_url,
            )
        )
        if config.mineru.token_env:
            token_set = bool(os.environ.get(config.mineru.token_env))
            checks.append(
                _check(
                    "mineru:token",
                    "PASS" if token_set else "ERROR",
                    (
                        f"environment variable {config.mineru.token_env} is set"
                        if token_set
                        else f"environment variable {config.mineru.token_env} is unset; "
                        "set token_env: null for an unauthenticated private endpoint"
                    ),
                )
            )

    if stage in {"pilot", "full"}:
        for name, path in (
            ("checkpoints", config.paths.build_dir / "parsed" / "checkpoints.json"),
            ("policy_index", config.paths.build_dir / "indexes" / "policy.json"),
            ("case_index", config.paths.build_dir / "indexes" / "cases.json"),
        ):
            checks.append(
                _check(
                    f"artifact:{name}",
                    "PASS" if path.exists() else "ERROR",
                    str(path),
                )
            )
        required = {
            "audit": config.models.audit,
            "verifier": config.models.verifier,
            "arbitrator": config.models.arbitrator,
        }
        if config.retrieval.reranker_mode in {
            RerankerMode.CROSS_ENCODER_API,
            RerankerMode.LLM_LISTWISE,
        }:
            required["reranker"] = config.models.reranker
        if config.retrieval.agent_mode == RetrievalAgentMode.LLM:
            required["retrieval_agent"] = config.models.retrieval_agent
        for name, endpoint in required.items():
            if endpoint is None:
                checks.append(_check(f"model:{name}", "ERROR", "endpoint is not configured"))
                continue
            if _endpoint_is_placeholder(endpoint.base_url, endpoint.model):
                checks.append(
                    _check(f"model:{name}", "ERROR", "endpoint/model is still a placeholder")
                )
                continue
            key_set = bool(os.environ.get(endpoint.api_key_env))
            checks.append(
                _check(
                    f"model:{name}",
                    "PASS" if key_set else "ERROR",
                    (
                        f"{endpoint.model}; {endpoint.api_key_env} is set"
                        if key_set
                        else f"{endpoint.api_key_env} is unset"
                    ),
                )
            )
        optional = {
            "query_rewriter": config.models.query_rewriter,
            "embedding": config.models.embedding,
            "vision": config.models.vision,
            "planner": config.models.planner,
            "critic": config.models.critic,
            "tiebreaker": config.models.tiebreaker,
            **(
                {}
                if "reranker" in required
                else {"reranker": config.models.reranker}
            ),
            **(
                {}
                if "retrieval_agent" in required
                else {"retrieval_agent": config.models.retrieval_agent}
            ),
        }
        for name, endpoint in optional.items():
            if endpoint is None:
                checks.append(_check(f"model:{name}", "WARNING", "not configured"))
            elif _endpoint_is_placeholder(endpoint.base_url, endpoint.model):
                checks.append(
                    _check(f"model:{name}", "WARNING", "endpoint/model is still a placeholder")
                )
            elif not os.environ.get(endpoint.api_key_env):
                checks.append(
                    _check(f"model:{name}", "WARNING", f"{endpoint.api_key_env} is unset")
                )
            else:
                checks.append(_check(f"model:{name}", "PASS", endpoint.model))

    return {
        "stage": stage,
        "ready": not any(item["status"] == "ERROR" for item in checks),
        "checks": checks,
    }
