from __future__ import annotations

from collections.abc import Sequence

from freca.experiments.models import ExperimentMethod, ExecutionPlan, ExecutionUnit
from freca.models import CheckpointDefinition


def build_execution_plan(
    method: ExperimentMethod,
    *,
    case_id: int,
    checkpoints: Sequence[CheckpointDefinition],
) -> ExecutionPlan:
    """Create deterministic, method-specific model-call units from official CPs."""
    ordered = tuple(sorted(checkpoints, key=lambda checkpoint: int(checkpoint.cp_id[2:])))
    if not ordered:
        raise ValueError("at least one checkpoint is required")

    groups = _groups_for_method(method, ordered)
    return ExecutionPlan(
        method=method,
        case_id=case_id,
        units=tuple(
            ExecutionUnit(
                case_id=case_id,
                method=method,
                checkpoint_ids=tuple(checkpoint.cp_id for checkpoint in group),
            )
            for group in groups
        ),
    )


def _groups_for_method(
    method: ExperimentMethod,
    checkpoints: tuple[CheckpointDefinition, ...],
) -> tuple[tuple[CheckpointDefinition, ...], ...]:
    if method == ExperimentMethod.CASE_FULL:
        return (checkpoints,)
    if method in {
        ExperimentMethod.CHECKPOINT_FULL,
        ExperimentMethod.AUTOMATIC_RETRIEVAL,
    }:
        return tuple((checkpoint,) for checkpoint in checkpoints)

    grouped: dict[int, list[CheckpointDefinition]] = {}
    for checkpoint in checkpoints:
        grouped.setdefault(checkpoint.element_id, []).append(checkpoint)
    return tuple(tuple(grouped[element_id]) for element_id in sorted(grouped))
