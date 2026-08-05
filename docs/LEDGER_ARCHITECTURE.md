# 事实账本审计架构（Ledger Architecture）

> 本文档说明 `freca.ledger` 这套**并行架构**的实现形态。
> 设计动机与论证见 [`STRUCTURED_RUBRIC_AUDIT_PROPOSAL.md`](STRUCTURED_RUBRIC_AUDIT_PROPOSAL.md)（下称"提案"，本文的 §n 引用均指该提案章节）。
> 旧架构说明见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 一句话

**每案一次结构化事实抽取（Stage A） → 每 CP 运行时检索法规生成可引用 Rubric（Stage B） → 紧凑证据包（Stage C） → 基于账本裁决（Stage D） → 门禁 + 五维评分 + 条件复核（Stage E）**，最后按 §8 把产物分成三类，各自带免责声明。

## 与旧架构的关系

`freca.ledger` 是**加法**，不是替换：

- 不改动 `freca.pipeline` 的任何一行；两套栈共享同一份解析产物、同一套索引、同一个 `build/` 目录、同一套 `1 / 0 / N/A` 词汇。
- ledger 栈**只写** `build/ledger/**`，因此两套可以先后跑、直接 diff。
- 切换架构是配置选择，不是改代码：

```bash
# 旧架构
freca --config config.yaml full --run-id r1

# 事实账本架构
python -m freca.ledger --config config.ledger.yaml run --run-id r1
```

"加法"是可验证的：这套架构落地后 `git status` 里**没有任何 modified 条目**，全部是新增未跟踪文件（`src/freca/ledger/`、`tests/test_ledger_*.py`、`tests/ledger_helpers.py`、`config.ledger.yaml`、本文档）。

`config.ledger.yaml` 中 `ledger:` 块以上的部分与 `config.yaml` 刻意保持一致——`PipelineConfig` 拒绝未知键，所以 `LedgerConfig.from_yaml` 会在校验前把 `ledger:` 块**切下来**再分别校验。这样同一份 YAML 对两个 CLI 都有效。

---

## 1. 主线流转

```
                        ┌──────────────────────────────────────────┐
  9 份案例材料 ────────▶ │ Stage A  extraction.py + contradictions  │
  (parsed chunks)       │  事实 / 矛盾 / 原始定位 / 完整性说明      │
                        └───────────────┬──────────────────────────┘
                                        │ CaseFactLedger（每案一份，可缓存复用）
                                        │
  CP 官方原文 ─────────▶ ┌──────────────┴───────────────────────────┐
                        │ Stage B  rubric.py                        │
                        │  运行时检索法规 → 带条款引用的 Rubric      │
                        └───────────────┬──────────────────────────┘
                                        │ CheckpointRubric（每 CP 一份，按输入哈希缓存）
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │ Stage C  selection.py                     │
                        │  按 Rubric 条目挑事实 → EvidencePack       │
                        └───────────────┬──────────────────────────┘
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │ Stage D  adjudicate.py                    │
                        │  1 / 0 / N/A + 双重引用 + 适用性理由       │
                        └───────────────┬──────────────────────────┘
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │ Stage E  gates.py + scoring.py + review.py│
                        │  硬门禁 / 五维评分卡 / 条件独立复核         │
                        └───────────────┬──────────────────────────┘
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │ §8  baseline.py  三类产物 + 免责声明        │
                        └──────────────────────────────────────────┘
```

一个 `case × CP` 任务的产物是一条 `TaskOutcome`，里面同时保留 primary 决策、（可选的）review 决策、两次门禁报告、评分卡与最终 `final`。**中间态不丢弃**，因为复核和答辩都要看链路。

---

## 2. Stage A —— 结构化事实账本（§4）

模块：`extraction.py`（753 行）、`contradictions.py`、`taxonomy.py`、`leakage.py`

**每案只跑一次**，与 CP 无关，因此 41 个 CP 复用同一份账本。

### 抽取模式

`ledger.extraction.mode` 三选一：

| 模式 | 行为 |
| --- | --- |
| `deterministic` | 完全不调模型；按段落切分产出段级事实。**全离线可跑** |
| `llm` | 只用模型；没有凭据就直接失败 |
| `llm_with_fallback` | 有凭据用模型，没有就退回确定性抽取（默认） |

确定性通路不是"应急兜底"，而是**一等结果**：整个 ledger 测试套件不需要任何凭据即可全绿。

### 事实记录（`FactRecord`）

