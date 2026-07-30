"""Tests for the audit prompt builder and verdict validation."""
from __future__ import annotations

import json
from pathlib import Path

from freca.experiments.models import (
    ExecutionUnit,
    ExperimentMethod,
    MaterialSnapshot,
    Track3Condition,
)
from freca.experiments.prompts import build_prompt
from freca.experiments.runner import validate_response
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
)


def _chunk(chunk_id: str, case_id: int | None = None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        source_id="policy" if case_id is None else "case-track-1",
        source_file="policy.pdf" if case_id is None else "track-1.docx",
        source_type=SourceType.PDF if case_id is None else SourceType.DOCX,
        location=SourceLocation(page=1) if case_id is None else SourceLocation(paragraph_index=0),
        content="content",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )


def _cp(cp_id: str) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element-1",
        section_title="section",
        text=f"checkpoint {cp_id}",
        source_file="cp.xlsx",
        cell="A1",
    )


def _snapshot(chunks: tuple[EvidenceChunk, ...], cp_ids: tuple[str, ...]) -> MaterialSnapshot:
    import hashlib

    payload = json.dumps(
        {
            "case_id": 1,
            "chunks": [c.model_dump(mode="json") for c in chunks],
            "track3": Track3Condition.RAW.value,
        },
        sort_keys=True,
    )
    return MaterialSnapshot(
        case_id=1,
        checkpoints=tuple(_cp(cid) for cid in cp_ids),
        chunks=chunks,
        image_paths=(),
        track3_condition=Track3Condition.RAW,
        input_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def test_build_prompt_includes_allowed_citation_ids_whitelist() -> None:
    chunks = (
        _chunk("case-001-t5_paragraph-0024_d8a344119e", case_id=1),
        _chunk("policy-rules-2021_page-0025_f8857ea0d7"),
    )
    snapshot = _snapshot(chunks, ("CP1",))
    unit = ExecutionUnit(
        case_id=1, method=ExperimentMethod.CHECKPOINT_FULL, checkpoint_ids=("CP1",)
    )

    envelope = build_prompt(unit=unit, material=snapshot)
    payload = json.loads(envelope.text)

    assert payload["allowed_citation_ids"] == [
        "case-001-t5_paragraph-0024_d8a344119e",
        "policy-rules-2021_page-0025_f8857ea0d7",
    ]


def test_validate_response_accepts_real_citation_ids() -> None:
    chunks = (_chunk("case-001-t5_paragraph-0024_d8a344119e", case_id=1),)
    snapshot = _snapshot(chunks, ("CP1",))
    unit = ExecutionUnit(
        case_id=1, method=ExperimentMethod.CHECKPOINT_FULL, checkpoint_ids=("CP1",)
    )

    raw = {
        "verdicts": [
            {
                "cp_id": "CP1",
                "verdict": "1",
                "reason": "ok",
                "citation_ids": ["case-001-t5_paragraph-0024_d8a344119e"],
                "uncertainty": 0.1,
            }
        ]
    }
    result = validate_response(unit=unit, material=snapshot, raw=raw, prompt_sha256="a" * 64)

    assert result.valid is True
    assert result.errors == ()


def test_validate_response_repairs_citation_with_correct_prefix_wrong_hash() -> None:
    chunks = (_chunk("case-001-t5_paragraph-0024_d8a344119e", case_id=1),)
    snapshot = _snapshot(chunks, ("CP1",))
    unit = ExecutionUnit(
        case_id=1, method=ExperimentMethod.CHECKPOINT_FULL, checkpoint_ids=("CP1",)
    )

    # Model invented a hash but kept the semantic prefix (paragraph-0024) correct.
    raw = {
        "verdicts": [
            {
                "cp_id": "CP1",
                "verdict": "1",
                "reason": "ok",
                "citation_ids": ["case-001-t5_paragraph-0024_05acc7b4ab"],
                "uncertainty": 0.1,
            }
        ]
    }
    result = validate_response(unit=unit, material=snapshot, raw=raw, prompt_sha256="a" * 64)

    assert result.valid is True
    assert result.verdicts[0].citation_ids == ("case-001-t5_paragraph-0024_d8a344119e",)


def test_validate_response_rejects_citation_with_no_prefix_match() -> None:
    chunks = (_chunk("case-001-t5_paragraph-0024_d8a344119e", case_id=1),)
    snapshot = _snapshot(chunks, ("CP1",))
    unit = ExecutionUnit(
        case_id=1, method=ExperimentMethod.CHECKPOINT_FULL, checkpoint_ids=("CP1",)
    )

    # Invented id whose prefix matches nothing real.
    raw = {
        "verdicts": [
            {
                "cp_id": "CP1",
                "verdict": "1",
                "reason": "ok",
                "citation_ids": ["case-001-t5_paragraph-9999_05acc7b4ab"],
                "uncertainty": 0.1,
            }
        ]
    }
    result = validate_response(unit=unit, material=snapshot, raw=raw, prompt_sha256="a" * 64)

    assert result.valid is False
    assert any("unknown citation_ids" in e for e in result.errors)


def test_validate_response_rejects_ambiguous_prefix_match() -> None:
    chunks = (
        _chunk("case-001-t5_paragraph-0024_d8a344119e", case_id=1),
        _chunk("case-001-t5_paragraph-0024_aaaaaaaaaa", case_id=1),
    )
    snapshot = _snapshot(chunks, ("CP1",))
    unit = ExecutionUnit(
        case_id=1, method=ExperimentMethod.CHECKPOINT_FULL, checkpoint_ids=("CP1",)
    )

    # Prefix matches two real ids -> cannot disambiguate -> reject.
    raw = {
        "verdicts": [
            {
                "cp_id": "CP1",
                "verdict": "1",
                "reason": "ok",
                "citation_ids": ["case-001-t5_paragraph-0024_bbbbbbbbbb"],
                "uncertainty": 0.1,
            }
        ]
    }
    result = validate_response(unit=unit, material=snapshot, raw=raw, prompt_sha256="a" * 64)

    assert result.valid is False
    assert any("unknown citation_ids" in e for e in result.errors)