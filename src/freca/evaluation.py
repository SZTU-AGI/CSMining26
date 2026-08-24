from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from freca.models import AuditDecision, Verdict
from freca.state import atomic_write_json, read_json


class GoldLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    verdict: Verdict
    confirmed: bool
    note: str


def _read_gold_payload(path: Path) -> dict:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("labels"), list):
        raise ValueError("gold label file must contain a labels list")
    if not isinstance(payload.get("version"), str) or not payload["version"]:
        raise ValueError("gold label file must contain a version")
    return payload


def load_gold_labels(path: Path) -> dict[tuple[int, str], GoldLabel]:
    payload = _read_gold_payload(path)

    labels: dict[tuple[int, str], GoldLabel] = {}
    for raw in payload["labels"]:
        label = GoldLabel.model_validate(raw)
        if not label.confirmed:
            continue
        key = (label.case_id, label.cp_id)
        if key in labels:
            raise ValueError(
                f"duplicate confirmed gold label: {label.case_id}/{label.cp_id}"
            )
        labels[key] = label
    return labels


def _decision_path(final_root: Path, case_id: int, cp_id: str) -> Path:
    return final_root / f"{case_id:03d}" / f"{cp_id}.json"


def _task_key(case_id: int, cp_id: str) -> str:
    return f"{case_id:03d}/{cp_id}"


def _validate_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
        raise ValueError("run_id must be a safe 1-80 character identifier")


def evaluate_run(
    build_dir: Path,
    *,
    run_id: str,
    gold_path: Path,
    final_root: Path | None = None,
) -> dict:
    _validate_run_id(run_id)
    payload = _read_gold_payload(gold_path)
    labels = load_gold_labels(gold_path)
    matched_count = 0
    evaluated_count = 0
    missing_tasks: list[str] = []
    mismatches: list[dict] = []
    per_cp: dict[str, dict[str, int | float | None]] = {}

    decision_root = final_root or build_dir / "final"
    for (case_id, cp_id), label in sorted(labels.items()):
        summary = per_cp.setdefault(
            cp_id,
            {"gold_count": 0, "evaluated_count": 0, "matched_count": 0, "agreement_rate": None},
        )
        summary["gold_count"] += 1
        path = _decision_path(decision_root, case_id, cp_id)
        if not path.exists():
            missing_tasks.append(_task_key(case_id, cp_id))
            continue

        decision = AuditDecision.model_validate(read_json(path))
        evaluated_count += 1
        summary["evaluated_count"] += 1
        if decision.verdict == label.verdict:
            matched_count += 1
            summary["matched_count"] += 1
            continue
        mismatches.append(
            {
                "task": _task_key(case_id, cp_id),
                "gold_verdict": label.verdict.value,
                "actual_verdict": decision.verdict.value,
                "reasoning_summary": decision.reasoning_summary,
                "supporting_evidence": decision.supporting_evidence,
                "contrary_evidence": decision.contrary_evidence,
            }
        )

    for summary in per_cp.values():
        count = summary["evaluated_count"]
        summary["agreement_rate"] = summary["matched_count"] / count if count else None

    report = {
        "run_id": run_id,
        "gold_version": payload["version"],
        "gold_count": len(labels),
        "evaluated_count": evaluated_count,
        "matched_count": matched_count,
        "agreement_rate": matched_count / evaluated_count if evaluated_count else None,
        "missing_tasks": missing_tasks,
        "mismatches": mismatches,
        "per_cp": dict(sorted(per_cp.items())),
    }
    atomic_write_json(build_dir / "evaluation" / f"{run_id}.json", report)
    return report


def compare_reports(build_dir: Path, run_ids: list[str]) -> dict:
    rows = []
    for run_id in dict.fromkeys(run_ids):
        _validate_run_id(run_id)
        path = build_dir / "evaluation" / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        report = read_json(path)
        rows.append(
            {
                "run_id": run_id,
                "agreement_rate": report["agreement_rate"],
                "evaluated_count": report["evaluated_count"],
                "matched_count": report["matched_count"],
            }
        )
    return {
        "runs": sorted(
            rows,
            key=lambda row: (
                row["agreement_rate"] is not None,
                row["agreement_rate"] if row["agreement_rate"] is not None else -1,
            ),
            reverse=True,
        )
    }
