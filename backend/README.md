# FRECA 后端

FRECA 合规审计竞赛后端：**100 个农场案例 × 41 个检查点（CP）→ 4100 个 1/0/N/A 判决**。
输入法规 PDF + 农场证据 + CP 编号，输出 `submission_*.xlsx`（RE Number + CP1..41 填值）。

> 红线约束见文末。推理只来自法规 PDF + 农场证据，提交需冻结 exact prompt + 模型名，可复现。

## 整体流程（六段）

```
① 解析(Parse) → ② 混合检索(Hybrid Retrieval) → ③ Rerank 精排 → ④ MMR 去重
              → ⑤ LLM 判决(Verdict) → ⑥ 提交(Submit)
```

| 段 | 做什么 | 关键模块 | 说明 |
|----|--------|---------|------|
| ① 解析 | 法规 PDF→条款 chunk；农场证据→带元数据 chunk | `parsing/` | Docling 主解析（降级 python-docx/openpyxl/pdfplumber） |
| ② 混合检索 | BM25（稀疏）+ Qwen3-Embedding（稠密）→ RRF 融合，召回宽 top-N | `index/` `retrieval/hybrid_retriever.py` | 双路互补：BM25 命中关键词，Dense 命中语义 |
| ③ Rerank 精排 | 对召回候选 (query, doc) 拼接打分，重排顺序 | `retrieval/reranker.py` `reranker_api.py` | cross-encoder，比 bi-encoder 更准；只对 top-N 跑 |
| ④ MMR 去重 | 平衡相关性 vs 证据多样性，砍到 final_k | `retrieval/mmr.py` | 避免喂给 LLM 的尽是讲同一件事的重复证据 |
| ⑤ LLM 判决 | 基于检索证据 + 法规，输出 1/0/N/A | `llm/auditor.py` `llm/prompt.py` `self_check.py` | DeepSeek V4，temp=0，非思考模式 |
| ⑥ 提交 | 按 submission_template 汇总 | `pipeline/run.py` | 附带成本/审计日志 |

检索阶段 query 的构造只来自法规 PDF 条款（`retrieval/query_builder.py` + `regulation_grounder.py`），绝不引用红线文件。

## 模块地图

```
backend/
├── main.py                  # 入口（解析命令行，转发到 run.main）
├── requirements.txt
├── config/
│   ├── config.yaml          # 主配置（向量 API=dashscope 模式）
│   └── config.cloud.yaml     # 云端 GPU 模式配置（local Qwen3 权重路径）
├── src/
│   ├── parsing/             # pdf_parser(法规→条款) · case_parser(农场→chunk) · chunking · chunk_cache · fallback
│   ├── index/               # bm25_index · dense_index(本地 Qwen3) · dense_api(阿里百炼 API)
│   ├── retrieval/           # hybrid_retriever(RRF) · reranker(本地) · reranker_api(百炼) · mmr · query_builder · regulation_grounder
│   ├── llm/                 # auditor(DeepSeek) · prompt(EXACT_PROMPT) · self_check(Agent 自检层)
│   ├── pipeline/            # run(全量编排/续跑/并发) · run_state(状态机)
│   └── utils/               # io(load_config)
├── scripts/                 # 运维/评估脚本
├── tests/                   # 单测与 demo
└── cloud/                   # 云端部署脚本（AutoDL/requirements/run_retrieval.sh）
```

## 环境要求

- Python 3.10+（云端 AutoDL RTX4090 24G 已验证；本地无 GPU 可跑 dashscope API 模式）
- 依赖：`pip install -r requirements.txt`
- 如需本地 GPU 跑 Qwen3 模型：需 CUDA + 约 16G 权重（Qwen3-Embedding-4B + Qwen3-Reranker-4B，fp16 各 ~8GB）

## 配置：两种运行模式

项目支持**两种向量后端**，通过 `config.yaml` 的 `vector_backend` 切换：

