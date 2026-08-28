# 人工评分标准 rubric 来源与 Gold v4 对比设计

## 目标

在法规侧定稿（`FRECA_41CP_评分标准_最终合并版_材料并入.xlsx`，41/41 CP）后，为 Ledger 增加第二种 Stage B rubric 来源——人工评分标准，与现有 PDF 检索派生来源在刷新后的 37 条 Gold 共识上做双口径 × 双 profile 的隔离对比，产出 `gold-v4.json` 排名与 HTML 汇报，回答"正式方案用哪个法规口径"。

## 已知问题

- 团队法规侧产出（最终合并版评分标准）只被王博的 deepseek 流水线消费过（v6 全量 4,100 格，样本一致率 81%）；本仓 Ledger 的 rubric 仍完全由 Rules 原始 PDF 检索 + LLM 生成，两个法规口径未在同一评测器下同台比较。
- `gold/consensus-v1.json` 停在 34 条确认标签；8/26 共识表新增 CP24-S065=1、CP26-S065=0、CP26-S074=0 共 3 条。
- 评分标准全文按 CP 长达 725-6,129 字符，而 rubric 链路存在三处截断：生成器提示词 `_render_policy`（1,800）、存储 `policy_snippets`（1,800）、复核 `compact_rubric`（1,200）。直接喂入会得到残缺 rubric。

## 方案比较

1. **伪 chunk 注入 + 前缀豁免截断（采用）**：curated 模式下向检索结果前置一个 `curated:{cp_id}` 伪 chunk（红线 R3 + 评分标准全文，排除 Act 层参考），换用 curated 系统提示词与 `rubric-curated-v1` 版本号；`curated:` 前缀在三处截断点全部豁免。检索链路逐字不变，引用契约（citations ∈ policy_chunk_ids）对伪 chunk 自动成立，下游 Stage C-E 零改动——消融严格单变量。
2. 全局调大 `snippet_char_limit`：实现最省，但会同时放大 PDF 臂 132 个真实 chunk 中 77 个超限 chunk 的可见度，两臂同时变化，无法归因。
3. 直构 rubric（不经 LLM）：把评分标准按合规/不合规/N/A 章节拆成 criteria。两批来源（v4.2 / 核验修正版）文本结构不统一，解析脆弱，且丢失 criteria-facts 接线与引用充实。

本轮只实现方案 1。

## 设计

### Gold 刷新

- `gold/consensus-v2.json`：v1 的 34 条原样保留并补 `re_number`（23→RE-NSW-2020-0144、35→RE-WA-2021-0077、38→RE-NSW-2021-0177、65→RE-NSW-2021-0222、74→RE-NSW-2020-0088），追加 3 条新共识；`GoldLabel` 增加可选 `re_number` 字段（向后兼容，`extra="forbid"` 不受影响）。

### 评分标准解析（新模块 `freca.ledger.criteria`）

- `CriteriaTable.load(path)`：openpyxl 只读解析；校验六列表头逐字一致、CP1..CP41 齐全且唯一；文件 sha256 复用 `freca.manifest.sha256_file`。
- `curated_chunk(entry, ...)`：构造 `EvidenceChunk`，content = 红线 R3 + 评分标准全文；**Act 层参考列永不入模型**（团队口径）；`flags=["curated"]`，chunk_id 前缀 `curated:`。
- 模块内不含任何 CP 编号或 verdict 字面量（延续 rubric.py 的防答案硬编码纪律）。

### Stage B 注入（`freca.ledger.rubric`）

- `RubricConfig` 增 `source: policy|curated`（默认 policy）与 `criteria_xlsx`；双向校验；`LedgerConfig.from_yaml` 按 yaml 目录 resolve。
- curated 模式：空检索守卫之后、`rubric_input_hash` 之前前置伪 chunk（内容自然进入哈希）；`RUBRIC_CURATED_PROMPT_VERSION` 贯穿 generator_identity / rubric_version / degraded 版本串，两臂缓存不可能互撞。
- `_CURATED_SYSTEM`：声明 `curated:` chunk 为本 CP 人工评分标准（authoritative），其余 chunk 为底层法规供引用充实；引用契约不变。
- `_render_policy` / `_snippets` 对 `curated:` 前缀跳过截断；`compact_rubric`（review.py）同样豁免。

### Profile 与运行

- `config.ledger.minimax.curated-na-gate.yaml`、`config.ledger.minimax.curated-conflict-critic.yaml`：对基线只改 rubric 行。
- 四条隔离 run：`ledger-na-gate-gold-v4`、`ledger-conflict-critic-gold-v4`、`ledger-curated-na-gate-gold-v4`、`ledger-curated-conflict-critic-gold-v4`，全部 37 条 consensus-v2、`--max-workers 1`。
- `gold-v4.json` 只收四条 v4 run；v3 及更早的 34 条口径历史仅进汇总文档对照表，注明分母不同不可直接混排。

## 验收

1. 单元测试：>1,800 字标记串在生成器提示词、存储 snippets、`compact_rubric` 之后三处全部存活；policy 臂真实 chunk 在三处仍按原 limit 截断。
2. 检索 queries 在两臂逐字一致（单变量消融）；`rubric.py` 字面量扫描测试保持绿。
3. `consensus-v2.json` 37 条、键唯一、`(65,CP24)/(65,CP26)/(74,CP26)` 就位；v1 文件原样保留。
4. 四条 run 的 evaluation coverage 分母均为 37；curated 臂 `rubrics/CP*.json` 的 `policy_chunk_ids` 含 `curated:{cp}`、`rubric_version=="rubric-curated-v1"`，PDF 臂仍为 `rubric-v1`。
5. HTML 汇报金标数量按评测 JSON 的 `gold_count` 派生，不再硬编码 34。
