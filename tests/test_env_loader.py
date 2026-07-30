"""Tests for .env loading and audit client factory — no network."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from freca.config import ModelEndpointConfig, PipelineConfig, ResponseFormatMode
from freca.llm import AnthropicMessagesClient, OpenAICompatibleJsonClient


def _audit_config(
    *,
    base_url: str,
    model: str = "MiniMax-M3",
    response_format: ResponseFormatMode = ResponseFormatMode.JSON_OBJECT,
) -> ModelEndpointConfig:
    return ModelEndpointConfig(
        base_url=base_url,
        model=model,
        api_key_env="FRECA_AUDIT_API_KEY",
        timeout_seconds=30,
        max_retries=2,
        response_format=response_format,
    )


def test_env_loader_maps_known_fields_into_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRECA_AUDIT_API_KEY", raising=False)
    monkeypatch.delenv("FRECA_AUDIT_BASE_URL", raising=False)
    monkeypatch.delenv("FRECA_AUDIT_MODEL", raising=False)
    from freca.env_loader import apply_env_file

    env_path = Path(__file__).parent / "fixtures" / "sample.env"
    apply_env_file(env_path, env=os.environ)

    assert os.environ["FRECA_AUDIT_API_KEY"].startswith("sk-")
    assert os.environ["FRECA_AUDIT_BASE_URL"].endswith("/anthropic")
    assert os.environ["FRECA_AUDIT_MODEL"] == "MiniMax-M3"


def test_env_loader_does_not_overwrite_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "pre-existing-key")
    from freca.env_loader import apply_env_file

    env_path = Path(__file__).parent / "fixtures" / "sample.env"
    apply_env_file(env_path, env=os.environ)

    assert os.environ["FRECA_AUDIT_API_KEY"] == "pre-existing-key"


def test_env_loader_searches_upwards_for_dot_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    from freca.env_loader import find_env_file

    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    target = tmp_path / ".env"
    target.write_text("FRECA_AUDIT_API_KEY=k\n", encoding="utf-8")

    assert find_env_file(nested) == target


def test_audit_client_factory_picks_anthropic_for_anthropic_base_url() -> None:
    from freca.llm import build_audit_client

    client = build_audit_client(_audit_config(base_url="https://api.minimaxi.com/anthropic"))
    assert isinstance(client, AnthropicMessagesClient)


def test_audit_client_factory_picks_openai_for_openai_base_url() -> None:
    from freca.llm import build_audit_client

    client = build_audit_client(_audit_config(base_url="https://api.openai.com/v1"))
    assert isinstance(client, OpenAICompatibleJsonClient)


def test_audit_client_factory_rejects_unrecognised_base_url() -> None:
    from freca.llm import build_audit_client

    with pytest.raises(ValueError, match="unrecognised audit base_url"):
        build_audit_client(_audit_config(base_url="https://example.invalid/api/chat"))


def test_pipeline_config_loads_audit_endpoint_from_yaml(tmp_path: Path) -> None:
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        """
paths:
  cases_root: /tmp/cases
  policy_pdf: /tmp/p.pdf
  checkpoints_xlsx: /tmp/cp.xlsx
  submission_template: /tmp/sub.xlsx
  build_dir: /tmp/build
models:
  audit:
    base_url: https://api.minimaxi.com/anthropic
    model: MiniMax-M3
    api_key_env: FRECA_AUDIT_API_KEY
    response_format: json_object
""",
        encoding="utf-8",
    )
    config = PipelineConfig.from_yaml(config_yaml)

    assert config.models.audit.base_url == "https://api.minimaxi.com/anthropic"
    assert config.models.audit.model == "MiniMax-M3"
    assert config.models.audit.response_format == ResponseFormatMode.JSON_OBJECT


def test_pipeline_config_overrides_audit_from_env(tmp_path: Path) -> None:
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        """
paths:
  cases_root: /tmp/cases
  policy_pdf: /tmp/p.pdf
  checkpoints_xlsx: /tmp/cp.xlsx
  submission_template: /tmp/sub.xlsx
  build_dir: /tmp/build
models:
  audit:
    base_url: https://api.example.invalid/v1
    model: configure-audit-model
    api_key_env: FRECA_AUDIT_API_KEY
""",
        encoding="utf-8",
    )
    import os

    from freca.config import PipelineConfig

    previous = {
        "FRECA_AUDIT_BASE_URL": os.environ.pop("FRECA_AUDIT_BASE_URL", None),
        "FRECA_AUDIT_MODEL": os.environ.pop("FRECA_AUDIT_MODEL", None),
    }
    try:
        config = PipelineConfig.from_yaml(config_yaml)
        assert config.models.audit.base_url == "https://api.example.invalid/v1"
        assert config.models.audit.model == "configure-audit-model"

        os.environ["FRECA_AUDIT_BASE_URL"] = "https://api.minimaxi.com/anthropic"
        os.environ["FRECA_AUDIT_MODEL"] = "MiniMax-M3"
        config = PipelineConfig.from_yaml(config_yaml)
        assert config.models.audit.base_url == "https://api.minimaxi.com/anthropic"
        assert config.models.audit.model == "MiniMax-M3"
    finally:
        for name, prior in previous.items():
            if prior is not None:
                os.environ[name] = prior
            else:
                os.environ.pop(name, None)