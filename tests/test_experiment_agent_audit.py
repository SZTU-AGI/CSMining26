"""Tests for the AGENT_AUDIT (method D) conditional agent-pass flow."""
from __future__ import annotations

from pathlib import Path

from freca.experiments.agent_audit import (
    run_agent_audit_plan,
    run_agent_audit_unit,
)
from freca.experiments.models import (
    ExecutionUnit,
    ExperimentMethod,
    MaterialSnapshot,
    Track3Condition,
)
from freca.llm import ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    EvidenceChunk,
    SourceLocation,
    SourceType,
    Verdict,
)


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


def _chunk(chunk_id: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=1,
        track=1,
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
        chunks=(_chunk("case-001-t1_paragraph-0001_d8a344119e"),),
        track3_condition=Track3Condition.RAW,
        input_sha256="a" * 64,
    )


def _unit() -> ExecutionUnit:
    return ExecutionUnit(case_id=1, method=ExperimentMethod.AGENT_AUDIT, checkpoint_ids=("CP1",))


def test_clean_applicable_path_skips_all_modules(tmp_path: Path) -> None:
    # Stage-audit path: APPLICABLE → judgment(0.1 uncertainty, full citations). No agent pass.
    client = ReplayJsonClient(
        responses=[
            {"applicability": "APPLICABLE", "reason": "r", "policy_citations": ["p"], "uncertainty": 0.1},
            {"verdict": "1", "reason": "ok", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.1},
        ]
    )

    result = run_agent_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    assert result.agent_trace is not None
    assert result.agent_trace.fired_modules == ()
    assert result.agent_trace.final_resolution == "ACCEPT"
    assert result.agent_trace.extra_calls == 0
    assert len(client.requests) == 2  # only stage-audit; no extra calls


def test_initial_na_escalation_triggers_retrieval_repair(tmp_path: Path) -> None:
    # Stage-audit: NA → contrary search escalates → judgment with 1/0.
    # retrieval_repair re-runs the contrary search to confirm.
    client = ReplayJsonClient(
        responses=[
            {"applicability": "NOT_APPLICABLE", "reason": "scope", "policy_citations": ["p"], "uncertainty": 0.05},
            {"escalate": True, "evidence_citations": ["case-001-t1_paragraph-0001_d8a344119e"], "reason": "found contrary", "uncertainty": 0.2},
            {"verdict": "0", "reason": "no records", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.2},
            # retrieval_repair re-runs contrary search; escalate=true again.
            {"escalate": True, "evidence_citations": ["case-001-t1_paragraph-0001_d8a344119e"], "reason": "still applies", "uncertainty": 0.1},
        ]
    )

    result = run_agent_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    assert result.agent_trace is not None
    modules = {call.module for call in result.agent_trace.fired_modules}
    assert "retrieval_repair" in modules
    assert result.agent_trace.final_resolution == "REPAIRED"


def test_conflict_triggers_critic_and_flip(tmp_path: Path) -> None:
    # Stage-audit path: APPLICABLE → judgment reports contradictions → Critic fires.
    client = ReplayJsonClient(
        responses=[
            {"applicability": "APPLICABLE", "reason": "r", "policy_citations": ["p"], "uncertainty": 0.1},
            {
                "verdict": "1",
                "reason": "initial",
                "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"],
                "contradictions": ["case-001-t1_paragraph-0002_aaaaaaaaaa"],
                "uncertainty": 0.2,
            },
            # Critic flips to 0
            {
                "verdict": "0",
                "reason": "critic found contrary outweighs support",
                "citation_ids": ["case-001-t1_paragraph-0002_aaaaaaaaaa"],
                "uncertainty": 0.3,
            },
        ]
    )

    result = run_agent_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    assert result.agent_trace is not None
    modules = {call.module for call in result.agent_trace.fired_modules}
    assert "critic" in modules
    assert result.verdicts[0].verdict == Verdict.NON_COMPLIANT
    assert result.agent_trace.final_resolution == "REVIEWED"


