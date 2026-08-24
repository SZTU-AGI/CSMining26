# Gold 方法选择实验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 34 条已确认 Gold 标签上，运行并比较检索消融、直连 LLM judge 与 Ledger 链路，选择唯一可晋级 pilot 的方法。

**Architecture:** 新增一个方法实验层，唯一负责 Gold 任务选择、方法专属输出布局和统一评测；它调用现有检索、审计和 Ledger 组件，而不把 Gold 写入 prompt。生产 `build/` 继续只提供解析与索引输入；每个候选方法写入 `build/method-runs/<run-id>/`，排行榜只读取已持久化的评测报告。

**Tech Stack:** Python 3.11、Pydantic、现有 `freca` CLI、MiniMax-M3 Anthropic 客户端、pytest。

---

### Task 1: 固化当前 MiniMax 兼容基线

**Files:**
- Modify: `src/freca/llm.py`, `src/freca/env_loader.py`, `src/freca/config.py`, `src/freca/audit.py`, `src/freca/quality.py`, `src/freca/pipeline.py`, `src/freca/ledger/pipeline.py`
- Create: `config.minimax.yaml`, `tests/test_minimax_compat.py`
- Modify: `tests/test_audit.py`, `tests/test_quality.py`, `tests/test_pipeline_quality.py`

- [ ] **Step 1: Run the compatibility regressions before changing the experiment layer**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_minimax_compat.py tests/test_audit.py tests/test_quality.py tests/test_pipeline_quality.py
```

Expected: all MiniMax protocol, citation canonicalization, non-compliance gate and JSON-shape tests pass.

- [ ] **Step 2: Verify the configured endpoint without calling the model**

Run:

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml doctor --stage pilot
```

Expected: audit/verifier/arbitrator show `MiniMax-M3` and the credential status is set; output never prints a secret.

- [ ] **Step 3: Commit the tested runtime baseline**

```powershell
git add src/freca/llm.py src/freca/env_loader.py src/freca/config.py src/freca/audit.py src/freca/quality.py src/freca/pipeline.py src/freca/ledger/pipeline.py config.minimax.yaml tests/test_minimax_compat.py tests/test_audit.py tests/test_quality.py tests/test_pipeline_quality.py
git commit -m "feat: 支持MiniMax Gold实验运行链路"
```

### Task 2: 建立 Gold 任务清单与方法专属输出布局

**Files:**
- Create: `src/freca/methods.py`
- Modify: `src/freca/evaluation.py`, `src/freca/cli.py`
- Create: `tests/test_methods.py`
- Modify: `tests/test_evaluation.py`, `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for Gold task projection and path isolation**

```python
def test_gold_tasks_preserve_only_confirmed_case_cp_pairs(tmp_path: Path) -> None:
    tasks = gold_tasks(tmp_path / "gold.json")
    assert {(task.case_id, task.cp_id) for task in tasks} == {(23, "CP1"), (74, "CP35")}

def test_method_layout_never_uses_shared_final_directory(tmp_path: Path) -> None:
    layout = MethodRunLayout(tmp_path, "bm25-gold-v1")
    assert layout.final_path(23, "CP1") == tmp_path / "method-runs" / "bm25-gold-v1" / "final" / "023" / "CP1.json"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_methods.py
```

Expected: collection fails because `freca.methods` does not exist.

- [ ] **Step 3: Implement the minimal method contract**

```python
class GoldTask(StrictModel):
    case_id: int
    cp_id: str
    expected: Verdict

class MethodRunLayout:
    def __init__(self, build_dir: Path, run_id: str) -> None: ...
    def final_path(self, case_id: int, cp_id: str) -> Path: ...

def gold_tasks(gold_path: Path) -> tuple[GoldTask, ...]: ...
```

`evaluate_run` receives an optional `final_root` argument, defaulting to the existing `build/final`; method runs pass `layout.final_dir`. Add CLI `method evaluate --run-id` that reads only the method layout.

- [ ] **Step 4: Verify GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_methods.py tests/test_evaluation.py tests/test_cli.py
git add src/freca/methods.py src/freca/evaluation.py src/freca/cli.py tests/test_methods.py tests/test_evaluation.py tests/test_cli.py
git commit -m "feat: 隔离Gold方法运行与评测产物"
```

