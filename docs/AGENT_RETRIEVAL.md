# Agent 纠错检索

检索 Agent 只判断“当前上下文是否足以交给审计模型”，不判断最终 `1 / 0 / N/A`。它接收官方 CP、当前法规/案例片段、当前查询和历史轮次，返回严格结构：

```json
{
  "action": "stop | retrieve",
  "complete": true,
  "gaps": [],
  "policy_query": null,
  "evidence_query": null,
  "target_tracks": [],
  "target_content_kinds": [],
  "reason": "..."
}
```

`retrieve` 必须设置 `complete=false` 并给出两个非空查询；`stop` 必须设置 `complete=true`。LLM 提示明确禁止把 Track 3 中接近答案的文本当作标签或真值。

## 三种模式

- `disabled`: 只执行初始检索，不补检索。
- `heuristic`: 确定性完整性检查，可选调用 `query_rewriter` 改写查询；没有模型密钥时使用本地通用改写器。
- `llm`: `models.retrieval_agent` 一次结构化调用同时判断缺口、生成查询和指定 Track/内容类型。

## 机械停止门禁

模型决定不能覆盖以下规则：

1. 最多 `max_repairs=2` 次补检索，即最多三轮（初始轮 + 两次修复）。
2. 案例结果出现其它 `case_id` 时立即失败。
3. 第二轮以后没有新增 chunk 时以 `no_new_chunks` 停止。
4. 新查询与任一历史查询对完全相同时以 `repeated_query` 停止。
5. Agent 声称完整但法规或案例证据为空时拒绝停止，记录 `agent_stop_rejected_missing_context`，使用确定性查询补一次；仍无新增证据则停止。

每轮落盘 Agent 原始决定、门禁标记、当轮过滤器、增加的 chunk ID，以及候选选择轨迹。技术失败、上下文不足或达到上限都不会自动变成 `N/A`；审计与提交质量门禁继续独立处理。

## 配置示例

```yaml
retrieval:
  agent_mode: llm
  max_repairs: 2
models:
  retrieval_agent:
    base_url: https://provider.example/v1
    model: your-json-model
    api_key_env: FRECA_RETRIEVAL_AGENT_API_KEY
    response_format: json_schema
```

密钥只放环境变量，不写入 YAML、日志或实验产物。

