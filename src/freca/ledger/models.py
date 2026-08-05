"""Domain models for the structured fact ledger + runtime regulatory rubric architecture.

This module implements the schemas described in
``docs/STRUCTURED_RUBRIC_AUDIT_PROPOSAL.md``. It is additive: nothing in
``freca.models`` is modified. Shared primitives (``StrictModel``,
``SourceLocation``, ``Applicability``, ``Verdict``) are reused directly so that
both architectures speak the same vocabulary for the final ``1 / 0 / N/A``
label.

Design invariants enforced here (not merely documented):

* §4  Fact extraction may not pre-judge compliance. ``FactPolarity`` has a
  single legal value and rejects ``supporting`` / ``contrary`` inputs.
* §5  Every rubric criterion must cite at least one policy chunk, and every
  cited chunk must be present in the rubric's own retrieval context.
* §7  ``N/A`` requires ``NOT_APPLICABLE`` applicability plus an applicability
  explanation; evidence-quality problems live in ``quality_flags`` and never
  silently become a business label.
* §6  Scorecards expose five independent dimensions and deliberately provide
  no weighted total.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from freca.models import Applicability, SourceLocation, StrictModel, Verdict

CP_ID_PATTERN = r"^CP(?:[1-9]|[1-3][0-9]|4[01])$"


# --------------------------------------------------------------------------
# Stage A — structured fact ledger (§4)
# --------------------------------------------------------------------------


class FactPolarity(StrEnum):
    """§4: the extraction stage records facts, it does not judge them.

    Only one value is legal. The proposal's verbose literal
    ``supporting_or_contrary_not_decided`` is accepted as an input alias.
    """

    UNDECIDED = "undecided"


_POLARITY_ALIASES = {
    "undecided": FactPolarity.UNDECIDED,
    "not_decided": FactPolarity.UNDECIDED,
    "supporting_or_contrary_not_decided": FactPolarity.UNDECIDED,
    "unknown": FactPolarity.UNDECIDED,
    "neutral": FactPolarity.UNDECIDED,
}

_FORBIDDEN_POLARITY = {
    "supporting",
    "contrary",
    "support",
    "against",
    "compliant",
    "non_compliant",
    "noncompliant",
    "violation",
    "pass",
    "fail",
}


class ContradictionKind(StrEnum):
    SAME_TOPIC_CONFLICT = "same_topic_conflict"
    IDENTITY_MISMATCH = "identity_mismatch"
    MISSING_RECORD = "missing_record"
    CROSS_DOCUMENT_VALUE = "cross_document_value"


class FactRecord(StrictModel):
    """One traceable factual statement extracted from a single evidence chunk.

    ``verbatim`` must be recoverable from the cited chunk; the extractor marks
    ``verbatim_not_found_in_source`` in ``quality_flags`` when it is not, so a
    hallucinated quote can never silently support a verdict.
    """

    fact_id: str = Field(min_length=1)
    case_id: int = Field(ge=1, le=100)
    topic: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    value: str = ""
    polarity: FactPolarity = FactPolarity.UNDECIDED
    source_file: str
    source_id: str
    chunk_id: str
    track: int | None = Field(default=None, ge=1, le=9)
    location: SourceLocation = Field(default_factory=SourceLocation)
    verbatim: str = ""
    evidence_categories: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    extraction_batch: str | None = None

    @field_validator("polarity", mode="before")
    @classmethod
    def _normalize_polarity(cls, value: Any) -> Any:
        if isinstance(value, FactPolarity):
            return value
        if value is None:
            return FactPolarity.UNDECIDED
        text = str(value).strip().casefold()
        if text in _FORBIDDEN_POLARITY:
            raise ValueError(
                "fact extraction must not pre-judge polarity; "
                f"received {value!r} (proposal §4)"
            )
        if text in _POLARITY_ALIASES:
            return _POLARITY_ALIASES[text]
        raise ValueError(f"unsupported fact polarity: {value!r}")

    @property
    def is_answer_like(self) -> bool:
        """True when the fact came from answer-like scenario text (§3 red line)."""

        return "answer_like_field" in self.quality_flags

    @property
    def is_contaminated(self) -> bool:
        return "exclude_from_compliance_evidence" in self.quality_flags

    @property
    def citable_for_support(self) -> bool:
        return not self.is_answer_like and not self.is_contaminated

    def locator(self) -> str:
        parts = [self.source_file]
        location = self.location.model_dump(exclude_none=True)
        for key in ("sheet", "cell_range", "page", "section", "paragraph_index"):
            if key in location:
                parts.append(f"{key}={location[key]}")
        return " ".join(str(part) for part in parts)


class FactContradiction(StrictModel):
    """A conflict discovered inside one case's ledger (§4.1 "矛盾")."""

    contradiction_id: str
    case_id: int = Field(ge=1, le=100)
    kind: ContradictionKind
    topic: str
    fact_ids: list[str] = Field(default_factory=list)
    detail: str
    severity: str = "REVIEW"

    @field_validator("severity")
    @classmethod
    def _severity_domain(cls, value: str) -> str:
        if value not in {"REVIEW", "BLOCKER"}:
            raise ValueError("severity must be REVIEW or BLOCKER")
        return value


