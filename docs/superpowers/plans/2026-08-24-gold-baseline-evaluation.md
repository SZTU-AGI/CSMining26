# Gold Baseline Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze confirmed team consensus as versioned Gold labels and compare every finished Task2 run against it without changing a run's verdicts.

**Architecture:** `Task2-agent-pipeline` remains the only executable baseline. A compact `freca.evaluation` module reads versioned Gold labels and `build/final/{case}/{cp}.json`, persists one report per run ID, and ranks saved reports. The independent `Task2` branch stays a documented source of selective ideas, not a merge target.

**Tech Stack:** Python 3.11, Pydantic, JSON artifacts, pytest, argparse.

---

## File structure

- Create: `gold/consensus-v1.json` — 34 confirmed consensus labels.
- Create: `src/freca/evaluation.py` — Gold loading, run evaluation, saved-report comparison.
- Create: `tests/test_evaluation.py` — Gold, mismatch, missing-output and ranking tests.
- Modify: `src/freca/cli.py` — `evaluation run` and `evaluation compare` commands.
- Modify: `tests/test_cli.py` — CLI grammar test.
- Create: `docs/BRANCH_SYNC.md` — independent-history and selective-import decision.
- Modify: `README.md` — operator workflow.

### Task 1: Freeze the confirmed Gold labels

**Files:**

- Create: `gold/consensus-v1.json`
- Create: `tests/test_evaluation.py`

- [ ] **Step 1: Write the failing Gold-loader test**

```python
from pathlib import Path

from freca.evaluation import load_gold_labels


def test_load_gold_labels_includes_only_confirmed_verdicts() -> None:
    labels = load_gold_labels(Path("gold/consensus-v1.json"))

    assert len(labels) == 34
    assert labels[(23, "CP1")].verdict == "0"
    assert labels[(65, "CP12")].verdict == "1"
    assert (23, "CP24") not in labels
    assert (35, "CP35") not in labels
    assert (23, "CP17") not in labels
    assert (23, "CP19") not in labels
```

- [ ] **Step 2: Verify RED**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py::test_load_gold_labels_includes_only_confirmed_verdicts -q`

Expected: collection fails because `freca.evaluation` does not yet exist.

- [ ] **Step 3: Add `gold/consensus-v1.json`**

Use this exact payload. CP17/CP19, CP24, CP26, and CP35/35 are deliberately absent and therefore excluded from the denominator.

```json
{
  "version": "consensus-v1",
  "source": "CP结果汇总(1).xlsx",
  "labels": [
    {"case_id":23,"cp_id":"CP1","verdict":"0","confirmed":true,"note":"official record prevails"},
    {"case_id":35,"cp_id":"CP1","verdict":"0","confirmed":true,"note":"official record prevails"},
    {"case_id":38,"cp_id":"CP1","verdict":"0","confirmed":true,"note":"official record prevails"},
    {"case_id":65,"cp_id":"CP1","verdict":"0","confirmed":true,"note":"official record prevails"},
    {"case_id":74,"cp_id":"CP1","verdict":"0","confirmed":true,"note":"official record prevails"},
    {"case_id":23,"cp_id":"CP12","verdict":"1","confirmed":true,"note":"site-level design evidence"},
    {"case_id":35,"cp_id":"CP12","verdict":"1","confirmed":true,"note":"site-level design evidence"},
    {"case_id":38,"cp_id":"CP12","verdict":"0","confirmed":true,"note":"site-level design evidence"},
    {"case_id":65,"cp_id":"CP12","verdict":"1","confirmed":true,"note":"site-level design evidence"},
    {"case_id":74,"cp_id":"CP12","verdict":"1","confirmed":true,"note":"site-level design evidence"},
    {"case_id":23,"cp_id":"CP14","verdict":"0","confirmed":true,"note":"atomic evidence independence"},
    {"case_id":35,"cp_id":"CP14","verdict":"0","confirmed":true,"note":"atomic evidence independence"},
    {"case_id":38,"cp_id":"CP14","verdict":"0","confirmed":true,"note":"atomic evidence independence"},
    {"case_id":65,"cp_id":"CP14","verdict":"1","confirmed":true,"note":"atomic evidence independence"},
    {"case_id":74,"cp_id":"CP14","verdict":"0","confirmed":true,"note":"atomic evidence independence"},
    {"case_id":23,"cp_id":"CP15","verdict":"0","confirmed":true,"note":"applicability then subject-consistent evidence"},
    {"case_id":35,"cp_id":"CP15","verdict":"0","confirmed":true,"note":"applicability then subject-consistent evidence"},
    {"case_id":38,"cp_id":"CP15","verdict":"0","confirmed":true,"note":"applicability then subject-consistent evidence"},
    {"case_id":65,"cp_id":"CP15","verdict":"1","confirmed":true,"note":"applicability then subject-consistent evidence"},
    {"case_id":74,"cp_id":"CP15","verdict":"0","confirmed":true,"note":"applicability then subject-consistent evidence"},
    {"case_id":23,"cp_id":"CP30","verdict":"0","confirmed":true,"note":"procedure is not execution"},
    {"case_id":35,"cp_id":"CP30","verdict":"0","confirmed":true,"note":"procedure is not execution"},
    {"case_id":38,"cp_id":"CP30","verdict":"0","confirmed":true,"note":"procedure is not execution"},
    {"case_id":65,"cp_id":"CP30","verdict":"0","confirmed":true,"note":"procedure is not execution"},
    {"case_id":74,"cp_id":"CP30","verdict":"0","confirmed":true,"note":"procedure is not execution"},
    {"case_id":23,"cp_id":"CP33","verdict":"0","confirmed":true,"note":"goods and process scope must match"},
    {"case_id":35,"cp_id":"CP33","verdict":"0","confirmed":true,"note":"goods and process scope must match"},
    {"case_id":38,"cp_id":"CP33","verdict":"0","confirmed":true,"note":"goods and process scope must match"},
    {"case_id":65,"cp_id":"CP33","verdict":"0","confirmed":true,"note":"goods and process scope must match"},
    {"case_id":74,"cp_id":"CP33","verdict":"0","confirmed":true,"note":"goods and process scope must match"},
    {"case_id":23,"cp_id":"CP35","verdict":"1","confirmed":true,"note":"design risk assessment"},
    {"case_id":38,"cp_id":"CP35","verdict":"0","confirmed":true,"note":"design risk assessment"},
    {"case_id":65,"cp_id":"CP35","verdict":"0","confirmed":true,"note":"design risk assessment"},
    {"case_id":74,"cp_id":"CP35","verdict":"0","confirmed":true,"note":"design risk assessment"}
  ]
}
```

- [ ] **Step 4: Implement the minimal Gold loader**

Create `src/freca/evaluation.py` with this public model and loader. It rejects malformed input and duplicate confirmed labels.

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from freca.models import Verdict
from freca.state import read_json


class GoldLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    verdict: Verdict
    confirmed: bool
    note: str


def load_gold_labels(path: Path) -> dict[tuple[int, str], GoldLabel]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("labels"), list):
        raise ValueError("gold label file must contain a labels list")
    labels: dict[tuple[int, str], GoldLabel] = {}
    for raw in payload["labels"]:
        label = GoldLabel.model_validate(raw)
        if not label.confirmed:
            continue
        key = (label.case_id, label.cp_id)
        if key in labels:
            raise ValueError(f"duplicate confirmed gold label: {label.case_id}/{label.cp_id}")
        labels[key] = label
    return labels
```

