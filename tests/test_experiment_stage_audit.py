"""Tests for the STAGE_AUDIT (method C) 4-stage audit flow."""
from __future__ import annotations

from pathlib import Path

from freca.experiments.models import (
    ExecutionUnit,
    ExperimentMethod,
    MaterialSnapshot,
    Track3Condition,
)
from freca.experiments.stage_audit import (
    run_stage_audit_plan,
    run_stage_audit_unit,
)
from freca.llm import ReplayJsonClient
from freca.models import CheckpointDefinition, EvidenceChunk, SourceLocation, SourceType, Verdict


def _cp(cp_id: str, element_id: int = 1) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=element_id,
        element_title=f"Element-{element_id}",
        section_title="section",
        text=f"checkpoint {cp_id} requires documented pest control",
        source_file="cp.xlsx",
        cell="A1",
    )


def _chunk(chunk_id: str, track: int = 1) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=1,
        track=track,
        source_id="src-1",
        source_file="src.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content=f"content for {chunk_id}",
        content_kind="paragraph",
        parser_name="test",
        parser_version="v1",
        source_sha256="a" * 64,
    )


def _material() -> MaterialSnapshot:
    return MaterialSnapshot(
        case_id=1,
        checkpoints=(_cp("CP1"),),
        chunks=(_chunk("case-001-t1_paragraph-0001_d8a344119e", track=1),),
        track3_condition=Track3Condition.RAW,
        input_sha256="a" * 64,
    )


def _unit() -> ExecutionUnit:
    return ExecutionUnit(case_id=1, method=ExperimentMethod.STAGE_AUDIT, checkpoint_ids=("CP1",))


def test_stage_audit_applicable_path_jumps_to_judgment(tmp_path: Path) -> None:
    client = ReplayJsonClient(
        responses=[
            {
                "applicability": "APPLICABLE",
                "reason": "case is a farm that should keep pest records",
                "policy_citations": ["policy_clause_1"],
                "uncertainty": 0.1,
            },
            {
                "verdict": "1",
                "reason": "records present",
                "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"],
                "uncertainty": 0.1,
                "contradictions": [],
            },
        ]
    )

    result = run_stage_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    assert len(client.requests) == 2
    # First call = applicability, second = judgment; contrary-search was skipped.
    assert result.stage_trace is not None
    assert result.stage_trace.applicability == "APPLICABLE"
    assert result.verdicts[0].verdict == Verdict.COMPLIANT
    assert result.verdicts[0].citation_ids == ("case-001-t1_paragraph-0001_d8a344119e",)


def test_stage_audit_na_path_commits_when_no_contrary_evidence(tmp_path: Path) -> None:
    client = ReplayJsonClient(
        responses=[
            {
                "applicability": "NOT_APPLICABLE",
                "reason": "establishment does not store chemicals",
                "policy_citations": ["policy_clause_chem"],
                "uncertainty": 0.05,
            },
            {
                "escalate": False,
                "evidence_citations": [],
                "reason": "no chemical records anywhere in the 9 tracks",
                "uncertainty": 0.1,
            },
        ]
    )

    result = run_stage_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    assert result.verdicts[0].verdict == Verdict.NOT_APPLICABLE
    assert result.stage_trace is not None
    assert result.stage_trace.applicability == "NOT_APPLICABLE"
    # Only 2 calls: applicability + contrary search (no judgment when escalation=false).
    assert len(client.requests) == 2


def test_stage_audit_na_path_escalates_to_judgment_when_contrary_found(tmp_path: Path) -> None:
    client = ReplayJsonClient(
        responses=[
            {
                "applicability": "NOT_APPLICABLE",
                "reason": "thought no chemicals",
                "policy_citations": ["policy_clause_chem"],
                "uncertainty": 0.4,
            },
            {
                "escalate": True,
                "evidence_citations": ["case-001-t1_paragraph-0001_d8a344119e"],
                "reason": "found a chemical-storage record after all",
                "uncertainty": 0.2,
            },
            {
                "verdict": "0",
                "reason": "record exists but is incomplete",
                "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"],
                "contradictions": [],
                "uncertainty": 0.2,
            },
        ]
    )

    result = run_stage_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    assert result.verdicts[0].verdict == Verdict.NON_COMPLIANT
    assert len(client.requests) == 3


def test_stage_audit_records_stage1_failure_as_invalid_result(tmp_path: Path) -> None:
    # Stage 1 returns something missing a required key.
    client = ReplayJsonClient(responses=[{"applicability": "APPLICABLE"}])

    result = run_stage_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is False
    assert any("schema validation" in err or "missing required" in err for err in result.errors)


def test_stage_audit_rejects_multi_cp_unit(tmp_path: Path) -> None:
    bad_unit = ExecutionUnit(
        case_id=1,
        method=ExperimentMethod.STAGE_AUDIT,
        checkpoint_ids=("CP1", "CP2"),
    )
    try:
        run_stage_audit_unit(
            unit=bad_unit,
            material=_material(),
            client=ReplayJsonClient(responses=[]),
            artifact_dir=tmp_path / "cp0",
        )
    except ValueError as exc:
        assert "exactly one checkpoint" in str(exc)
    else:
        raise AssertionError("expected ValueError for multi-cp unit")


def test_stage_audit_plan_iterates_each_cp(tmp_path: Path) -> None:
    material = MaterialSnapshot(
        case_id=1,
        checkpoints=(_cp("CP1"), _cp("CP2")),
        chunks=(_chunk("case-001-t1_paragraph-0001_d8a344119e"),),
        track3_condition=Track3Condition.RAW,
        input_sha256="a" * 64,
    )
    plan_units = (
        ExecutionUnit(case_id=1, method=ExperimentMethod.STAGE_AUDIT, checkpoint_ids=("CP1",)),
        ExecutionUnit(case_id=1, method=ExperimentMethod.STAGE_AUDIT, checkpoint_ids=("CP2",)),
    )
    # 2 CPs × 2 calls each (APPLICABLE → judgment)
    client = ReplayJsonClient(
        responses=[
            {"applicability": "APPLICABLE", "reason": "r1", "policy_citations": [], "uncertainty": 0.1},
            {"verdict": "1", "reason": "ok", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.1},
            {"applicability": "APPLICABLE", "reason": "r2", "policy_citations": [], "uncertainty": 0.1},
            {"verdict": "0", "reason": "no records", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.1},
        ]
    )

    results = run_stage_audit_plan(
        plan_units=plan_units,
        material=material,
        client=client,
        artifact_root=tmp_path,
    )

    assert len(results) == 2
    assert all(result.valid for result in results)
    assert [r.verdicts[0].verdict for r in results] == [Verdict.COMPLIANT, Verdict.NON_COMPLIANT]


def test_planning_groups_stage_audit_one_per_cp() -> None:
    from freca.experiments.planning import build_execution_plan

    plan = build_execution_plan(
        ExperimentMethod.STAGE_AUDIT, case_id=1, checkpoints=[_cp("CP1"), _cp("CP2"), _cp("CP3")]
    )
    assert plan.method == ExperimentMethod.STAGE_AUDIT
    assert len(plan.units) == 3
    assert all(len(unit.checkpoint_ids) == 1 for unit in plan.units)