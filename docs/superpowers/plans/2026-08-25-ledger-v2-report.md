# Ledger v2 与 Gold HTML 汇报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Ledger 的不合法 `N/A`，比较三个 v2 设计，并生成离线 HTML Gold 汇报。

**Architecture:** 三条 v2 均沿用同一 MiniMax-M3、同一 34 条 Gold、同一事实账本和评测器。基础修复位于模型响应规范化边界；额外实验仅改变配置，以便归因。HTML 只读取已持久化的 JSON 评测与排名，不含模型密钥或完整案例材料。

**Tech Stack:** Python 3.11、Pydantic、pytest、MiniMax-M3、纯 HTML/CSS。

---

### Task 1: 收紧 N/A 语义规范化

**Files:**

- Modify: `src/freca/ledger/adjudicate.py:241-270`
- Modify: `tests/test_ledger_gates.py`

- [ ] **Step 1: 写出失败测试**

```python
def test_unknown_applicability_cannot_be_normalized_to_na() -> None:
    decision = normalize_decision(
        _na_payload(applicability="UNKNOWN"), rubric=rubric, pack=pack,
        config=AdjudicationConfig(),
    )
    assert decision.verdict == Verdict.NON_COMPLIANT
    assert decision.applicability == Applicability.UNKNOWN
    assert "na_withdrawn_nonlegal_applicability" in decision.quality_flags

def test_legal_not_applicable_survives_normalization() -> None:
    decision = normalize_decision(
        _na_payload(applicability="NOT_APPLICABLE"), rubric=rubric, pack=pack,
        config=AdjudicationConfig(),
    )
    assert decision.verdict == Verdict.NOT_APPLICABLE
```

- [ ] **Step 2: 确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_gates.py`

Expected: 第一条测试失败；当前实现把 `UNKNOWN + N/A` 错误写成 `NOT_APPLICABLE`。

- [ ] **Step 3: 实现最小修复**

在 `normalize_decision` 的 `N/A` 分支先检查原始 applicability：

```python
if applicability != Applicability.NOT_APPLICABLE:
    verdict = Verdict.NON_COMPLIANT
    flags.append("na_withdrawn_nonlegal_applicability")
elif not policy_citations:
    verdict = Verdict.NON_COMPLIANT
    applicability = Applicability.UNKNOWN
    flags.append("na_withdrawn_no_policy_basis")
elif not applicability_reasoning:
    verdict = Verdict.NON_COMPLIANT
    applicability = Applicability.UNKNOWN
    flags.append("na_withdrawn_no_applicability_reasoning")
```

不从理由文本推断法规适用性，不读取 Gold，不引入 CP/case 分支。

- [ ] **Step 4: 验证并提交**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_gates.py tests/test_ledger_review.py`

Expected: PASS。

```powershell
git add src/freca/ledger/adjudicate.py tests/test_ledger_gates.py
git commit -m "fix: 严格限制Ledger的N/A适用性"
```

### Task 2: 固化三种可比较的 v2 profile

**Files:**

- Create: `config.ledger.minimax.na-gate.yaml`
- Create: `config.ledger.minimax.review-always.yaml`
- Create: `config.ledger.minimax.evidence-expanded.yaml`
- Create: `tests/test_ledger_v2_profiles.py`

- [ ] **Step 1: 写出配置测试**

```python
def test_review_profile_only_changes_review_mode() -> None:
    config = LedgerConfig.from_yaml(Path("config.ledger.minimax.review-always.yaml"))
    assert config.ledger.review.mode == ReviewMode.ALWAYS
    assert config.ledger.selection.max_facts == 28

def test_evidence_profile_expands_clean_evidence_only() -> None:
    config = LedgerConfig.from_yaml(Path("config.ledger.minimax.evidence-expanded.yaml"))
    assert config.ledger.selection.max_facts == 42
    assert config.ledger.selection.include_contaminated is False
```

- [ ] **Step 2: 确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_v2_profiles.py`

Expected: FAIL，因为 profile 文件不存在。

- [ ] **Step 3: 添加 profile**

- `na-gate`：保持现有 `selection` 与 `review.mode: on_trigger`，只衡量 Task 1。
- `review-always`：只将 `review.mode` 改为 `always`，衡量全量独立复核。
- `evidence-expanded`：`selection.max_facts: 42` 且 `include_contaminated: false`；仍传递全部矛盾记录，但不将外部场所事实当作候选支持事实。

三份文件复用原有路径、MiniMax 端点和环境变量名称，不写入密钥。

- [ ] **Step 4: 验证并提交**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_v2_profiles.py tests/test_ledger_config.py`

Expected: PASS。

```powershell
git add config.ledger.minimax.na-gate.yaml config.ledger.minimax.review-always.yaml config.ledger.minimax.evidence-expanded.yaml tests/test_ledger_v2_profiles.py
git commit -m "feat: 增加Ledger v2可比实验配置"
```