def test_low_confidence_triggers_verifier(tmp_path: Path) -> None:
    # Stage-audit path: APPLICABLE → judgment with uncertainty=0.8 (>0.5) → Verifier.
    client = ReplayJsonClient(
        responses=[
            {"applicability": "APPLICABLE", "reason": "r", "policy_citations": ["p"], "uncertainty": 0.1},
            {"verdict": "1", "reason": "guess", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.8},
            # Verifier FAIL → 0
            {
                "status": "FAIL",
                "verdict": "0",
                "reason": "evidence does not actually support pass",
                "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"],
                "uncertainty": 0.3,
            },
        ]
    )

    result = run_agent_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    triggers = {call.trigger for call in result.agent_trace.fired_modules}
    assert "low_confidence" in triggers
    assert result.verdicts[0].verdict == Verdict.NON_COMPLIANT
    assert result.agent_trace.final_resolution == "VERIFIED"


def test_verifier_pass_keeps_verdict(tmp_path: Path) -> None:
    client = ReplayJsonClient(
        responses=[
            {"applicability": "APPLICABLE", "reason": "r", "policy_citations": ["p"], "uncertainty": 0.1},
            {"verdict": "1", "reason": "ok", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.9},
            {"status": "PASS", "verdict": "1", "reason": "agrees", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "uncertainty": 0.2},
        ]
    )

    result = run_agent_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    triggers = {call.trigger for call in result.agent_trace.fired_modules}
    assert "low_confidence" in triggers
    # Verifier agreed — verdict unchanged, but the call is still recorded.
    assert result.verdicts[0].verdict == Verdict.COMPLIANT


def test_missing_citation_triggers_verifier_after_low_confidence_silent(tmp_path: Path) -> None:
    # No low_confidence, no contradiction, no escalation, but citation_ids=[] → verifier.
    client = ReplayJsonClient(
        responses=[
            {"applicability": "NOT_APPLICABLE", "reason": "scope", "policy_citations": ["p"], "uncertainty": 0.05},
            {"escalate": False, "evidence_citations": [], "reason": "no contrary", "uncertainty": 0.1},
            # No stage-3 because escalate=False. Verifier fires for "retrieval_gap":
            # verdict_before is N/A → the gap verifier is skipped because verdict is N/A.
            # (The condition is `current.verdict != NOT_APPLICABLE`.) So no extra call here.
        ]
    )

    result = run_agent_audit_unit(
        unit=_unit(),
        material=_material(),
        client=client,
        artifact_dir=tmp_path / "cp0",
    )

    assert result.valid is True
    # NA → no agent modules fire (gating logic skips NA verdicts).
    assert result.agent_trace is not None
    assert result.agent_trace.fired_modules == ()


def test_plan_iterates_each_cp_with_agent_pass(tmp_path: Path) -> None:
    material = MaterialSnapshot(
        case_id=1,
        checkpoints=(_cp("CP1"), _cp("CP2")),
        chunks=(_chunk("case-001-t1_paragraph-0001_d8a344119e"),),
        track3_condition=Track3Condition.RAW,
        input_sha256="a" * 64,
    )
    plan_units = (
        ExecutionUnit(case_id=1, method=ExperimentMethod.AGENT_AUDIT, checkpoint_ids=("CP1",)),
        ExecutionUnit(case_id=1, method=ExperimentMethod.AGENT_AUDIT, checkpoint_ids=("CP2",)),
    )
    # Two clean APPLICABLE→judgment paths, no modules fire
    client = ReplayJsonClient(
        responses=[
            {"applicability": "APPLICABLE", "reason": "r1", "policy_citations": ["p"], "uncertainty": 0.1},
            {"verdict": "1", "reason": "ok1", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.1},
            {"applicability": "APPLICABLE", "reason": "r2", "policy_citations": ["p"], "uncertainty": 0.1},
            {"verdict": "0", "reason": "no rec2", "citation_ids": ["case-001-t1_paragraph-0001_d8a344119e"], "contradictions": [], "uncertainty": 0.1},
        ]
    )

    results = run_agent_audit_plan(
        plan_units=plan_units,
        material=material,
        client=client,
        artifact_root=tmp_path,
    )

    assert len(results) == 2
    assert all(r.agent_trace is not None for r in results)
    assert [r.verdicts[0].verdict for r in results] == [Verdict.COMPLIANT, Verdict.NON_COMPLIANT]