每条事实带：`fact_id`、`verbatim`（原文片段）、`source_locator`（文件 / track / chunk）、`topic`、`evidence_categories`、`quality_flags`。

三条硬约束：

1. **原文可核**：`require_verbatim_match: true` 时，引文若无法在自己声称的 chunk 里找回，就打 `verbatim_not_found_in_source`，而不是默默相信。
2. **极性只有一个合法值**（§4）：`FactPolarity` 枚举**只有** `UNDECIDED`。抽取阶段记录事实，不判断它对 CP 有利还是不利——这件事只在 Stage D 结合 Rubric 才发生。提案里那个啰嗦字面量 `supporting_or_contrary_not_decided` 作为输入别名被接受，但归一化成 `undecided`。
3. **可引用性是派生属性**：

   ```python
   citable_for_support = (not is_answer_like) and (not is_contaminated)
   ```

   注意这是"能否**作为支持证据**被引用"。被污染的事实（例如境外机构文书）**仍然保留在包里、仍可作为反证**，只是永远不能拿来支撑"合规"。

### §3 红线：答案泄露防护

`leakage.py` 用 9 组保守正则识别 Track 3 的场景编排元数据：`Audit scenario:`、`NOTE: NON-COMPLIANT`、`expected answer`、`ground truth`、`verdict: 1` 等。命中后：

1. 事实打 `answer_like_field`；
2. Stage C 默认把它排除出裁决包（`include_answer_like: false`）；
3. Stage E 的 `ANSWER_LIKE_SUPPORT` 门禁拒绝任何倚赖它的判定。

检测只做"移掉捷径"，**从不自己改判**。

### 通用主题分类

`taxonomy.py` 只描述"这份材料装的是什么信息"：`registration` / `facilities` / `sanitation_pest` / `records` / `traceability_quarantine` / `personnel` / `unclassified`。

它是**归档系统，不是答案表**——模块里不出现任何 CP id，也不存在"出现主题 X ⇒ CP_n 判 1"的规则。合规含义要等到运行时由 `rubric.py` 从检索到的官方条文里推导。

### 矛盾检测

`contradictions.py` 确定性地找同一账本内的冲突，四类：`same_topic_conflict`、`identity_mismatch`、`missing_record`、`cross_document_value`。矛盾**随证据包一起传给裁决者**，不预先消解。

---

## 3. Stage B —— 运行时法规 Rubric（§5）

模块：`rubric.py`（516 行）

对每个 CP：

1. `build_policy_queries(checkpoint)` —— **只用 CP 官方措辞**构造 3 条检索式（原文 / 要素 / 关键词），不掺任何人工先验。
2. `retrieve_policy_context(...)` —— 三条检索式并集，同一 chunk 取最高分，留下检索 trace。
3. `RubricGenerator.generate(...)` —— 让模型基于**检索到的条文**产出可引用的 Rubric，返回 `(CheckpointRubric, from_cache)`。

### Rubric 条目

`RubricCriterion` 四种 `kind`：`applicability`（适用性）、`supporting`（支撑）、`contrary`（反证）、`exception_timing`（例外/时点）。每条必须挂上真实检索到的条款 id。

### §3 诚实性：宁缺毋修

- 引用了检索结果里不存在的条款（如 `policy-999`）的条目 → **直接丢弃**，不做"修补"。
- 缺失 `applicability` / `supporting` 类型时，从 CP 官方原文自动补一条兜底条目——用的仍是官方措辞。
- 完全无法落地（模型不可用、响应不可用、索引为空）→ 产出**降级 Rubric**：`generator["degraded"] = <原因>`，版本号打成 `rubric-v1:degraded`。
- 降级会把评分卡上限压到 ≤ 0.5，并触发 `RUBRIC_DEGRADED` 复核信号。
- `rubric.py` 里不允许出现任何 CP id 字面量或判定字面量——`tests/test_ledger_rubric.py::test_the_module_contains_no_checkpoint_specific_answer_map` 用正则守着这条红线。

### 缓存

`rubric_input_hash(checkpoint, chunks, model)` 与检索顺序无关，但对条文内容、模型签名、CP 文本任一变化敏感。命中则直接复用（`cache_enabled: true`），法规索引一变就自动重建。

---

## 4. Stage C —— 紧凑证据包（§5.4）

模块：`selection.py`（394 行）

`build_evidence_pack(...)` 的选取顺序：