### Task 3: 将检索消融接入同一 judge 与 Gold 输出

**Files:**
- Modify: `src/freca/pipeline.py`, `src/freca/ablation.py`, `src/freca/cli.py`
- Modify: `tests/test_pipeline_quality.py`, `tests/test_ablation.py`, `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for a precomputed retrieval bundle**

```python
def test_process_task_uses_supplied_bundle_and_writes_method_final(tmp_path: Path) -> None:
    output = process_retrieved_audit_task(task=task, checkpoint=checkpoint, retrieval=bundle, ...)
    assert AuditDecision.model_validate(read_json(output)).cp_id == "CP1"
    assert output.parts[-5:-2] == ("method-runs", "bm25-gold-v1", "final")
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline_quality.py::test_process_task_uses_supplied_bundle_and_writes_method_final
```

Expected: import error because `process_retrieved_audit_task` does not exist.

- [ ] **Step 3: Factor the existing quality chain without changing its semantics**

Extract the body after `retrieve_for_checkpoint` from `process_audit_task` into:

```python
def process_retrieved_audit_task(*, task: AuditTask, checkpoint: CheckpointDefinition,
    retrieval: RetrievalBundle, audit_client: JsonChatClient, verifier_client: JsonChatClient,
    arbitrator_client: JsonChatClient | None, output_build_dir: Path, ...) -> Path: ...
```

`process_audit_task` continues to retrieve then delegates. Add `run_retrieval_judge_experiment` that iterates `gold_tasks`, creates each existing ablation variant bundle, calls the extracted function, and writes only to its `MethodRunLayout`.

- [ ] **Step 4: Add CLI and verify Green**

Add:

```text
freca method retrieval --run-id <id> --variant bm25_only --variant weighted_hybrid --gold-labels gold/consensus-v1.json
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline_quality.py tests/test_ablation.py tests/test_cli.py
git add src/freca/pipeline.py src/freca/ablation.py src/freca/cli.py tests/test_pipeline_quality.py tests/test_ablation.py tests/test_cli.py
git commit -m "feat: 让检索消融输出Gold裁决"
```

### Task 4: 迁移两条直连 LLM judge

**Files:**
- Create: `src/freca/direct_judge.py`
- Modify: `src/freca/cli.py`, `src/freca/methods.py`
- Create: `tests/test_direct_judge.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing material-boundary tests**

```python
def test_checkpoint_full_uses_only_current_case_and_policy_chunks() -> None:
    envelope = build_direct_envelope(method="checkpoint_full_judge", case_id=23, cp_id="CP1", ...)
    assert "case-024" not in envelope.text

def test_direct_judge_rejects_annotated_or_unknown_citations() -> None:
    with pytest.raises(ValueError, match="citation"):
        decision_from_direct_payload(payload_with_unknown_id, allowed_chunk_ids)
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_direct_judge.py
```

Expected: collection fails because `freca.direct_judge` does not exist.

- [ ] **Step 3: Implement only the two approved direct methods**

Migrate the old worktree's material loading and automatic BM25 selection into `direct_judge.py`; do not migrate case_full, element_full, stage_audit, agent_audit or historical silver code. Both methods call the shared MiniMax JSON client and convert the response into an `AuditDecision` with exact chunk IDs, current `case_id`, explicit applicability and the common team-consensus system prompt.

- [ ] **Step 4: Add runner, verify Green and commit**

```text
freca method direct --run-id automatic-retrieval-gold-v1 --method automatic_retrieval_judge
freca method direct --run-id checkpoint-full-gold-v1 --method checkpoint_full_judge
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_direct_judge.py tests/test_cli.py
git add src/freca/direct_judge.py src/freca/methods.py src/freca/cli.py tests/test_direct_judge.py tests/test_cli.py
git commit -m "feat: 增加直连LLM Gold对照方法"
```

### Task 5: 接入 Ledger 候选方法

**Files:**
- Modify: `src/freca/ledger/config.py`, `src/freca/ledger/pipeline.py`, `src/freca/ledger/cli.py`, `src/freca/methods.py`
- Create: `config.ledger.minimax.yaml`, `tests/test_ledger_gold_adapter.py`

- [ ] **Step 1: Write failing outcome projection test**