- [ ] **Step 5: Verify GREEN and commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py::test_load_gold_labels_includes_only_confirmed_verdicts -q`

Expected: `1 passed`.

```bash
git add gold/consensus-v1.json src/freca/evaluation.py tests/test_evaluation.py
git commit -m "feat: 固化Task2人工金标基线"
```

### Task 2: Evaluate a completed run without mutating it

**Files:**

- Modify: `src/freca/evaluation.py`
- Modify: `tests/test_evaluation.py`

- [ ] **Step 1: Write the failing evaluation test**

Add a `_write_decision` helper that serializes a valid `AuditDecision`, then add:

```python
from freca.evaluation import evaluate_run


def test_evaluate_run_reports_match_mismatch_and_missing(tmp_path: Path) -> None:
    gold = _gold_file(tmp_path, [(23, "CP1", "0"), (35, "CP1", "1"), (38, "CP1", "0")])
    _write_decision(tmp_path / "final" / "023" / "CP1.json", 23, "CP1", "0")
    _write_decision(tmp_path / "final" / "035" / "CP1.json", 35, "CP1", "0")

    report = evaluate_run(tmp_path, run_id="baseline-a", gold_path=gold)

    assert report["evaluated_count"] == 2
    assert report["matched_count"] == 1
    assert report["agreement_rate"] == 0.5
    assert report["missing_tasks"] == ["038/CP1"]
    assert report["mismatches"][0]["task"] == "035/CP1"
    assert (tmp_path / "evaluation" / "baseline-a.json").exists()
```

- [ ] **Step 2: Verify RED**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py::test_evaluate_run_reports_match_mismatch_and_missing -q`

Expected: import failure because `evaluate_run` does not exist.

- [ ] **Step 3: Implement `evaluate_run`**

Use the existing final-artifact contract and the existing `AuditDecision` validator:

```python
from freca.models import AuditDecision
from freca.state import atomic_write_json


def _decision_path(build_dir: Path, case_id: int, cp_id: str) -> Path:
    return build_dir / "final" / f"{case_id:03d}" / f"{cp_id}.json"


def _task_key(case_id: int, cp_id: str) -> str:
    return f"{case_id:03d}/{cp_id}"
```

For every Gold key, read and validate its decision if present. Equal verdicts increase `matched_count`; absent files are appended to sorted `missing_tasks`; unequal verdicts append a record with `task`, `gold_verdict`, `actual_verdict`, `reasoning_summary`, `supporting_evidence`, and `contrary_evidence`. Return and persist `{run_id, gold_version, evaluated_count, matched_count, agreement_rate, missing_tasks, mismatches, per_cp}` to `build/evaluation/{run_id}.json`. `agreement_rate` is `None` when `evaluated_count == 0`.

