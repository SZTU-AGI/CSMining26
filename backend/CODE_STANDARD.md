# FRECA 代码规范（CODE_STANDARD）

> 本文档是 FRECA 合规审计系统的**代码准绳**。所有新增/修改代码必须先对齐本文档，再动手。
> 最后更新：2026-07-18（基于代码实际核对 + Boss 红线二分澄清 + 运行脚本/API 接口规范，参考 `D:/桌面/code/labeling_script_v2.py`）。

---

## 0. 目的与范围

端到端：输入(法规 PDF + 农场证据 + CP 编号) → 离线双索引(BM25+Qwen3) → 运行时(query 来自法规, 证据召回+法规 grounding, RRF 融合) → LLM 判决 → submission。
代码必须保证：**推理不泄露答案（红线禁用）、结果可复现、命名一致、与架构图对齐**。

---

## 1. 红线与数据边界（最高优先级）

### 1.1 CP 文件二分（Boss 19:25 澄清）
红线文件 `checkingpoints_all_elements_onesheet.xlsx`（sheet `All Elements`, 4行×41列）内部含两类信息，必须严格区分：

| 类别 | 位置 | 内容 | 是否可进代码 |
|---|---|---|---|
| **CP 定义** | `row1` | `1.1 Export operations` … `4.4 Importing country requirements`（41 个章节标题） | ✅ **可用**：作 query 锚点 / 输出槽位说明 |
| **设立标准（红线）** | `row2/row3` | 每个 CP 对应的法规条款原文句子（如 `col0: "The establishment is operating within its registered operations..."`） | ❌ **推理/运行时严禁进任何 AI** |

- **CP 定义**用于告诉检索/LLM「当前在审哪个 CP」，是任务描述的一部分。
- **设立标准**本质是「考试答案 / 规则本身」，一旦进 prompt/query/语料，LLM 就抄答案，推理失去意义且不可复现。

### 1.2 红线文件禁用边界（推理期）
以下环节**绝对禁止**读取或使用 `row2/row3` 的设立标准内容：
- ❌ 不进 LLM prompt（含 system / user）
- ❌ 不进检索 query / instruction / 3-shot
- ❌ 不进任何语料索引（BM25 / Dense 的 corpus）
- ❌ 不拼进任何「规则表」「判定标准」辅助输入

### 1.3 红线作 Ground Truth（验证期，唯一合法用法）
红线文件（含 `row2/3`）**只能**在离线验证/评估时作为标准答案阅卷：
- 例：法规检索阶段，对 CP `1.1` 的定义做检索，理应收回的正确条款 = 红线 `row2 col0` 那句法规原文 → 用它算检索**命中率/召回率**。
- 验证脚本读取红线文件**仅用于计算指标**，**输出不反哺推理、不改变任何模型输入**。
- 验证集（human-in-the-loop 自建）同理：用红线/人工标注作 GT 评估，不参与生成。

### 1.4 自检机制（待实现，见 §7）
当前仅靠注释约定 + `tests/test_qwen3_retrieval.py` 的 red-line self-check。生产代码需补**代码级阻断**：
- 加载语料/构建 query 前扫描，若命中红线文件内容片段立即 `assert` 失败。
- `config.constraint.forbid_checkingpoints_in_ai=true` 须有对应运行期校验，而非仅标识。

---

## 2. 可复现性

### 2.1 Exact Prompt 常量
- LLM 系统提示必须来自 `src/llm/prompt.py` 的 `EXACT_PROMPT` 常量，**不得散落在调用处拼接**。
- `EXACT_PROMPT` 只给「框架 + 法规片段 + 农场证据」，**不写任何 `CPx requires Y`** 类规则。
- 提交评审须附此 exact prompt 原文 + 模型名，保证可复现。

