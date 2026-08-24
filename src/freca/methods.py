from __future__ import annotations

from pathlib import Path

from freca.evaluation import load_gold_labels
from freca.models import StrictModel, Verdict


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
