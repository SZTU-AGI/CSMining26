from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from freca.models import StrictModel


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
