from freca.experiments.planning import build_execution_plan
from freca.experiments.models import ExperimentMethod
from freca.models import CheckpointDefinition


def _checkpoint(cp_id: str, element_id: int) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=element_id,
        element_title=f"Element-{element_id}",
        section_title="Official section",
        text=f"Official checkpoint {cp_id}",
        source_file="checkingpoints_all_elements_onesheet.xlsx",
        cell="A3",
    )


def _checkpoints() -> list[CheckpointDefinition]:
    return [
        _checkpoint("CP1", 1),
        _checkpoint("CP2", 1),
        _checkpoint("CP3", 2),
        _checkpoint("CP4", 4),
    ]


def test_case_full_plans_one_call_with_all_checkpoints() -> None:
    plan = build_execution_plan(
        ExperimentMethod.CASE_FULL,
        case_id=7,
        checkpoints=_checkpoints(),
    )

    assert [unit.checkpoint_ids for unit in plan.units] == [
        ("CP1", "CP2", "CP3", "CP4")
    ]


def test_element_full_groups_checkpoints_by_official_element() -> None:
    plan = build_execution_plan(
        ExperimentMethod.ELEMENT_FULL,
        case_id=7,
        checkpoints=_checkpoints(),
    )

    assert [unit.checkpoint_ids for unit in plan.units] == [
        ("CP1", "CP2"),
        ("CP3",),
        ("CP4",),
    ]


def test_single_checkpoint_methods_plan_one_call_per_checkpoint() -> None:
    for method in (
        ExperimentMethod.CHECKPOINT_FULL,
        ExperimentMethod.AUTOMATIC_RETRIEVAL,
    ):
        plan = build_execution_plan(method, case_id=7, checkpoints=_checkpoints())

        assert [unit.checkpoint_ids for unit in plan.units] == [
            ("CP1",),
            ("CP2",),
            ("CP3",),
            ("CP4",),
        ]
