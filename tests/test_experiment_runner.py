from pathlib import Path

from freca.experiments.materials import build_material_snapshot
from freca.experiments.models import ExperimentMethod, ExecutionUnit
from freca.experiments.runner import run_execution
from freca.llm import ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
)
from freca.state import read_json


def _unit() -> ExecutionUnit:
    return ExecutionUnit(
        case_id=7,
        method=ExperimentMethod.CASE_FULL,
        checkpoint_ids=("CP1",),
    )


def _material():
    checkpoint = CheckpointDefinition(
        cp_id="CP1",
        element_id=1,
        element_title="Element-1",
        section_title="Official section",
        text="Official checkpoint CP1",
        source_file="checkingpoints_all_elements_onesheet.xlsx",
        cell="A3",
    )
    evidence = EvidenceChunk(
        chunk_id="case:7:track1",
        case_id=7,
        track=1,
        source_id="case-7-track-1",
        source_file="track-1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content="Official farm evidence",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )
    return build_material_snapshot(
        case_id=7,
        checkpoints=[checkpoint],
        policy_chunks=[],
        case_chunks=[evidence],
    )


def test_runner_persists_raw_response_and_accepts_snapshot_citations(tmp_path: Path) -> None:
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
                    }
                ]
            }
        ]
    )

    result = run_execution(
        unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path
    )

    assert result.valid is True
    assert result.verdicts[0].verdict == "1"
    assert read_json(tmp_path / "response.json")["verdicts"][0]["cp_id"] == "CP1"
    assert "Official checkpoint CP1" in read_json(tmp_path / "request.json")["text"]


def test_runner_rejects_verdicts_that_cite_unknown_sources(tmp_path: Path) -> None:
    client = ReplayJsonClient(
        [
            {
                "verdicts": [
                    {
                        "cp_id": "CP1",
                        "verdict": "0",
                        "reason": "unsupported",
                        "citation_ids": ["other-case"],
                        "uncertainty": 0.8,
                    }
                ]
            }
        ]
    )

    result = run_execution(
        unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path
    )

    assert result.valid is False
    assert result.errors == ("unknown citation_ids: other-case",)
