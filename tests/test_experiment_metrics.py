"""Tests for the full single-run and cross-run metrics surface."""
from __future__ import annotations

from freca.experiments.metrics import (
    compute_instability,
    compute_mask_delta,
    compute_run_metrics,
)
from freca.experiments.models import (
    ExecutionResult,
    ExecutionUnit,
    ExperimentMethod,
    ExperimentVerdict,
    RunCostMetric,
    SilverEntry,
    SilverReference,
    SilverTier,
    Track3Condition,
)
from freca.models import CheckpointDefinition, Verdict


def _cp(cp_id: str, element_id: int = 1) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=element_id,
        element_title=f"Element-{element_id}",
        section_title="section",
        text=f"checkpoint {cp_id}",
        source_file="cp.xlsx",
        cell="A1",
    )


def _verdict(cp_id: str, verdict: str, citations: tuple[str, ...] = ("case:1:track1",)) -> ExperimentVerdict:
    return ExperimentVerdict(
        cp_id=cp_id,
        verdict=Verdict(verdict),
        reason="r",
        citation_ids=citations,
        uncertainty=0.1,
    )


def _result(case_id: int, values: dict[str, str]) -> ExecutionResult:
    checkpoints = [_cp(cp_id) for cp_id in values]
    return ExecutionResult(
        unit=ExecutionUnit(
            case_id=case_id,
            method=ExperimentMethod.CASE_FULL,
            checkpoint_ids=tuple(values),
        ),
        valid=True,
        verdicts=tuple(_verdict(cp_id, verdict) for cp_id, verdict in values.items()),
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )


def test_run_metrics_without_silver_reports_only_structure() -> None:
    result = _result(1, {"CP1": "1", "CP2": "0"})
    metrics = compute_run_metrics(result=result, checkpoints=[_cp("CP1"), _cp("CP2")])

    assert metrics.verdicts_total == 2
    assert metrics.verdicts_valid == 2
    assert metrics.valid_rate == 1.0
    assert metrics.anchored_total == 0
    assert metrics.overall_accuracy == 0.0
    assert metrics.per_checkpoint == ()
    assert metrics.per_element == ()
    assert metrics.na_classification is None
    assert metrics.citations is not None and metrics.citations.validity_rate == 1.0


def test_run_metrics_anchors_only_human_and_anomaly() -> None:
    checkpoints = [_cp("CP1"), _cp("CP2"), _cp("CP3")]
    reference = SilverReference(
        entries={
            "1": {
                "CP1": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
                "CP2": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.WEAK_CONSENSUS),
                "CP3": SilverEntry(verdict=Verdict.NOT_APPLICABLE, tier=SilverTier.ANOMALY_RULE),
            }
        }
    )
    result = _result(1, {"CP1": "1", "CP2": "1", "CP3": "N/A"})

    metrics = compute_run_metrics(result=result, checkpoints=checkpoints, silver=reference)

    # Only CP1 (HUMAN) and CP3 (ANOMALY_RULE) are anchored; CP2 (WEAK) is excluded.
    assert metrics.anchored_total == 2
    assert metrics.anchored_correct == 2
    assert metrics.overall_accuracy == 1.0
    assert {m.cp_id for m in metrics.per_checkpoint} == {"CP1", "CP3"}


def test_run_metrics_per_element_aggregates_across_cps() -> None:
    checkpoints = [_cp("CP1", element_id=1), _cp("CP2", element_id=1), _cp("CP3", element_id=2)]
    reference = SilverReference(
        entries={
            "1": {
                "CP1": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
                "CP2": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
                "CP3": SilverEntry(verdict=Verdict.NON_COMPLIANT, tier=SilverTier.HUMAN),
            }
        }
    )
    result = _result(1, {"CP1": "1", "CP2": "0", "CP3": "0"})

    metrics = compute_run_metrics(result=result, checkpoints=checkpoints, silver=reference)

    by_element = {m.element_id: m for m in metrics.per_element}
    assert by_element[1].anchored_total == 2 and by_element[1].anchored_correct == 1
    assert by_element[1].accuracy == 0.5
    assert by_element[2].anchored_total == 1 and by_element[2].anchored_correct == 1