class CaseFactLedger(StrictModel):
    """Per-case output of the single materials pass (§4)."""

    case_id: int = Field(ge=1, le=100)
    re_number: str = ""
    facts: list[FactRecord] = Field(default_factory=list)
    contradictions: list[FactContradiction] = Field(default_factory=list)
    topic_coverage: dict[str, int] = Field(default_factory=dict)
    track_coverage: dict[str, int] = Field(default_factory=dict)
    missing_tracks: list[int] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    chunk_count: int = 0
    extractor: str = "unknown"
    ledger_version: str = "1"
    input_hash: str = ""

    @model_validator(mode="after")
    def _facts_belong_to_case(self) -> CaseFactLedger:
        wrong = [fact.fact_id for fact in self.facts if fact.case_id != self.case_id]
        if wrong:
            raise ValueError(f"facts do not belong to case {self.case_id}: {wrong[:5]}")
        duplicates = len(self.facts) - len({fact.fact_id for fact in self.facts})
        if duplicates:
            raise ValueError(f"ledger contains {duplicates} duplicate fact_id values")
        return self

    def by_id(self, fact_id: str) -> FactRecord:
        for fact in self.facts:
            if fact.fact_id == fact_id:
                return fact
        raise KeyError(fact_id)

    def index(self) -> dict[str, FactRecord]:
        return {fact.fact_id: fact for fact in self.facts}


# --------------------------------------------------------------------------
# Stage B — runtime regulatory rubric (§5)
# --------------------------------------------------------------------------


class CriterionKind(StrEnum):
    APPLICABILITY = "applicability"
    SUPPORTING = "supporting"
    CONTRARY = "contrary"
    EXCEPTION_TIMING = "exception_timing"


class RubricCriterion(StrictModel):
    """One rubric line item, always grounded in retrieved policy text."""

    criterion_id: str = Field(min_length=1)
    kind: CriterionKind
    statement: str = Field(min_length=1)
    policy_citations: list[str] = Field(min_length=1)
    facts_to_verify: list[str] = Field(default_factory=list)
    required_evidence_categories: list[str] = Field(default_factory=list)


