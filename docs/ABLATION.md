# 消融实验

消融 runner 对相同的 case/CP 集合调用生产检索和 Agent 代码，只替换 `RetrievalConfig`。内置变体：

| 变体 | 作用 |
|---|---|
| `bm25_only` | BM25 + top-k，关闭其它阶段和修复 |
| `vector_only` | 向量 + top-k，关闭其它阶段和修复 |
| `weighted_hybrid` | BM25/向量加权融合 |
| `rrf_no_reranker` | RRF，不使用精排和 MMR |
| `rrf_reranker_no_mmr` | RRF + 当前 reranker，top-k |
| `full_retrieval` | 当前生产检索配置快照 |
| `full_no_agent_repair` | 完整排序链，只运行初始轮 |
| `full_heuristic_agent` | 完整排序链 + 最多两轮启发式修复 |
| `full_llm_agent` | 完整排序链 + 最多两轮结构化 LLM 修复 |

## 命令

```powershell
# 查看变体
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml ablation list

# 无网络基线烟雾实验
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml ablation run `
  --experiment-id smoke-001 --variant bm25_only --variant rrf_no_reranker `
  --case-id 1 --cp-id CP1

# 重建汇总
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml ablation report `
  --experiment-id smoke-001
```

不指定 `--variant` 会运行全部 9 个变体；`full_llm_agent`、外部 reranker 或语义 embedding 只有在对应 endpoint 和环境密钥就绪时才能成功。单个任务失败会写独立失败 JSON，并继续其它实验单元。

## 人工相关性标签

可选标签是 JSON 对象，键为三位 case ID 和 CP，例如：

```json
{
  "001:CP1": ["policy-...", "case-001_track-3_..."]
}
```

运行时传 `--relevance-labels labels.json`。所有 chunk ID 必须存在；案例 chunk 必须属于键中的 case，否则拒绝整个标签文件。有标签时计算 Recall@K 和 MRR；无标签时这两个字段为 `null`，绝不把 Track 3 的答案样文本自动当标签。

## 指标边界

无需标签即可报告命中数、法规/案例命中数、唯一来源、唯一 Track、跨案例命中、轮数、修复轮数、新增 chunk、完整性和停止原因。有人工标签时增加 Recall@K/MRR。最终 verdict accuracy 只有在另行提供可信人工 verdict 标签并运行审计结果对齐后才有意义；当前检索 runner 不伪造该指标。

产物位于：

```text
build/ablation/{experiment_id}/{variant}/{case_id}/{cp_id}.json
build/ablation/{experiment_id}/summary.json
```

