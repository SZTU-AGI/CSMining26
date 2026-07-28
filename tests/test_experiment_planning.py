from freca.experiments.planning import build_execution_plan, select_cases
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


def test_select_cases_returns_all_case_ids_when_unbounded() -> None:
    assert select_cases(case_ids=(1, 2, 3, 4, 5)) == (1, 2, 3, 4, 5)


def test_select_cases_applies_a_deterministic_limit() -> None:
    assert select_cases(case_ids=(1, 2, 3, 4, 5), limit=2) == (1, 2)


def test_select_cases_explicit_subset_overrides_and_orders_against_the_full_set() -> None:
    assert select_cases(case_ids=(1, 2, 3, 4, 5), only=(5, 3)) == (3, 5)


def test_select_cases_rejects_subset_ids_outside_the_known_set() -> None:
    try:
        select_cases(case_ids=(1, 2, 3), only=(2, 99))
    except ValueError as error:
        assert "99" in str(error)
    else:
        raise AssertionError("expected unknown case id to be rejected")
