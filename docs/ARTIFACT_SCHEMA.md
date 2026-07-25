# 关键产物 Schema

## 检索 bundle

`build/retrieval/{case_id}/{cp_id}.json` 或消融产物中的 `bundle` 包含：

- `case_id/cp_id`
- `policy_hits/evidence_hits`：chunk、最终分数、最终排名、`score_trace`
- `rounds`：初始轮与最多两轮修复
- `complete/stop_reason`

每个 `RetrievalRound` 包含：

- 本轮 `policy_query/evidence_query`
- 新增法规/案例 chunk ID
- Agent 缺口、完整结构化决定和 `gate_flags`
- 本轮生效的 Track/内容类型过滤器
- `policy_candidate_trace/evidence_candidate_trace`

候选轨迹可能包含 `bm25`、`vector`、`rrf` 或 `weighted_fusion`、`reranker`、`relevance`、`max_similarity`、`source_penalty`、`track_penalty`、`location_penalty`、`coverage_penalty`、`mmr`、`selected` 和 `reason`。只记录实际执行的召回/融合阶段，不用零值冒充未执行阶段。

## 污染证据 trace

当 chunk 携带 `exclude_from_compliance_evidence` flag 时，`evidence_candidate_trace` 会出现 `{"selected": false, "reason": "contaminated_excluded_evidence"}` 记录。该 chunk 不会出现在 `evidence_hits` 列表中，从而裁决 prompt 与 `validate_citations` 都看不到它的支持性引用。Verfier 与一致性门禁可从 trace 反查到它仍然在原索引中，只是被默认隔离。

当案例上有任何被识别的污染 Track 时，`audit_artifact_path` 之外的 `shared_facts._establishment_name_vs_case` / `_registered_commodity_vs_case` 会出现在 `consistency/{run_id}.json`，触发选择性仲裁。

## 消融任务

成功任务：

```json
{
  "status": "COMPLETED",
  "experiment_id": "smoke-001",
  "variant": "bm25_only",
  "retrieval_config": {},
  "bundle": {},
  "metrics": {}
}
```

失败任务保存 `case_id/cp_id/retrieval_config/error_type/error`，不生成空 bundle。`summary.json` 按变体聚合完成数、失败数、数值指标均值和停止原因计数。

## 模型日志与秘密

结构化模型调用缓存位于 `build/cache/models/{client}/`，调用账本位于 `build/logs/model-calls.jsonl`。元数据只记录 endpoint、模型名、响应格式和请求摘要；API key 从环境变量读取，不写入产物。

## 案例 manifest

`build/manifests/cases.json` 现在包含 `contaminated_tracks` 字段（`{track_number: relation}`）与 `metadata.expected_establishment_name`。`flags` 中以 `track_contaminated:N:relation` 形式记录每个污染 Track。`signature_foreign` flag 表示该案至少有一个 Track 被别家农场的证据替换。
