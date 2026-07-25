from pathlib import Path

from freca.index import HybridIndex
from freca.llm import ReplayJsonClient
from freca.models import (
    Applicability,
    AuditDecision,
    AuditTask,
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
    TaskStatus,
    Verdict,
)
from freca.pipeline import process_audit_task, run_consistency_gate
from freca.state import TaskStore, read_json


def _chunk(chunk_id: str, content: str, *, case_id: int | None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        re_number="RE-X" if case_id else None,
        track=1 if case_id else None,
        source_id="t1" if case_id else "policy",
        source_file="t1.docx" if case_id else "policy.pdf",
        source_type=SourceType.DOCX if case_id else SourceType.PDF,
        location=SourceLocation(paragraph_index=0) if case_id else SourceLocation(page=1),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="e" * 64,
    )


def _checkpoint(cp_id: str = "CP1") -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element-1",
        section_title="1.1 Export operations",
        text="The operation is within registration.",
        source_file="cp.xlsx",
        cell="A3",
    )


def _payload(*, confidence: float, reasoning: str = "Supported.") -> dict:
    return {
        "case_id": 1,
        "cp_id": "CP1",
        "applicability": "APPLICABLE",
        "regulatory_requirement": "Registration covers operations.",
        "policy_citations": ["p1"],
        "supporting_evidence": ["e1"],
        "contrary_evidence": [],
        "contradictions": [],
        "verdict": "1",
        "reasoning_summary": reasoning,
        "confidence": confidence,
        "retrieval_complete": True,
        "review_flags": [],
        "shared_facts": {"registration_status": "current"},
    }


def _verification() -> dict:
    return {
        "case_id": 1,
        "cp_id": "CP1",
        "status": "PASS",
        "issues": [],
        "checked_citations": ["p1", "e1"],
    }


def test_process_task_selectively_arbitrates_low_confidence_and_writes_final(
    tmp_path: Path,
) -> None:
    policy_index = HybridIndex(
        [_chunk("p1", "registration must cover operations and applies", case_id=None)],
        scope="policy",
    )
    case_index = HybridIndex(
        [
            _chunk("e1", "registration covers operations 2026", case_id=1),
            _chunk("e2", "independent registration record 2026", case_id=1),
        ],
        scope="case",
    )
    first_client = ReplayJsonClient([_payload(confidence=0.5, reasoning="FIRST_ANCHOR")])
    verifier_client = ReplayJsonClient([_verification(), _verification()])
    arbitrator_client = ReplayJsonClient([_payload(confidence=0.9, reasoning="Independent")])
    task = AuditTask(
        task_id="run:case-001:CP1",
        run_id="run",
        case_id=1,
        cp_id="CP1",
    )

    output = process_audit_task(
        task=task,
        checkpoint=_checkpoint(),
        policy_index=policy_index,
        case_index=case_index,
        audit_client=first_client,
        verifier_client=verifier_client,
        arbitrator_client=arbitrator_client,
        build_dir=tmp_path,
    )

    assert output.exists()
    final = AuditDecision.model_validate(read_json(output))
    assert final.confidence == 0.9
    assert "FIRST_ANCHOR" not in arbitrator_client.requests[0]["user"]
    assert (tmp_path / "arbitration" / "001" / "CP1.json").exists()


def test_consistency_gate_blocks_completed_tasks_with_conflicting_shared_facts(
    tmp_path: Path,
) -> None:
    final_dir = tmp_path / "final" / "001"
    final_dir.mkdir(parents=True)
    cp1 = AuditDecision.model_validate(_payload(confidence=0.9))
    cp2_payload = _payload(confidence=0.9)
    cp2_payload.update(
        {"cp_id": "CP2", "shared_facts": {"registration_status": "suspended"}}
    )
    cp2 = AuditDecision.model_validate(cp2_payload)
    (final_dir / "CP1.json").write_text(cp1.model_dump_json(), encoding="utf-8")
    (final_dir / "CP2.json").write_text(cp2.model_dump_json(), encoding="utf-8")
    store = TaskStore(tmp_path / "state" / "run-tasks.json")
    tasks = [
        AuditTask(
            task_id=f"run:case-001:{cp_id}",
            run_id="run",
            case_id=1,
            cp_id=cp_id,
            status=TaskStatus.COMPLETED,
        )
        for cp_id in ("CP1", "CP2")
    ]
    store.initialize(tasks)

    report = run_consistency_gate(tmp_path, run_id="run")

    assert report["finding_count"] == 1
    assert all(task.status == TaskStatus.BLOCKED for task in store.all())