```python
def test_ledger_outcome_projects_to_method_final(tmp_path: Path) -> None:
    path = export_ledger_final(outcome, MethodRunLayout(tmp_path, "ledger-gold-v1"))
    assert AuditDecision.model_validate(read_json(path)).verdict == Verdict.NON_COMPLIANT
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_gold_adapter.py
```

Expected: import error because `export_ledger_final` does not exist.

- [ ] **Step 3: Add a Gold-limited Ledger command**

`config.ledger.minimax.yaml` contains no secret and reuses MiniMax endpoints. Add `freca method ledger --run-id ledger-gold-v1 --gold-labels ...`; it builds facts/rubrics only for the Gold task projection, runs C–E, projects successful ledger outcomes to the method layout, and persists non-terminal ledger outcomes as method failures rather than inventing verdicts.

- [ ] **Step 4: Verify Green and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_gold_adapter.py tests/test_ledger_*.py
git add src/freca/ledger/config.py src/freca/ledger/pipeline.py src/freca/ledger/cli.py src/freca/methods.py config.ledger.minimax.yaml tests/test_ledger_gold_adapter.py
git commit -m "feat: 接入Ledger Gold候选链路"
```

### Task 6: 统一排行榜、错例报告与 34 项运行编排

**Files:**
- Modify: `src/freca/evaluation.py`, `src/freca/cli.py`, `README.md`
- Create: `tests/test_method_comparison.py`

- [ ] **Step 1: Write failing eligibility and ranking tests**

```python
def test_comparison_excludes_low_coverage_run_from_winner(tmp_path: Path) -> None:
    report = compare_method_runs(tmp_path, ["high-score-low-coverage", "eligible"])
    assert report["winner"]["run_id"] == "eligible"

def test_comparison_keeps_blocked_tasks_in_failure_summary(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_method_comparison.py
```

Expected: collection fails because `compare_method_runs` does not exist.

- [ ] **Step 3: Implement comparison and exact 34-task orchestration**

`compare_method_runs` loads persisted evaluation reports plus method task summaries, computes coverage and terminal failure rate, applies the 90%/10% eligibility gate, then writes:

```text
build/method-comparison/gold-v1.json
build/method-comparison/gold-v1-mismatches.jsonl
```

Add `freca method compare --run-id ...` and `freca method gold-suite --max-workers 1`; the suite runs registered methods sequentially, always evaluates before starting the next method, and never invokes a 369/4100 task set.

- [ ] **Step 4: Verify Green, run the offline suite and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m freca.cli method gold-suite --help
git add src/freca/evaluation.py src/freca/cli.py README.md tests/test_method_comparison.py
git commit -m "feat: 汇总Gold方法实验排行榜"
```

### Task 7: 受控真实运行与选择

**Files:**
- Create: `docs/method-runs/gold-v1-summary.md`

- [ ] **Step 1: Run only the registered Gold suite**

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method gold-suite --max-workers 1
```

Expected: each method has a separate `build/method-runs/<run-id>/evaluation.json`; no task outside the 34 confirmed Gold pairs is created.

- [ ] **Step 2: Verify isolation and ranking**

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method compare --run-id bm25-gold-v1 --run-id checkpoint-full-gold-v1 --run-id ledger-gold-v1
```

Expected: output names one eligible winner or explicitly states that no method passed the coverage/failure gate.

- [ ] **Step 3: Write the decision record and commit**

The summary must state every method's agreement, coverage, terminal failure rate, per-CP rates, cost, winner/none, and the five largest error families. It must not claim an accuracy beyond the 34 labels.

```powershell
git add docs/method-runs/gold-v1-summary.md
git commit -m "docs: 记录Gold方法选择结果"
```

## Plan self-review

- Spec coverage: Tasks 2–6 cover isolation, all three method groups, unified Gold scoring and ranking; Task 7 covers the 34-item real run and explicit stop condition.
- Scope: no task invokes 369 or 4,100 items; historical direct-experiment methods outside the two approved comparators are excluded.
- Type consistency: every runner writes `AuditDecision` to `MethodRunLayout.final_path`, so `evaluate_run(..., final_root=...)` has one contract.
- Placeholder scan: no deferred implementation placeholders; each behavior has a named file, failing test, command and expected result.
