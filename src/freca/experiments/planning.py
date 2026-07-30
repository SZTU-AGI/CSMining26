from __future__ import annotations

from collections.abc import Sequence

from freca.experiments.models import ExperimentMethod, ExecutionPlan, ExecutionUnit
from freca.models import CheckpointDefinition


def select_cases(
    *,
    case_ids: Sequence[int],
    limit: int | None = None,
    only: Sequence[int] | None = None,
) -> tuple[int, ...]:
    """Return the deterministic subset of case ids an experiment method should run on.

    ``only`` (when given) selects an explicit subset, ordered against the full set and
    validated against it. Otherwise ``limit`` takes the first N cases in order. With
    neither, every case id is returned. This lets expensive methods such as
    ``checkpoint_full`` run on a bounded sample while ``case_full`` runs the full set.
    """
    ordered = tuple(sorted(case_ids))
    if not ordered:
        raise ValueError("at least one case id is required")
    if only is not None:
        subset = tuple(sorted(only))
        unknown = tuple(case_id for case_id in subset if case_id not in set(ordered))
        if unknown:
            raise ValueError(f"unknown case ids: {', '.join(str(c) for c in unknown)}")
        return subset
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least one")
        return ordered[:limit]
    return ordered


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
        ExperimentMethod.STAGE_AUDIT,
        ExperimentMethod.AGENT_AUDIT,
    }:
        return tuple((checkpoint,) for checkpoint in checkpoints)

    grouped: dict[int, list[CheckpointDefinition]] = {}
    for checkpoint in checkpoints:
        grouped.setdefault(checkpoint.element_id, []).append(checkpoint)
    return tuple(tuple(grouped[element_id]) for element_id in sorted(grouped))
