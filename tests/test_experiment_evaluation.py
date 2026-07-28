from freca.experiments.evaluation import compare_to_reference
from freca.experiments.models import (
    ExecutionResult,
    ExecutionUnit,
    ExperimentMethod,
    ExperimentVerdict,
)


def _result(values: dict[str, str]) -> ExecutionResult:
    return ExecutionResult(
        unit=ExecutionUnit(
            case_id=7,
            method=ExperimentMethod.CASE_FULL,
            checkpoint_ids=tuple(values),
        ),
        valid=True,
        verdicts=tuple(
            ExperimentVerdict(
                cp_id=cp_id,
                verdict=verdict,
                reason="test",
                citation_ids=("case:7:track1",),
                uncertainty=0.1,
            )
            for cp_id, verdict in values.items()
        ),
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )


def test_compare_results_reports_silver_agreement_not_accuracy() -> None:
    comparison = compare_to_reference(
        candidate=_result({"CP1": "1", "CP2": "0"}),
        reference=_result({"CP1": "1", "CP2": "1"}),
    )

    assert comparison.silver_agreement == 0.5
    assert comparison.matched_checkpoints == ("CP1",)
    assert "accuracy" not in comparison.model_dump()


def test_compare_results_uses_only_shared_checkpoints() -> None:
    comparison = compare_to_reference(
        candidate=_result({"CP1": "1", "CP2": "0"}),
        reference=_result({"CP1": "1"}),
    )

    assert comparison.shared_checkpoints == ("CP1",)
    assert comparison.silver_agreement == 1.0