1. **每条准则的覆盖下限**先满足（`min_facts_per_criterion: 2`），避免窄准则被热门事实挤掉；
2. 再做全局排序补齐到 `max_facts: 28`；
3. `include_all_contradictions: true` 时矛盾全量带上。

`score_fact` 的加分项是词面匹配 + `topic_bonus: 1.5` + `category_bonus: 1.0`；被污染的事实**只在 `contrary` 类准则上加分**。

### 证据包不携带任何判定

这是 Stage C 的核心不变量：`PackedFact` 里没有 verdict、没有 status，`polarity` 始终是 `undecided`。包里只有：

- `matched_criteria` / `match_reasons`（可归因，不是黑盒打分）
- `uncovered_criteria`（**如实上报**没覆盖到的准则，不掩盖）
- `selection_trace`（可复现）
- `excluded_fact_count`
- 完整性说明（如 `missing_tracks:6,7`）与矛盾清单

`citable_fact_ids(pack)` 返回的是 `citable_for_support` 为真的事实——被污染的事实**不在其中**，即使它留在包里。

`compact_pack(...)` 用于复核时压缩上下文（截断以 `…[truncated]` 标注），但**不改变账本计数**。`render_pack(...)` 输出人可读版本，含 `RUBRIC / POLICY / FACTS / CONTRADICTIONS / COVERAGE` 分区，被污染的条目前缀 `⚠CONTAMINATED`，每条都带源定位。

选取过程是确定性的：同样输入，同样的包。

---

## 5. Stage D —— 裁决（§5.5、§7）

模块：`adjudicate.py`（426 行）

`Adjudicator.adjudicate(...)` 产出 `LedgerDecision`：`verdict`（`1` / `0` / `N/A`）、`applicability`、`policy_citations`、`cited_fact_ids`、`criterion_outcomes`、`evidence_coverage`、`confidence`、`quality_flags`。

### 保守归一化

`normalize_decision(...)` 只做**收紧方向**的修补，每次修补都留痕：

| flag | 含义 |
| --- | --- |
| `dropped_answer_like_support` | 用答案泄露字段做支撑 → 剔除该引用 |
| `na_withdrawn_no_policy_basis` | N/A 但没引条款 → 撤回 N/A 主张 |
| `na_withdrawn_no_reasoning` | N/A 但没写适用性理由 → 撤回 |
| `missing_policy_citation` / `missing_case_citation` | 引用缺失 |

### 阻断是一等结果，不是异常

没有配置模型客户端、或调用失败时，`blocked_decision(...)` 返回一条**占位记录**：

```
verdict = NON_COMPLIANT, applicability = UNKNOWN,
quality_flags = ["adjudication_blocked"], confidence = 0.0
```

它会被 `ADJUDICATION_BLOCKED` 门禁立刻标红，并在 §8 分类里被排除出投票。这样"没跑成"和"跑出来判 0"在数据上是可区分的，不会污染统计。

---

## 6. Stage E —— 门禁、评分卡、复核（§6、§7）

### 6.1 硬门禁（ERROR，会让 `passed = False`）

`gates.py` 的 `evaluate_gates(...)`：

| 门禁码 | 触发条件 |
| --- | --- |
| `ADJUDICATION_BLOCKED` | 裁决没有真正执行 |
| `MISSING_POLICY_CITATION` | 非 N/A 判定没有法规引用 |
| `MISSING_CASE_CITATION` | 非 N/A 判定没有本案事实引用 |
| `NA_WITHOUT_NOT_APPLICABLE` | N/A 判定但 applicability 不是 `NOT_APPLICABLE` |
| `NA_WITHOUT_POLICY_BASIS` | N/A 没引"使其不适用"的条款 |
| `NA_WITHOUT_APPLICABILITY_REASONING` | N/A 只说"证据不足"，没讲适用性 |
| `APPLICABILITY_INCOHERENT` | `NOT_APPLICABLE` 配了实质判定 |
| `ANSWER_LIKE_SUPPORT` | 判定倚赖答案泄露字段 |
| `CITATION_NOT_TRACEABLE` / `CITATION_UNRESOLVED` / `CITATION_FOREIGN_CASE` | 引用无法回溯 / 指向包外 / 指向其它案例 |
| `POLICY_CITATION_OUT_OF_RUBRIC` | 引了 Rubric 之外的条款 |
| `RUBRIC_CRITERION_UNGROUNDED` / `RUBRIC_MISSING_POLICY_BASIS` | Rubric 本身没落地 |

