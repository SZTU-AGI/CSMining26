from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    IMAGE = "image"


class ContentKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    IMAGE_DESCRIPTION = "image_description"


class Applicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class Verdict(StrEnum):
    COMPLIANT = "1"
    NON_COMPLIANT = "0"
    NOT_APPLICABLE = "N/A"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class RetrievalAction(StrEnum):
    STOP = "stop"
    RETRIEVE = "retrieve"


class SourceLocation(StrictModel):
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    sheet: str | None = None
    cell_range: str | None = None
    object_id: str | None = None
    paragraph_index: int | None = Field(default=None, ge=0)


class SourceRecord(StrictModel):
    source_id: str
    case_id: int | None = Field(default=None, ge=1, le=100)
    track: int | None = Field(default=None, ge=1, le=9)
    re_number: str | None = None
    path: Path
    source_type: SourceType
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flags: list[str] = Field(default_factory=list)


class CaseRecord(StrictModel):
    case_id: int = Field(ge=1, le=100)
    re_number: str
    sources: list[SourceRecord]
    missing_tracks: list[int] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    contaminated_tracks: dict[int, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def sources_belong_to_case(self) -> CaseRecord:
        wrong = [source.source_id for source in self.sources if source.case_id != self.case_id]
        if wrong:
            raise ValueError(f"sources do not belong to case {self.case_id}: {wrong}")
        return self

    @property
    def source_paths(self) -> list[Path]:
        return [source.path for source in self.sources]

    @property
    def expected_establishment_name(self) -> str:
        name = self.metadata.get("expected_establishment_name")
        return name if isinstance(name, str) else ""

    @property
    def foreign_contaminated_tracks(self) -> list[int]:
        return sorted(
            track
            for track, relation in self.contaminated_tracks.items()
            if relation == "foreign_farm"
        )


class CaseManifest(StrictModel):
    cases_root: Path
    cases: list[CaseRecord]
    source_count: int

    def by_id(self, case_id: int) -> CaseRecord:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)


class EvidenceChunk(StrictModel):
    chunk_id: str
    case_id: int | None = Field(default=None, ge=1, le=100)
    re_number: str | None = None
    track: int | None = Field(default=None, ge=1, le=9)
    source_id: str
    source_file: str
    source_type: SourceType
    location: SourceLocation
    content: str
    content_kind: ContentKind
    derived_from: str | None = None
    parser_name: str
    parser_version: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckpointDefinition(StrictModel):
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    element_id: int = Field(ge=1, le=4)
    element_title: str
    section_title: str
    text: str
    source_file: str
    cell: str


class RetrievalHit(StrictModel):
    chunk: EvidenceChunk
    score: float
    rank: int = Field(ge=1)
    score_trace: dict[str, Any] = Field(default_factory=dict)


class RetrievalAgentDecision(StrictModel):
    action: RetrievalAction
    complete: bool
    gaps: list[str] = Field(default_factory=list)
    policy_query: str | None = None
    evidence_query: str | None = None
    target_tracks: list[int] = Field(default_factory=list)
    target_content_kinds: list[ContentKind] = Field(default_factory=list)
    reason: str

    @model_validator(mode="after")
    def validate_action_contract(self) -> RetrievalAgentDecision:
        if self.action == RetrievalAction.STOP:
            if not self.complete:
                raise ValueError("stop action requires complete=true")
        else:
            if self.complete:
                raise ValueError("retrieve action requires complete=false")
            if not (self.policy_query or "").strip() or not (
                self.evidence_query or ""
            ).strip():
                raise ValueError("retrieve action requires nonempty policy and evidence queries")
        return self


