from pathlib import Path

from freca.methods import MethodRunLayout, gold_tasks
from freca.state import atomic_write_json


def test_gold_tasks_preserve_only_confirmed_case_cp_pairs(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    atomic_write_json(
        gold_path,
        {
            "version": "test-v1",
            "labels": [
                {
                    "case_id": 23,
                    "cp_id": "CP1",
                    "verdict": "0",
                    "confirmed": True,
                    "note": "confirmed",
                },
                {
                    "case_id": 23,
                    "cp_id": "CP17",
                    "verdict": "1",
                    "confirmed": False,
                    "note": "excluded",
                },
            ],
        },
    )

    tasks = gold_tasks(gold_path)

    assert {(task.case_id, task.cp_id) for task in tasks} == {(23, "CP1")}
    assert tasks[0].expected.value == "0"


def test_method_layout_never_uses_shared_final_directory(tmp_path: Path) -> None:
    layout = MethodRunLayout(tmp_path, "bm25-gold-v1")

    assert layout.final_path(23, "CP1") == (
        tmp_path / "method-runs" / "bm25-gold-v1" / "final" / "023" / "CP1.json"
    )