§7 的双重引用要求就落在这里：**除 N/A 外，任何判定必须同时给出法规引用与本案事实引用**；N/A 必须是 `NOT_APPLICABLE` + 条款 + 适用性理由三者齐备。

### 6.2 复核触发信号（WARNING，不阻断但升优先级）

`RUBRIC_DEGRADED`、`EMPTY_EVIDENCE_PACK`、`EVIDENCE_INSUFFICIENT`、`UNCOVERED_CRITERIA`、`KEY_FACTS_MISSING`、`MISSING_RECORDS`、`SAME_TOPIC_CONFLICT`、`IDENTITY_MISMATCH`、`CROSS_DOCUMENT_VALUE_CONFLICT`、`COMPLIANT_WITHOUT_SUPPORT`、`COMPLIANT_WITH_CONTRARY_FACTS`、`COMPLIANT_ON_FOREIGN_PAPERWORK`、`NON_COMPLIANT_WITHOUT_CONTRARY`、`VERDICT_REASONING_INCONSISTENT`、`LOW_CONFIDENCE`、`NORMALIZATION_REPAIRS`、`CITATION_VERBATIM_UNVERIFIED`、`NA_WITHDRAWN`。

`gate_flags(report)` 把门禁发现渲染成 `gate:<code>` 形式的质量标记贴到决策上——**它们描述证据问题，永远不用来翻转 `1 / 0 / N/A`**。

### 6.3 五维评分卡（§6：不做加权总分）

`scoring.py` 的 `build_scorecard(...)` 产出五个 `[0, 1]` 独立维度：

| 维度 | 衡量 |
| --- | --- |
| `regulatory_coverage` | Rubric 准则被实际处理的比例 |
| `support_coverage` | 支撑类准则的取证充分度 |
| `contrary_strength` | 反证信号强度 |
| `citation_quality` | 引用可回溯性与原文可核性 |
| `evidence_integrity` | 材料完整性、矛盾、污染惩罚 |

`EvidenceScorecard` **刻意不提供加权总分**——这些数值只表达证据质量与复核优先级，绝不能被合成"≥ 80 判合规"的阈值。`review_priority` 是排队用的单一标量，由评分卡、错误数、触发数、置信度共同决定，它决定"先看谁"，不决定"判什么"。

### 6.4 条件独立复核（§7）

`review.py`。`ReviewConfig.mode` 三选一：`disabled` / `on_trigger`（默认，看 `gate.needs_review`）/ `always`。

复核走**压缩上下文**：`compact_rubric` + `compact_pack`（`max_facts: 14`、`snippet_char_limit: 1200`），保留引用契约但去掉长文。

`choose_final(...)` 的合流是**保守的**——共 8 个分支，但铁律只有一条：

> **被阻断的、或门禁未通过的复核结果，永远不能覆盖一个通过门禁的主判定。**

分支包括 `ACCEPT_PRIMARY`、`REVIEW_BLOCKED`、`REVIEW_FAILED_GATES`、`PRIMARY_FAILED_GATES`、`CONFIRMED`、`ON_CONFLICT` / `REVIEW_ON_CONFLICT`（受 `prefer_review_on_conflict` 控制）、`ESCALATE_BOTH_GATES_FAILED`。无论走哪条，**最终决策一定是已产出的那两条之一**，不会凭空造第三个答案。

`resolve(...)` 往 `final.quality_flags` 追加 `resolution:<分支>`、`independent_review_performed`、`review_agreed_with_primary` / `review_disagreed_with_primary`、`review_triggers:`（最多 6 条），并且**不修改任何入参**。

---

## 7. §8 —— 三类产物与免责声明

模块：`baseline.py`（384 行）

`build_baseline_report(...)` 把结果分成三类，**互不冒充**：

| `ArtifactClass` | 含义 |
| --- | --- |
| `evidence_integrity_qa` | 证据完整性质检。**从不输出判定标签** |
| `silver_consistency` | 多方法一致性银标。有严格准入条件 |
| `production_candidate` | 生产候选答案 |

### 独立证据视角要求

这是 §8 最关键的约束。`EvidenceView.view_signature()` = `model_signature | context_construction | retrieval_scope`。