class RetrievalRound(StrictModel):
    round_number: int = Field(ge=0, le=2)
    policy_query: str
    evidence_query: str
    added_policy_chunk_ids: list[str]
    added_evidence_chunk_ids: list[str]
    gaps: list[str]
    agent_decision: RetrievalAgentDecision | None = None
    gate_flags: list[str] = Field(default_factory=list)
    target_tracks: list[int] = Field(default_factory=list)
    target_content_kinds: list[ContentKind] = Field(default_factory=list)
    policy_candidate_trace: list[dict[str, Any]] = Field(default_factory=list)
    evidence_candidate_trace: list[dict[str, Any]] = Field(default_factory=list)
    # Tier-1 / Tier-3 agent upgrades (向后兼容: 默认 None / 空 list)
    planner_plan: "PlannerPlan | None" = None
    critic_decision: "CriticDecision | None" = None
    dropped_chunk_ids: list[str] = Field(default_factory=list)
    flagged_chunk_ids: list[str] = Field(default_factory=list)


class PlannerPlan(StrictModel):
    """Tier-1 规划: 决定先查哪些 track / content kind,不出 1/0/N/A。"""

    target_tracks: list[int] = Field(default_factory=list)
    target_content_kinds: list[ContentKind] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class CriticDecision(StrictModel):
    """Tier-3 自我审视: 默认只 flag 不 drop,HeuristicCritic 可写 weighted_down_chunk_ids。"""

    drop_chunk_ids: list[str] = Field(default_factory=list)
    flag_chunk_ids: list[str] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    suggested_query_focus: str | None = None
    rationale: str
    weighted_down_chunk_ids: list[str] = Field(default_factory=list)


class AgentDecisionTrace(StrictModel):
    """一次任务内所有 agent 决策的可复盘 trace。"""

    planner_plan: PlannerPlan | None = None
    critic_decision: CriticDecision | None = None
    failure_mode_count: int = 0


class FailureModeRecord(StrictModel):
    """TaskStore 持久化的失败模式记录。"""

    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    gap_signature: str
    last_round_summary: str
    occurred_at: str  # ISO timestamp


class EscalationTier(StrEnum):
    DISABLED = "disabled"
    SINGLE = "single"
    BLIND = "blind"
    ESCALATED = "escalated"


class RetrievalBundle(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    policy_hits: list[RetrievalHit]
    evidence_hits: list[RetrievalHit]
    rounds: list[RetrievalRound]
    complete: bool
    stop_reason: str


class AuditDecision(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    applicability: Applicability
    regulatory_requirement: str
    policy_citations: list[str]
    supporting_evidence: list[str]
    contrary_evidence: list[str]
    contradictions: list[str]
    verdict: Verdict
    reasoning_summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval_complete: bool
    review_flags: list[str] = Field(default_factory=list)
    shared_facts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_verdict_semantics(self) -> AuditDecision:
        if self.verdict == Verdict.NOT_APPLICABLE:
            if self.applicability != Applicability.NOT_APPLICABLE:
                raise ValueError("N/A requires NOT_APPLICABLE applicability")
            if not self.policy_citations:
                raise ValueError("N/A requires policy support")
        elif self.applicability == Applicability.NOT_APPLICABLE:
            raise ValueError("NOT_APPLICABLE applicability requires N/A verdict")
        return self


class CitationValidationResult(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    passed: bool
    errors: list[str]
    checked_citations: list[str]


class VerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


class VerificationResult(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    status: VerificationStatus
    issues: list[str]
    checked_citations: list[str]


class ConsistencyFinding(StrictModel):
    case_id: int = Field(ge=1, le=100)
    fact_key: str
    cp_ids: list[str]
    values: dict[str, str]


class ArbitrationResult(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    first_verdict: Verdict
    second_decision: AuditDecision
    agreement: bool
    resolution: str


class AuditTask(StrictModel):
    task_id: str
    run_id: str
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    artifact_path: str | None = None
    error: str | None = None
    cache_key: str | None = None


class PipelineRunSummary(StrictModel):
    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    completed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    failed: int = Field(ge=0)


class SubmissionReport(StrictModel):
    output_path: Path
    rows: int
    columns: int
    decision_count: int
    candidate_only: bool
    duplicate_re_numbers: list[str]
    sha256: str
