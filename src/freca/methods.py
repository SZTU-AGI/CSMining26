from __future__ import annotations

from pathlib import Path

from freca.evaluation import _validate_run_id, load_gold_labels
from freca.models import StrictModel, Verdict
from freca.state import atomic_write_json, read_json


class GoldTask(StrictModel):
    case_id: int
    cp_id: str
    expected: Verdict


class MethodRunLayout:
    def __init__(self, build_dir: Path, run_id: str) -> None:
        if not run_id or any(part in {"", ".", ".."} for part in Path(run_id).parts):
            raise ValueError("run_id must be a non-empty relative identifier")
        self.root = build_dir / "method-runs" / run_id

    @property
    def final_dir(self) -> Path:
        return self.root / "final"

    def final_path(self, case_id: int, cp_id: str) -> Path:
        return self.final_dir / f"{case_id:03d}" / f"{cp_id}.json"


def gold_tasks(gold_path: Path) -> tuple[GoldTask, ...]:
    return tuple(
        GoldTask(case_id=case_id, cp_id=cp_id, expected=label.verdict)
        for (case_id, cp_id), label in sorted(load_gold_labels(gold_path).items())
    )


def _method_tasks_path(build_dir: Path, run_id: str) -> Path:
    standard = build_dir / "method-runs" / run_id / "state" / "tasks.json"
    if standard.exists():
        return standard
    return build_dir / "method-runs" / run_id / "ledger" / "state" / f"{run_id}-tasks.json"


def compare_method_runs(build_dir: Path, run_ids: list[str]) -> dict:
    rows = []
    for run_id in dict.fromkeys(run_ids):
        _validate_run_id(run_id)
        report = read_json(build_dir / "evaluation" / f"{run_id}.json")
        tasks_path = _method_tasks_path(build_dir, run_id)
        tasks = read_json(tasks_path) if tasks_path.exists() else []
        terminal_failures = sum(task.get("status") in {"BLOCKED", "FAILED"} for task in tasks)
        gold_count = int(report["gold_count"])
        coverage = int(report["evaluated_count"]) / gold_count if gold_count else 0.0
        failure_rate = terminal_failures / gold_count if gold_count else 0.0
        rows.append(
            {
                "run_id": run_id,
                "agreement_rate": report["agreement_rate"],
                "coverage": coverage,
                "terminal_failure_rate": failure_rate,
                "eligible": coverage >= 0.9 and failure_rate <= 0.1,
            }
        )
    ranked = sorted(
        rows,
        key=lambda row: (row["eligible"], row["agreement_rate"] is not None, row["agreement_rate"] or -1),
        reverse=True,
    )
    winner = next((row for row in ranked if row["eligible"]), None)
    payload = {"runs": ranked, "winner": winner}
    atomic_write_json(build_dir / "method-comparison" / "gold-v1.json", payload)
    return payload
