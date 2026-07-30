from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from freca.models import CheckpointDefinition, EvidenceChunk, StrictModel, Verdict


class ExperimentMethod(StrEnum):
    CASE_FULL = "case_full"
    ELEMENT_FULL = "element_full"
    CHECKPOINT_FULL = "checkpoint_full"
    AUTOMATIC_RETRIEVAL = "automatic_retrieval"
    STAGE_AUDIT = "stage_audit"
    AGENT_AUDIT = "agent_audit"


class Track3Condition(StrEnum):
    """Whether the Track 3 "Audit scenario" near-answer narrative is sent raw or redacted."""

    RAW = "raw"
    MASKED = "masked"


class ExecutionUnit(StrictModel):
    case_id: int = Field(ge=1, le=100)
    method: ExperimentMethod
    checkpoint_ids: tuple[str, ...] = Field(min_length=1)


class ExecutionPlan(StrictModel):
    method: ExperimentMethod
    case_id: int = Field(ge=1, le=100)
    units: tuple[ExecutionUnit, ...] = Field(min_length=1)


class MaterialSnapshot(StrictModel):
    case_id: int = Field(ge=1, le=100)
    checkpoints: tuple[CheckpointDefinition, ...] = Field(min_length=1)
    chunks: tuple[EvidenceChunk, ...]
    image_paths: tuple[str, ...] = ()
    track3_condition: Track3Condition = Track3Condition.RAW
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(chunk.chunk_id for chunk in self.chunks)


class PromptEnvelope(StrictModel):
    system: str
    text: str
    image_paths: tuple[str, ...] = ()
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperimentVerdict(StrictModel):
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    verdict: Verdict
    reason: str = Field(min_length=1)
    citation_ids: tuple[str, ...] = Field(min_length=1)
    uncertainty: float = Field(ge=0.0, le=1.0)


class ExecutionResult(StrictModel):
    unit: ExecutionUnit
    valid: bool
    errors: tuple[str, ...] = ()
    verdicts: tuple[ExperimentVerdict, ...] = ()
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage_trace: "StageTrace | None" = None
    agent_trace: "AgentTrace | None" = None


class AgentModuleCall(StrictModel):
    """One fired module inside an AGENT_AUDIT execution.

    Only populated when ``ExecutionResult.unit.method == AGENT_AUDIT`` and the
    condition was triggered; the trace is the audit log the reviewer reads to
    explain why a non-trivial case went through the extra LLM calls.
    """

    module: str  # "retrieval_repair" | "critic" | "verifier" | "arbitration"
    trigger: str
    verdict_before: str | None = None
    verdict_after: str | None = None
    extra_calls: int = 1


class AgentTrace(StrictModel):
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    fired_modules: tuple[AgentModuleCall, ...] = Field(default_factory=tuple)
    extra_calls: int = 0
    final_resolution: str = ""  # e.g. "ACCEPT", "REPAIRED", "VERIFIED", "REVIEW_DISAGREEMENT"


class StageTrace(StrictModel):
    """Per-stage observations captured during a STAGE_AUDIT execution.

    Only populated when ``ExecutionResult.unit.method == STAGE_AUDIT``; the rest
    of the metric / silver pipeline ignores this field so non-stage methods
    keep their existing shape.
    """

    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    applicability: str | None = None  # APPLICABLE | NOT_APPLICABLE
    applicability_reason: str | None = None
    policy_citations: tuple[str, ...] = Field(default_factory=tuple)
    contradictions: tuple[str, ...] = Field(default_factory=tuple)
    verdict: str | None = None
    reason: str | None = None
    citation_ids: tuple[str, ...] = Field(default_factory=tuple)
    uncertainty: float = 1.0


class SilverComparison(StrictModel):
    shared_checkpoints: tuple[str, ...]
    matched_checkpoints: tuple[str, ...]
    silver_agreement: float = Field(ge=0.0, le=1.0)


class SilverTier(StrEnum):
    """Provenance of a silver verdict — how trustworthy it is as a reference."""

    ANOMALY_RULE = "anomaly_rule"  # derived from anomaly_report (all N/A)
    HUMAN = "human"  # hand-labelled by a domain reviewer
    WEAK_CONSENSUS = "weak_consensus"  # case_full self-consensus, no external anchor


