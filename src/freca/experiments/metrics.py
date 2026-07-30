"""Comprehensive metrics for direct-LLM audit experiments.

A single ``compute_run_metrics`` call turns one ``ExecutionResult`` (+ optional
silver reference + cost telemetry) into a ``RunMetrics`` covering everything
the experiment plan needs to report per-run:

- overall / per-CP / per-Element accuracy against silver anchors
- N/A Precision/Recall/F1 on the silver set
- citation validity (after the runner's prefix-repair pass)
- cost / latency aggregated from ``usage.json`` and elapsed seconds

The cross-run comparisons (mask delta, instability across reruns) live in
``compute_mask_delta`` and ``compute_instability`` so the per-run path stays
single-result.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from freca.experiments.models import (
    CitationValidityMetric,
    ExecutionResult,
    ExperimentMethod,
    NAClassificationMetric,
    PerCheckpointMetric,
    PerElementMetric,
    RunCostMetric,
    RunMetrics,
    InstabilityMetric,
    MaskDeltaMetric,
    SilverReference,
    SilverTier,
    Track3Condition,
)
from freca.models import CheckpointDefinition, Verdict


_ANCHORED_TIERS: tuple[SilverTier, ...] = (SilverTier.ANOMALY_RULE, SilverTier.HUMAN)


def compute_run_metrics(
    *,
    result: ExecutionResult,
    checkpoints: Sequence[CheckpointDefinition],
    silver: SilverReference | None = None,
    cost: RunCostMetric | None = None,
    track3_condition: Track3Condition | str = Track3Condition.RAW,
) -> RunMetrics:
    """Compute every single-run metric for one ``ExecutionResult``.

    ``silver`` is optional; when omitted, per-CP / per-Element / N/A metrics
    are empty / null and only structural counts (``verdicts_total``,
    ``verdicts_valid``, ``valid_rate``) plus citation validity are populated.
    ``cost`` is optional and typically read from ``usage.json`` + ``pilot-summary``.
    """
    cp_index = {checkpoint.cp_id: checkpoint for checkpoint in checkpoints}
    expected_ids = set(cp_index)
    candidate_by_cp = {verdict.cp_id: verdict for verdict in result.verdicts}

    verdicts_total = len(expected_ids)
    verdicts_valid = sum(1 for cp_id in expected_ids if cp_id in candidate_by_cp)

    citation_metric = _compute_citation_validity(result)
    anchored_total = 0
    anchored_correct = 0
    per_checkpoint: list[PerCheckpointMetric] = []
    per_element_acc: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    na_metric = NAClassificationMetric(
        predicted_na=0,
        predicted_non_na=0,
        silver_na=0,
        silver_non_na=0,
        true_positives=0,
        false_positives=0,
        false_negatives=0,
        precision=0.0,
        recall=0.0,
        f1=0.0,
    )

    if silver is not None:
        case_entries = silver.entries.get(str(result.unit.case_id), {})
        for cp_id in sorted(expected_ids, key=lambda cid: int(cid[2:])):
            checkpoint = cp_index[cp_id]
            entry = case_entries.get(cp_id)
            candidate_verdict = candidate_by_cp.get(cp_id)
            candidate_counts = Counter([verdict.verdict.value for verdict in result.verdicts if verdict.cp_id == cp_id])
            if entry is None or entry.tier not in _ANCHORED_TIERS:
                continue
            anchored_total += 1
            silver_counts = Counter([entry.verdict.value])
            correct = candidate_verdict is not None and candidate_verdict.verdict == entry.verdict
            if correct:
                anchored_correct += 1
            per_checkpoint.append(
                PerCheckpointMetric(
                    cp_id=cp_id,
                    element_id=checkpoint.element_id,
                    anchored_total=1,
                    anchored_correct=1 if correct else 0,
                    accuracy=1.0 if correct else 0.0,
                    candidate_verdict_counts=dict(candidate_counts),
                    silver_verdict_counts=dict(silver_counts),
                )
            )
            per_element_acc[checkpoint.element_id].append(1.0 if correct else 0.0)

            if entry.verdict == Verdict.NOT_APPLICABLE:
                na_metric = na_metric.model_copy(update={"silver_na": na_metric.silver_na + 1})
            else:
                na_metric = na_metric.model_copy(update={"silver_non_na": na_metric.silver_non_na + 1})
            if candidate_verdict is not None:
                if candidate_verdict.verdict == Verdict.NOT_APPLICABLE:
                    na_metric = na_metric.model_copy(update={"predicted_na": na_metric.predicted_na + 1})
                else:
                    na_metric = na_metric.model_copy(update={"predicted_non_na": na_metric.predicted_non_na + 1})
                if candidate_verdict.verdict == entry.verdict == Verdict.NOT_APPLICABLE:
                    na_metric = na_metric.model_copy(update={"true_positives": na_metric.true_positives + 1})
                elif candidate_verdict.verdict == Verdict.NOT_APPLICABLE and entry.verdict != Verdict.NOT_APPLICABLE:
                    na_metric = na_metric.model_copy(update={"false_positives": na_metric.false_positives + 1})
                elif candidate_verdict.verdict != Verdict.NOT_APPLICABLE and entry.verdict == Verdict.NOT_APPLICABLE:
                    na_metric = na_metric.model_copy(update={"false_negatives": na_metric.false_negatives + 1})

        na_metric = _finalize_na_metric(na_metric)

    per_element = tuple(
        PerElementMetric(
            element_id=element_id,
            anchored_total=len(per_element_acc[element_id]),
            anchored_correct=int(sum(per_element_acc[element_id])),
            accuracy=(sum(per_element_acc[element_id]) / len(per_element_acc[element_id])) if per_element_acc[element_id] else 0.0,
        )
        for element_id in sorted(per_element_acc)
        if per_element_acc[element_id]
    )

    overall_accuracy = (anchored_correct / anchored_total) if anchored_total else 0.0
    valid_rate = (verdicts_valid / verdicts_total) if verdicts_total else 0.0

    return RunMetrics(
        case_id=result.unit.case_id,
        method=result.unit.method,
        track3_condition=str(track3_condition),
        verdicts_total=verdicts_total,
        verdicts_valid=verdicts_valid,
        valid_rate=valid_rate,
        anchored_total=anchored_total,
        anchored_correct=anchored_correct,
        overall_accuracy=overall_accuracy,
        per_checkpoint=tuple(per_checkpoint),
        per_element=per_element,
        na_classification=na_metric if silver is not None else None,
        citations=citation_metric,
        cost=cost,
    )


def compute_mask_delta(
    *,
    case_id: int,
    method: ExperimentMethod,
    raw: RunMetrics,
    masked: RunMetrics,
    metric: str = "overall_accuracy",
) -> MaskDeltaMetric:
    """Pairwise delta of one numeric metric between raw and masked runs.

    ``metric`` must be a numeric attribute of ``RunMetrics``; common choices
    are ``overall_accuracy``, ``valid_rate``, and ``citations.validity_rate``.
    """
    raw_value = float(getattr(raw, metric))
    masked_value = float(getattr(masked, metric))
    return MaskDeltaMetric(
        case_id=case_id,
        method=method,
        metric=metric,
        raw_value=raw_value,
        masked_value=masked_value,
        delta=masked_value - raw_value,
    )


def compute_instability(
    *,
    case_id: int,
    method: ExperimentMethod,
    reruns: Sequence[ExecutionResult],
) -> InstabilityMetric:
    """Measure per-CP agreement across N re-runs of the same (case, method).

    Each CP's "dominant verdict" is the majority prediction; agreement_rate is
    its fraction. CPs with agreement_rate < 1.0 are reported as unstable. The
    overall agreement is the mean across CPs that appear in every rerun.
    """
    if len(reruns) < 2:
        raise ValueError("instability requires at least two reruns")

    cp_union: set[str] = set()
    for result in reruns:
        cp_union.update(verdict.cp_id for verdict in result.verdicts)
    ordered_cps = sorted(cp_union, key=lambda cp_id: int(cp_id[2:]))

    verdict_lists: list[dict[str, str]] = [
        {verdict.cp_id: verdict.verdict.value for verdict in result.verdicts}
        for result in reruns
    ]

    dominant: dict[str, str] = {}
    rates: dict[str, float] = {}
    agreement_values: list[float] = []
    for cp_id in ordered_cps:
        per_run_values = [values.get(cp_id) for values in verdict_lists]
        present = [value for value in per_run_values if value is not None]
        if not present:
            continue
        counts = Counter(present)
        top, top_count = counts.most_common(1)[0]
        rate = top_count / len(reruns)
        dominant[cp_id] = top
        rates[cp_id] = rate
        agreement_values.append(rate)

    overall = (sum(agreement_values) / len(agreement_values)) if agreement_values else 0.0
    unstable = sum(1 for rate in rates.values() if rate < 1.0)

    return InstabilityMetric(
        case_id=case_id,
        method=method,
        reruns=len(reruns),
        per_cp_dominant_verdict=dominant,
        per_cp_agreement_rate=rates,
        unstable_cp_count=unstable,
        overall_agreement=overall,
    )


def _compute_citation_validity(result: ExecutionResult) -> CitationValidityMetric:
    """A citation is valid iff the prefix-repair pass produced no ``unknown``.

    The runner persists ``unknown citation_ids: ...`` in ``result.errors`` when
    an id couldn't be repaired; we count verdict-level invalidity by checking
    whether each verdict's citations appear in the unrepaired-error list.
    Since the runner has already merged repairs into ``verdict.citation_ids``
    before persisting, an unrepairable citation would still be in the verdict's
    list. We instead rely on the error string: every ``unknown citation_ids``
    error increments ``verdicts_with_invalid`` for the verdict(s) involved.
    """
    total_citations = sum(len(verdict.citation_ids) for verdict in result.verdicts)
    invalid_count = 0
    verdicts_with_invalid = 0
    invalid_ids: set[str] = set()
    for error in result.errors:
        if error.startswith("unknown citation_ids:"):
            tail = error.split(":", 1)[1]
            for raw_id in tail.split(","):
                token = raw_id.strip()
                if token:
                    invalid_ids.add(token)
    if invalid_ids:
        # Attribute unknown ids to verdicts that cite them. We don't have the
        # pre-repair list, but repaired verdicts still contain the (unmatched)
        # id verbatim when repair fails — so this attribution is exact.
        for verdict in result.verdicts:
            overlap = invalid_ids.intersection(verdict.citation_ids)
            if overlap:
                invalid_count += len(overlap)
                verdicts_with_invalid += 1
    valid_count = max(0, total_citations - invalid_count)
    validity_rate = (valid_count / total_citations) if total_citations else 0.0
    return CitationValidityMetric(
        total_citations=total_citations,
        valid_citations=valid_count,
        invalid_citations=invalid_count,
        validity_rate=validity_rate,
        verdicts_with_invalid=verdicts_with_invalid,
        verdicts_total=len(result.verdicts),
    )


def _finalize_na_metric(metric: NAClassificationMetric) -> NAClassificationMetric:
    """Compute precision/recall/f1 from the running counter fields."""
    precision = metric.true_positives / (metric.true_positives + metric.false_positives) if (metric.true_positives + metric.false_positives) else 0.0
    recall = metric.true_positives / (metric.true_positives + metric.false_negatives) if (metric.true_positives + metric.false_negatives) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return metric.model_copy(update={"precision": precision, "recall": recall, "f1": f1})