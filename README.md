# FRECA Task 2 — Compliance Audit Pipeline

> 主线架构 + 模块职责见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。可视化流转图:[`architecture.html`](architecture.html)(用浏览器打开)。
>
> 检索 / Agent / 消融 / 产物 schema 的实现细节: [`docs/RETRIEVAL.md`](docs/RETRIEVAL.md)、[`docs/AGENT_RETRIEVAL.md`](docs/AGENT_RETRIEVAL.md)、[`docs/ABLATION.md`](docs/ABLATION.md)、[`docs/ARTIFACT_SCHEMA.md`](docs/ARTIFACT_SCHEMA.md)、[`docs/SIGNATURE_CONTAMINATION.md`](docs/SIGNATURE_CONTAMINATION.md)。

## 一句话

100 个 case × 41 个 CP = **4,100 个幂等审计任务**:解析法规与案例 → 双索引 → 检索(Planner / Retrieval / Critic)→ 审计 → 引用校验 → Verifier → 选择性仲裁 → 提交门禁。

## Direct LLM experiment architecture (active)

The active evaluation path is a direct, official-material LLM experiment framework rather than the legacy retrieval/agent chain. It compares `case_full`, `element_full`, `checkpoint_full`, and `automatic_retrieval` using the original checkpoint text, policy, current-case evidence, and optional original images. It never uses external label workbooks or handmade CP-to-rule mappings.

Create a deterministic plan without contacting a model:

```powershell
python -m freca.cli --config config.yaml experiment plan --method case_full --case-id 7
```

Plans are written to `build/experiments/plans/`. The execution API persists the request, material hash, raw response, validation result, and image paths. Candidate scores are **silver agreement** with a frozen LLM reference, never official accuracy. Live execution is intentionally gated behind `--allow-live-model`; this repository's tests make no provider calls.

The older retrieval pipeline remains available for backward compatibility, but it is not a dependency of the direct experiment path. The detailed design and implementation plan are in `docs/superpowers/specs/2026-07-28-freca-direct-llm-experiment-design.md` and `docs/superpowers/plans/2026-07-28-freca-direct-llm-experiments.md`.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
# 详细命令见下方"推荐运行顺序"段落
```

> 本项目仓库**不包含原始数据集**:33M 的 `extracted/SFRE_cases/` 在 `.gitignore` 里,clone 后自行放回或重新生成。

## 当前实现边界

本地无需模型凭据即可运行：

- 100 案例 / 898 文件清单恢复与 SHA-256；
- DOCX 段落、标题、表格和嵌入图片提取；
- XLSX Sheet、坐标、公式、空值和合并单元格提取；
- PDF 页码保留解析；MinerU 不可用时明确记录回退状态；
- BM25 + 本地向量 + RRF + 通用 Reranker + 来源感知 MMR；
- 两轮封顶的查询修复；
- 4,100 个持久化任务、失败隔离和断点恢复；
- 引用真实性、案例归属、裁决语义和提交结构校验；
- replay 模型端到端测试。

需要外部服务配置后才能运行：

- 语义 embedding（未配置时使用可复现的本地 hashing vector，并在报告中标明）；
- 图片中性描述（未配置时保留原图并标记 `vision_description_pending`）；
- LLM 审计、Verifier、查询改写和选择性仲裁。

系统不会在缺少外部服务时生成假裁决；相关任务进入 `BLOCKED`。

## 安装

```powershell
cd D:\Data\Desktop\contest\Task2
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mineru]"
.\.venv\Scripts\python.exe -m pytest -q
```

本项目不安装 MinerU 本地推理引擎，只安装轻量的官方云 SDK。当前 `mineru.mode: disabled` 使用 PyMuPDF 工程回退；拿到 token 后改为 `cloud_sdk`，并设置 `MINERU_TOKEN`。如果提供的是自建 `mineru-api` 地址，则改为 `remote_api`，系统会调用 `/file_parse` 并读取 ZIP/JSON 中的结构化 `content_list`。

## 模型配置

编辑 `config.yaml` 中的 endpoint 和 model 名称，只在当前 PowerShell 会话中设置凭据：

```powershell
$env:FRECA_AUDIT_API_KEY = "..."
$env:FRECA_VERIFIER_API_KEY = "..."
$env:FRECA_ARBITRATOR_API_KEY = "..."
$env:FRECA_QUERY_REWRITER_API_KEY = "..."
$env:FRECA_EMBEDDING_API_KEY = "..."
$env:FRECA_VISION_API_KEY = "..."
$env:MINERU_TOKEN = "..."
```

不要把密钥写入 YAML、Markdown、日志或提交表。完成运行后从当前会话移除这些变量。

## 推荐运行顺序

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml doctor --stage prepare
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml prepare
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml doctor --stage pilot
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml pilot --pilot-file pilot_cases.json --max-workers 2
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml full --run-id full-001 --max-workers 4 --allow-unconfirmed-identifiers
```