- [ ] **Step 4: Verify GREEN and commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py -q`

Expected: all evaluation tests pass.

```bash
git add src/freca/evaluation.py tests/test_evaluation.py
git commit -m "feat: 增加运行结果与金标对比"
```

### Task 3: Expose report comparison through the existing CLI style

**Files:**

- Modify: `src/freca/evaluation.py`
- Modify: `src/freca/cli.py`
- Modify: `tests/test_evaluation.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing comparison and parser tests**

```python
from freca.cli import build_parser
from freca.evaluation import compare_reports


def test_compare_reports_orders_runs_by_agreement_rate(tmp_path: Path) -> None:
    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation" / "slow.json").write_text('{"agreement_rate":0.5,"evaluated_count":4,"matched_count":2}', encoding="utf-8")
    (tmp_path / "evaluation" / "fast.json").write_text('{"agreement_rate":0.75,"evaluated_count":4,"matched_count":3}', encoding="utf-8")

    result = compare_reports(tmp_path, ["slow", "fast"])

    assert [row["run_id"] for row in result["runs"]] == ["fast", "slow"]


def test_cli_parses_evaluation_actions() -> None:
    parser = build_parser()
    assert parser.parse_args(["evaluation", "run", "--run-id", "a"]).evaluation_action == "run"
    assert parser.parse_args(["evaluation", "compare", "--run-id", "a", "--run-id", "b"]).evaluation_action == "compare"
```

- [ ] **Step 2: Verify RED**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py::test_compare_reports_orders_runs_by_agreement_rate tests/test_evaluation.py::test_cli_parses_evaluation_actions -q`

Expected: import/parser failures because report comparison and CLI actions do not exist.

- [ ] **Step 3: Implement the exact CLI grammar**

Add to `build_parser` using the existing nested `ablation` pattern:

```python
evaluation = subparsers.add_parser("evaluation", help="Compare final verdicts with versioned Gold labels")
evaluation_actions = evaluation.add_subparsers(dest="evaluation_action", required=True)
evaluation_run = evaluation_actions.add_parser("run", help="Write one Gold comparison report")
evaluation_run.add_argument("--run-id", required=True)
evaluation_run.add_argument("--gold-labels", type=Path, default=Path("gold/consensus-v1.json"))
evaluation_compare = evaluation_actions.add_parser("compare", help="Rank saved Gold reports")
evaluation_compare.add_argument("--run-id", action="append", required=True)
```

Implement `compare_reports(build_dir, run_ids)` by loading only `build/evaluation/{run_id}.json`, returning `runs` ordered by non-null `agreement_rate` descending. In `main`, print reports for both actions and return `0`: mismatches are expected experimental observations, not failures.

- [ ] **Step 4: Verify GREEN and commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py tests/test_cli.py -q`

Expected: all focused tests pass.

```bash
git add src/freca/evaluation.py src/freca/cli.py tests/test_evaluation.py tests/test_cli.py
git commit -m "feat: 提供金标评估与方案对比命令"
```

### Task 4: Finish the branch synchronization record and operator guide

**Files:**

- Create: `docs/BRANCH_SYNC.md`
- Modify: `README.md`

- [ ] **Step 1: Record the branch decision**

Create `docs/BRANCH_SYNC.md` stating: `Task2-agent-pipeline` is the executable baseline; `Task2` is an independent July 25 farm pipeline; there is no merge base; bulk merge is prohibited. Document that current `freca` already parses DOCX/XLSX/PDF and persists chunks/indexes, so this release imports no farm code. Any future parsing/cache import requires a focused compatibility test first.

- [ ] **Step 2: Add the evaluated-run sequence to README**

Insert:

```powershell
# Run immediately after each method finishes, before another method overwrites build/final/.
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml evaluation run --run-id baseline-v1

# Rank persisted reports after all methods have been evaluated.
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml evaluation compare --run-id baseline-v1 --run-id structured-v1 --run-id review-v1
```

State that only confirmed team consensus enters the denominator; CP17/CP19 and unresolved CP24/CP26 wait for explicit Gold confirmation.

- [ ] **Step 3: Verify the full suite and commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`

Expected: all current and added tests pass.

```bash
git add README.md docs/BRANCH_SYNC.md
git commit -m "docs: 说明基线同步与金标评测流程"
```

### Task 5: Verify the operator path

**Files:**

- No source changes expected.

- [ ] **Step 1: Verify command discovery**

Run: `./.venv/Scripts/python.exe -m freca.cli evaluation --help`

Expected: `run` and `compare` are listed.

- [ ] **Step 2: Verify immutable report behavior**

Run the focused evaluation tests and inspect `build/evaluation/<run-id>.json` after `evaluation run`.

Expected: the report exists, while the underlying `build/final` JSON checksums remain unchanged.

- [ ] **Step 3: Review repository state**

Run: `git status --short --branch; git log --oneline -4`

Expected: only planned source/doc changes are present and every new commit subject is Chinese.
