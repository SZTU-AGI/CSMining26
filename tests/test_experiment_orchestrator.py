"""Tests for the experiment orchestrator — uses replay client, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from freca.experiments.materials import load_material_snapshot_from_parsed
from freca.experiments.models import ExperimentMethod, ExecutionPlan, Track3Condition
from freca.experiments.orchestrator import materialize_for_unit, run_experiment
from freca.experiments.planning import build_execution_plan
from freca.experiments.runner import run_execution
from freca.llm import ModelResponseError, ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
)


def _checkpoint(cp_id: str) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element-1",
        section_title="Official section",
        text=f"Official checkpoint {cp_id}",
        source_file="checkingpoints_all_elements_onesheet.xlsx",
        cell="A3",
    )


def _make_parsed(tmp_path: Path, case_id: int = 7) -> Path:
    parsed = tmp_path / "parsed"
    (parsed / "cases" / f"{case_id:03d}").mkdir(parents=True)
    policy = EvidenceChunk(
        chunk_id="policy:page:1",
        case_id=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=1),
        content="Official policy text",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )
    case = EvidenceChunk(
        chunk_id=f"case:{case_id}:track1",
        case_id=case_id,
        source_id="case-track-1",
        source_file="track-1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content="Farm evidence",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
    )
    (parsed / "policy.json").write_text(
        json.dumps([policy.model_dump(mode="json")]), encoding="utf-8"
    )
    (parsed / "cases" / f"{case_id:03d}" / "track-1.json").write_text(
        json.dumps([case.model_dump(mode="json")]), encoding="utf-8"
    )
    return parsed


def _plan(method: ExperimentMethod, case_id: int = 7) -> ExecutionPlan:
    return build_execution_plan(
        method,
        case_id=case_id,
        checkpoints=[_checkpoint("CP1"), _checkpoint("CP2")],
    )


def test_orchestrator_runs_all_units_and_persists_artifacts(tmp_path: Path) -> None:
    parsed = _make_parsed(tmp_path)
    plan = _plan(ExperimentMethod.CASE_FULL)

    client = ReplayJsonClient(
        [
            {
                "verdicts": [
                    {
                        "cp_id": "CP1",
                        "verdict": "1",
                        "reason": "documented",
                        "citation_ids": ["case:7:track1"],
                        "uncertainty": 0.1,
                    },
                    {
                        "cp_id": "CP2",
                        "verdict": "0",
                        "reason": "missing",
                        "citation_ids": ["case:7:track1"],
                        "uncertainty": 0.5,
                    },
                ]
            }
        ]
    )

    results = run_experiment(
        plan=plan,
        checkpoints=[_checkpoint("CP1"), _checkpoint("CP2")],
        parsed_dir=parsed,
        track3_condition=Track3Condition.RAW,
        client=client,
        artifact_root=tmp_path / "build",
    )

    assert len(results) == 1
    assert results[0].valid is True
    summary = json.loads(
        (tmp_path / "build" / "case_full" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["units_valid"] == 1
    assert summary["verdicts_total"] == 2
    unit_dir = tmp_path / "build" / "case_full" / "case-007" / "track3-raw" / "unit-000"
    assert (unit_dir / "request.json").exists()
    assert (unit_dir / "response.json").exists()
    assert (unit_dir / "result.json").exists()


def test_orchestrator_runs_each_unit_for_checkpoint_full(tmp_path: Path) -> None:
    parsed = _make_parsed(tmp_path)
    plan = _plan(ExperimentMethod.CHECKPOINT_FULL)

    client = ReplayJsonClient(
        [
            {
                "verdicts": [
                    {
                        "cp_id": "CP1",
                        "verdict": "1",
                        "reason": "ok",
                        "citation_ids": ["case:7:track1"],
                        "uncertainty": 0.1,
                    }
                ]
            },
            {
                "verdicts": [
                    {
                        "cp_id": "CP2",
                        "verdict": "0",
                        "reason": "bad",
                        "citation_ids": ["case:7:track1"],
                        "uncertainty": 0.1,
                    }
                ]
            },
        ]
    )

    results = run_experiment(
        plan=plan,
        checkpoints=[_checkpoint("CP1"), _checkpoint("CP2")],
        parsed_dir=parsed,
        track3_condition=Track3Condition.RAW,
        client=client,
        artifact_root=tmp_path / "build",
    )

    assert len(results) == 2
    assert all(result.valid for result in results)
    summary = json.loads(
        (tmp_path / "build" / "checkpoint_full" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["units_total"] == 2
    assert summary["units_valid"] == 2


def test_run_execution_persists_response_when_client_raises(tmp_path: Path) -> None:
    parsed = _make_parsed(tmp_path)
    material = load_material_snapshot_from_parsed(
        parsed_dir=parsed,
        case_id=7,
        checkpoints=[_checkpoint("CP1")],
        track3_condition=Track3Condition.RAW,
    )
    plan = build_execution_plan(
        ExperimentMethod.CHECKPOINT_FULL,
        case_id=7,
        checkpoints=[_checkpoint("CP1")],
    )

    class RaisingClient:
        def complete_json(self, **_: object) -> dict[str, object]:
            raise ModelResponseError("model response is not valid JSON")


    artifact_dir = tmp_path / "build" / "fail"
    result = run_execution(
        unit=plan.units[0],
        material=material,
        client=RaisingClient(),  # type: ignore[arg-type]
        artifact_dir=artifact_dir,
    )

    assert result.valid is False
    assert "model response is not valid JSON" in result.errors
    response = json.loads((artifact_dir / "response.json").read_text(encoding="utf-8"))
    assert "error" in response


def test_run_execution_scales_max_tokens_with_checkpoint_count(tmp_path: Path) -> None:
    parsed = tmp_path / "parsed"
    (parsed / "cases" / "007").mkdir(parents=True)
    policy = EvidenceChunk(
        chunk_id="policy:page:1",
        case_id=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=1),
        content="Policy",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )
    case = EvidenceChunk(
        chunk_id="case:7:track1",
        case_id=7,
        source_id="case-track-1",
        source_file="track-1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content="case",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
    )
    (parsed / "policy.json").write_text(
        json.dumps([policy.model_dump(mode="json")]), encoding="utf-8"
    )
    (parsed / "cases" / "007" / "track-1.json").write_text(
        json.dumps([case.model_dump(mode="json")]), encoding="utf-8"
    )

    checkpoints = [_checkpoint(f"CP{i}") for i in range(1, 6)]
    material = load_material_snapshot_from_parsed(
        parsed_dir=parsed,
        case_id=7,
        checkpoints=checkpoints,
        track3_condition=Track3Condition.RAW,
    )
    plan = build_execution_plan(
        ExperimentMethod.CASE_FULL,
        case_id=7,
        checkpoints=checkpoints,
    )

    captured: dict[str, int] = {}

    class CapturingClient:
        def complete_json(self, **kwargs: object) -> dict[str, object]:
            captured["max_tokens"] = kwargs.get("max_tokens")  # type: ignore[assignment]
            return {"verdicts": []}


    run_execution(
        unit=plan.units[0],
        material=material,
        client=CapturingClient(),  # type: ignore[arg-type]
        artifact_dir=tmp_path / "build" / "capt",
    )

    assert captured["max_tokens"] == len(checkpoints) * 1500 + 4096


def test_orchestrator_surfaces_invalid_results_without_aborting(tmp_path: Path) -> None:
    parsed = _make_parsed(tmp_path)
    plan = _plan(ExperimentMethod.CASE_FULL)

    client = ReplayJsonClient(
        [
            {
                "verdicts": [
                    {
                        "cp_id": "CP1",
                        "verdict": "BAD",
                        "reason": "x",
                        "citation_ids": ["case:7:track1"],
                        "uncertainty": 0.1,
                    }
                ]
            }
        ]
    )

    results = run_experiment(
        plan=plan,
        checkpoints=[_checkpoint("CP1"), _checkpoint("CP2")],
        parsed_dir=parsed,
        track3_condition=Track3Condition.RAW,
        client=client,
        artifact_root=tmp_path / "build",
    )

    assert len(results) == 1
    assert results[0].valid is False
    assert any("Input should be '1', '0' or 'N/A'" in e for e in results[0].errors)


def test_materialize_for_unit_applies_automatic_retrieval(tmp_path: Path) -> None:
    parsed = tmp_path / "parsed"
    (parsed / "cases" / "007").mkdir(parents=True)
    policy_a = EvidenceChunk(
        chunk_id="policy:trace",
        case_id=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=1),
        content="Traceability records identify consignments",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )
    policy_b = EvidenceChunk(
        chunk_id="policy:pests",
        case_id=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=2),
        content="Pest controls are documented",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )
    case_a = EvidenceChunk(
        chunk_id="case:7:trace",
        case_id=7,
        source_id="case-track-1",
        source_file="track-1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content="Consignment traceability record 2026",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
    )
    case_b = EvidenceChunk(
        chunk_id="case:7:water",
        case_id=7,
        source_id="case-track-1",
        source_file="track-1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=1),
        content="Irrigation water analysis",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
    )
    (parsed / "policy.json").write_text(
        json.dumps(
            [policy_a.model_dump(mode="json"), policy_b.model_dump(mode="json")]
        ),
        encoding="utf-8",
    )
    (parsed / "cases" / "007" / "track-1.json").write_text(
        json.dumps(
            [case_a.model_dump(mode="json"), case_b.model_dump(mode="json")]
        ),
        encoding="utf-8",
    )

    cp = _checkpoint("CP1").model_copy(
        update={"text": "Traceability records must identify consignments"}
    )
    material = materialize_for_unit(
        parsed_dir=parsed,
        case_id=7,
        checkpoints=[cp],
        unit_method=ExperimentMethod.AUTOMATIC_RETRIEVAL,
        unit_checkpoint_ids=("CP1",),
        track3_condition=Track3Condition.RAW,
        per_scope_limit=1,
    )

    assert material.chunk_ids == ("policy:trace", "case:7:trace")


def test_orchestrator_runs_automatic_retrieval_end_to_end(tmp_path: Path) -> None:
    parsed = tmp_path / "parsed"
    (parsed / "cases" / "007").mkdir(parents=True)
    policy = EvidenceChunk(
        chunk_id="policy:trace",
        case_id=None,
        source_id="policy",
        source_file="policy.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=1),
        content="Traceability records identify consignments",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )
    case = EvidenceChunk(
        chunk_id="case:7:trace",
        case_id=7,
        source_id="case-track-1",
        source_file="track-1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content="Consignment traceability record 2026",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
    )
    (parsed / "policy.json").write_text(
        json.dumps([policy.model_dump(mode="json")]), encoding="utf-8"
    )
    (parsed / "cases" / "007" / "track-1.json").write_text(
        json.dumps([case.model_dump(mode="json")]), encoding="utf-8",
    )

    cp = _checkpoint("CP1").model_copy(
        update={"text": "Traceability records must identify consignments"}
    )
    plan = build_execution_plan(
        ExperimentMethod.AUTOMATIC_RETRIEVAL,
        case_id=7,
        checkpoints=[cp],
    )
    client = ReplayJsonClient(
        [
            {
                "verdicts": [
                    {
                        "cp_id": "CP1",
                        "verdict": "1",
                        "reason": "ok",
                        "citation_ids": ["case:7:trace"],
                        "uncertainty": 0.1,
                    }
                ]
            }
        ]
    )

    results = run_experiment(
        plan=plan,
        checkpoints=[cp],
        parsed_dir=parsed,
        track3_condition=Track3Condition.RAW,
        client=client,
        artifact_root=tmp_path / "build",
    )

    assert len(results) == 1
    assert results[0].valid is True