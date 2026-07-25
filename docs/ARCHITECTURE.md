# FRECA Audit Pipeline — 架构说明

> 本文档解释系统**整体流向**与**职责切分**。深入细节请看 `docs/` 下其它专题文档:
> [`RETRIEVAL.md`](RETRIEVAL.md)、[`AGENT_RETRIEVAL.md`](AGENT_RETRIEVAL.md)、[`ABLATION.md`](ABLATION.md)、[`ARTIFACT_SCHEMA.md`](ARTIFACT_SCHEMA.md)、[`SIGNATURE_CONTAMINATION.md`](SIGNATURE_CONTAMINATION.md)。

## 一句话

**`case × CP` 的幂等审计流水线**:解析法规与案例 → 双索引 → 检索 (Planner→Retrieval→Critic) → 审计 → 引用校验 → Verifier → 选择性仲裁 → 提交门禁。

## 主线流转(单 case × 单 CP)

```
┌─────────────────────────────────────────────────────────────────────┐
│  ① Planner          Element→Track 映射(启发式)                  │
│       ↓             产出 target_tracks / target_content_kinds     │
│  ┌─────────────────── ② Retrieval (循环) ───────────────────┐    │
│  │  HybridIndex(BM25+Vector) → RRF → Reranker → MMR → merge │    │
│  │  RetrievalAgent:Gap 评估 / STOP-RETRIEVE                  │    │
│  │  Critic:去重 + flag 答案泄露(只 flag 不 drop)              │    │
│  │  loop:max_repairs=2                                       │    │
│  └───────────────────────────────────────────────────────────┘    │
│       ↓ RetrievalBundle                                            │
│  ③ Audit        → AuditDecision(含 shared_facts / flags)         │
│       ↓ citation_validation(强校验,失败即 raise)                   │
│  ④ Verifier + Arbitrator                                           │
│     - Verifier PASS → 落盘                                         │
│     - 分歧 → ArbitrateCheckpoint(SINGLE/BLIND)                     │
│     - tier=ESCALATED → Blind → Tiebreaker → 多数票                │
│       ↓ final AuditDecision                                        │
│  ⑤ 落盘          submission.xlsx + state/{run_id}-tasks.jsonl     │
└─────────────────────────────────────────────────────────────────────┘
```

可视化:`architecture.html`(SVG,直接用浏览器打开)。

## 五大模块

| 模块 | 入口 | 职责 |
|---|---|---|
| `freca.parsing` | `pipeline` ingest 阶段 | DOCX/XLSX/PDF 解析 |
| `freca.index` | `pipeline` index 阶段 | HybridIndex(BM25 + 向量) |
| `freca.retrieval` | `retrieve_for_checkpoint` | 检索 + Planner + Critic 编排 |
| `freca.agent` | `planner / critic / escalation / memory` | 自治增强层 |
| `freca.quality` | `audit / verify / arbitrate` | 审计核心 |

**索引层**(`HybridIndex`)、**规划层**(`PlannerAgent`)、**反思层**(`Critic`)、**审计层**(`AuditDecision`)与**仲裁层**(`Verifier + Arbitrator`)——五者边界明确,不混职责。

## 配置开关矩阵

| 开关 | 默认 | 其它档 |
|---|---|---|
| `retrieval.recall_mode` | `hybrid` | `bm25` / `vector` |
| `retrieval.fusion_mode` | `rrf` | `weighted` |
| `retrieval.selector_mode` | `source_aware_mmr` | `top_k` |
| `retrieval.agent_mode` | `heuristic` | `disabled` / `llm` / `planner` / `critic` / `planner_critic` |
| `arbitration.tier` | `blind` | `disabled` / `single` / `escalated` |
| `mineru.mode` | `disabled` | `cloud_sdk` / `remote_api` |

**默认值组合 = 向后兼容**(行为与改 Tier-1/3 之前完全一致)。

## Ablation(消融)

`src/freca/ablation.py` 暴露 5 档:

| 档位 | recall | fusion | reranker | selector | agent | repair |
|---|---|---|---|---|---|---|
| `bm25_only` | bm25 | none | none | top_k | disabled | 0 |
| `vector_only` | vector | none | none | top_k | disabled | 0 |
| `weighted_hybrid` | hybrid | weighted | none | top_k | disabled | 0 |
| `rrf_reranker_no_mmr` | hybrid | rrf | rerank | top_k | disabled | 0 |
| `full_retrieval` | hybrid | rrf | rerank | mmr | base | base |

```powershell
python -m freca.cli --config config.yaml ablation run \
    --experiment-id exp-01 --variants bm25_only full_retrieval \
    --case-ids 1,2,3 --cp-ids CP1,CP17
```

## 数据隔离约束

- **case_id 强制隔离**:`policy_index` 与 `case_index` 检索时按 `case_id` 过滤;`HybridIndex.search` 已经把 `exclude_from_compliance_evidence` 标签的 chunk 物理排除
- **跨 case 立刻 raise**:`retrieval.py` 收尾若发现 evidence 命中 case_id 与请求 case_id 不符,RuntimeError 抛出
- **引用真实性**:审计完后用 `chunk_id` 反查来源,缺失/伪造/跨 case 全部阻断

## 落盘结构

```
build/
├── manifests/         ← 100 case × 898 file 元数据
├── parsed/            ← 解析产物(structured JSON,不含源文件)
├── indexes/           ← 索引(政策 + 案例)
├── retrieval/         ← 每个 (case,cp) 的 RetrievalBundle
├── decisions/         ← 每个 (case,cp) 的 AuditDecision
├── verification/      ← Verifier 报告
├── arbitration/       ← 仲裁结果
├── final/             ← 最终决策(可入 submission)
├── consistency/       ← 跨 CP / 跨 run 一致性
├── state/             ← TaskStore + failure_modes.jsonl
├── cache/models/      ← 模型调用缓存(按 client + request_hash)
├── logs/model-calls.jsonl
└── submission.xlsx    ← 最终提交表(100 行 × 42 列)
```

## 与旧版差异(避免误用)

| 维度 | 当前 | 历史(已废) |
|---|---|---|
| 案例数 | **100**(物理) | 96(已迁移) |
| 任务数 | **4,100** = 100 × 41 CP | 3,936 |
| 提交列数 | **42**(含 `submission_id` 等) | 41 |
| `case_filter.py` | **只报告结构性风险,不再筛** | 旧版会生成 N/A |
| `anomaly_report.json` | **已废,流水线不读** | 96 案例时代遗物 |
| `ablation` 变体 | **5 档**(`docs/ABLATION.md`) | 11 档 |

> 一句话:本次仓库初始化时把 ablation 从 11 缩到 5,功能等价——更细的差异用 config 调,不开新变体。