### 2.2 固定模型与温度
- **LLM 判决/生成模型固定 `deepseek-v4-flash`**（`temperature=0`，非思考模式 `thinking=disabled`）。见 `config.llm` 与 `src/llm/auditor.py`。
  - ⚠️ 旧 `deepseek-chat` / `deepseek-reasoner` 将于 **2026-07-24 停用**，已迁移到 V4；提交文档须写明用的是 `deepseek-v4-flash`。
  - **所有非向量检索的 LLM 调用统一用此模型**（判决、验证集草稿、3-shot 归纳等）；向量检索/重排是 Qwen3 本地模型，不走此接口。
- 检索模型固定：`Qwen3-Embedding-4B`（dense）、`Qwen3-Reranker-4B`（rerank）。**全千问，无 BGE**。
- 模型版本/路径变更必须走 config，不得 hard-code 在业务代码。

### 2.3 超参固化（待扫参，见 §6.3）
当前默认（`config.retrieval`）：`chunk_size=1600`(字符) / `chunk_overlap=160` / `top_k=8` / `rrf_k=60` / `rerank_top_n=20` / `mmr_lambda=0.5` / `final_k=8`。
- 均为经验默认，**未经验证集扫参**，标注为「待调优」。
- 调优后须回写 config 并记录取值依据。

### 2.4 提交 Checklist
- [ ] exact prompt 原文 + 模型名 + 版本
- [ ] 超参取值（含 chunk 单位标注「字符非 token」）
- [ ] 红线禁用声明（证明推理未用设立标准）
- [ ] 验证指标（检索命中率 / 判决准确率）及 GT 来源

---

## 3. 命名规范

### 3.1 文件 / 模块
- 检索相关放 `src/retrieval/`，索引相关放 `src/index/`，解析放 `src/parsing/`，LLM 放 `src/llm/`，编排放 `src/pipeline/`。
- 文件名用**小写蛇形**：`hybrid_retriever.py` / `dense_index.py` / `bm25_index.py` / `query_builder.py` / `auditor.py` / `prompt.py`。
- 临时/运维脚本禁止进 `src/` 与 `cloud/` 根（已清理，仅留 `download_aria2.py` + `run_retrieval.sh` + `setup.sh` + `deploy.py` + `requirements.txt` + `README.md`）。

### 3.2 类命名
- **统一用 `*Retriever` 或 `*Index` 语义一致**。当前 `src/index/bm25_index.py` 内类是 `BM25Retriever`，与文件名 `index` 不一致 → 待修正（建议文件改名 `bm25_retriever.py` 或类改名 `BM25Index`）。
- 对照：`DenseRetriever`(dense_index.py) / `HybridRetriever`(hybrid_retriever.py) / `Qwen3Reranker`(reranker.py) 已一致。

### 3.3 函数 / 变量
- 函数小写蛇形；常量全大写下划线（`RETRIEVE_INSTRUCTION` / `EXACT_PROMPT`）。
- query/instruction 变量名须体现来源：来自法规用 `clause`/`regulation`，来自 CP 定义用 `cp_def`，红线内容变量名禁止出现（不应存在）。

### 3.4 当前待修正命名清单（见 §7）
1. `src/index/bm25_index.py` 文件名/类不一致
2. `main.py` 是 PyCharm 示例未改（非真实入口）
3. `src/pipeline/run.py` 注释残留 `BGE`

---

## 4. 架构对齐（与架构图）

### 4.1 全千问
检索双塔 + 重排均为 Qwen3（非 BGE）。架构图「首轮检索」标注的 **BGE 检索**须修订为 `Qwen3-Embedding`。

