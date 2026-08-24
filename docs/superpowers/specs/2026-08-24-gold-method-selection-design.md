# Gold 驱动的方法选择实验设计

## 目标

在团队已确认的 34 条 case×CP 共识标签上，比较 FRECA Task2 的检索、裁决和事实账本链路，选出后续 pilot 的唯一候选方法。Gold 仅用于离线评分，绝不作为模型输入。

## 范围与边界

- **评测集合**：`gold/consensus-v1.json` 中 `confirmed=true` 的 34 条标签；它覆盖 case 023、035、038、065、074 的 CP1、12、14、15、30、33、35（CP35 对 case 035 不计标签）。
- **排除项**：CP17、CP19 和未形成共识的 CP24、CP26 不进入本轮分母。
- **模型**：沿用本地 `.env` 映射出的 MiniMax-M3 Anthropic 兼容端点；密钥不进入代码、配置样例、日志或报告。
- **禁止输入**：Gold verdict、人工汇总理由、CP↔答案级映射表、任何根据 Gold 写出的 case/CP 特判均不得进入模型上下文。
- **运行隔离**：每个候选方法有独立 `run_id`、缓存命名空间、状态文件、最终 verdict 目录与评测报告；不得覆盖其它方法的 `build/final/`。

## 成功指标

每个方法产出以下可复现指标：

1. **Gold agreement**：`matched_count / evaluated_count`，仅在有最终 verdict 的 Gold 项上计算。
2. **Gold coverage**：`evaluated_count / 34`；BLOCKED、FAILED、PENDING 均视为未覆盖，不从运行质量统计中隐藏。
3. **Terminal failure rate**：`(BLOCKED + FAILED) / 34`。
4. **分 CP 指标与错例账本**：每条不一致项必须保留 verdict、理由、引用、review flags 与终态原因。
5. **成本与时长**：请求数、模型使用量（若提供）、缓存命中数、总墙钟时间。

候选方法只有在 coverage 不低于 90%、terminal failure rate 不高于 10% 时，才按 agreement 排名。未达覆盖门槛的方法仅作诊断，不得晋级 pilot。

## 候选方法矩阵

### A. 检索消融 + 同一裁决链

这些方法只替换 `RetrievalConfig`，其余裁决、Verifier 和仲裁规则保持一致：

| 方法 ID | 检索设置 |
|---|---|
| `bm25_judge` | BM25、top-k、无重排/MMR/repair |
| `vector_judge` | 本地向量、top-k、无融合 |
| `weighted_hybrid_judge` | BM25+向量加权融合、top-k |
| `rrf_topk_judge` | RRF + 可用 reranker、无 MMR |
| `full_retrieval_judge` | 当前 RRF、来源感知 MMR 与启发式修复 |

向量端点不可用时，`vector_judge` 与 hybrid 变体必须报告为未就绪，不用 BM25 静默替代。

### B. 直连 LLM judge

从旧 `feature/direct-llm-experiments` 工作树迁移已验证的、与 MiniMax Anthropic 客户端兼容的最小执行逻辑，统一输出为当前 `AuditDecision`：

| 方法 ID | 输入 |
|---|---|
| `automatic_retrieval_judge` | 通用 BM25 选取的法规与当前 case 证据片段 |
| `checkpoint_full_judge` | 当前 CP、完整法规材料与当前 case 全量证据；仅运行 34 项 Gold |

两者都使用同一通用裁决契约、原子引用规则和污染证据约束。`checkpoint_full_judge` 是高成本上限对照，不因成本扩展到 pilot。

### C. 结构化事实账本

`ledger_judge` 使用既有 Ledger 的事实抽取、运行时 rubric、紧凑证据包、裁决和条件复核。Ledger 的最终结果必须投影成 `AuditDecision`，并复用同一 Gold 评测器。

## 共同裁决契约

所有候选方法都必须把以下团队共识作为模型无关的输出门禁和提示词要求，而非 case/CP 硬编码答案：

- 官方记录优先于企业自述；记录日期与有效期矛盾时不证明持续有效。
- 先判实际操作对象是否适用，再判证据是否足以满足条件。
- 场所级物理事实可跨货物复用；货物路线、批次、返工、重新放行和流转事实不得跨货物复用。
- 程序只证明“能做”；要求持续执行时必须有当前主体的实际记录。
- 混合文件仅在原子证据独立、主体一致时可拆分使用。
- 明确全称要求存在一条未满足即为 `0`。
- `0` 可由反证或无法证明条件满足支持；`N/A` 必须有明确的非适用依据。

## 运行顺序与停止条件

1. 离线测试并验证方法注册、任务选择、输出隔离和评分器。
2. 运行 A 组；任一未就绪组件以显式状态落盘。
3. 运行 B 组；`checkpoint_full_judge` 严格限制在 34 Gold 项。
4. 运行 C 组。
5. 生成统一排行榜与按 CP 错例报告。
6. 仅对排名第一且达 coverage/失败率门槛的方法，进行一轮由错例类别驱动的最小改动，再以新 run-id 重跑 Gold v2。
7. Gold v2 仍未达到 80% agreement 且 90% coverage 时，停止扩展；达到门槛后才申请运行 369 项 pilot。

## 产物

```text
build/method-runs/<run-id>/...             # 方法专属状态、final、缓存、日志
build/evaluation/<run-id>.json             # 与 consensus-v1 的不可变报告
build/method-comparison/gold-v1.json       # 方法排行榜和逐 CP 汇总
build/method-comparison/gold-v1-mismatches.jsonl
```

每个终态异常还要记录原始类型（FAILED/BLOCKED）、阶段、原因与可重试标记。排行榜不能把缺失项算作正确，也不能因某方法输出少而虚高。

## 非目标

- 不修改 Gold 标签或用模型自动补标签。
- 不在本轮运行 369 项或 4,100 项。
- 不因某一 CP 的 Gold verdict 编写专属 `if CP... then ...` 规则。
- 不推送、合并或更改远端仓库，除非另获明确授权。