class CheckpointRubric(StrictModel):
    """Runtime-derived, citation-complete rubric for one checking point.

    The rubric is a *derived product of the official regulation*, regenerated
    from retrieval output and cached by ``input_hash``. It is never a
    hand-written answer rule (§3, §5).
    """

    cp_id: str = Field(pattern=CP_ID_PATTERN)
    element_id: int = Field(ge=1, le=4)
    element_title: str = ""
    checkpoint_text: str = ""
    applicability_note: str = ""
    criteria: list[RubricCriterion] = Field(min_length=1)
    policy_chunk_ids: list[str] = Field(min_length=1)
    policy_snippets: dict[str, str] = Field(default_factory=dict)
    retrieval_queries: list[str] = Field(default_factory=list)
    generator: dict[str, str] = Field(default_factory=dict)
    rubric_version: str = ""
    input_hash: str = ""

    @model_validator(mode="after")
    def _citations_resolve(self) -> CheckpointRubric:
        available = set(self.policy_chunk_ids)
        unknown = sorted(
            {
                citation
                for criterion in self.criteria
                for citation in criterion.policy_citations
                if citation not in available
            }
        )
        if unknown:
            raise ValueError(
                "rubric cites policy chunks outside its retrieval context: "
                + ", ".join(unknown[:5])
            )
        kinds = {criterion.kind for criterion in self.criteria}
        if CriterionKind.APPLICABILITY not in kinds:
            raise ValueError("rubric requires at least one applicability criterion")
        if CriterionKind.SUPPORTING not in kinds:
            raise ValueError("rubric requires at least one supporting criterion")
        ids = [criterion.criterion_id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("rubric criterion_id values must be unique")
        return self

    def by_id(self, criterion_id: str) -> RubricCriterion:
        for criterion in self.criteria:
            if criterion.criterion_id == criterion_id:
                return criterion
        raise KeyError(criterion_id)

    def criteria_of(self, kind: CriterionKind) -> list[RubricCriterion]:
        return [criterion for criterion in self.criteria if criterion.kind == kind]


# --------------------------------------------------------------------------
# Stage C — compact evidence pack (§5.4, §7)
# --------------------------------------------------------------------------


class PackedFact(StrictModel):
    fact: FactRecord
    relevance: float = Field(ge=0.0)
    matched_criteria: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)


class EvidencePack(StrictModel):
    """Everything the adjudicator is allowed to look at for one case×CP."""

    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=CP_ID_PATTERN)
    rubric_version: str = ""
    facts: list[PackedFact] = Field(default_factory=list)
    contradictions: list[FactContradiction] = Field(default_factory=list)
    integrity_notes: list[str] = Field(default_factory=list)
    coverage_by_criterion: dict[str, int] = Field(default_factory=dict)
    uncovered_criteria: list[str] = Field(default_factory=list)
    ledger_fact_count: int = 0
    excluded_fact_count: int = 0
    selection_trace: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def fact_ids(self) -> set[str]:
        return {item.fact.fact_id for item in self.facts}

    def fact_index(self) -> dict[str, FactRecord]:
        return {item.fact.fact_id: item.fact for item in self.facts}


# --------------------------------------------------------------------------
# Stage D — adjudication (§5.5, §7)
# --------------------------------------------------------------------------


class CriterionStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    NOT_EVIDENCED = "not_evidenced"
    NOT_APPLICABLE = "not_applicable"


class EvidenceCoverage(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class DecisionStage(StrEnum):
    PRIMARY = "primary"
    REVIEW = "review"


class CriterionOutcome(StrictModel):
    criterion_id: str
    status: CriterionStatus
    fact_ids: list[str] = Field(default_factory=list)
    note: str = ""


class LedgerDecision(StrictModel):
    """A rubric-anchored verdict with mandatory dual citation."""

    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=CP_ID_PATTERN)
    applicability: Applicability
    verdict: Verdict
    criterion_outcomes: list[CriterionOutcome] = Field(default_factory=list)
    policy_citations: list[str] = Field(default_factory=list)
    supporting_fact_ids: list[str] = Field(default_factory=list)
    contrary_fact_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverage = EvidenceCoverage.PARTIAL
    applicability_reasoning: str = ""
    reasoning_summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    quality_flags: list[str] = Field(default_factory=list)
    stage: DecisionStage = DecisionStage.PRIMARY
    rubric_version: str = ""

    @model_validator(mode="after")
    def _verdict_semantics(self) -> LedgerDecision:
        if self.verdict == Verdict.NOT_APPLICABLE:
            if self.applicability != Applicability.NOT_APPLICABLE:
                raise ValueError("N/A requires NOT_APPLICABLE applicability")
            if not self.policy_citations:
                raise ValueError("N/A requires at least one policy citation")
        elif self.applicability == Applicability.NOT_APPLICABLE:
            raise ValueError("NOT_APPLICABLE applicability requires an N/A verdict")
        return self

    @property
    def cited_fact_ids(self) -> list[str]:
        return [*self.supporting_fact_ids, *self.contrary_fact_ids]


