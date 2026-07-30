"""Aggregate per-run metrics across a directory of ExecutionResult artifacts.

Usage::

    python -m scripts.aggregate_metrics \\
        --results build/experiments/runs/case1_case_full_raw \\
        --results build/experiments/runs/case1_case_full_masked \\
        --checkpoints checkingpoints_all_elements_onesheet.xlsx \\
        --anomaly-report build/parsed/anomaly_report.json \\
        --output build/experiments/summary.json

For each ``--results`` directory the script reads the ``result.json`` written
by ``run_execution``, the optional ``usage.json`` written next to it, and
prints / saves a fully-populated ``RunMetrics`` row. When both raw and masked
runs are supplied for the same (case, method), a ``MaskDeltaMetric`` is also
emitted so the report shows how much Track 3 near-answer leakage moves the
needle.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(WORKTREE_SRC))

from freca.cp import load_checkpoints
from freca.experiments.metrics import compute_mask_delta, compute_run_metrics
from freca.experiments.models import (
    ExecutionResult,
    RunCostMetric,
    RunMetrics,
    Track3Condition,
)
from freca.experiments.silver import build_silver_reference
from freca.state import atomic_write_json, read_json


def _load_artifact(path: Path) -> ExecutionResult:
    payload = read_json(path / "result.json")
    return ExecutionResult.model_validate(payload)


def _load_cost(path: Path, elapsed_seconds: float | None) -> RunCostMetric | None:
    usage_path = path / "usage.json"
    payload = read_json(usage_path) if usage_path.exists() else None
    if not payload and elapsed_seconds is None:
        return None
    input_tokens = int((payload or {}).get("input_tokens", 0))
    output_tokens = int((payload or {}).get("output_tokens", 0))
    calls = int((payload or {}).get("calls", 0))
    return RunCostMetric(
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        elapsed_seconds=float(elapsed_seconds or 0.0),
    )


def _track3_condition(path: Path) -> Track3Condition:
    name = path.name
    if "masked" in name:
        return Track3Condition.MASKED
    return Track3Condition.RAW


def _infer_elapsed_seconds(path: Path) -> float | None:
    summary = path / "pilot-summary.json"
    if not summary.exists():
        return None
    payload = read_json(summary)
    try:
        return float(payload.get("elapsed_seconds", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _summarise(metrics: RunMetrics) -> dict[str, object]:
    cost = metrics.cost
    return {
        "case_id": metrics.case_id,
        "method": metrics.method,
        "track3_condition": metrics.track3_condition,
        "verdicts_total": metrics.verdicts_total,
        "verdicts_valid": metrics.verdicts_valid,
        "valid_rate": round(metrics.valid_rate, 4),
        "anchored_total": metrics.anchored_total,
        "anchored_correct": metrics.anchored_correct,
        "overall_accuracy": round(metrics.overall_accuracy, 4),
        "per_element_accuracy": [m.model_dump(mode="json") for m in metrics.per_element],
        "na": metrics.na_classification.model_dump(mode="json") if metrics.na_classification else None,
        "citations": metrics.citations.model_dump(mode="json") if metrics.citations else None,
        "cost": cost.model_dump(mode="json") if cost else None,
    }


def _collect_deltas(rows: list[tuple[Path, RunMetrics]]) -> list[dict[str, object]]:
    by_key: dict[tuple[int, str], dict[str, RunMetrics]] = {}
    for _path, metrics in rows:
        key = (metrics.case_id, str(metrics.method))
        bucket = by_key.setdefault(key, {})
        bucket[metrics.track3_condition] = metrics
    deltas: list[dict[str, object]] = []
    for (case_id, method), bucket in by_key.items():
        raw = bucket.get(Track3Condition.RAW.value)
        masked = bucket.get(Track3Condition.MASKED.value)
        if raw is None or masked is None:
            continue
        delta = compute_mask_delta(case_id=case_id, method=raw.method, raw=raw, masked=masked)
        deltas.append(delta.model_dump(mode="json"))
    return deltas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", action="append", type=Path, required=True, help="directory containing result.json (repeatable)")
    parser.add_argument("--checkpoints", type=Path, default=Path("checkingpoints_all_elements_onesheet.xlsx"))
    parser.add_argument("--anomaly-report", type=Path, default=None)
    parser.add_argument("--human-labels", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("build/experiments/summary.json"))
    args = parser.parse_args()

    checkpoints = load_checkpoints(args.checkpoints)
    silver = build_silver_reference(
        anomaly_report_path=args.anomaly_report,
        human_labels_path=args.human_labels,
        checkpoints=checkpoints,
    )

    rows: list[tuple[Path, RunMetrics]] = []
    summaries: list[dict[str, object]] = []
    for path in args.results:
        result = _load_artifact(path)
        cost = _load_cost(path, _infer_elapsed_seconds(path))
        metrics = compute_run_metrics(
            result=result,
            checkpoints=checkpoints,
            silver=silver,
            cost=cost,
            track3_condition=_track3_condition(path),
        )
        rows.append((path, metrics))
        summaries.append(_summarise(metrics))

    payload = {
        "rows": summaries,
        "mask_deltas": _collect_deltas(rows),
    }
    atomic_write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())