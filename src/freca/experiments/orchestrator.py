"""Experiment run orchestrator: iterate ExecutionUnits over a real client and persist artifacts."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from freca.experiments.materials import (
    load_material_snapshot_from_parsed,
    select_automatic_retrieval_material,
)
from freca.experiments.models import (
    ExecutionPlan,
    ExecutionResult,
    ExperimentMethod,
    MaterialSnapshot,
    Track3Condition,
)
from freca.experiments.planning import build_execution_plan
from freca.experiments.runner import run_execution
from freca.experiments.stage_audit import run_stage_audit_plan
from freca.experiments.agent_audit import run_agent_audit_plan
from freca.experiments.verify_audit import run_verify_audit_plan
from freca.llm import JsonChatClient
from freca.models import CheckpointDefinition
from freca.state import atomic_write_json


def materialize_for_unit(
    *,
    parsed_dir: Path,
    case_id: int,
    checkpoints: Sequence[CheckpointDefinition],
    unit_method: ExperimentMethod,
    unit_checkpoint_ids: tuple[str, ...],
    track3_condition: Track3Condition,
    per_scope_limit: int = 12,
) -> MaterialSnapshot:
    """Build the per-unit material snapshot, applying automatic_retrieval selection when needed."""
    snapshot = load_material_snapshot_from_parsed(
        parsed_dir=parsed_dir,
        case_id=case_id,
        checkpoints=list(checkpoints),
        track3_condition=track3_condition,
    )
    if unit_method == ExperimentMethod.AUTOMATIC_RETRIEVAL:
        snapshot = select_automatic_retrieval_material(
            snapshot,
            checkpoint_ids=unit_checkpoint_ids,
            per_scope_limit=per_scope_limit,
        )
    return snapshot


def run_experiment(
    *,
    plan: ExecutionPlan,
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    track3_condition: Track3Condition,
    client: JsonChatClient,
    artifact_root: Path,
    per_scope_limit: int = 12,
    uncertainty_threshold: float = 0.5,
) -> list[ExecutionResult]:
    """Execute every unit in the plan, persist artifacts under artifact_root/method/case-NNN/."""
    if plan.method == ExperimentMethod.STAGE_AUDIT:
        return _run_stage_audit_experiment(
            plan=plan,
            checkpoints=checkpoints,
            parsed_dir=parsed_dir,
            track3_condition=track3_condition,
            client=client,
            artifact_root=artifact_root,
        )
    if plan.method == ExperimentMethod.AGENT_AUDIT:
        return _run_agent_audit_experiment(
            plan=plan,
            checkpoints=checkpoints,
            parsed_dir=parsed_dir,
            track3_condition=track3_condition,
            client=client,
            artifact_root=artifact_root,
            uncertainty_threshold=uncertainty_threshold,
        )
    if plan.method == ExperimentMethod.VERIFY_AUDIT:
        return _run_verify_audit_experiment(
            plan=plan,
            checkpoints=checkpoints,
            parsed_dir=parsed_dir,
            track3_condition=track3_condition,
            client=client,
            artifact_root=artifact_root,
        )
    results: list[ExecutionResult] = []
    for index, unit in enumerate(plan.units):
        material = materialize_for_unit(
            parsed_dir=parsed_dir,
            case_id=unit.case_id,
            checkpoints=checkpoints,
            unit_method=unit.method,
            unit_checkpoint_ids=unit.checkpoint_ids,
            track3_condition=track3_condition,
            per_scope_limit=per_scope_limit,
        )
        unit_dir = (
            artifact_root
            / plan.method.value
            / f"case-{unit.case_id:03d}"
            / f"track3-{track3_condition.value}"
            / f"unit-{index:03d}"
        )
        result = run_execution(
            unit=unit,
            material=material,
            client=client,
            artifact_dir=unit_dir,
        )
        results.append(result)
    summary = {
        "method": plan.method.value,
        "case_id": plan.case_id,
        "track3_condition": track3_condition.value,
        "units_total": len(results),
        "units_valid": sum(1 for result in results if result.valid),
        "verdicts_total": sum(len(result.verdicts) for result in results),
    }
    atomic_write_json(artifact_root / plan.method.value / "summary.json", summary)
    return results


def _run_stage_audit_experiment(
    *,
    plan: ExecutionPlan,
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    track3_condition: Track3Condition,
    client: JsonChatClient,
    artifact_root: Path,
) -> list[ExecutionResult]:
    material = load_material_snapshot_from_parsed(
        parsed_dir=parsed_dir,
        case_id=plan.case_id,
        checkpoints=list(checkpoints),
        track3_condition=track3_condition,
    )
    root = artifact_root / plan.method.value / f"case-{plan.case_id:03d}" / f"track3-{track3_condition.value}"
    results = run_stage_audit_plan(
        plan_units=plan.units,
        material=material,
        client=client,
        artifact_root=root,
    )
    summary = {
        "method": plan.method.value,
        "case_id": plan.case_id,
        "track3_condition": track3_condition.value,
        "units_total": len(results),
        "units_valid": sum(1 for result in results if result.valid),
        "verdicts_total": sum(len(result.verdicts) for result in results),
    }
    atomic_write_json(artifact_root / plan.method.value / "summary.json", summary)
    return results


def _run_verify_audit_experiment(
    *,
    plan: ExecutionPlan,
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    track3_condition: Track3Condition,
    client: JsonChatClient,
    artifact_root: Path,
) -> list[ExecutionResult]:
    material = load_material_snapshot_from_parsed(
        parsed_dir=parsed_dir,
        case_id=plan.case_id,
        checkpoints=list(checkpoints),
        track3_condition=track3_condition,
    )
    root = artifact_root / plan.method.value / f"case-{plan.case_id:03d}" / f"track3-{track3_condition.value}"
    results = run_verify_audit_plan(
        plan_units=plan.units,
        material=material,
        client=client,
        artifact_root=root,
    )
    summary = {
        "method": plan.method.value,
        "case_id": plan.case_id,
        "track3_condition": track3_condition.value,
        # VERIFY_AUDIT emits one per-CP result, so units_total == CP count.
        "units_total": len(results),
        "units_valid": sum(1 for result in results if result.valid),
        "verdicts_total": sum(len(result.verdicts) for result in results),
    }
    atomic_write_json(artifact_root / plan.method.value / "summary.json", summary)
    return results


def _run_agent_audit_experiment(
    *,
    plan: ExecutionPlan,
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    track3_condition: Track3Condition,
    client: JsonChatClient,
    artifact_root: Path,
    uncertainty_threshold: float = 0.5,
) -> list[ExecutionResult]:
    material = load_material_snapshot_from_parsed(
        parsed_dir=parsed_dir,
        case_id=plan.case_id,
        checkpoints=list(checkpoints),
        track3_condition=track3_condition,
    )
    root = artifact_root / plan.method.value / f"case-{plan.case_id:03d}" / f"track3-{track3_condition.value}"
    results = run_agent_audit_plan(
        plan_units=plan.units,
        material=material,
        client=client,
        artifact_root=root,
        uncertainty_threshold=uncertainty_threshold,
    )
    summary = {
        "method": plan.method.value,
        "case_id": plan.case_id,
        "track3_condition": track3_condition.value,
        "units_total": len(results),
        "units_valid": sum(1 for result in results if result.valid),
        "verdicts_total": sum(len(result.verdicts) for result in results),
    }
    atomic_write_json(artifact_root / plan.method.value / "summary.json", summary)
    return results