若某次因 key、限流或服务错误进入 `BLOCKED/FAILED`，补齐条件后恢复原 run-id：

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml retry --run-id pilot-001
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml pilot --pilot-file pilot_cases.json --max-workers 2
```

已完成任务不会重跑；相同模型、prompt 和 schema 会使用 `build/cache/models/`，调用台账写入 `build/logs/model-calls.jsonl`。

## 首批试跑

试跑清单见 `pilot_cases.json`：case 1–5、24、35、80、100，全部 41 个 CP，共 369 个任务。它同时覆盖普通案例、缺 Track 1 和重复 RE Number 三类路径。

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml pilot --pilot-file pilot_cases.json --max-workers 2
```

模型参数尚未配置时，这些任务应明确进入 `BLOCKED`，不会生成假裁决。凭据到位后使用同一 `run-id` 可按任务状态恢复。

查看进度：

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml status --run-id full-001
```

单案例或单 CP 调试：

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml ingest --case-id 35 --no-mineru
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml index
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml audit --run-id debug-035 --case-id 35 --cp-id CP17 --max-workers 1
```

全流程入口：

```powershell
.\.venv\Scripts\python.exe -m freca.cli --config config.yaml full --run-id full-001 --max-workers 4 --allow-unconfirmed-identifiers
```

## 提交门禁

正式 assemble 要求：

- 恰好 4,100 个任务且全部 `COMPLETED`；
- 4,100 个最终裁决文件均存在；
- 引用校验和 Verifier 均通过；
- 选择性仲裁没有未解决分歧；
- Element 一致性检查无未解决发现；
- 每个值都属于 `1 / 0 / N/A`；
- 输出为 100 行数据、42 列。

case 35/100 共用 RE Number、模板仅有表头等情况按数据质量问题标注，不阻断清洗、检索或逐 CP 审计。为避免把未经确认的行标识冒充正式格式，默认只允许在显式开关下生成候选表：

```powershell
python -m freca.cli --config config.yaml assemble --run-id run-001 --allow-unconfirmed-identifiers
```

该开关只解除标识符口径阻断，不会绕过任务、引用、Verifier、一致性或 4,100 项完整性门禁。

## 产物

```text
build/
├── manifests/cases.json
├── parsed/policy.json
├── parsed/checkpoints.json
├── parsed/cases/{case_id}/track-{track}.json
├── parsed/images/{case_id}/
├── indexes/policy.json
├── indexes/cases.json
├── retrieval/{case_id}/{cp_id}.json
├── decisions/{case_id}/{cp_id}.json
├── verification/{case_id}/{cp_id}.json
├── arbitration/{case_id}/{cp_id}.json
├── final/{case_id}/{cp_id}.json
├── consistency/{run_id}.json
├── state/{run_id}-tasks.json
├── runs/{run_id}.json
├── cache/models/{client}/{request_hash}.json
├── logs/model-calls.jsonl
└── submission.xlsx
```

## 旧文件说明

旧 `case_filter.py` 已改为只报告结构性风险，不再筛掉任何案例，也不再生成 `N/A`。历史文件 `anomaly_report.json` 和 `skeleton_submission.xlsx` 来自已废弃的 96 案例假设，当前流水线完全不读取它们，不能作为提交或标签来源。

本轮已确认的实施口径记录在 `DECISIONS.md`。
