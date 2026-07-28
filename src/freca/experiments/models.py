from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from freca.models import CheckpointDefinition, EvidenceChunk, StrictModel, Verdict


class ExperimentMethod(StrEnum):
    CASE_FULL = "case_full"
    ELEMENT_FULL = "element_full"
    CHECKPOINT_FULL = "checkpoint_full"
    AUTOMATIC_RETRIEVAL = "automatic_retrieval"


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


class SilverComparison(StrictModel):
    shared_checkpoints: tuple[str, ...]
    matched_checkpoints: tuple[str, ...]
    silver_agreement: float = Field(ge=0.0, le=1.0)