class SilverReference(StrictModel):
    """Layered silver standard: case_id -> cp_id -> (verdict, tier).

    Only ANOMALY_RULE and HUMAN tiers carry an external anchor and contribute to
    ``silver_agreement``. WEAK_CONSENSUS entries are tracked for method-agreement
    reporting but are excluded from accuracy-style metrics because the consensus
    source (case_full) may itself be biased (e.g. blanket-approve).
    """

    entries: dict[str, dict[str, "SilverEntry"]] = Field(default_factory=dict)

    def cp_verdict(self, case_id: int, cp_id: str) -> "SilverEntry | None":
        return self.entries.get(str(case_id), {}).get(cp_id)


class SilverEntry(StrictModel):
    verdict: Verdict
    tier: SilverTier
    note: str = ""


class PerCheckpointMetric(StrictModel):
    """Per-CP accuracy and verdict distribution across one or more candidate runs."""

    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    element_id: int = Field(ge=1, le=4)
    anchored_total: int = Field(ge=0)
    anchored_correct: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)
    candidate_verdict_counts: dict[str, int] = Field(default_factory=dict)
    silver_verdict_counts: dict[str, int] = Field(default_factory=dict)


class PerElementMetric(StrictModel):
    """Aggregate per-Element accuracy across all CPs in the element."""

    element_id: int = Field(ge=1, le=4)
    anchored_total: int = Field(ge=0)
    anchored_correct: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)


class NAClassificationMetric(StrictModel):
    """N/A detection quality on the silver anchors."""

    predicted_na: int = Field(ge=0)
    predicted_non_na: int = Field(ge=0)
    silver_na: int = Field(ge=0)
    silver_non_na: int = Field(ge=0)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)


class CitationValidityMetric(StrictModel):
    """Citation correctness across all verdicts in a run.

    A citation is "valid" iff it appears in the material's allowed set after the
    prefix-repair pass (so a repaired verdict counts as fully valid; an
    unrepairable unknown contributes to ``invalid_count``).
    """

    total_citations: int = Field(ge=0)
    valid_citations: int = Field(ge=0)
    invalid_citations: int = Field(ge=0)
    validity_rate: float = Field(ge=0.0, le=1.0)
    verdicts_with_invalid: int = Field(ge=0)
    verdicts_total: int = Field(ge=0)


class RunCostMetric(StrictModel):
    """Per-run cost / latency aggregated from client usage telemetry."""

    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0.0)


class RunMetrics(StrictModel):
    """All single-run metrics that can be computed from one ExecutionResult.

    ``per_checkpoint`` and ``per_element`` are empty when no silver reference
    is supplied; ``na_classification`` likewise requires silver anchors.
    """

    case_id: int = Field(ge=1, le=100)
    method: ExperimentMethod
    track3_condition: str = "raw"  # raw | masked — tag only, not enforced here
    verdicts_total: int = Field(ge=0)
    verdicts_valid: int = Field(ge=0)
    valid_rate: float = Field(ge=0.0, le=1.0)
    anchored_total: int = Field(ge=0)
    anchored_correct: int = Field(ge=0)
    overall_accuracy: float = Field(ge=0.0, le=1.0)
    per_checkpoint: tuple[PerCheckpointMetric, ...] = Field(default_factory=tuple)
    per_element: tuple[PerElementMetric, ...] = Field(default_factory=tuple)
    na_classification: NAClassificationMetric | None = None
    citations: CitationValidityMetric | None = None
    cost: RunCostMetric | None = None


class MaskDeltaMetric(StrictModel):
    """Pairwise delta of a metric value between masked and raw runs on the same case."""

    case_id: int = Field(ge=1, le=100)
    method: ExperimentMethod
    metric: str  # e.g. "overall_accuracy"
    raw_value: float
    masked_value: float
    delta: float  # masked - raw


class InstabilityMetric(StrictModel):
    """Per-CP variance across N re-runs of the same (case, method).

    Requires multiple ExecutionResults with the same unit.case_id and method;
    case_full runs are the canonical target since they cover all 41 CPs at once.
    """

    case_id: int = Field(ge=1, le=100)
    method: ExperimentMethod
    reruns: int = Field(ge=2)
    per_cp_dominant_verdict: dict[str, str] = Field(default_factory=dict)
    per_cp_agreement_rate: dict[str, float] = Field(default_factory=dict)
    unstable_cp_count: int = Field(ge=0)  # agreement_rate < 1.0
    overall_agreement: float = Field(ge=0.0, le=1.0)
