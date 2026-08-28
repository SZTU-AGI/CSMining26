# 人工评分标准 rubric 来源与 Gold v4 对比 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Ledger 增加人工评分标准（最终合并版 xlsx）作为 Stage B 的可选 rubric 来源，在 37 条 consensus-v2 Gold 上完成双口径 × 双 profile 的隔离对比，产出 gold-v4 排名、HTML 汇报与结论文档。

**Architecture:** curated 模式只在 Stage B 生效：检索逐字不变，向检索结果前置 `curated:{cp_id}` 伪 chunk（红线 R3 + 评分标准全文，排除 Act 层参考），换用 curated 系统提示词与 `rubric-curated-v1` 版本号；`curated:` 前缀在生成器提示词 / 存储 snippets / 复核压缩三处豁免截断，其余限制与 PDF 臂完全一致；下游 `CheckpointRubric` 契约不变。

**Tech Stack:** Python 3.11、Pydantic v2、pytest、openpyxl、MiniMax-M3、纯 HTML/CSS。

---

### Task 1: Gold 标签扩充至 37 条并支持 re_number

**Files:**
- Modify: `src/freca/evaluation.py`
- Create: `gold/consensus-v2.json`
- Modify: `tests/test_evaluation.py`

- [ ] **Step 1: 写出失败测试**：`load_gold_labels(gold/consensus-v2.json)` 返回 37 条且含 `(65,"CP24")=="1"`、`(65,"CP26")=="0"`、`(74,"CP26")=="0"`；`re_number` 正确映射；缺 `re_number` 的 GoldLabel 合法。
- [ ] **Step 2: 确认 RED** — `.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation.py`
- [ ] **Step 3: 最小实现**：`GoldLabel` 加 `re_number: str | None = None`；生成 consensus-v2.json（34 条补 re_number + 3 条新增，version=consensus-v2）。
- [ ] **Step 4: 验证并提交** — `pytest -q tests/test_evaluation.py tests/test_methods.py`

```powershell
git add src/freca/evaluation.py gold/consensus-v2.json tests/test_evaluation.py
git commit -m "feat: 扩充Gold共识标签至37条"
```

### Task 2: 人工评分标准 xlsx 解析器

**Files:**
- Create: `src/freca/ledger/criteria.py`
- Create: `tests/test_ledger_criteria.py`
- Create: `FRECA_41CP_评分标准_最终合并版_材料并入.xlsx`（资产拷贝至仓库根）

- [ ] **Step 1: 写出失败测试**：openpyxl 造假表（>1,800 字标记串 + Act 层占位文本）；断言列映射、Act 层不入 content、超长不截断、缺 CP 抛 KeyError、EvidenceChunk 可构造。
- [ ] **Step 2: 确认 RED**
- [ ] **Step 3: 最小实现**：`CURATED_CHUNK_PREFIX`、`CriteriaTable.load`（表头/CP 完整性校验 + sha256）、`curated_chunk`。
- [ ] **Step 4: 验证并提交**

```powershell
git add src/freca/ledger/criteria.py tests/test_ledger_criteria.py FRECA_41CP_评分标准_最终合并版_材料并入.xlsx
git commit -m "feat: 增加人工评分标准xlsx解析"
```

### Task 3: RubricConfig 增加 source 与 criteria_xlsx

**Files:**
- Modify: `src/freca/ledger/config.py`
- Modify: `tests/test_ledger_rubric.py`

- [ ] **Step 1: 写出失败测试**：curated 无 xlsx 报 ValidationError；policy 带 xlsx 报 ValidationError；from_yaml 相对路径按 yaml 目录 resolve。
- [ ] **Step 2: 确认 RED**
- [ ] **Step 3: 最小实现**：`RubricSource` 枚举 + 双向 model_validator + from_yaml resolve。
- [ ] **Step 4: 验证并提交** — `pytest -q tests/test_ledger_rubric.py tests/test_ledger_v2_profiles.py`

```powershell
git add src/freca/ledger/config.py tests/test_ledger_rubric.py
git commit -m "feat: 增加Ledger rubric来源配置"
```

### Task 4: Stage B curated 生成路径

**Files:**
- Modify: `src/freca/ledger/rubric.py`
- Modify: `src/freca/ledger/pipeline.py`
- Modify: `tests/test_ledger_rubric.py`

- [ ] **Step 1: 写出失败测试**（复用 FakePolicyIndex/StubJsonClient）：伪 chunk 入 `policy_chunk_ids`；>1,800 字标记在生成器 user prompt 与存储 snippets 存活；检索 queries 与 policy 臂逐字一致；两臂 hash 不同；缺 CP 行走异常路径；`rubric_version == "rubric-curated-v1"`。
- [ ] **Step 2: 确认 RED**
- [ ] **Step 3: 最小实现**：`RUBRIC_CURATED_PROMPT_VERSION`、`_CURATED_SYSTEM`、`RubricGenerator.criteria` 字段与 `prompt_version` property、generate 空检索守卫后前置伪 chunk、`_render_policy`/`_snippets` 前缀豁免、`rubric_input_hash` 加 prompt_version 参数；`build_rubrics` 加载 CriteriaTable。
- [ ] **Step 4: 验证并提交** — `pytest -q tests/test_ledger_rubric.py tests/test_ledger_criteria.py tests/test_ledger_review.py tests/test_ledger_gates.py`（字面量扫描必须绿）

