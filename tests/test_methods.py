from pathlib import Path

from freca.methods import MethodRunLayout, compare_method_runs, gold_tasks
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


def test_comparison_excludes_low_coverage_run_from_winner(tmp_path: Path) -> None:
    atomic_write_json(
        tmp_path / "evaluation" / "high-score-low-coverage.json",
        {"run_id": "high-score-low-coverage", "gold_count": 34, "evaluated_count": 10, "matched_count": 10, "agreement_rate": 1.0},
    )
    atomic_write_json(
        tmp_path / "evaluation" / "eligible.json",
        {"run_id": "eligible", "gold_count": 34, "evaluated_count": 32, "matched_count": 24, "agreement_rate": 0.75},
    )
    atomic_write_json(
        tmp_path / "method-runs" / "high-score-low-coverage" / "state" / "tasks.json",
        [],
    )
    atomic_write_json(
        tmp_path / "method-runs" / "eligible" / "state" / "tasks.json",
        [],
    )

    report = compare_method_runs(tmp_path, ["high-score-low-coverage", "eligible"])

    assert report["winner"]["run_id"] == "eligible"


def test_comparison_can_write_a_named_snapshot(tmp_path: Path) -> None:
    atomic_write_json(
        tmp_path / "evaluation" / "eligible.json",
        {"run_id": "eligible", "gold_count": 34, "evaluated_count": 34, "matched_count": 25, "agreement_rate": 25 / 34},
    )
    atomic_write_json(tmp_path / "method-runs" / "eligible" / "state" / "tasks.json", [])

    output = tmp_path / "method-comparison" / "gold-v2.json"
    compare_method_runs(tmp_path, ["eligible"], output_path=output)

    assert output.exists()
    assert not (tmp_path / "method-comparison" / "gold-v1.json").exists()
