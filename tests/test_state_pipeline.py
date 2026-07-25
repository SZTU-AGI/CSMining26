from pathlib import Path

from freca.models import TaskStatus
from freca.pipeline import create_audit_tasks, run_pending_tasks
from freca.state import TaskStore, atomic_write_json, build_cache_key, read_json


def test_atomic_json_write_and_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "value.json"
    atomic_write_json(path, {"message": "完整", "count": 3})
    assert read_json(path) == {"message": "完整", "count": 3}
    assert not list(path.parent.glob("*.tmp"))


def test_cache_key_changes_when_any_material_input_changes() -> None:
    first = build_cache_key({"source": "abc"}, {"model": "m1"}, {"prompt": "p1"})
    second = build_cache_key({"source": "abc"}, {"model": "m2"}, {"prompt": "p1"})
    assert first != second
    assert first == build_cache_key({"source": "abc"}, {"model": "m1"}, {"prompt": "p1"})


def test_creates_exactly_4100_case_cp_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    tasks = create_audit_tasks(store, run_id="run-1")

    assert len(tasks) == 4100
    assert len({task.task_id for task in tasks}) == 4100
    assert tasks[0].task_id == "run-1:case-001:CP1"
    assert tasks[-1].task_id == "run-1:case-100:CP41"


def test_resume_skips_completed_tasks_and_isolates_failures(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    tasks = create_audit_tasks(
        store,
        run_id="run-small",
        case_ids=[1],
        cp_ids=["CP1", "CP2", "CP3"],
    )
    store.update(tasks[0].task_id, status=TaskStatus.COMPLETED, artifact_path="done.json")
    calls: list[str] = []

    def worker(task):
        calls.append(task.task_id)
        if task.cp_id == "CP2":
            raise RuntimeError("simulated failure")
        return f"{task.cp_id}.json"

    summary = run_pending_tasks(store, worker, max_workers=2)

    assert tasks[0].task_id not in calls
    assert store.get(tasks[1].task_id).status == TaskStatus.FAILED
    assert store.get(tasks[2].task_id).status == TaskStatus.COMPLETED
    assert summary.completed == 2
    assert summary.failed == 1
    assert summary.blocked == 0