### 4.2 已实现 / 未实现对照
| 架构图层 | 模块 | 状态 |
|---|---|---|
| 输入层 | 法规 PDF 解析(docling→rules_raw.md) | ✅ |
| 输入层 | 农场证据解析(case_parser+chunking) | ✅ |
| 索引层 | BM25 索引 | ✅ |
| 索引层 | Qwen3-Embedding 向量索引 | ✅ |
| 检索层 | BM25+Dense → RRF 融合 | ✅ |
| 检索层 | Qwen3-Reranker 精排 | ✅ |
| 检索层 | 来源感知 MMR 去重 | ✅（MMR 已实现，来源感知待确认） |
| 运行层 | Query 构建（CP定义/法规条款） | ✅ |
| 运行层 | 首轮检索 | ✅ |
| 运行层 | **检索充分性评估** | ❌ 未实现 |
| 运行层 | **Agent 纠错检索(≤2轮)** | ❌ 未实现 |
| 运行层 | 融合与整理 | ✅（基础） |
| 运行层 | LLM 审计裁决(1/0/N/A) | ✅（auditor，待全量） |
| 质控层 | 引用校验 | ⚠️ 部分（MMR 带来源，显式校验未实现） |
| 质控层 | **独立验证器 Verifier** | ❌ 未实现 |
| 质控层 | **Element 一致性检查** | ❌ 未实现 |
| 质控层 | **选择性双模型仲裁** | ❌ 未实现 |
| 质控层 | **审计日志存档** | ❌ 未实现 |
| 输出层 | submission.xlsx (100×41) | ❌ 未实现（run.py 空骨架） |

### 4.3 架构图修订项
- 「首轮检索」BGE → Qwen3-Embedding。
- 质控层（Verifier / 一致性 / 仲裁 / 日志）若决定实现，需在图与代码同步补齐。

---

## 5. 数据与路径管理

### 5.1 代码存放
所有后端代码统一在 `D:\桌面\农场任务二\farm-case-analysis\backend`（英文命名）。脚本禁止散落 `Task2` 根目录。

