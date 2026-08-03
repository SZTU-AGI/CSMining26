from __future__ import annotations

from pathlib import Path

from freca.integrity import assess_evidence_integrity
from freca.models import (
    CaseRecord,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceRecord,
    SourceType,
)
from freca.pipeline import run_evidence_integrity_gate
from freca.state import atomic_write_json


def _source(track: int, *, flags: list[str] | None = None) -> SourceRecord:
    return SourceRecord(
        source_id=f"case-001-t{track}",
        case_id=1,
        track=track,
        re_number="RE-TEST-0001",
        path=Path(f"track-{track}.docx"),
        source_type=SourceType.DOCX,
        sha256="a" * 64,
        flags=flags or [],
    )


def _chunk(track: int, *, flags: list[str] | None = None, content: str = "evidence") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"case-001-t{track}-p1",
        case_id=1,
        re_number="RE-TEST-0001",
        track=track,
        source_id=f"case-001-t{track}",
        source_file=f"track-{track}.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=1),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
        flags=flags or [],
    )


def test_integrity_gate_reports_missing_track_contamination_and_empty_source() -> None:
    case = CaseRecord(
        case_id=1,
        re_number="RE-TEST-0001",
        sources=[_source(1), _source(2, flags=["shared_re_directory"]), _source(3)],
        missing_tracks=[4],
        flags=["missing_track_4", "shared_re_directory"],
    )

    report = assess_evidence_integrity(
        cases=[case],
        chunks=[_chunk(1), _chunk(2, flags=["embedded_re_number_mismatch"]), _chunk(3, content="")],
    )

    assert report.summary == {"BLOCKER": 2, "REVIEW": 2, "PASS": 0}
    assert [(finding.code, finding.track) for finding in report.findings] == [
        ("embedded_re_number_mismatch", 2),
        ("shared_re_directory", 2),
        ("empty_parsed_source", 3),
        ("missing_track", 4),
    ]
    assert all(finding.business_verdict is None for finding in report.findings)


def test_integrity_gate_returns_pass_for_complete_case_with_nonempty_evidence() -> None:
    case = CaseRecord(
        case_id=1,
        re_number="RE-TEST-0001",
        sources=[_source(track) for track in range(1, 10)],
    )

    report = assess_evidence_integrity(
        cases=[case],
        chunks=[_chunk(track) for track in range(1, 10)],
    )

    assert report.summary == {"BLOCKER": 0, "REVIEW": 0, "PASS": 1}
    assert report.findings == []
    assert report.case_statuses[0].status == "PASS"


def test_integrity_gate_deduplicates_repeated_chunk_flags_from_one_source() -> None:
    case = CaseRecord(
        case_id=1,
        re_number="RE-TEST-0001",
        sources=[_source(1)],
    )

    report = assess_evidence_integrity(
        cases=[case],
        chunks=[
            _chunk(1, flags=["embedded_re_number_mismatch"]),
            _chunk(1, flags=["embedded_re_number_mismatch"]),
        ],
    )

    assert report.summary == {"BLOCKER": 1, "REVIEW": 0, "PASS": 0}
    assert len(report.findings) == 1


def test_pipeline_gate_reads_parsed_artifacts_and_writes_a_report(tmp_path: Path) -> None:
    case = CaseRecord(
        case_id=1,
        re_number="RE-TEST-0001",
        sources=[_source(1)],
        missing_tracks=[2],
    )
    atomic_write_json(
        tmp_path / "manifests" / "cases.json",
        {
            "cases_root": str(tmp_path),
            "cases": [case.model_dump(mode="json")],
            "source_count": 1,
        },
    )
    atomic_write_json(
        tmp_path / "parsed" / "cases" / "001" / "track-1.json",
        [_chunk(1).model_dump(mode="json")],
    )

    result = run_evidence_integrity_gate(tmp_path)

    assert result["summary"] == {"BLOCKER": 0, "REVIEW": 1, "PASS": 0}
    assert Path(result["path"]).name == "evidence-integrity.json"