def test_run_metrics_na_precision_recall_f1() -> None:
    checkpoints = [_cp("CP1"), _cp("CP2"), _cp("CP3"), _cp("CP4")]
    reference = SilverReference(
        entries={
            "1": {
                "CP1": SilverEntry(verdict=Verdict.NOT_APPLICABLE, tier=SilverTier.HUMAN),  # TP
                "CP2": SilverEntry(verdict=Verdict.NOT_APPLICABLE, tier=SilverTier.HUMAN),  # FN
                "CP3": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),      # TN
                "CP4": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),      # FP (model says N/A)
            }
        }
    )
    result = _result(1, {"CP1": "N/A", "CP2": "1", "CP3": "1", "CP4": "N/A"})

    metrics = compute_run_metrics(result=result, checkpoints=checkpoints, silver=reference)
    na = metrics.na_classification
    assert na is not None
    assert na.true_positives == 1
    assert na.false_positives == 1
    assert na.false_negatives == 1
    assert na.precision == 0.5
    assert na.recall == 0.5
    assert na.f1 == 0.5


def test_run_metrics_citation_validity_counts_unknown() -> None:
    valid_citation = "case:1:track1"
    result = ExecutionResult(
        unit=ExecutionUnit(case_id=1, method=ExperimentMethod.CASE_FULL, checkpoint_ids=("CP1",)),
        valid=False,
        errors=("unknown citation_ids: bogus_id",),
        verdicts=(_verdict("CP1", "1", citations=(valid_citation, "bogus_id")),),
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )

    metrics = compute_run_metrics(result=result, checkpoints=[_cp("CP1")])

    assert metrics.citations is not None
    assert metrics.citations.total_citations == 2
    assert metrics.citations.invalid_citations == 1
    assert metrics.citations.valid_citations == 1
    assert metrics.citations.validity_rate == 0.5
    assert metrics.citations.verdicts_with_invalid == 1


def test_run_metrics_includes_cost_when_provided() -> None:
    result = _result(1, {"CP1": "1"})
    cost = RunCostMetric(calls=1, input_tokens=10, output_tokens=20, total_tokens=30, elapsed_seconds=1.5)
    metrics = compute_run_metrics(result=result, checkpoints=[_cp("CP1")], cost=cost)
    assert metrics.cost is not None and metrics.cost.total_tokens == 30


def test_run_metrics_track3_tag_round_trip() -> None:
    result = _result(1, {"CP1": "1"})
    metrics = compute_run_metrics(result=result, checkpoints=[_cp("CP1")], track3_condition=Track3Condition.MASKED)
    assert metrics.track3_condition == "masked"


def test_mask_delta_subtracts_raw_from_masked() -> None:
    raw = _result(1, {"CP1": "1"})
    masked = _result(1, {"CP1": "0"})
    raw_metrics = compute_run_metrics(result=raw, checkpoints=[_cp("CP1")])
    masked_metrics = compute_run_metrics(result=masked, checkpoints=[_cp("CP1")], track3_condition=Track3Condition.MASKED)
    # No silver anchors → both overall_accuracy are 0.0 and delta is 0.0
    delta = compute_mask_delta(case_id=1, method=ExperimentMethod.CASE_FULL, raw=raw_metrics, masked=masked_metrics)
    assert delta.metric == "overall_accuracy"
    assert delta.raw_value == 0.0
    assert delta.masked_value == 0.0
    assert delta.delta == 0.0


def test_instability_marks_diverging_cps() -> None:
    run_a = _result(1, {"CP1": "1", "CP2": "1"})
    run_b = _result(1, {"CP1": "1", "CP2": "0"})
    run_c = _result(1, {"CP1": "1", "CP2": "1"})

    inst = compute_instability(case_id=1, method=ExperimentMethod.CASE_FULL, reruns=[run_a, run_b, run_c])

    assert inst.reruns == 3
    assert inst.per_cp_dominant_verdict["CP1"] == "1"
    assert inst.per_cp_dominant_verdict["CP2"] == "1"
    assert inst.per_cp_agreement_rate["CP1"] == 1.0
    assert inst.per_cp_agreement_rate["CP2"] == 2 / 3
    assert inst.unstable_cp_count == 1


def test_instability_rejects_single_rerun() -> None:
    try:
        compute_instability(case_id=1, method=ExperimentMethod.CASE_FULL, reruns=[_result(1, {"CP1": "1"})])
    except ValueError as exc:
        assert "two" in str(exc)
    else:
        raise AssertionError("expected ValueError for single rerun")