### 5.2 数据源隔离
- 数据源：`D:\桌面\农场任务二\Task2\`（法规 PDF、红线 xlsx、submission 模板、SFRE_cases/）。
- 红线文件**只读、且仅验证期访问**（见 §1.3），业务推理代码目录不得 import 或读取其内容。

### 5.3 目录约定（config.paths）
- `index_dir`: 离线索引缓存
- `validation_dir`: 验证集（GT）存放
- `submission_out`: 提交输出
- `rules_md`: 法规解析缓存（Docling 已生成，避免每次重跑）

---

## 6. 验证集与超参

### 6.1 3-shot
计划「复用验证集 case 提炼『法规↔证据』配对（剥掉 verdict 标签）」。Qwen3 无 ICL，3-shot 改为 **task instruction**（原生 instruction-aware），不拼 demo。
- 验证集文件未建 → 3-shot 加载逻辑缺失，待补。

### 6.2 GT 用法
验证集 / 红线文件均只作 GT 评估（§1.3），不参与生成。

### 6.3 超参扫参计划
在验证集上扫 `top_k` / `rrf_k` / `rerank_top_n` / `mmr_lambda` / `final_k` / `chunk_size`，择优回写 config 并记录依据。当前值均为默认。

---

## 7. 当前偏差清单（待修，按优先级）

| # | 偏差 | 位置 | 优先级 | 规范依据 |
|---|---|---|---|---|
| B1 | 红线无代码级自检（仅注释） | 全局 | 🔴 高 | §1.4 |
| B2 | `pipeline/run.py` 空骨架 + BGE 残留注释 | run.py:21 | ✅ 已修 | §3.4 / §4.2 |
| B3 | `main.py` PyCharm 示例未改 | main.py | ✅ 已修 | §3.4 |
| B4 | `bm25_index.py` 文件名/类不一致 | bm25_index.py | 🟡 中 | §3.2 |
| B5 | 质控层未实现（Verifier/一致性/仲裁/日志） | src/ | 🟡 中 | §4.2 |
| B6 | 检索充分性评估 / Agent 纠错未实现 | src/ | 🟡 中 | §4.2 |
| B7 | 3-shot / 验证集缺失 | src/ | 🟡 中 | §6.1 |
| B8 | 超参未扫参（默认） | config | 🟢 低 | §6.3 |
| B9 | 架构图 BGE 标注未改 | 架构图 | 🟢 低 | §4.1 |
| B10 | `auditor.py` 丢弃 `usage`（无 token 统计/计费） | src/llm/auditor.py | ✅ 已修 | §9.2 / §9.3 |
| B11 | `run.py` 无并发/断点续跑/运行日志/成本结算（空骨架） | src/pipeline/run.py | ✅ 已修 | §9.1 / §9.4 / §9.5 / §9.6 |

---

## 9. 运行脚本与 LLM API 接口规范（参考 labeling_script_v2.py）

> 参考实现：`D:/桌面/code/labeling_script_v2.py`（鞋服行业码表打标脚本）已验证的成熟模式：队列生产-消费、单写线程、速率限制、分级重试、token 全局统计+费用结算、progress.json 断点续跑、SIGINT 优雅退出、loguru 结构化日志。
> FRECA 全量 **100×41 = 4100 次 LLM 判决**，必须套用以下规范，并**修正该脚本的反面做法**（temp=0、Key 走环境变量、结构化审计日志、红线禁用）。

### 9.1 运行脚本通用骨架
每个运行脚本（`pipeline/run.py`、验证脚本、`tests/test_qwen3_retrieval.py` 等）须有统一骨架：
1. 顶部**集中配置区**（常量集中在文件头/ config，禁止散落魔法数）。
2. loguru 初始化（带 `run_id` + 时间戳文件名，见 §9.4）。
3. 进度加载 → 任务入队 → worker 并发 → 单写线程落盘 → 进度监控 → 费用结算 → 导出终态。
4. `signal.signal(signal.SIGINT, graceful_exit)`：保存进度后退出，可重跑续算。
- 真实入口为 `src/pipeline/run.py`（覆盖 B2/B3 占位示例）；`main.py` 改为调用 `run.py` 或删除。

### 9.2 LLM API 调用接口规范（对齐 labeling.call_api_with_retry）
统一走 `src/llm/auditor.py::DeepSeekAuditor`，满足：
- **模型固定 `deepseek-v4-flash` + `temperature=0` + `thinking={"type":"disabled"}`**（判决单 token，非思考更快/省/确定；labeling 用 0.1 不适用 FRECA）。
- **速率限制**：每次调用前 `time.sleep(rate_limit_delay)`（config `llm.rate_limit_delay`，默认 0.3s）防 429 限流。
- **超时**：`timeout`（config `llm.timeout`，默认 60）。
- **分级重试（max_retries≥3，指数退避）**：
  - `429` → 等 `5*(attempt+1)`s 重试（限流）
  - `5xx`(500/502/503/504) → 等 `3*(attempt+1)`s 重试
  - `Timeout` → 等 2s 重试
  - `ConnectionError` → 等 5s 重试
  - 其他异常 → 记日志、返回 `(None, {})`（不拖垮整体），计入重试原因
- **用量捕获**：`audit()` 已改为返回 `(verdict, usage)`，`usage` 含 `prompt_tokens`/`completion_tokens`/`prompt_cache_hit_tokens`；调用方加锁累加用于计费（§9.3）与审计日志（§9.4）。**B10 已修**（旧版丢弃 usage）。
- **API Key 必须走环境变量** `DEEPSEEK_API_KEY`（`auditor.py` 已做）；**禁止**如 labeling 脚本那样 hard-code 在源码（安全反面教材）。
- 响应解析：判决输出剥 ```` ```json ```` 包裹并容错、`_parse` 归一到 `1/0/"N/A"/None`（对齐 labeling.parse_response 思路）。

