"""Tests for the VERIFY_AUDIT method: one-shot base + unconditional per-CP verify."""
from __future__ import annotations

from pathlib import Path

from freca.experiments.models import (
    ExecutionUnit,
    ExperimentMethod,
    MaterialSnapshot,
    Track3Condition,
)
from freca.experiments.verify_audit import (
    run_verify_audit_plan,
    run_verify_audit_unit,
)
from freca.llm import ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    EvidenceChunk,
    SourceLocation,
    SourceType,
)

_CHUNK = "case-001-t1_paragraph-0001_d8a344119e"


def _cp(cp_id: str) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element-1",
        section_title="section",
        text=f"checkpoint {cp_id} requires documented pest control",
        source_file="cp.xlsx",
        cell="A1",
    )


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=_CHUNK,
        case_id=1,
        track=1,
        source_id="src-1",
        source_file="src.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content="pest control records present",
        content_kind="paragraph",
        parser_name="test",
        parser_version="v1",
        source_sha256="a" * 64,
    )


def _material() -> MaterialSnapshot:
    return MaterialSnapshot(
        case_id=1,
        checkpoints=(_cp("CP1"), _cp("CP2")),
        chunks=(_chunk(),),
        track3_condition=Track3Condition.RAW,
        input_sha256="a" * 64,
    )


def _unit() -> ExecutionUnit:
    # One-shot base unit: all CPs in one call (mirrors the VERIFY_AUDIT plan).
    return ExecutionUnit(case_id=1, method=ExperimentMethod.VERIFY_AUDIT, checkpoint_ids=("CP1", "CP2"))


def _base_response() -> dict:
    return {
        "verdicts": [
            {"cp_id": "CP1", "verdict": "1", "reason": "documented", "citation_ids": [_CHUNK], "uncertainty": 0.2},
            {"cp_id": "CP2", "verdict": "0", "reason": "missing", "citation_ids": [_CHUNK], "uncertainty": 0.3},
        ]
    }


def _verify(status: str, verdict: str) -> dict:
    return {
        "status": status,
        "verdict": verdict,
        "reason": "second look",
        "citation_ids": [_CHUNK],
        "uncertainty": 0.2,
    }


def test_base_failure_propagates_without_verifier_calls(tmp_path: Path) -> None:
    # Base call returns an invalid payload → single failure result, no verify pass.
    client = ReplayJsonClient(responses=[{"not_verdicts": True}])

    results = run_verify_audit_unit(unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path / "v")

    assert len(results) == 1
    assert results[0].valid is False
    assert len(client.requests) == 1  # only the base call; no verifier fired


def test_unconditional_verify_runs_on_every_cp(tmp_path: Path) -> None:
    # Both CPs get a verifier pass regardless of confidence (both PASS → kept).
    client = ReplayJsonClient(
        responses=[_base_response(), _verify("PASS", "1"), _verify("PASS", "0")]
    )

    results = run_verify_audit_unit(unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path / "v")

    assert len(results) == 2  # one per-CP result
    assert len(client.requests) == 3  # 1 base + 2 verify
    for result, expected in zip(results, ("1", "0"), strict=True):
        assert result.valid is True
        assert result.agent_trace is not None
        assert len(result.agent_trace.fired_modules) == 1
        call = result.agent_trace.fired_modules[0]
        assert call.module == "verifier"
        assert call.trigger == "always"  # unconditional, not gated on confidence
        assert call.verdict_before == call.verdict_after  # PASS → no change
        assert result.agent_trace.final_resolution == "VERIFIED"
        assert result.verdicts[0].verdict.value == expected  # base verdict kept


def test_fail_flips_verdict_pass_keeps_it(tmp_path: Path) -> None:
    # CP1: verifier FAIL flips 1 → 0. CP2: verifier PASS keeps 0.
    client = ReplayJsonClient(
        responses=[_base_response(), _verify("FAIL", "0"), _verify("PASS", "0")]
    )

    results = run_verify_audit_unit(unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path / "v")

    # CP1 flipped
    assert results[0].verdicts[0].verdict.value == "0"
    flip = results[0].agent_trace.fired_modules[0]
    assert flip.verdict_before == "1" and flip.verdict_after == "0"
    # CP2 kept
    assert results[1].verdicts[0].verdict.value == "0"
    assert results[1].agent_trace.fired_modules[0].verdict_before == results[1].agent_trace.fired_modules[0].verdict_after


def test_plan_returns_one_result_per_cp(tmp_path: Path) -> None:
    # A VERIFY_AUDIT plan holds one all-CPs unit; the plan runner emits N results.
    client = ReplayJsonClient(
        responses=[_base_response(), _verify("PASS", "1"), _verify("PASS", "0")]
    )
    plan_units = (_unit(),)

    results = run_verify_audit_plan(plan_units=plan_units, material=_material(), client=client, artifact_root=tmp_path)

    assert len(results) == 2
    assert {r.verdicts[0].cp_id for r in results} == {"CP1", "CP2"}
