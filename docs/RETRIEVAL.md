# 检索、重排序与 MMR

生产审计和消融实验共用 `HybridIndex.search`，不存在单独的"实验版检索"。执行路径由 `retrieval` 配置逐级控制：

```text
BM25 / Vector -> none / weighted / RRF -> reranker -> top-k / MMR / source-aware MMR
                                       (隔离污染证据的二次过滤)
```

## 召回与融合

- `recall_mode`：`bm25`、`vector` 或 `hybrid`。关闭的召回器不会被调用。
- `fusion_mode=none`：单路召回分数归一化后直接使用。
- `fusion_mode=weighted`：`bm25_weight * BM25_norm + vector_weight * vector_norm`。
- `fusion_mode=rrf`：对每个排序列表累加 `1 / (rrf_k + rank)`，再按本次候选最大值归一化。

`candidate_limit` 控制进入精排的最大候选数；最终法规与案例证据预算分别由调用方的 `policy_limit` 和 `evidence_limit` 控制。

## Reranker 后端

`reranker_mode` 支持：

- `none`：融合分数直接作为 relevance。
- `lexical`：本地词项覆盖率基线，不需要网络或密钥。
- `cross_encoder_api`：调用 `{base_url}/rerank`，请求包含 `model/query/documents/top_n`。响应必须为 `results`，每个候选通过 `index`、`document.id` 或 `id` 唯一映射，并提供 `relevance_score` 或 `score`。
- `llm_listwise`：通过 JSON 模型返回 `ranking[{chunk_id, score}]`。

外部 reranker 必须恰好返回每个候选一次，禁止未知 ID、重复 ID、缺失 ID 和越界索引，分数必须位于 0–1。最终相关性为：

```text
fusion_weight * fusion_score + reranker_weight * reranker_score
```

推荐有专用服务时使用 `cross_encoder_api`，只有通用结构化 LLM 时使用 `llm_listwise`，本地无凭据开发使用 `lexical`。

## MMR

普通 MMR：

```text
mmr = lambda * relevance - (1-lambda) * max_selected_similarity
```

`source_aware_mmr` 还对同一 `source_id`、Track、页/Sheet/章节/对象位置施加可配置惩罚；未达到 `min_unique_sources` 且存在其它来源时，对重复来源增加覆盖惩罚。候选并列时按 `chunk_id` 稳定选择。

每个入选 hit 的 `score_trace` 保存实际执行阶段的分数。每轮的 `policy_candidate_trace` 和 `evidence_candidate_trace` 同时保存入选与未入选候选、MMR、相似度、各项惩罚和淘汰原因。

## 案例隔离

案例索引检索必须传 `case_id`，返回后再次验证每个 chunk 的 `case_id`。Agent 可限制下一轮的 `target_tracks` 和 `target_content_kinds`，但不能解除案例过滤。任何跨案例结果都会使当前任务失败，而不是映射成 `N/A`。

## 污染证据隔离与可见

`HybridIndex.search` 在分数阶段之前，会先把 chunk 按 `exclude_from_compliance_evidence` flag 分为两类：

```text
eligible_subset         := chunks - {exclude_from_compliance_evidence}
contaminated_subset     := {chunks | chunk has the flag}
evidence_hits          := ranked hits from eligible_subset
trace_sink entries     := {"reason": "contaminated_excluded_evidence"} per contaminated chunk
```

行为约定：

- `evidence_hits` 永远是干净 chunk，决策模型与 `validate_citations` 都看不到污染 chunk 当 `supporting_evidence`。
- 污染 chunk 仍然出现在 `trace_sink`，供 `Verifier` 与 `run_consistency_gate` 透明对照，避免悄悄丢失证据。
- 调用方可以通过 `include_excluded_evidence=True` 把污染 chunk 作为可见但仍被标记的 hit 返回；当前生产路径不开启，避免裁决 prompt 误用。
- `validate_citations` 在裁决后独立拦截 `supporting_evidence` 含污染 chunk 的情形，让 `process_audit_task` 抛 `BlockedTaskError`。

## Agent gap 与污染

当污染 Track 是某 CP 的关键证据类型时（例如 CP20 / CP21 主要看 Track 3；如果该 case 的 Track 3 被替换为别家），Agent 的 heuristic gap 检查会自然产生 `case_evidence` 缺口，触发一次修复轮。修复轮改写查询时 `target_tracks` 不允许包含污染 Track，`stop_reason` 维持 `no_new_chunks`，最终由 LLM 看到污染 notice 并裁决。
