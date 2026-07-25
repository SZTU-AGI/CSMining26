import json

import httpx
import pytest

from freca.audit import audit_checkpoint, build_audit_messages
from freca.config import ModelEndpointConfig
from freca.llm import ModelResponseError, OpenAICompatibleJsonClient, ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    RetrievalBundle,
    RetrievalHit,
    SourceLocation,
    SourceType,
)


def _checkpoint() -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id="CP1",
        element_id=1,
        element_title="Element-1",
        section_title="1.1 Export operations",
        text="The establishment is operating within its registered operations.",
        source_file="cp.xlsx",
        cell="A3",
    )


def _chunk(chunk_id: str, content: str, *, case_id: int | None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        re_number="RE-TEST" if case_id else None,
        track=1 if case_id else None,
        source_id="case-001-t1" if case_id else "policy-rules-2021",
        source_file="track1.docx" if case_id else "policy.pdf",
        source_type=SourceType.DOCX if case_id else SourceType.PDF,
        location=SourceLocation(paragraph_index=0) if case_id else SourceLocation(page=10),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="c" * 64,
    )


def _bundle() -> RetrievalBundle:
    return RetrievalBundle(
        case_id=1,
        cp_id="CP1",
        policy_hits=[
            RetrievalHit(
                chunk=_chunk("policy:p010:block-01", "Registration must cover operations.", case_id=None),
                score=1,
                rank=1,
            )
        ],
        evidence_hits=[
            RetrievalHit(
                chunk=_chunk("case-001:t1:p0", "Registration covers grain storage.", case_id=1),
                score=1,
                rank=1,
            )
        ],
        rounds=[],
        complete=True,
        stop_reason="complete",
    )


def _valid_payload() -> dict:
    return {
        "case_id": 1,
        "cp_id": "CP1",
        "applicability": "APPLICABLE",
        "regulatory_requirement": "Registration must cover the operations performed.",
        "policy_citations": ["policy:p010:block-01"],
        "supporting_evidence": ["case-001:t1:p0"],
        "contrary_evidence": [],
        "contradictions": [],
        "verdict": "1",
        "reasoning_summary": "The registered scope covers the stated operation.",
        "confidence": 0.9,
        "retrieval_complete": True,
        "review_flags": [],
    }


def test_audit_uses_general_prompt_and_returns_structured_decision() -> None:
    client = ReplayJsonClient([_valid_payload()])

    decision = audit_checkpoint(client, _checkpoint(), _bundle())

    assert decision.verdict.value == "1"
    assert decision.policy_citations == ["policy:p010:block-01"]
    request = client.requests[0]
    assert "determine applicability" in request["system"].lower()
    assert _checkpoint().text in request["user"]
    assert "CP1 requires" not in request["system"]


def test_replay_client_rejects_malformed_json() -> None:
    client = ReplayJsonClient(["not-json"])
    with pytest.raises(ModelResponseError, match="valid JSON"):
        client.complete_json(system="s", user="u", schema={"type": "object"})


def test_audit_rejects_na_without_not_applicable_reasoning() -> None:
    payload = _valid_payload()
    payload.update({"applicability": "UNKNOWN", "verdict": "N/A"})

    with pytest.raises(ValueError, match="N/A requires NOT_APPLICABLE"):
        audit_checkpoint(ReplayJsonClient([json.dumps(payload)]), _checkpoint(), _bundle())


def test_prompt_contains_exact_chunk_ids_for_citation() -> None:
    system, user = build_audit_messages(_checkpoint(), _bundle())
    assert "policy:p010:block-01" in user
    assert "case-001:t1:p0" in user
    assert "Only cite chunk_id values" in system


def test_openai_compatible_client_uses_environment_secret(monkeypatch) -> None:
    monkeypatch.setenv("FRECA_TEST_API_KEY", "ephemeral-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://models.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer ephemeral-test-key"
        body = json.loads(request.content)
        assert body["model"] == "audit-model"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_valid_payload())}}]},
        )

    client = OpenAICompatibleJsonClient(
        ModelEndpointConfig(
            base_url="https://models.example/v1",
            model="audit-model",
            api_key_env="FRECA_TEST_API_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.complete_json(system="s", user="u", schema={})["verdict"] == "1"