### 模式 A：dashscope（阿里百炼 API，推荐，无需本地 GPU）

`config.yaml` 已默认开启：`vector_backend: "dashscope"`。
Embedding 走 `text-embedding-v3`，Rerank 走 `qwen3-rerank`（Boss MaaS 专属实例）。

需配置的环境变量（**只从环境变量读，禁止 hard-code**）：

```bash
export DEEPSEEK_API_KEY="sk-..."        # LLM 判决必需
export DASHSCOPE_API_KEY="sk-..."       # 向量 Embedding + Rerank 必需
# 可选：覆盖 MaaS 端点（默认已在 config.yaml 写死 Boss 专属实例）
# export DASHSCOPE_API_BASE="https://.../compatible-mode/v1"
```

> 也可把上面两行写进 `backend/.env`，`main.py` 启动时自动 `load_dotenv()` 加载。

### 模式 B：local（本地 Qwen3-4B @ GPU，离线兜底）

把 `vector_backend` 改为 `local`，并需用 `config.cloud.yaml`（或环境变量）指向已下载的权重：

```bash
export DEEPSEEK_API_KEY="sk-..."
export QWEN3_EMBEDDING_PATH="/root/freca/models/Qwen3-Embedding-4B"
export QWEN3_RERANKER_PATH="/root/freca/models/Qwen3-Reranker-4B"
```

> 本地模式两个 4B 模型共用 24G 显存，代码已做 GPU 错峰（同时仅 1 个 4B 驻留），避免 OOM。

## 运行

```bash
cd farm-case-analysis/backend

# 1) 先看要花多少钱（不调任何模型）
python main.py --estimate-only

# 2) 冒烟：只跑 1 个 case 的全部 CP（验证检索+判决链路）
python main.py --cases 1

# 3) 只验证检索质量（做法规 grounding + 证据召回，不调 LLM，省钱）
python main.py --dry-run-retrieval

# 4) 全量：100 case × 41 CP = 4100 判决
python main.py

# 其他常用参数
python main.py --cps 1,2,3          # 只跑指定 CP
python main.py --no-resume          # 忽略已完成进度，从头跑
```

- 全量运行支持**断点续跑**：中断后重跑 `python main.py` 会自动跳过已完成 case。
- 所有文本 LLM 调用统一走 `DeepSeekAuditor`（`deepseek-v4-flash`, `temperature=0`, 非思考模式），输出单 token 判决。
- 成本与进度写入 `logs/`，提交文件落到 `data/` 或脚本指定目录。

## 红线约束（最高优先级）

`checkingpoints_all_elements_onesheet.xlsx`（CP↔法规条款映射 = 设立标准 = 答案本身）**严禁进入任何 AI 输入**（不进 prompt / query / 3-shot / 语料索引）。

- CP「定义」（描述查什么）→ 可进代码，作 query 构造依据。
- CP「设立标准」（红线文件）→ 仅作**验证期 ground truth 阅卷**，推理/运行时绝不加载。
- query 只来自法规 PDF 条款；3-shot / instruction 不引用红线。

详见 `farm-case-analysis/README.md`（若存在）及 `CODE_STANDARD.md`。

## 已知事项

- Docling 首次 import ≈15s（模型加载），之后缓存；安装会降级 Pillow/pypdfium2，与 pdfplumber 冲突（仅影响降级解析器，主用 Docling）。
- Reranker 为 GPU cross-encoder 且非线程安全，代码用全局锁串行化，避免多线程 CUDA 死锁；无 GPU/模型缺失时自动降级为"保持原序"。
- `dense_api` / `reranker_api` 调用阿里百炼 API 失败时不会中断主流程，仅降级（重排失效时保留 RRF 顺序）。
- 向量 API 成本估算见 `vector_api_cost_estimate.md`；全量 4100 决策实测约 USD 1 出头（¥8 左右）。