# --------------------------------------------------------------------------
# Stage E — gates and internal scoring (§6, §7)
# --------------------------------------------------------------------------


class GateSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class GateFinding(StrictModel):
    code: str
    severity: GateSeverity
    message: str


class EvidenceScorecard(StrictModel):
    """§6 — five independent dimensions, deliberately without a weighted total.

    These values express evidence quality and review priority only. They must
    never be summed into a "80 = compliant" threshold.
    """

    regulatory_coverage: float = Field(ge=0.0, le=1.0)
    support_coverage: float = Field(ge=0.0, le=1.0)
    contrary_strength: float = Field(ge=0.0, le=1.0)
    citation_quality: float = Field(ge=0.0, le=1.0)
    evidence_integrity: float = Field(ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)

    def as_dimensions(self) -> dict[str, float]:
        return {
            "regulatory_coverage": self.regulatory_coverage,
            "support_coverage": self.support_coverage,
            "contrary_strength": self.contrary_strength,
            "citation_quality": self.citation_quality,
            "evidence_integrity": self.evidence_integrity,
        }


class GateReport(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=CP_ID_PATTERN)
    passed: bool
    findings: list[GateFinding] = Field(default_factory=list)
    review_triggers: list[str] = Field(default_factory=list)
    scorecard: EvidenceScorecard
    review_priority: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_triggers) or not self.passed

    @property
    def errors(self) -> list[GateFinding]:
        return [
            finding
            for finding in self.findings
            if finding.severity == GateSeverity.ERROR
        ]


class TaskOutcome(StrictModel):
    """The full auditable record for one case×CP."""

    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=CP_ID_PATTERN)
    primary: LedgerDecision
    primary_gate: GateReport
    review: LedgerDecision | None = None
    review_gate: GateReport | None = None
    final: LedgerDecision
    reviewed: bool = False
    resolution: str = "ACCEPT_PRIMARY"
    pack_summary: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# §8 — artifact classification
# --------------------------------------------------------------------------


class ArtifactClass(StrEnum):
    EVIDENCE_INTEGRITY_QA = "evidence_integrity_qa"
    SILVER_CONSISTENCY = "silver_consistency"
    PRODUCTION_CANDIDATE = "production_candidate"


class EvidenceView(StrictModel):
    """§8 — methods sharing an evidence view are not independent voters."""

    method: str
    model_signature: str
    context_construction: str
    retrieval_scope: str

    def view_signature(self) -> str:
        return "|".join(
            (self.model_signature, self.context_construction, self.retrieval_scope)
        )


class SilverEntry(StrictModel):
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=CP_ID_PATTERN)
    verdict: Verdict
    agreeing_methods: list[str]
    distinct_view_count: int = Field(ge=0)
    citation_complete: bool


class BaselineReport(StrictModel):
    run_id: str
    integrity_qa: dict[str, Any] = Field(default_factory=dict)
    silver: dict[str, Any] = Field(default_factory=dict)
    production: dict[str, Any] = Field(default_factory=dict)
    disclaimers: list[str] = Field(default_factory=list)


__all__ = [
    "CP_ID_PATTERN",
    "ArtifactClass",
    "BaselineReport",
    "CaseFactLedger",
    "CheckpointRubric",
    "ContradictionKind",
    "CriterionKind",
    "CriterionOutcome",
    "CriterionStatus",
    "DecisionStage",
    "EvidenceCoverage",
    "EvidencePack",
    "EvidenceScorecard",
    "EvidenceView",
    "FactContradiction",
    "FactPolarity",
    "FactRecord",
    "GateFinding",
    "GateReport",
    "GateSeverity",
    "LedgerDecision",
    "PackedFact",
    "RubricCriterion",
    "SilverEntry",
    "TaskOutcome",
]