### Task 3: 生成静态 HTML 汇报

**Files:**

- Create: `src/freca/method_report.py`
- Modify: `src/freca/cli.py`
- Create: `tests/test_method_report.py`

- [ ] **Step 1: 写出失败测试**

```python
def test_write_gold_html_report_renders_ranked_runs(tmp_path: Path) -> None:
    output = write_gold_html_report(
        build_dir=tmp_path,
        comparison_path=tmp_path / "method-comparison" / "gold-v2.json",
    )
    html = output.read_text(encoding="utf-8")
    assert "ledger-gold-v1" in html
    assert "70.6%" in html
    assert "Gold 仅用于离线评分" in html
```

- [ ] **Step 2: 确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_method_report.py`

Expected: collection FAIL，因为 `freca.method_report` 尚不存在。

- [ ] **Step 3: 实现报告器与 CLI**

实现：

```python
def write_gold_html_report(
    *, build_dir: Path, comparison_path: Path, output_path: Path | None = None
) -> Path: ...
```

它读取 comparison 和对应 `build/evaluation/<run_id>.json`，经 `html.escape` 生成范围说明、排名表、eligible 标记、Ledger 的逐 CP 表和 v2 区域；默认写 `build/reports/gold-method-selection.html`。添加：

```text
freca method report --comparison build/method-comparison/gold-v2.json
```

- [ ] **Step 4: 验证并提交**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_method_report.py tests/test_cli.py`

Expected: PASS，HTML 可安全转义 run id。

```powershell
git add src/freca/method_report.py src/freca/cli.py tests/test_method_report.py
git commit -m "feat: 生成Gold方法HTML汇报"
```

### Task 4: 运行、评分并比较三种 v2

**Files:**

- Create: `docs/method-runs/gold-v2-summary.md`
- Generate: `build/method-runs/ledger-na-gate-gold-v2/`
- Generate: `build/method-runs/ledger-review-always-gold-v2/`
- Generate: `build/method-runs/ledger-evidence-expanded-gold-v2/`
- Generate: `build/evaluation/ledger-*-gold-v2.json`
- Generate: `build/method-comparison/gold-v2.json`
- Generate: `build/reports/gold-method-selection.html`

- [ ] **Step 1: 启动三条隔离的 34 项运行**

每条使用 `--max-workers 1`，可并行但绝不共享 run-id：

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method ledger --run-id ledger-na-gate-gold-v2 --ledger-config config.ledger.minimax.na-gate.yaml --gold-labels gold/consensus-v1.json --max-workers 1
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method ledger --run-id ledger-review-always-gold-v2 --ledger-config config.ledger.minimax.review-always.yaml --gold-labels gold/consensus-v1.json --max-workers 1
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method ledger --run-id ledger-evidence-expanded-gold-v2 --ledger-config config.ledger.minimax.evidence-expanded.yaml --gold-labels gold/consensus-v1.json --max-workers 1
```

- [ ] **Step 2: 评测每条终态运行**

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method evaluate --run-id ledger-na-gate-gold-v2
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method evaluate --run-id ledger-review-always-gold-v2
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method evaluate --run-id ledger-evidence-expanded-gold-v2
```

- [ ] **Step 3: 统一排名并重建 HTML**

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method compare --run-id ledger-gold-v1 --run-id automatic-retrieval-gold-v1 --run-id vector-gold-v1 --run-id checkpoint-full-gold-v1 --run-id full-retrieval-gold-v1 --run-id rrf-topk-gold-v1 --run-id weighted-hybrid-gold-v1 --run-id bm25-gold-v1 --run-id ledger-na-gate-gold-v2 --run-id ledger-review-always-gold-v2 --run-id ledger-evidence-expanded-gold-v2
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method report --comparison build/method-comparison/gold-v2.json
```

Expected: 只让 coverage ≥90%、终态失败率 ≤10% 的方法争夺 winner。

- [ ] **Step 4: 写出结论并提交**

`docs/method-runs/gold-v2-summary.md` 必须记录每条 v2 的 agreement、coverage、失败率、相对 v1 和逐 CP 变化，并说明 Gold 没有进入模型输入且没有扩展到 369/4,100 项。

```powershell
git add docs/method-runs/gold-v2-summary.md build/method-comparison/gold-v2.json build/reports/gold-method-selection.html
git commit -m "docs: 记录Ledger v2 Gold对比结果"
```

## Plan self-review

- Spec coverage: Task 1 修复 N/A；Task 2 隔离三个方案；Task 3 生成 HTML；Task 4 运行、评分、排名并记录。
- Scope: 只跑 34 条 confirmed Gold，不修改 Gold、不写 case/CP 特判、不推送远端。
- 类型一致性: 三种方案均通过 `method ledger`、`method evaluate`、`method compare` 现有合约产出；报告仅读取这些 JSON。
- Placeholder scan: 未包含 TODO、TBD 或未定义的代码行为。
