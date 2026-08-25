"""Regression coverage for the previously used MiniMax Anthropic endpoint."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from freca.config import ModelEndpointConfig, PipelineConfig, ResponseFormatMode
from freca.ledger.config import LedgerConfig


def _endpoint() -> ModelEndpointConfig:
    return ModelEndpointConfig(
        base_url="https://api.minimaxi.com/anthropic",
        model="MiniMax-M3",
        api_key_env="FRECA_AUDIT_API_KEY",
        max_retries=0,
        response_format=ResponseFormatMode.JSON_OBJECT,
    )


def test_minimax_anthropic_client_posts_messages_request(monkeypatch) -> None:
    from freca.llm import AnthropicMessagesClient

    monkeypatch.setenv("FRECA_AUDIT_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.minimaxi.com/anthropic/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["model"] == "MiniMax-M3"
        assert body["system"].startswith("system")
        assert "JSON schema" in body["system"]
        assert '"required": ["ok"]' in body["system"]
        assert body["messages"] == [{"role": "user", "content": "user"}]
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"ok": true}'}]},
        )

    client = AnthropicMessagesClient(_endpoint(), transport=httpx.MockTransport(handler))

    assert client.complete_json(
        system="system",
        user="user",
        schema={"type": "object", "required": ["ok"]},
    ) == {"ok": True}


def test_legacy_dotenv_overrides_audit_endpoint_without_exposing_secret(
    monkeypatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "llm_key='test-key'\nllm_url='https://api.minimaxi.com/anthropic'\nllm_model='MiniMax-M3'\n",
        encoding="utf-8",
    )
    for name in ("FRECA_AUDIT_API_KEY", "FRECA_AUDIT_BASE_URL", "FRECA_AUDIT_MODEL"):
        monkeypatch.delenv(name, raising=False)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
paths:
  cases_root: cases
  policy_pdf: policy.pdf
  checkpoints_xlsx: checkpoints.xlsx
  submission_template: submission.xlsx
  build_dir: build
models:
  audit:
    base_url: https://api.example.invalid/v1
    model: configure-audit-model
    api_key_env: FRECA_AUDIT_API_KEY
""".strip(),
        encoding="utf-8",
    )

    config = PipelineConfig.from_yaml(config_path)

    assert config.models.audit.base_url == "https://api.minimaxi.com/anthropic"
    assert config.models.audit.model == "MiniMax-M3"
    assert os.environ["FRECA_AUDIT_API_KEY"] == "test-key"


def test_minimax_runtime_config_reuses_the_audit_credential_for_all_review_roles() -> None:
    config = PipelineConfig.from_yaml(Path("config.minimax.yaml"))

    for endpoint in (
        config.models.audit,
        config.models.verifier,
        config.models.arbitrator,
    ):
        assert endpoint is not None
        assert endpoint.base_url == "https://api.minimaxi.com/anthropic"
        assert endpoint.model == "MiniMax-M3"
        assert endpoint.api_key_env == "FRECA_AUDIT_API_KEY"
        assert endpoint.response_format == ResponseFormatMode.JSON_OBJECT


def test_minimax_request_contract_metadata_invalidates_pre_schema_cache() -> None:
    from freca.llm import request_contract_metadata

    metadata = request_contract_metadata(_endpoint())

    assert metadata["protocol"] == "anthropic_messages"
    assert metadata["request_contract_version"] == 2


def test_ledger_config_loads_the_same_local_minimax_credential(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("llm_key='test-key'\n", encoding="utf-8")
    monkeypatch.delenv("FRECA_AUDIT_API_KEY", raising=False)
    config_path = tmp_path / "config.ledger.yaml"
    config_path.write_text(
        """
paths:
  cases_root: cases
  policy_pdf: policy.pdf
  checkpoints_xlsx: checkpoints.xlsx
  submission_template: submission.xlsx
  build_dir: build
models:
  audit:
    base_url: https://api.minimaxi.com/anthropic
    model: MiniMax-M3
    api_key_env: FRECA_AUDIT_API_KEY
ledger: {}
""".strip(),
        encoding="utf-8",
    )

    config = LedgerConfig.from_yaml(config_path)

    assert config.endpoint("adjudicator") is not None
    assert os.environ["FRECA_AUDIT_API_KEY"] == "test-key"
