"""Artifact layout and persistence for the ledger architecture.

Everything the ledger stack writes lives under ``<build_dir>/ledger/`` so the
legacy pipeline's artifacts (``build/decisions``, ``build/final``, ...) are
never touched. Both architectures can be run against the same ``build/``
directory and compared side by side.

Layout::

    build/ledger/
      facts/001.json                 CaseFactLedger
      facts/001.trace.json           per-batch extraction trace
      rubrics/CP9.json               CheckpointRubric (cached by input_hash)
      rubrics/CP9.retrieval.json     the retrieval context that produced it
      packs/001/CP9.json             EvidencePack handed to the adjudicator
      outcomes/001/CP9.json          TaskOutcome (primary + gate + review)
      final/001/CP9.json             the final LedgerDecision only
      state/<run>-tasks.json         durable task store (reuses TaskStore)
      runs/<run>.json                workflow report
      baseline/<run>.json            §8 artifact classification
      cache/models/<stage>/          LLM response cache
      logs/model-calls.jsonl         full request/response ledger
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from freca.state import TaskStore, atomic_write_json, read_json

from freca.ledger.models import (
    CaseFactLedger,
    CheckpointRubric,
    EvidencePack,
    LedgerDecision,
    TaskOutcome,
)


def case_key(case_id: int) -> str:
    return f"{case_id:03d}"


class LedgerStore:
    """Filesystem-backed artifact store for one build directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- directories ------------------------------------------------------

    @property
    def facts_dir(self) -> Path:
        return self.root / "facts"

    @property
    def rubrics_dir(self) -> Path:
        return self.root / "rubrics"

    @property
    def packs_dir(self) -> Path:
        return self.root / "packs"

    @property
    def outcomes_dir(self) -> Path:
        return self.root / "outcomes"

    @property
    def final_dir(self) -> Path:
        return self.root / "final"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def baseline_dir(self) -> Path:
        return self.root / "baseline"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache" / "models"

    @property
    def ledger_log_path(self) -> Path:
        return self.root / "logs" / "model-calls.jsonl"

    # -- paths ------------------------------------------------------------

    def ledger_path(self, case_id: int) -> Path:
        return self.facts_dir / f"{case_key(case_id)}.json"

    def ledger_trace_path(self, case_id: int) -> Path:
        return self.facts_dir / f"{case_key(case_id)}.trace.json"

    def rubric_path(self, cp_id: str) -> Path:
        return self.rubrics_dir / f"{cp_id}.json"

    def rubric_retrieval_path(self, cp_id: str) -> Path:
        return self.rubrics_dir / f"{cp_id}.retrieval.json"

    def pack_path(self, case_id: int, cp_id: str) -> Path:
        return self.packs_dir / case_key(case_id) / f"{cp_id}.json"

    def outcome_path(self, case_id: int, cp_id: str) -> Path:
        return self.outcomes_dir / case_key(case_id) / f"{cp_id}.json"

    def final_path(self, case_id: int, cp_id: str) -> Path:
        return self.final_dir / case_key(case_id) / f"{cp_id}.json"

    def run_report_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def baseline_path(self, run_id: str) -> Path:
        return self.baseline_dir / f"{run_id}.json"

    def task_store(self, run_id: str) -> TaskStore:
        return TaskStore(self.state_dir / f"{run_id}-tasks.json")

    # -- fact ledgers -----------------------------------------------------

    def write_ledger(self, ledger: CaseFactLedger) -> Path:
        path = self.ledger_path(ledger.case_id)
        atomic_write_json(path, ledger.model_dump(mode="json"))
        return path

    def read_ledger(self, case_id: int) -> CaseFactLedger:
        return CaseFactLedger.model_validate(read_json(self.ledger_path(case_id)))

    def has_ledger(self, case_id: int) -> bool:
        return self.ledger_path(case_id).exists()

    def write_ledger_trace(self, case_id: int, payload: Any) -> Path:
        path = self.ledger_trace_path(case_id)
        atomic_write_json(path, payload)
        return path

    def iter_ledgers(self) -> Iterator[CaseFactLedger]:
        if not self.facts_dir.exists():
            return
        for path in sorted(self.facts_dir.glob("*.json")):
            if path.name.endswith(".trace.json"):
                continue
            yield CaseFactLedger.model_validate(read_json(path))

    # -- rubrics ----------------------------------------------------------

    def write_rubric(self, rubric: CheckpointRubric) -> Path:
        path = self.rubric_path(rubric.cp_id)
        atomic_write_json(path, rubric.model_dump(mode="json"))
        return path

    def read_rubric(self, cp_id: str) -> CheckpointRubric:
        return CheckpointRubric.model_validate(read_json(self.rubric_path(cp_id)))

    def has_rubric(self, cp_id: str) -> bool:
        return self.rubric_path(cp_id).exists()

    def load_cached_rubric(
        self,
        cp_id: str,
        *,
        input_hash: str,
    ) -> CheckpointRubric | None:
        """Return the cached rubric only when its inputs are unchanged."""

        path = self.rubric_path(cp_id)
        if not path.exists():
            return None
        try:
            rubric = CheckpointRubric.model_validate(read_json(path))
        except Exception:
            return None
        if rubric.input_hash != input_hash:
            return None
        return rubric

    def write_rubric_retrieval(self, cp_id: str, payload: Any) -> Path:
        path = self.rubric_retrieval_path(cp_id)
        atomic_write_json(path, payload)
        return path

    # -- packs / outcomes -------------------------------------------------

    def write_pack(self, pack: EvidencePack) -> Path:
        path = self.pack_path(pack.case_id, pack.cp_id)
        atomic_write_json(path, pack.model_dump(mode="json"))
        return path

    def read_pack(self, case_id: int, cp_id: str) -> EvidencePack:
        return EvidencePack.model_validate(read_json(self.pack_path(case_id, cp_id)))

    def write_outcome(self, outcome: TaskOutcome) -> Path:
        path = self.outcome_path(outcome.case_id, outcome.cp_id)
        atomic_write_json(path, outcome.model_dump(mode="json"))
        final_path = self.final_path(outcome.case_id, outcome.cp_id)
        atomic_write_json(final_path, outcome.final.model_dump(mode="json"))
        return path

    def read_outcome(self, case_id: int, cp_id: str) -> TaskOutcome:
        return TaskOutcome.model_validate(read_json(self.outcome_path(case_id, cp_id)))

    def read_final(self, case_id: int, cp_id: str) -> LedgerDecision:
        return LedgerDecision.model_validate(read_json(self.final_path(case_id, cp_id)))

    def iter_finals(self) -> Iterator[LedgerDecision]:
        if not self.final_dir.exists():
            return
        for path in sorted(self.final_dir.glob("*/CP*.json")):
            yield LedgerDecision.model_validate(read_json(path))

    def iter_outcomes(self) -> Iterator[TaskOutcome]:
        if not self.outcomes_dir.exists():
            return
        for path in sorted(self.outcomes_dir.glob("*/CP*.json")):
            yield TaskOutcome.model_validate(read_json(path))

    # -- reports ----------------------------------------------------------

    def write_run_report(self, run_id: str, payload: dict[str, Any]) -> Path:
        path = self.run_report_path(run_id)
        atomic_write_json(path, payload)
        return path

    def write_baseline(self, run_id: str, payload: dict[str, Any]) -> Path:
        path = self.baseline_path(run_id)
        atomic_write_json(path, payload)
        return path


__all__ = ["LedgerStore", "case_key"]
