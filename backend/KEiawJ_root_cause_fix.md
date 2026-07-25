# KEiawJ 假死根因定位与修复报告

> 任务：排查云端后台任务 `KEiawJ`（`--cases 1`，验证串行检索修复）为何卡死 8 分钟无输出，并彻底修复。

## 1. 结论（一句话）

`KEiawJ` 并非多线程 GPU 死锁，而是 `_build_case_retriever` 里残留的 `dense.offload_to_cpu()` 让**每个 CP 的 query 编码退化到 CPU 跑 4B Qwen3-Embedding**，单 CP 卡 8 分钟；删除该行并修正 reranker 的 device 同步后，单 CP 端到端 32s 跑通。

## 2. 真·根因（vs 之前的误判）

| 维度 | 之前误判 | 实际根因 |
|------|----------|----------|
| 死因 | 多线程同时抢 GPU 死锁 | `dense.offload_to_cpu()` 把 dense 移出 GPU；`retrieve()` 内的 **query 编码**发生在 dense 上 → 在 CPU 跑 4B 模型 |
| 瓶颈位置 | reranker（CrossEncoder） | 证据/法规 **query 编码**（单句，但 4B fp16 在 CPU 极慢） |
| GPU 状态 | 0%（误以为等锁） | 0%（CPU 在烧 4B 编码，GPU 空闲） |
| 隔离验证 | — | 单独 `CrossEncoder` 测试证明 reranker 默认 `device=cuda:0`、`predict` 正常 → **reranker 不是死锁源** |

**派生坑（reranker device 错配）**：旧 `reranker.py` 用 `self.model.model.to("cuda")` 搬运权重，但 `SentenceTransformer.device` 属性**不会自动更新**。`CrossEncoder.predict()` 内部 `if device is None: device = str(self.device)` 仍按旧 `device=cpu` 把输入搬回 CPU → reranker 在 GPU 0% 下假死。
正确做法：`self.model.to("cuda")` + 显式 `self.model.device = torch.device("cuda")`。

## 3. 修复内容（3 文件，已同步云端）

| 文件 | 改动 |
|------|------|
| `src/pipeline/run.py` | `_build_case_retriever` **删掉 `dense.offload_to_cpu()`**（dense 留在 GPU 供 retrieve 的 query 编码）；reranker 构造在 CPU（`device="cpu"`, `cpu-deferred`），由 `retrieve()` 内部错峰 `to_gpu()`；每 case 释放前 `base_rerank.offload_to_cpu()` 把显存让回下一 case 的 dense |
| `src/retrieval/hybrid_retriever.py` | `retrieve()` 顶部先 `reranker.offload_to_cpu()` + `dense.to_gpu()`（query 编码走 GPU dense）；重排前 `dense.offload_to_cpu()` + `reranker.to_gpu()`（错峰，GPU 同一时刻仅 1 个 4B，避免 24G OOM） |
| `src/retrieval/reranker.py` | `to_gpu()`/`offload_to_cpu()` 改用 `self.model.to("cuda"/"cpu")` + 显式同步 `self.model.device`；`__init__` 加 `device` 参数透传 `CrossEncoder(..., device=device)`；`predict` 加 `show_progress_bar=False` |

错峰逻辑（最终）：GPU 同一时刻只有 1 个 4B 模型 —— query 编码时 dense 在 GPU、reranker 在 CPU；重排时 reranker 在 GPU、dense 在 CPU。

## 4. 单 CP 冒烟验证（run_id=test_20260718_220622, `--cases 1 --cps 1`）

```
[qwen3] reranker loaded (kind=qwen3-reranker, device=cpu-deferred)
[retriever] built RE-NSW-2020-0033: chunks=89 dense=qwen3-embedding rerank=qwen3-reranker
[retrieve] CP1 done: reg=3 ev=8
[RE-NSW-2020-0033][CP1] verdict=0 policy=['1-6-p2','1-6-p8','1-6-4-p18'] ev=8 tok=2307 err=None
[cost] calls=1 prompt_tok=2306 completion_tok=1 ≈ $0.0003 (¥0.00)
[finalize] wrote .../submission_test_20260718_220622.xlsx | filled=1/4100
结束 run_id=test_20260718_220622 | 完成 1/4100
```

| 检查项 | 结果 |
|--------|------|
| 端到端耗时 | ~32s（含模型加载），此前 8 分钟卡死已消除 |
| verdict | `0`（合法） |
| policy 条款 | `['1-6-p2','1-6-p8','1-6-4-p18']` 正确召回 |
| 检索召回 | 法规 3 + 证据 8 |
| 计费捕获 | `calls=1 prompt_tok=2306 completion_tok=1 ≈ $0.0003` |
| 测试隔离 | 产物落 `test_runs/`，`run_id` 带 `test_` 前缀 ✅ |

## 5. 下一步建议

1. **扩 `--cases 3`**（3 case × 41 CP = 123 判决）验证多 case 错峰切换稳定性，再推全量 100×41=4100。
2. **真 Qwen3 全量质量** vs 之前的 MiniLM 兜底差异，待全量后比对。
3. **⚠️ 云端计费**：AutoDL 实例 ¥1.65/h，跑完务必控制台「停止实例」省费。

> 红线自检：全程未读取 `checkingpoints_all_elements_onesheet.xlsx`，推理仅来自法规 PDF + 农场证据，符合约束。