def test_consistency_gate_ignores_decisions_not_owned_by_current_run(tmp_path: Path) -> None:
    for case_id, status in ((1, "current"), (2, "stale")):
        final_dir = tmp_path / "final" / f"{case_id:03d}"
        final_dir.mkdir(parents=True)
        first = _payload(confidence=0.9)
        first.update({"case_id": case_id, "shared_facts": {"registration_status": status}})
        second = _payload(confidence=0.9)
        second.update(
            {
                "case_id": case_id,
                "cp_id": "CP2",
                "shared_facts": {
                    "registration_status": status if case_id == 1 else "other"
                },
            }
        )
        (final_dir / "CP1.json").write_text(
            AuditDecision.model_validate(first).model_dump_json(), encoding="utf-8"
        )
        (final_dir / "CP2.json").write_text(
            AuditDecision.model_validate(second).model_dump_json(), encoding="utf-8"
        )
    store = TaskStore(tmp_path / "state" / "run-tasks.json")
    store.initialize(
        [
            AuditTask(
                task_id=f"run:case-001:{cp_id}",
                run_id="run",
                case_id=1,
                cp_id=cp_id,
                status=TaskStatus.COMPLETED,
                artifact_path=str(tmp_path / "final" / "001" / f"{cp_id}.json"),
            )
            for cp_id in ("CP1", "CP2")
        ]
    )

    report = run_consistency_gate(tmp_path, run_id="run")

    assert report["case_count"] == 1
    assert report["finding_count"] == 0


def test_process_task_with_escalated_tier_invokes_tiebreaker(tmp_path: Path) -> None:
    """ESCALATED tier: first 触发仲裁 → blind 分歧 → tiebreaker 调 → 多数票."""
    from freca.models import EscalationTier

    policy_index = HybridIndex(
        [_chunk("p1", "registration must cover operations and applies", case_id=None)],
        scope="policy",
    )
    case_index = HybridIndex(
        [
            _chunk("e1", "registration covers operations 2026", case_id=1),
            _chunk("e2", "independent registration record 2026", case_id=1),
        ],
        scope="case",
    )
    # first=compliant, blind=non-compliant, tiebreaker=compliant → 多数票通过
    first = _payload(confidence=0.5, reasoning="FIRST")
    blind_payload = first.copy()
    blind_payload["verdict"] = "0"
    blind_payload["reasoning_summary"] = "BLIND"
    tiebreaker_payload = first.copy()
    tiebreaker_payload["reasoning_summary"] = "TIEBREAKER"
    first_client = ReplayJsonClient([first])
    verifier_client = ReplayJsonClient([_verification(), _verification()])
    blind_client = ReplayJsonClient([blind_payload])
    tiebreaker_client = ReplayJsonClient([tiebreaker_payload])
    task = AuditTask(
        task_id="run:case-001:CP1",
        run_id="run",
        case_id=1,
        cp_id="CP1",
    )

    output = process_audit_task(
        task=task,
        checkpoint=_checkpoint(),
        policy_index=policy_index,
        case_index=case_index,
        audit_client=first_client,
        verifier_client=verifier_client,
        arbitrator_client=blind_client,
        tiebreaker_client=tiebreaker_client,
        arbitration_tier=EscalationTier.ESCALATED,
        build_dir=tmp_path,
    )

    final = AuditDecision.model_validate(read_json(output))
    assert final.reasoning_summary == "TIEBREAKER"
    # blind 已被调一次 (分歧),tiebreaker 已被调一次 (返回 tiebreaker_payload)
    assert len(blind_client.requests) == 1
    assert len(tiebreaker_client.requests) == 1


def test_process_task_with_escalated_tier_silently_degrades_when_no_tiebreaker(tmp_path: Path) -> None:
    """ESCALATED tier 但 tiebreaker 未配置 → 静默降级到盲式行为."""
    from freca.models import EscalationTier

    policy_index = HybridIndex(
        [_chunk("p1", "registration must cover operations and applies", case_id=None)],
        scope="policy",
    )
    case_index = HybridIndex(
        [
            _chunk("e1", "registration covers operations 2026", case_id=1),
            _chunk("e2", "independent registration record 2026", case_id=1),
        ],
        scope="case",
    )
    # first 与 blind 一致 → ACCEPT_AGREEMENT (不走 tiebreaker)
    first = _payload(confidence=0.5, reasoning="FIRST")
    blind_payload = first.copy()
    blind_payload["reasoning_summary"] = "BLIND_AGREE"
    first_client = ReplayJsonClient([first])
    verifier_client = ReplayJsonClient([_verification(), _verification()])
    blind_client = ReplayJsonClient([blind_payload])
    task = AuditTask(
        task_id="run:case-001:CP1",
        run_id="run",
        case_id=1,
        cp_id="CP1",
    )

    output = process_audit_task(
        task=task,
        checkpoint=_checkpoint(),
        policy_index=policy_index,
        case_index=case_index,
        audit_client=first_client,
        verifier_client=verifier_client,
        arbitrator_client=blind_client,
        tiebreaker_client=None,  # 未配置
        arbitration_tier=EscalationTier.ESCALATED,
        build_dir=tmp_path,
    )

    final = AuditDecision.model_validate(read_json(output))
    assert final.reasoning_summary == "BLIND_AGREE"