### 9.3 Token 统计与计费
- 全局累加器 `total_prompt_tokens` / `total_completion_tokens`，配 `threading.Lock()`（labeling 同款）。
- 每次 LLM 调用后**加锁累加**。
- 单价配置 `config.llm.pricing`（官方 `deepseek-v4-flash`，单位 **USD/百万 token**）：
  - 输入 cache miss `$0.14/M`、cache hit `$0.028/M`（前缀命中自动 1/10）、输出 `$0.28/M`（`usd_to_cny≈7.2` 估人民币，以账单为准）。
  - EXACT_PROMPT 固定 → system 前缀高概率 cache hit，实际输入成本远低于 cache miss 估值。
- 结算公式：`input_cost = prompt_tokens * price_in / 1e6`；`output_cost = completion_tokens * price_out / 1e6`；`total = input+output`；`avg = total / processed`。
- 运行结束**打印 + 写入 run log**（见 §9.4），统计块格式对齐 labeling 的 Token/费用输出。
- **4100 次调用成本估算（量级参考）**：input≈2000tok/次、output≈5tok/次 → 保守 cache miss ≈ `4100×2000×0.14/1e6 + 4100×5×0.28/1e6 ≈ $1.16`（约 ¥8.4）；命中缓存后更低。上线前须按实际 chunk 量重估并写入提交文档（§8 checklist）。

### 9.4 可追溯运行日志（审计日志）
- 每个运行生成独立 `run_id = {YYYYMMDD_HHMMSS}` + `run.log`（loguru，按 run 分文件，目录 `config.paths.run_log_dir`）。
- 日志须可回溯到「哪次判决用了什么」：
  - **启动**：run_id、模型名、temperature、`config` 指纹（hash）、红线禁用确认。
  - **每次 LLM 判决**记一行结构化记录：`case_id, cp_id, verdict, input_tokens, output_tokens, attempts, latency_ms, retry_reason`。
  - 错误/重试记 warning 级；空结果/解析失败记 warning。
  - **结束**：token 汇总 + 费用 + 耗时 + 完成率 + 缺失项清单。
- 这是 §4.2「审计日志存档」质控项的实现载体；须满足可审计：**谁/何时/用什么模型/什么输入 → 什么 verdict**。
- 红线内容**绝不**进日志（§1.2）；日志只记录 CP 定义锚点 + 法规/证据 chunk_id，不记录设立标准原文。

### 9.5 规范化数据存储（可恢复）
- 中间结果**增量落盘**，防 4100 次长任务崩溃归零：
  - 中间文件：`submission_inprogress.jsonl`（每行一决策：`case_id,cp_id,verdict,usage,ts`）。
  - 进度文件：`progress.json`（已完成 `{case_id}_{cp_id}` 集合）。
  - 单写线程 `result_queue` + `batch_size` 批量追加（JSONL），避免多线程写竞争（labeling 同款）。
  - `FLUSH_INTERVAL` 定时 flush（如 10s）。
- **重跑机制**：`load_progress()` 读 progress.json / 扫描 inprogress.jsonl 恢复已完成，跳过已处理。
- **终态导出**：从 inprogress 汇总生成 `submission.xlsx`（100×41）；导出后校验行数=4100，再备份 inprogress（`.bak`+时间戳），删除 progress。
- 红线文件只读且仅验证期访问（§1.2/§1.3），中间存储不得写入红线内容。

### 9.6 线程/并发管理（对齐 labeling_script_v2）
- 并发模型：生产-消费队列 + worker 线程池。
  - `task_queue = queue.Queue()` 装 `(case_id, cp_id)` 任务。
  - `MAX_WORKERS` 个 `threading.Thread(worker, daemon=True)`。
  - worker 取任务 → 调 `auditor.audit` → 结果入 `result_queue` → 标记完成。
- 写线程：`ProgressManager` 单写线程从 `result_queue` 批量落盘（无锁，避免竞争）。
- 监控线程：定时打印 进度/速度/ETA（对齐 labeling.progress_monitor）。
- 线程安全：仅 `completed_ids` 与 token 累加器用 `Lock`；结果写入交给单写线程。
- 优雅退出：`signal.SIGINT` → `flush_remaining()` + `save_progress()` → `exit(0)`，可续跑。
- 注意：DeepSeek 限流下 `MAX_WORKERS` 不宜过大（建议 3–8，配合 `RATE_LIMIT_DELAY`）；`temp=0` 保证单调用确定性，并发不影响可复现。

