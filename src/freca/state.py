from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from freca.models import AuditTask, TaskStatus


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"cannot JSON-encode {type(value).__name__}")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=_json_default,
    )
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_cache_key(*parts: Any) -> str:
    canonical = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def all(self) -> list[AuditTask]:
        with self._lock:
            if not self.path.exists():
                return []
            return [AuditTask.model_validate(item) for item in read_json(self.path)]

    def initialize(self, tasks: list[AuditTask]) -> list[AuditTask]:
        with self._lock:
            existing = self.all()
            if existing:
                return existing
            self._save(tasks)
            return tasks

    def _save(self, tasks: list[AuditTask]) -> None:
        atomic_write_json(
            self.path,
            [task.model_dump(mode="json") for task in sorted(tasks, key=lambda item: item.task_id)],
        )

    def get(self, task_id: str) -> AuditTask:
        for task in self.all():
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def update(self, task_id: str, **updates: Any) -> AuditTask:
        with self._lock:
            tasks = self.all()
            for index, task in enumerate(tasks):
                if task.task_id == task_id:
                    updated = task.model_copy(update=updates)
                    tasks[index] = AuditTask.model_validate(updated.model_dump())
                    self._save(tasks)
                    return tasks[index]
        raise KeyError(task_id)

    def pending(self) -> list[AuditTask]:
        return [task for task in self.all() if task.status == TaskStatus.PENDING]

    def reset(
        self,
        *,
        statuses: set[TaskStatus],
        case_ids: set[int] | None = None,
        cp_ids: set[str] | None = None,
    ) -> int:
        with self._lock:
            tasks = self.all()
            reset_count = 0
            for index, task in enumerate(tasks):
                if task.status not in statuses:
                    continue
                if case_ids is not None and task.case_id not in case_ids:
                    continue
                if cp_ids is not None and task.cp_id not in cp_ids:
                    continue
                tasks[index] = task.model_copy(
                    update={
                        "status": TaskStatus.PENDING,
                        "error": None,
                        "artifact_path": None,
                    }
                )
                reset_count += 1
            if reset_count:
                self._save(tasks)
            return reset_count