```powershell
git add src/freca/ledger/rubric.py src/freca/ledger/pipeline.py tests/test_ledger_rubric.py
git commit -m "feat: 增加Ledger人工评分标准rubric来源"
```

### Task 5: 复核 pass 豁免 curated 截断

**Files:**
- Modify: `src/freca/ledger/review.py`
- Modify: `tests/test_ledger_review.py`

- [ ] **Step 1: 写出失败测试**：`compact_rubric` 截断 `policy-1` 而完整保留 `curated:` 前缀 snippet。
- [ ] **Step 2: 确认 RED** → **Step 3: 最小实现**：前缀跳过截断（compact_pack 的 verbatim 限制不动）→ **Step 4: 验证并提交**

```powershell
git add src/freca/ledger/review.py tests/test_ledger_review.py
git commit -m "fix: 复核pass保留人工评分标准全文"
```

### Task 6: 汇报页金标数量改为按评测派生

**Files:**
- Modify: `src/freca/method_report.py`
- Modify: `tests/test_method_report.py`

- [ ] **Step 1: 写出失败测试**：`gold_count: 37` 的评测使 HTML 含 "37 条" 与 "/ 37" 且不含 "/ 34"；无评测时显示 "—"。
- [ ] **Step 2: 确认 RED** → **Step 3: `_gold_total` 取各行 gold_count 的 max** → **Step 4: 验证并提交**

```powershell
git add src/freca/method_report.py tests/test_method_report.py
git commit -m "fix: 汇报页金标数量改为按评测派生"
```

### Task 7: 两个 curated profile

**Files:**
- Create: `config.ledger.minimax.curated-na-gate.yaml`
- Create: `config.ledger.minimax.curated-conflict-critic.yaml`
- Modify: `tests/test_ledger_v2_profiles.py`

- [ ] **Step 1: 写出失败测试**：curated profile 与基线只差 (source, criteria_xlsx) 一组旋钮，其余 ledger 设置全等。
- [ ] **Step 2: 确认 RED** → **Step 3: 添加 profile（无密钥）** → **Step 4: 验证并提交**

```powershell
git add config.ledger.minimax.curated-na-gate.yaml config.ledger.minimax.curated-conflict-critic.yaml tests/test_ledger_v2_profiles.py
git commit -m "feat: 增加人工评分标准实验profile"
```

### Task 8: 运行四条 v4、评测、对比、HTML 与汇总

**Files:**
- Generate: `build/method-runs/ledger-{na-gate,conflict-critic,curated-na-gate,curated-conflict-critic}-gold-v4/`
- Generate: `build/evaluation/ledger-*-gold-v4.json`、`build/method-comparison/gold-v4.json`、`build/reports/gold-v4-method-selection.html`
- Create: `docs/method-runs/gold-v4-summary.md`

- [ ] **Step 0: 跑前向用户确认 MiniMax 配额（约 330-360 次调用）**
- [ ] **Step 1: 顺序执行四条 `method ledger`（`--max-workers 1`，run-id 隔离）**；抽查 curated 臂 `rubrics/CP26.json`（chunk_ids 含 `curated:CP26`、snippets 全文、`rubric-curated-v1`）与 PDF 臂（`rubric-v1`）。
- [ ] **Step 2: `method evaluate` ×4 → `method compare`（仅四条 v4）→ `method report`**
- [ ] **Step 3: 写 `docs/method-runs/gold-v4-summary.md`**（评测边界/结果表/curated vs PDF 逐 CP 差异/v3 历史对照注明口径不同/结论/产物）
- [ ] **Step 4: 提交**

```powershell
git add docs/method-runs/gold-v4-summary.md
git commit -m "docs: 记录Ledger v4双rubric金标对比"
```

## Plan self-review

- Spec coverage: Task 1 对应 Gold 刷新；Task 2-5 对应伪 chunk 注入与三处豁免；Task 6 汇报派生；Task 7-8 双口径对比与交付。
- Scope: 只跑 37 条 consensus-v2；不改检索、不改 gates 语义、不写 case/CP 特判、Gold 与 Act 层参考不进提示词、不推送远端。
- 类型一致性: 全部经现有 `method ledger/evaluate/compare/report` 合约产出；报告仅读取这些 JSON。
- Placeholder scan: 未包含 TODO、TBD 或未定义的代码行为。