### 9.7 与 labeling_script_v2.py 的差异（必改项）
| 项 | labeling 脚本 | FRECA 规范 |
|---|---|---|
| temperature | 0.1 | **0（可复现，§2.2）** |
| API Key | hard-code 源码 ❌ | **环境变量 ✅** |
| 输出解析 | 候选词数组 | **单 token 1/0/N/A** |
| 红线 | 无 | **严禁设立标准进 AI（§1）** |
| 日志 | 基础 loguru | **结构化审计日志（run_id + 每判决一行，§9.4）** |
| 成本 | doubao 单价 | **deepseek 单价（config，§9.3）** |
| 数据落地 | CSV + Excel | **JSONL 增量 + progress 续跑 + xlsx 终态（§9.5）** |
| 模型 | doubao 1.5pro | **deepseek-v4-flash（§2.2）** |

---

## 10. 环境变量（.env）规范

### 10.1 原则
- **敏感信息（API Key 等）只进环境变量 / `.env`，绝不 hard-code、绝不入库**（labeling 脚本把 Key 写死在源码是反面教材）。
- 仓库提供 `.env.example` 模板（占位、无真值）；真实值放本地 `.env`（已被 `.gitignore` 排除）。
- 业务代码统一 `os.environ.get("XXX")` 读取；缺失必需项时 fail-fast 报错（如 `auditor` 无 `DEEPSEEK_API_KEY` 直接 `RuntimeError`）。
- 建议入口用 `python-dotenv` 自动 `load_dotenv()` 加载 `.env`（不改动系统环境）。

### 10.2 环境变量清单
| 变量 | 用途 | 必需 | 备注 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | LLM 判决/生成鉴权 | ✅ 推理期 | 只此一处提供 Key |
| `DEEPSEEK_API_BASE` | 覆盖 api_base | ❌ | 一般走 config |
| `QWEN3_EMBEDDING_PATH` | 云端 embedding local_dir | ☁️ 云端 | 本地留空走 HF id |
| `QWEN3_RERANKER_PATH` | 云端 reranker local_dir | ☁️ 云端 | 同上 |
| `HF_HUB_DISABLE_XET` | 绕 xet CAS 桥 | 📥 下载期 | =1 必需，否则 403/卡死 |
| `HF_ENDPOINT` | HF 源 | 📥 下载期 | 云端直连最稳；沙箱勿设 hf-mirror |
| `HF_HUB_DOWNLOAD_TIMEOUT` / `_RETRIES` | 下载容错 | 📥 下载期 | timeout 用适中值 120，非 1800 |
| `HTTPS_PROXY` | 本地沙箱代理 | 🏠 本地 | 云端不用 |

### 10.3 边界
- 环境变量分三类场景：**推理期**（DEEPSEEK_*）、**下载期**（HF_*）、**运行环境**（QWEN3_*/PROXY）。互不混用，写清注释。
- 红线：`checkingpoints` 路径虽在 `config.paths`，但**任何环境变量都不得指向或加载其 row2/3 内容进推理**（§1）。

---

## 11. 大模型 API 集成接口规范

> 目标：所有"文本生成/判决"类 LLM 调用**收敛到单一客户端**，便于统一重试/计费/日志/可复现。向量检索/重排是本地 Qwen3，**不属于此接口**。

### 11.1 唯一入口
- 文本 LLM 调用统一经 `src/llm/auditor.py::DeepSeekAuditor`（当前判决场景）。若后续新增"验证集草稿""3-shot 归纳"等生成场景，**复用同一客户端类**（或抽出 `LLMClient` 基类），不得在业务代码里另起 `requests.post`。
- 检索侧（`dense_index.py` / `reranker.py`）走 Qwen3 本地推理，与本接口物理隔离。

