"""Shared builders for the ledger-architecture tests.

Every helper here is offline: no model client, no network, no parsed corpus.
The ledger stack is designed so that Stage A has a deterministic path and
Stages B–E accept plain objects, which makes the whole architecture testable
without credentials.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from freca.config import (
    ModelEndpointConfig,
    ModelsConfig,
    PathsConfig,
    PipelineConfig,
)
from freca.models import (
    Applicability,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
    Verdict,
)

from freca.ledger.config import LedgerConfig, LedgerSettings
from freca.ledger.models import (
    CheckpointRubric,
    ContradictionKind,
    CriterionKind,
    CriterionOutcome,
    CriterionStatus,
    DecisionStage,
    EvidenceCoverage,
    EvidencePack,
    EvidenceScorecard,
    FactContradiction,
    FactRecord,
    GateFinding,
    GateReport,
    GateSeverity,
    LedgerDecision,
    PackedFact,
    RubricCriterion,
)

ANSWER_LIKE_FLAG = "answer_like_field"
CONTAMINATION_FLAG = "exclude_from_compliance_evidence"
VERBATIM_MISSING_FLAG = "verbatim_not_found_in_source"


# --------------------------------------------------------------------------
# Stage A inputs
# --------------------------------------------------------------------------


def make_chunk(
    *,
    content: str,
    track: int = 1,
    case_id: int = 1,
    index: int = 1,
    source_file: str | None = None,
    flags: Sequence[str] = (),
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"case-{case_id:03d}-t{track}-p{index}",
        case_id=case_id,
        re_number="RE-WA-2021-0041",
        track=track,
        source_id=f"case-{case_id:03d}-t{track}",
        source_file=source_file or f"track-{track}.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=index),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="c" * 64,
        flags=list(flags),
    )


def make_fact(
    fact_id: str,
    *,
    case_id: int = 1,
    topic: str = "sanitation_pest",
    claim: str = "Pest control treatment: carried out on 2021-03-04",
    value: str = "carried out on 2021-03-04",
    verbatim: str = "Pest control treatment: carried out on 2021-03-04 by licensed operator.",
    track: int | None = 5,
    source_file: str = "track-5.docx",
    chunk_id: str | None = None,
    categories: Sequence[str] = ("dated_record",),
    flags: Sequence[str] = (),
) -> FactRecord:
    return FactRecord(
        fact_id=fact_id,
        case_id=case_id,
        topic=topic,
        claim=claim,
        value=value,
        source_file=source_file,
        source_id=f"case-{case_id:03d}-t{track or 0}",
        chunk_id=chunk_id or f"case-{case_id:03d}-chunk-{fact_id}",
        track=track,
        location=SourceLocation(paragraph_index=3),
        verbatim=verbatim,
        evidence_categories=list(categories),
        quality_flags=list(flags),
    )


def make_contradiction(
    *,
    kind: ContradictionKind = ContradictionKind.IDENTITY_MISMATCH,
    case_id: int = 1,
    severity: str = "BLOCKER",
    fact_ids: Sequence[str] = (),
    detail: str = "materials carry more than one RE number",
) -> FactContradiction:
    return FactContradiction(
        contradiction_id=f"case-{case_id:03d}-{kind.value}",
        case_id=case_id,
        kind=kind,
        topic="registration",
        fact_ids=list(fact_ids),
        detail=detail,
        severity=severity,
    )


# --------------------------------------------------------------------------
# Stage B
# --------------------------------------------------------------------------


def make_rubric(
    *,
    cp_id: str = "CP1",
    policy_chunk_ids: Sequence[str] = ("policy-1", "policy-2"),
    criteria: Sequence[RubricCriterion] | None = None,
    degraded: str | None = None,
    rubric_version: str = "rubric-v1",
) -> CheckpointRubric:
    resolved = list(
        criteria
        or [
            RubricCriterion(
                criterion_id="C1",
                kind=CriterionKind.APPLICABILITY,
                statement="The premises is a registered export establishment.",
                policy_citations=["policy-1"],
                facts_to_verify=["registration status"],
                required_evidence_categories=["registration_document"],
            ),
            RubricCriterion(
                criterion_id="C2",
                kind=CriterionKind.SUPPORTING,
                statement="Pest control treatments are performed and recorded.",
                policy_citations=["policy-2"],
                facts_to_verify=["pest control treatment record"],
                required_evidence_categories=["dated_record"],
            ),
        ]
    )
    generator: dict[str, str] = {"model": "test-model", "prompt_version": "test-1"}
    if degraded:
        generator["degraded"] = degraded
    return CheckpointRubric(
        cp_id=cp_id,
        element_id=1,
        element_title="Element 1 - premises and pest control",
        checkpoint_text="Is pest control performed and recorded for the premises?",
        applicability_note="Applies to all registered export establishments.",
        criteria=resolved,
        policy_chunk_ids=list(policy_chunk_ids),
        policy_snippets={
            chunk_id: f"Official clause text for {chunk_id}. " * 20
            for chunk_id in policy_chunk_ids
        },
        retrieval_queries=["pest control record requirement"],
        generator=generator,
        rubric_version=rubric_version,
        input_hash="input-hash-1",
    )


# --------------------------------------------------------------------------
# Stage C
# --------------------------------------------------------------------------


def make_pack(
    *,
    rubric: CheckpointRubric,
    facts: Sequence[FactRecord],
    case_id: int = 1,
    contradictions: Sequence[FactContradiction] = (),
    uncovered: Sequence[str] = (),
    integrity_notes: Sequence[str] = (),
) -> EvidencePack:
    skip = set(uncovered)
    matched = [
        criterion.criterion_id
        for criterion in rubric.criteria
        if criterion.criterion_id not in skip
    ]
    packed = [
        PackedFact(
            fact=fact,
            relevance=float(len(facts) - position),
            matched_criteria=list(matched),
            match_reasons=[f"{criterion}:topic" for criterion in matched],
        )
        for position, fact in enumerate(facts)
    ]
    coverage = {
        criterion.criterion_id: (len(facts) if criterion.criterion_id in matched else 0)
        for criterion in rubric.criteria
    }
    return EvidencePack(
        case_id=case_id,
        cp_id=rubric.cp_id,
        rubric_version=rubric.rubric_version,
        facts=packed,
        contradictions=list(contradictions),
        integrity_notes=list(integrity_notes),
        coverage_by_criterion=coverage,
        uncovered_criteria=sorted(skip),
        ledger_fact_count=len(facts),
    )


# --------------------------------------------------------------------------
# Stage D
# --------------------------------------------------------------------------


def make_decision(
    *,
    rubric: CheckpointRubric,
    pack: EvidencePack,
    verdict: Verdict = Verdict.COMPLIANT,
    applicability: Applicability = Applicability.APPLICABLE,
    policy_citations: Sequence[str] | None = None,
    supporting: Sequence[str] | None = None,
    contrary: Sequence[str] = (),
    outcomes: Sequence[CriterionOutcome] | None = None,
    status: CriterionStatus = CriterionStatus.SATISFIED,
    confidence: float = 0.9,
    coverage: EvidenceCoverage = EvidenceCoverage.COMPLETE,
    applicability_reasoning: str = "",
    reasoning: str = "Dated pest control records were located for the audit period.",
    flags: Sequence[str] = (),
    stage: DecisionStage = DecisionStage.PRIMARY,
    case_id: int | None = None,
) -> LedgerDecision:
    citations = (
        list(policy_citations)
        if policy_citations is not None
        else list(rubric.policy_chunk_ids)
    )
    support = (
        list(supporting)
        if supporting is not None
        else [item.fact.fact_id for item in pack.facts][:1]
    )
    resolved_outcomes = (
        list(outcomes)
        if outcomes is not None
        else [
            CriterionOutcome(
                criterion_id=criterion.criterion_id,
                status=status,
                fact_ids=list(support),
                note="",
            )
            for criterion in rubric.criteria
        ]
    )
    return LedgerDecision(
        case_id=case_id if case_id is not None else pack.case_id,
        cp_id=rubric.cp_id,
        applicability=applicability,
        verdict=verdict,
        criterion_outcomes=resolved_outcomes,
        policy_citations=citations,
        supporting_fact_ids=support,
        contrary_fact_ids=list(contrary),
        evidence_coverage=coverage,
        applicability_reasoning=applicability_reasoning,
        reasoning_summary=reasoning,
        confidence=confidence,
        quality_flags=list(flags),
        stage=stage,
        rubric_version=rubric.rubric_version,
    )


# --------------------------------------------------------------------------
# Stage E
# --------------------------------------------------------------------------


def perfect_scorecard() -> EvidenceScorecard:
    return EvidenceScorecard(
        regulatory_coverage=1.0,
        support_coverage=1.0,
        contrary_strength=0.0,
        citation_quality=1.0,
        evidence_integrity=1.0,
    )


def make_gate_report(
    *,
    passed: bool = True,
    errors: int = 0,
    triggers: Sequence[str] = (),
    case_id: int = 1,
    cp_id: str = "CP1",
    review_priority: float = 0.0,
) -> GateReport:
    findings = [
        GateFinding(
            code=f"ERROR_{index}",
            severity=GateSeverity.ERROR,
            message="contract violation",
        )
        for index in range(errors)
    ]
    return GateReport(
        case_id=case_id,
        cp_id=cp_id,
        passed=passed,
        findings=findings,
        review_triggers=list(triggers),
        scorecard=perfect_scorecard(),
        review_priority=review_priority,
    )


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def make_ledger_config(
    tmp_path: Path,
    *,
    settings: LedgerSettings | None = None,
) -> LedgerConfig:
    pipeline = PipelineConfig(
        paths=PathsConfig(
            cases_root=tmp_path / "cases",
            policy_pdf=tmp_path / "policy.pdf",
            checkpoints_xlsx=tmp_path / "checkpoints.xlsx",
            submission_template=tmp_path / "template.xlsx",
            build_dir=tmp_path / "build",
        ),
        models=ModelsConfig(
            audit=ModelEndpointConfig(
                base_url="http://localhost:1/v1",
                model="test-audit",
                api_key_env="FRECA_TEST_KEY",
            )
        ),
    )
    return LedgerConfig.from_pipeline(pipeline, settings)


class StubJsonClient:
    """A ``JsonChatClient`` stand-in that replays canned payloads."""

    def __init__(self, payloads: Sequence[dict]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, str]] = []

    def complete_json(self, *, system: str, user: str, schema=None) -> dict:
        self.calls.append({"system": system, "user": user})
        if not self._payloads:
            raise AssertionError("StubJsonClient ran out of payloads")
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload
