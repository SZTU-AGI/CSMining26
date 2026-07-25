from __future__ import annotations

from pathlib import Path

from freca.agent.memory import CaseMemory, FailureModeMemory
from freca.models import (
    Applicability,
    AuditDecision,
    Verdict,
)


def _decision(*, cp_id: str, facts: dict[str, str], verdict: Verdict = Verdict.COMPLIANT, confidence: float = 0.9) -> AuditDecision:
    return AuditDecision(
        case_id=1,
        cp_id=cp_id,
        applicability=Applicability.APPLICABLE,
        regulatory_requirement="Test requirement",
        policy_citations=["p1"],
        supporting_evidence=["e1"],
        contrary_evidence=[],
        contradictions=[],
        verdict=verdict,
        reasoning_summary="Test reasoning",
        confidence=confidence,
        retrieval_complete=True,
        review_flags=[],
        shared_facts=facts,
    )


def test_failure_mode_memory_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "fm.jsonl"
    memory = FailureModeMemory(path)
    memory.record(
        case_id=1,
        cp_id="CP1",
        gap_signature="policy_requirement+time_or_retention",
        last_round_summary="missing retention",
    )
    memory.record(
        case_id=2,
        cp_id="CP2",
        gap_signature="policy_requirement",
        last_round_summary="no policy hit",
    )
    counts = memory.signature_counts()
    assert counts["policy_requirement"] == 1
    assert counts["policy_requirement+time_or_retention"] == 1
    recent = memory.recent("policy_requirement")
    assert len(recent) == 1
    assert recent[0].cp_id == "CP2"


def test_failure_mode_memory_trims_per_signature(tmp_path: Path) -> None:
    path = tmp_path / "fm.jsonl"
    memory = FailureModeMemory(path, max_per_signature=2)
    for i in range(1, 6):  # case_id 必须 1-100
        memory.record(
            case_id=i,
            cp_id="CP1",
            gap_signature="policy_requirement",
            last_round_summary=f"record-{i}",
        )
    counts = memory.signature_counts()
    assert counts["policy_requirement"] == 2  # 滚动裁剪到 2


def test_case_memory_facts_so_far_aggregates(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    memory = CaseMemory(path)
    memory.update(_decision(cp_id="CP1", facts={"registration_status": "current", "scope": "grain"}))
    memory.update(_decision(cp_id="CP3", facts={"registration_status": "current", "commodity": "wheat"}))
    merged = memory.facts_so_far()
    assert merged["registration_status"] == "current"
    assert merged["scope"] == "grain"
    assert merged["commodity"] == "wheat"
    assert memory.known_cp_ids() == ["CP1", "CP3"]


def test_case_memory_records_gaps(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    memory = CaseMemory(path)
    memory.update(_decision(cp_id="CP1", facts={}))
    memory.record_gaps(cp_id="CP1", missing_dims=["registered_scope_anchor"])
    memory.record_gaps(cp_id="CP1", missing_dims=["registered_scope_anchor", "date_floor"])
    recent = memory.recent_gaps(n=5)
    assert "registered_scope_anchor" in recent
    assert "date_floor" in recent


def test_failure_mode_memory_survives_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "fm.jsonl"
    path.write_text(
        "{not-json}\n"
        + '{"case_id":1,"cp_id":"CP1","gap_signature":"x","last_round_summary":"y","occurred_at":"2026-01-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    memory = FailureModeMemory(path)
    counts = memory.signature_counts()
    assert counts["x"] == 1  # 损坏行被忽略, 正常行计数