### 11.2 接口契约
- 构造：`DeepSeekAuditor(model, temperature, api_key, api_base, max_retries, thinking_mode, timeout)`，默认值来自 config，Key 来自环境变量。
- 方法：`audit(policy_excerpts, evidence_excerpts) -> (verdict, usage)`；`verdict ∈ {1, 0, "N/A", None}`，`usage` 为原始用量 dict。
- 请求体固定字段：`model` / `temperature` / `thinking.type` / `messages`（system=EXACT_PROMPT，user=法规+证据）。
- DeepSeek API 为 **OpenAI 兼容**：`POST {api_base}/chat/completions`，`Authorization: Bearer $KEY`。base_url `https://api.deepseek.com`（`/v1` 亦兼容）。

### 11.3 可复现与安全
- 提交必须能凭 `EXACT_PROMPT` 原文 + `deepseek-v4-flash` + `temperature=0` + `thinking=disabled` 复现判决。
- Key 只从 `DEEPSEEK_API_KEY` 读；日志/异常/审计记录**绝不打印 Key 或红线内容**。
- 模型/温度/thinking/单价全部 config 化，切模型只改 config 不改业务代码。

### 11.4 落地状态
- ✅ `auditor.py` 已迁移 `deepseek-v4-flash` + 非思考 + 返回 usage（B10 修复）。
- ✅ 调用方 `run.py` 已接入：按 §9.3 累加 usage、§9.4 写审计日志(`audit.jsonl`+`run.log`)、§9.5 增量续跑(`submission_inprogress.jsonl`+`progress.json`)、§9.6 并发调度(`ThreadPoolExecutor`)+SIGINT 优雅退出、§9.1 真实入口 `main.py`（B11 修复）。
  - 红线自检(§1.4)：仅针对红线表唯一指纹(`checkingpoints_all_elements_onesheet`/`onesheet`)，并断言 `cp_definitions.yaml` 路径 ≠ 红线 xlsx 路径（物理隔离）。**实施中抓出 `EXACT_PROMPT` 原 "checking point" 措辞，已改写回避。**
  - 运行模式：`--dry-run-retrieval`(只做检索写 `retrieval_dryrun.jsonl`, 不调 LLM)、`--estimate-only`(成本估算)、`--cases/--cps`(冒烟)、`--no-resume`(重跑)。
  - 依赖微调：`dense_index.py`/`reranker.py` 支持注入已加载模型实例，使全量 100 case 共享同一份 Qwen3 权重、各 case 独立 `build_corpus` 不互相覆盖。

---

## 12. 提交 Checklist（摘要）
- [ ] 红线禁用（推理未用设立标准）+ 验证期 GT 用法合规
- [ ] exact prompt + 模型名 + 超参固化且标注单位
- [ ] 命名一致（无 BGE 残留、无示例占位）
- [ ] 验证指标可复现（检索命中率 / 判决准确率 + GT 来源）
- [ ] LLM 调用分级重试 + 速率限制 + 超时（§9.2）
- [ ] token 统计 + 费用结算并写入 run log（§9.3）
- [ ] 结构化审计日志可回溯到每一条判决（§9.4）
- [ ] 中间结果增量落盘 + 断点续跑（§9.5）
- [ ] 并发 worker + 单写线程 + 优雅退出（§9.6）
- [ ] API Key 走环境变量，无 hard-code（§9.2 / §10）
- [ ] `.env` 不入库、`.env.example` 已提供（§10）
- [ ] 文本 LLM 调用收敛到单一客户端 `DeepSeekAuditor`（§11）
- [ ] 模型 = `deepseek-v4-flash`（非 `deepseek-chat`，后者 07-24 停用）（§2.2）
- [ ] 4100 次调用预计成本已估算并写入提交文档（§9.3）