- 单一方法**不能**自己构成一致性集合 → `too_few_agreeing_methods`；
- 两个方法共享同一视角签名 → `shared_evidence_view`，拒绝计为独立投票者；
- 只有视角确实不同（`require_distinct_views: true`、`min_distinct_views: 2`）且都通过引用完整性检查（有法规引用 + 本案引用 + 门禁干净；N/A 还要有适用性理由）才准入；
- 其它拒绝码：`methods_disagree`、`no_citation_complete_vote`。

两个方法来源：`method_from_outcomes(...)`（视角 `ledger-adjudicator | rubric+fact-pack | policy-index+case-fact-ledger`）与 `method_from_legacy_finals(...)`（读 `build/final/*/CP*.json`，跳过损坏文件，视角与前者不同）。这正是"两套架构并行"的实际收益：它们天然构成两个不同的证据视角。

### 四条固定免责声明

```
不得直接充当业务 1/0 标签
不声称官方准确率或真值
不取代官方金标
共享同一模型与同一上下文构造的方法不计为独立投票者
```

---

## 8. 离线 / 降级 / 阻断：三种一等结果

整套设计的一个刻意选择是：**在没有任何模型凭据的环境里，全流程仍然跑得通，并且结果是可解释的，而不是抛异常。**

| 阶段 | 无凭据时的行为 | 数据上的痕迹 |
| --- | --- | --- |
| A 抽取 | 退回确定性段级抽取 | 抽取器签名标注模式 |
| B Rubric | 降级 Rubric | `generator.degraded`、版本 `rubric-v1:degraded`、评分上限 0.5 |
| D 裁决 | `blocked_decision` | `adjudication_blocked` flag、confidence 0.0 |
| E 门禁 | 立即 ERROR | `ADJUDICATION_BLOCKED` |
| §8 分类 | 排除出投票 | 不进入 silver |

因此全部 ledger 测试都可以离线跑。

---

## 9. 产物布局

```
build/ledger/
├── facts/       CaseFactLedger（每案一份）+ 抽取 trace
├── rubrics/     CheckpointRubric（每 CP 一份）+ 检索 trace
├── packs/       EvidencePack（case × CP）
├── outcomes/    TaskOutcome（primary + review + 两份门禁 + 评分卡 + final）
├── final/       LedgerDecision（最终决策，与旧架构同构）
├── state/       durable TaskStore（支持 retry / status）
├── runs/        run 级报告
├── baseline/    §8 分类报告
├── cache/models/
└── logs/model-calls.jsonl
```

`output_dirname: ledger` 可改，但默认就落在 `build/ledger/`，与旧架构的 `build/final/` 物理隔离。

---

## 10. CLI

```bash
python -m freca.ledger --config config.ledger.yaml <command>
```

前提是按 README 做过可编辑安装（`python -m pip install -e ".[dev]"`）。若当前虚拟环境未安装本包，等价写法是：

```bash
PYTHONPATH=src ./.venv/Scripts/python.exe -m freca.ledger --config config.ledger.yaml describe
```

| 命令 | 作用 |
| --- | --- |
| `describe` | 打印解析后的完整配置 |
| `facts` | Stage A，`--case-id` / `--force` / `--max-workers` |
| `rubrics` | Stage B，`--cp-id` / `--max-workers` |
| `audit --run-id` | Stage C–E |
| `gates` | 只读地对已存 outcome 重跑门禁，按 `review_priority` 倒序给 top 20 |
| `baseline --run-id` | §8 分类（`--no-legacy` 可只用 ledger 方法） |
| `inspect --case-id --cp-id` | 打印单个 `case × CP` 的完整 outcome |
| `status --run-id` | 任务状态计数 |
| `retry --run-id` | 把 `blocked` / `failed` 重置为 pending |
| `assemble --run-id` | 写提交工作簿 |
| `run --run-id` | Stage A→E + §8，一条命令跑完 |

退出码：正常 `0`，有阻断/失败/门禁不过 `2`，配置或路径错误 `2`（stderr 打 `ERROR: ...`）。

`run` 的短路逻辑：Stage A 一份账本都没建成 → `status: BLOCKED`；Stage B 一份 Rubric 都没建成 → `status: BLOCKED`；Stage C–E 有 blocked/failed → `status: INCOMPLETE`；全绿才 `COMPLETED`，且只有 `COMPLETED` 才允许 `--assemble`。

前置条件：`load_policy_index` 需要 `build/indexes/policy.json` 存在（由旧架构的解析/建索引阶段产出，两套栈共用）。

---

## 11. 与旧架构互操作

