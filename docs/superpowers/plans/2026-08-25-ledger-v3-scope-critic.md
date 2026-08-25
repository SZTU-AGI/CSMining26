# Ledger v3 证据层级与冲突裁判 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增主体/证据层级裁决模式和只选已有结论的冲突 Critic，并在同一 34 条 Gold 上独立评测。

**Architecture:** 证据层级模式只扩展裁决契约，默认保持关闭；Critic 位于 primary/review 协调之后，只在两者均通过门禁且 verdict 冲突时选择其中一个。两个 profile 单独运行，复用现有 Ledger Gold 输出、评测和 HTML 报告器。

**Tech Stack:** Python 3.11、Pydantic、pytest、MiniMax-M3。

---

### Task 1: 增加证据层级裁决模式

**Files:**

- Modify: `src/freca/ledger/config.py`
- Modify: `src/freca/ledger/adjudicate.py`
- Modify: `tests/test_ledger_adjudicate.py`

- [ ] **Step 1: 写出模式边界测试**

```python
def test_scope_aware_prompt_separates_design_and_execution_evidence() -> None:
    system, _ = build_adjudication_messages(rubric=rubric, pack=pack, scope_aware=True)
    assert "execution incident" in system
    assert "design or facility requirement" in system

def test_default_prompt_keeps_existing_contract() -> None:
    system, _ = build_adjudication_messages(rubric=rubric, pack=pack)
    assert "execution incident" not in system
```

- [ ] **Step 2: 确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_adjudicate.py`

Expected: `scope_aware` 参数不存在。

- [ ] **Step 3: 实现最小配置与提示词扩展**

在 `AdjudicationConfig` 增加 `scope_aware_evidence: bool = False`；`Adjudicator` 将该值传给 `build_adjudication_messages`。开启时补充四项规则：外部主体不可支持、全局冲突不否定原子同主体事实、执行事件不能单独反证场所设计、执行型要件仍需实际记录。

- [ ] **Step 4: 验证并提交**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_adjudicate.py tests/test_ledger_gates.py`

Expected: PASS。

```powershell
git add src/freca/ledger/config.py src/freca/ledger/adjudicate.py tests/test_ledger_adjudicate.py
git commit -m "feat: 增加Ledger证据层级裁决模式"
```

### Task 2: 实现冲突 Critic

**Files:**

- Create: `src/freca/ledger/critic.py`
- Modify: `src/freca/ledger/config.py`
- Modify: `src/freca/ledger/models.py`
- Modify: `src/freca/ledger/review.py`
- Modify: `src/freca/ledger/pipeline.py`
- Create: `tests/test_ledger_critic.py`

- [ ] **Step 1: 写出 Critic 安全测试**

```python
def test_critic_can_choose_only_an_existing_decision() -> None:
    result = resolve_conflict(primary=primary, review=review, payload={"choice": "primary"})
    assert result.final is primary

def test_invalid_critic_choice_preserves_reconciled_decision() -> None:
    result = resolve_conflict(primary=primary, review=review, payload={"choice": "new_verdict"})
    assert result.final is review
    assert result.used_critic is False
```

- [ ] **Step 2: 确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_critic.py`

Expected: collection FAIL，因为 `freca.ledger.critic` 不存在。

- [ ] **Step 3: 实现只选择 primary/review 的 Critic**

增加 `CriticConfig(enabled=False, only_on_disagreement=True)` 与 `models.critic` endpoint；`critic.py` 的 JSON schema 只允许 `choice: primary|review` 与不超过 1,200 字的理由。Critic 输入紧凑 rubric/pack、两份 verdict/理由/引用和 gate 摘要；它不接收 Gold。只有双方门禁通过且 verdict 冲突时调用。失败、无效或选中门禁失败项时保留原 `choose_final` 结果，并落盘 Critic 摘要和 quality flag。

- [ ] **Step 4: 验证并提交**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_critic.py tests/test_ledger_review.py tests/test_ledger_gates.py`

Expected: PASS。

```powershell
git add src/freca/ledger/critic.py src/freca/ledger/config.py src/freca/ledger/models.py src/freca/ledger/review.py src/freca/ledger/pipeline.py tests/test_ledger_critic.py
git commit -m "feat: 增加Ledger冲突理由裁判"
```

### Task 3: 运行 v3 并更新报告

**Files:**

- Create: `config.ledger.minimax.evidence-scope.yaml`
- Create: `config.ledger.minimax.conflict-critic.yaml`
- Create: `docs/method-runs/gold-v3-summary.md`
- Generate: `build/method-runs/ledger-evidence-scope-gold-v3/`
- Generate: `build/method-runs/ledger-conflict-critic-gold-v3/`
- Generate: `build/method-comparison/gold-v3.json`

- [ ] **Step 1: 写出两个 profile 的配置测试并确认 RED**

```python
def test_scope_profile_enables_only_scope_aware_evidence() -> None: ...
def test_critic_profile_enables_only_conflict_critic() -> None: ...
```

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_v3_profiles.py`

Expected: FAIL，因为 profile 文件不存在。

- [ ] **Step 2: 添加 profile、验证并提交**

`evidence-scope` 只启用 `adjudication.scope_aware_evidence`；`conflict-critic` 只启用 `critic.enabled`。二者保持 v2 的 N/A 硬门、同一 MiniMax endpoint 和 `review.mode: on_trigger`。

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger_v3_profiles.py`

Expected: PASS。

- [ ] **Step 3: 运行、评测与比较**

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method ledger --run-id ledger-evidence-scope-gold-v3 --ledger-config config.ledger.minimax.evidence-scope.yaml --gold-labels gold/consensus-v1.json --max-workers 1
.\.venv\Scripts\python.exe -m freca.cli --config config.minimax.yaml method ledger --run-id ledger-conflict-critic-gold-v3 --ledger-config config.ledger.minimax.conflict-critic.yaml --gold-labels gold/consensus-v1.json --max-workers 1
```

终态后分别 `method evaluate`，将 v1、v2、两条 v3 传入 `method compare --output build/method-comparison/gold-v3.json`，再以 `method report --comparison ...` 刷新 HTML。

- [ ] **Step 4: 记录结论并提交**

`gold-v3-summary.md` 记录两条 agreement、coverage、失败率、Critic 调用/选择数，并明确保留 79.4% v2 作为对照。

```powershell
git add config.ledger.minimax.evidence-scope.yaml config.ledger.minimax.conflict-critic.yaml docs/method-runs/gold-v3-summary.md build/method-comparison/gold-v3.json build/reports/gold-method-selection.html
git commit -m "docs: 记录Ledger v3 Gold对比结果"
```

## Plan self-review

- Task 1、2 将两个变化隔离且保留默认链路；Task 3 只运行 34 条并统一评测。
- Critic 不能产生第三个 verdict，profile 不含密钥，Gold 不进入 prompt。