`pipeline.to_audit_decision(...)` 把 `LedgerDecision` **无损投影**成旧架构的 `AuditDecision`，所以：

- 现有的提交装配、报表、质量脚本无需改动即可消费 ledger 结果；
- §8 可以把两套栈当成两个独立证据视角做一致性检查；
- 想回退旧架构，换个 `--config` 就行。

---

## 12. 配置速查

```yaml
ledger:
  output_dirname: ledger
  extraction:   { mode, batch_char_budget, max_chunks_per_batch, max_facts_per_batch,
                  require_verbatim_match, verbatim_min_length, drop_answer_like_facts,
                  max_workers, max_facts_per_chunk, max_facts_per_case,
                  min_segment_chars, segment_char_limit }
  rubric:       { policy_limit: 12, max_criteria: 10, snippet_char_limit: 1800,
                  cache_enabled: true, max_workers: 4 }
  selection:    { max_facts: 28, min_facts_per_criterion: 2, include_all_contradictions: true,
                  topic_bonus: 1.5, category_bonus: 1.0,
                  include_answer_like: false, include_contaminated: true,
                  verbatim_char_limit: 600 }
  adjudication: { confidence_threshold: 0.65, require_dual_citation: true, max_workers: 4 }
  review:       { mode: on_trigger, max_facts: 14, snippet_char_limit: 1200,
                  prefer_review_on_conflict: true }
  baseline:     { require_distinct_views: true, min_distinct_views: 2, min_agreeing_methods: 2 }
  models:       { extractor: null, rubric: null, adjudicator: null, reviewer: null }
```

端点回退顺序（`LedgerConfig.endpoint(stage)`）：

```
extractor / rubric / adjudicator  →  models.audit
reviewer                          →  models.verifier → models.arbitrator → models.audit
```

---

## 13. 测试地图

```
tests/ledger_helpers.py          共享构造器：make_chunk / make_fact / make_contradiction /
                                 make_rubric / make_pack / make_decision / perfect_scorecard /
                                 make_gate_report / make_ledger_config / StubJsonClient
tests/test_ledger_models.py      模型不变量（含 FactPolarity 单值、citable_for_support）
tests/test_ledger_extraction.py  Stage A 抽取、verbatim 校验、答案泄露标记
tests/test_ledger_rubric.py      Stage B 检索式、落地性、降级、缓存、§3 红线正则
tests/test_ledger_selection.py   Stage C 打分、覆盖下限、包内无判定、污染只作反证
tests/test_ledger_scoring.py     §6 五维评分卡
tests/test_ledger_gates.py       §7 硬门禁与触发信号
tests/test_ledger_review.py      Stage E 复核合流 8 分支、压缩上下文、flag 注入
tests/test_ledger_baseline.py    §8 三类产物、独立视角、免责声明
```

运行（Windows）：

```bash
./.venv/Scripts/python.exe -m pytest -q
```

当前全量（旧架构 + ledger）**290 passed**，全程无需任何模型凭据。

> 若运行环境带有"安全删除"守卫，把 `--basetemp` 指到**项目目录内**会让 pytest 在会话开始清理 basetemp 时被拦截，表现为大量 setup ERROR（不是测试失败）。此时把 basetemp 指到系统临时目录即可：
> `--basetemp="$TEMP/freca-pytest"`。

---

## 14. 红线对照表

| 提案条款 | 实现位置 | 守护方式 |
| --- | --- | --- |
| §3 不硬编码答案 | `taxonomy.py`、`rubric.py`、`leakage.py` | 模块内无 CP id / 判定字面量；测试用正则断言 |
| §4 事实极性只有未定 | `models.FactPolarity` | 枚举单值，别名归一化 |
| §6 五分不加权 | `models.EvidenceScorecard` | 不提供 total 字段 |
| §7 双重引用 + N/A 三要件 | `gates._gate_dual_citation`、`_gate_not_applicable` | ERROR 级门禁 |
| §7 复核不得越权 | `review.choose_final` | 最终值必为 primary 或 review 之一 |
| §8 三类产物 + 独立视角 | `baseline.py`、`models.EvidenceView` | `view_signature` 去重 + 四条免责声明 |

---

## 15. 非目标

- 不追求"多方法投票即真值"——这正是提案 §2 要避开的错误。
- 不替换旧架构；两者并存是特性而非过渡状态。
- 不在事实层做合规判断；合规含义一律运行时从官方条文推导。
- 不用评分阈值代替判定；评分只排队，不定性。
