# FRECA Task 2 审计流水线 — 架构设计

> 本文档描述**当前仓库(`main` 分支)实际实现**的完整架构思路,从法规/案例处理一直到最终提交装配。
> 所有结论均可在 `src/freca/` 下源码中对照;行号/函数名在文中标注以便复核。
>
> 与简略版 `docs/ARCHITECTURE.md` 的差异见文末 §20。

---

## 1. 系统目标与规模

**任务**:对 100 个农场案例 × 41 个官方检查点(CP1–CP41)逐格做合规审计,每个格子输出 `1`(合规)/`0`(不合规)/`N/A`(不适用),共 **4,100** 个审计决策,最终装配成一张 `100 行 × 42 列` 的提交表(`RE Number` + `CP1..CP41`)。

**核心矛盾**:审计必须基于「检索到的法规条款 + 案例证据」做出可溯源判断,而非让模型凭空作答;同时案例数据里混入了「他家农场」的污染证据,必须识别并隔离,否则会把别人的合规材料算到当前农场头上。

**设计取向**:把流水线切成**幂等、可中断、可重放**的阶段,每阶段产物落盘;用多重机械门禁(引用校验、Verifier、仲裁、一致性)兜底 LLM 的不确定性;配置驱动、未配置的模型端点**优雅降级**到启发式实现,保证零配置也能跑通骨架。

---

## 2. 端到端数据流(总览)

```
                       ┌─────────────────────────────────────────────┐
   法规 PDF            │  阶段一  清单与源处理                         │
   案例目录(100 case) │   build_manifest  →  CaseManifest(100)       │
   检查点 xlsx ──────▶ │   load_checkpoints → CP1..CP41 定义          │
   署名污染真值 xlsx   │   SignatureTruthLoader → 污染索引            │
                       └────────────────────┬────────────────────────┘
                                            ▼
                       ┌─────────────────────────────────────────────┐
                       │  阶段二  证据解析 ingest_sources              │
                       │   PDF  : MinerU 或 PyMuPDF fallback          │
                       │   DOCX : 段落 / 表格 / 图像(+ 视觉描述)      │
                       │   XLSX : 分块 + 嵌入 RE 号校验                │
                       │   污染 Track 的 chunk 打 exclude 标签        │
                       └────────────────────┬────────────────────────┘
                                            ▼
                       ┌─────────────────────────────────────────────┐
                       │  阶段三  混合索引 build_hybrid_indexes        │
                       │   policy_index + case_index(按 case_id 隔离) │
                       │   BM25 + Vector → RRF → Reranker → MMR       │
                       └────────────────────┬────────────────────────┘
                                            ▼
          每个 (case_id, cp_id) = 1 个 AuditTask,共 4100 个
                       ┌─────────────────────────────────────────────┐
                       │  阶段四  检索 Agent(循环 ≤ max_repairs+1)    │
                       │   Planner → retrieve → Critic → Agent(STOP?) │
                       │   产出 RetrievalBundle                       │
                       └────────────────────┬────────────────────────┘
                                            ▼
                       ┌─────────────────────────────────────────────┐
                       │  阶段五  审计 audit_checkpoint                │
                       │   → AuditDecision(verdict / 引用 / facts)   │
                       └────────────────────┬────────────────────────┘
                                            ▼
                       ┌─────────────────────────────────────────────┐
                       │  阶段六  质量门                               │
                       │   validate_citations(机械,失败即阻断)       │
                       │   verify_decision(独立 Verifier 模型)        │
                       │   should_arbitrate? → 盲式 / 升级仲裁         │
                       │   不收敛 → BlockedTaskError                  │
                       └────────────────────┬────────────────────────┘
                                            ▼ final AuditDecision 落盘
                       ┌─────────────────────────────────────────────┐
                       │  阶段七  一致性门 run_consistency_gate        │
                       │   同 case 跨 CP 的 shared_facts 冲突 → BLOCK │
                       └────────────────────┬────────────────────────┘
                                            ▼
                       ┌─────────────────────────────────────────────┐
                       │  阶段八  提交装配 assemble_submission         │
                       │   4100 决策 → submission.xlsx(101×42)        │
                       └─────────────────────────────────────────────┘
```

单 case × 单 CP 的内部循环细节见 §8–§10。

---

## 3. 代码模块地图

| 模块 | 关键文件 | 职责 |
|---|---|---|
| 配置 | `config.py` | YAML → 强类型 `PipelineConfig`;端点/检索/仲裁/MinerU 全开关 |
| 数据模型 | `models.py` | 全部 Pydantic `StrictModel`(`extra="forbid"`),schema 即契约 |
| 清单 | `manifest.py` | 从目录结构恢复 case_id/track,叠加污染真值 |
| 检查点 | `cp.py` | 解析 41 个 CP 定义,校验 CP1..CP41 顺序 |
| 解析 | `parsing/{pdf,docx,xlsx,chunking,images,mineru}.py` | 源文件 → `EvidenceChunk`,保留溯源 |
| 署名污染 | `signatures.py` | 署名表 → 污染索引 → chunk 打标 |
| 索引 | `index/{store,bm25,vector,ranking,rerankers}.py` | `HybridIndex`:召回/融合/重排/选择 |
| 检索 | `retrieval.py` | `retrieve_for_checkpoint` 主循环 + Agent 实现 |
| Agent | `agent/{planner,critic,escalation,memory,tools}.py` | 规划/反思/升级仲裁/记忆/工具 |
| 审计 | `audit.py` | `audit_checkpoint` + prompt 构造 |
| 质量门 | `quality.py` | 引用校验 / Verifier / 仲裁触发 / 一致性 |
| 编排 | `pipeline.py` | 阶段串联、任务调度、装配 |
| 工作流 | `workflow.py` | prepare/pilot/full + 推广门 |
| 运行时 | `runtime.py` | `check_readiness` 就绪体检 |
| LLM 客户端 | `llm.py` | OpenAI 兼容 client + 缓存 + embedding + vision |
| 状态 | `state.py` | `TaskStore` + 原子写 JSON |
| 提交 | `submission.py` | 决策 → xlsx,多重形状校验 |
| CLI | `cli.py` | 全部子命令入口 |
| 消融 | `ablation.py` | 5 档检索变体实验 |

---

## 4. 配置体系与优雅降级

`config.yaml` 是唯一入口,`PipelineConfig.from_yaml` 强校验(`extra="forbid"`)。设计上**每一个 LLM/外部依赖都有「未配置」的兜底**:

| 配置项 | 未配置时(默认)的行为 |
|---|---|
| `models.audit` | **必填**,无兜底(没有主审计模型就无法跑) |
| `models.verifier` | 必填(pilot/full 阶段);缺失 → `BlockedTaskError` |
| `models.arbitrator` | 可选;缺失时风险任务直接阻断 |
| `models.embedding` | 退化为 `HashingEmbeddingProvider`(sklearn hashing,确定性本地向量) |
| `models.reranker` | `reranker_mode=lexical` 时不需要;其它模式未配则报错 |
| `models.planner/critic/tiebreaker` | `null` → 退化为 `HeuristicPlanner/HeuristicCritic` + 盲式仲裁 |
| `models.vision` | 未配 → 图像只保留占位 chunk,不生成中性描述 |
| `mineru.mode` | `disabled` → PDF 走 PyMuPDF 文本抽取 fallback |
| `retrieval.agent_mode` | `heuristic` → 规则驱动的 Gap 评估 |

**当前 `config.yaml` 的所有端点都是占位符**(`https://api.example.invalid/v1` + `configure-*-model`),`runtime.check_readiness` 会把含 `.invalid` 或 `configure-` 的端点判为 `ERROR/WARNING`。这意味着**开箱即跑的是骨架**:索引/检索/装配的机械路径全通,但任何需要 LLM 的阶段会在就绪检查处拦住。

`planner`/`critic`/`tiebreaker` 在默认配置里显式为 `null`,所以**默认行为 = 改 Agent 之前的旧版**:启发式 Planner + 启发式 Critic + 盲式仲裁。把这三项配上 LLM 端点即升级为 LLM 规划/反思 + 三级仲裁,无需改代码。

---

## 5. 阶段一:清单构建与法规/检查点处理

### 5.1 案例清单 `build_manifest`(`manifest.py`)

输入是 `cases_root` 下若干 `RE-XXX/` 目录,每个目录里是 `1_xxx_001.docx`、`8_xxx_001.xlsx` 这类文件。文件名编码了 track(首位数字)和 case_id(中间/末尾的数字):

- `recover_track`:文件名首字符 `1`–`9` → track 号;
- `recover_case_id`:早期命名 `1_Farm_001_` 与晚期 `_001.docx`(仅 track 8/9)两种正则兜底;
- 当一个 `RE-` 目录被多个 case 共享时,打 `shared_re_directory` / `duplicate_re_number` flag;
- 缺失的 track(1–9 中未出现的)打 `missing_track_N` flag(只报告,不补造)。

清单强制 `actual == set(range(1,101))`,差一个都报错——保证规模锁死在 100 案例。

### 5.2 检查点加载 `load_checkpoints`(`cp.py`)

从 `checkingpoints_all_elements_onesheet.xlsx` 读 41 列:第 1 行 `Element-N`、第 2 行 section、第 3 行 CP 文本、第 4 行 `CPn` ID。校验 `actual == [CP1..CP41]`,顺序错也报错。每个 `CheckpointDefinition` 带 `element_id`(1–4)、`element_title`、`section_title`、`text`、`source_file`、`cell`(溯源到具体单元格)。

> **Element 划分**(由 xlsx 提供,`planner.py` 注释中给出的对应关系):Element-1 经营业务范围、Element-2 建筑/设施、Element-3 控制体系、Element-4 追溯/检疫。Planner 的 Element→Track 映射依赖 `element_id`,不依赖 CP 编号,因此即使 CP 与 Element 的对应关系调整也不影响。

### 5.3 法规源 `build_policy_source`

把法规 PDF 包成 `SourceRecord`(`source_id="policy-rules-2021"`、`case_id=None`、sha256),交给解析阶段。

### 5.4 署名污染真值 `SignatureTruthLoader`(`signatures.py`)

读取用户整理的「文件署名整理表」xlsx,产出 `{re_number: ContaminatedCaseIndex}`,其中 `contaminated: dict[track → relation]`。关系归一为四类:

- `consistent` — 与 Track 1 一致,不算污染;
- `supplier` — 供应链材料,**合法,不视为污染**;
- `foreign_farm` — 他家农场证据,**污染**;
- 其它 — `unknown`。

真值在 `build_manifest` 阶段叠加到 `CaseRecord.contaminated_tracks`,并把 `expected_establishment_name`(Track 1 预期名称)写入 metadata,供一致性门比对。**只识别,不清洗**——证据原文保留,污染 Track 的 chunk 在解析阶段打标、在召回阶段隔离(见 §6.5、§7.6)。

---

## 6. 阶段二:证据解析 `ingest_sources`(`pipeline.py`)

逐源文件解析为 `EvidenceChunk` 列表,落盘到 `build/parsed/`。所有 chunk 都带 `source_sha256` / `parser_name` / `parser_version` / `SourceLocation`,**可追溯到源文件的具体位置**。

### 6.1 法规 PDF(`parsing/pdf.py`)

优先 MinerU(`mineru_client`),把结构化 `content_list` 归一为 heading/table/image/paragraph 块,带 page 与 bbox;MinerU 不可用时退化为 PyMuPDF 逐页文本,抽不出文字的页打 `page_text_empty` flag 并写占位内容(提示需 OCR 复核)。无论哪条路径,产物都标 `mineru_generated` 或 `mineru_unavailable`。

### 6.2 案例 DOCX(`parsing/docx.py`)

- 段落 → `PARAGRAPH` / `HEADING`(按 `paragraph.style`);
- 表格 → `TABLE`,逐行 `|` 拼接,记录行列数;
- 内嵌图像 → 从 OOXML `word/media/` 抽出落盘,生成 `IMAGE` chunk(`vision_description_pending`);若配了 `vision` 模型,再生成一条 `IMAGE_DESCRIPTION` chunk(`derived_from` 指向原图 chunk,`model_generated_neutral_description` flag)。视觉描述的 system prompt 强制「中性描述可见内容,不做合规判断」。

### 6.3 案例 XLSX(`parsing/xlsx.py`)

按 `max_rows=20` 分块,每块一条 `TABLE` chunk,内容形如 `A1=值 | B1=值`。解析时用正则扫嵌入的 `RE-XX-0000-0000`,若与该源 `re_number` 不符,打 `embedded_re_number_mismatch` flag——这是**解析侧的污染初筛**,与 §5.4 的署名真值交叉验证(以真值为准)。

### 6.4 chunk_id 稳定性(`parsing/chunking.py`)

`stable_chunk_id(source, locator) = f"{source_id}_{safe_locator}_{sha256(source_id|sha256|locator)[:10]}"`。**纯函数、确定性**,所以同一份源文件 + 同一解析器产出的 chunk_id 跨运行稳定 → 模型调用缓存可命中(§14.4)。

### 6.5 污染标注 `annotate_chunks`(`signatures.py`)

对落在 `case.contaminated_tracks` 里的 chunk 追加两个 flag:`track_contaminated:{track}:{relation}` 与 `exclude_from_compliance_evidence`。后者是**全局隔离信号**:索引层召回时会把它物理排除(§7.6),审计层 prompt 也会显式警告(§9.2)。

### 6.6 flag_and_continue 容错

单个源解析失败不中断整体:写 `track-N.error.json`,记录错误,继续下一个。`ingest-report.json` 汇总 `failures` / `chunk_flag_counts` / `contaminated_chunk_counts` / `data_quality`,供人审。

---

## 7. 阶段三:混合索引构建 `build_hybrid_indexes`

构建两个 `HybridIndex`(`index/store.py`):`policy_index`(`case_id=None`)与 `case_index`(`case_id` 必填)。构造时强校验 scope——policy 索引不能含 case chunk,反之亦然,从源头防混。

`HybridIndex.search` 是检索的核心,一次调用走完五步:

### 7.1 召回(BM25 + Vector)

- BM25(`index/bm25.py`,`rank_bm25` + 自定义 tokenizer,保留 `A-B_C/D` 这类连字符 token);
- Vector(`index/vector.py`):配了 `embedding` 端点用语义向量,否则 `HashingEmbeddingProvider`(4096 维 word 1-2 gram,确定性 fallback)。
- `recall_mode`: `hybrid`(默认)/ `bm25` / `vector`。

### 7.2 融合(RRF / weighted)

- `fusion_mode=rrf`(默认):`reciprocal_rank_fusion`,`score += 1/(k+rank)`,`k=60`,再归一化;
- `weighted`:`bm25_weight * norm(bm25) + vector_weight * norm(vector)`;
- `none`:单路归一化。

### 7.3 重排

`reranker_mode`:
- `lexical`(默认):`lexical_rerank_score`,token 重叠率 + 短语命中 bonus,纯本地;
- `cross_encoder_api`:`CrossEncoderApiReranker`,调外部 `/rerank`,带 429/5xx 重试,强校验「每个候选恰好返回一次、分数 ∈ [0,1]」;
- `llm_listwise`:`LLMListwiseReranker`,让 LLM 对候选打分;
- `none`:跳过。

最终 `relevance = fusion_weight * fusion_score + reranker_weight * rerank_score`(默认 0.45 / 0.55)。

### 7.4 选择(Top-K / Source-Aware MMR)

- `top_k`:按 relevance 直取前 N;
- `source_aware_mmr`(默认):`select_with_mmr_trace`(`index/ranking.py`),`mmr = λ·relevance − (1−λ)·diversity`,`λ=0.65`。diversity 含四项惩罚:同 source(0.5)、同 track(0.15)、同 location(0.1)、覆盖惩罚(未达 `min_unique_sources=2` 时优先选新 source)。**目的**:避免同一份文件反复占坑,强制证据来源多样性,为「正反证据并存」留空间。每个候选的 MMR 打分全程留 trace,可复盘为什么被选/被淘汰。

### 7.5 候选上限

`candidate_limit=40` 先截断再重排/选择,policy 取 6 条、case 取 10 条(`retrieve_for_checkpoint` 的 `policy_limit`/`evidence_limit`)。

### 7.6 污染证据物理隔离

`search` 开头把带 `exclude_from_compliance_evidence` 的 chunk 从 `eligible_subset` 里**剔除**(只留 trace 记录被排除原因),不参与打分、不进候选。这是污染隔离的第一道闸,发生在召回阶段——污染证据根本不进检索结果。

---

## 8. 阶段四:检索 Agent(Planner→Retrieve→Critic→Agent 循环)

`retrieve_for_checkpoint`(`retrieval.py`)是单 case×CP 的检索编排,最多 `max_repairs+1` 轮(默认 3 轮)。

### 8.1 初始查询构造 `build_initial_queries`

把 `cp_id + element_title + section_title + checkpoint.text` 拼成 `official`,再分别派生 policy_query(加 `applicability obligation exception condition time requirement definition`)与 evidence_query(加 `farm evidence records facilities status dates supporting and contradictory evidence`)。两路查询分别打 policy_index 与 case_index。

> ⚠ **已知约束(见 §18)**:`checkpoint.text`(CP 全文)进入了检索 query。这是当前实现的真实行为,文档据实记录。

### 8.2 Tier-1 Planner(`agent/planner.py`)

第一轮前先 `planner.plan(checkpoint, case_id, available_tracks)`,产出 `PlannerPlan`:先查哪些 `target_tracks` / `target_content_kinds`。

- `HeuristicPlanner`(默认):`_ELEMENT_TRACK_MAP` 静态映射(Element-1→[1]、Element-2→[5,6]、Element-3→[2,3,4,6]、Element-4→[8,9]),再与该 case 实际存在的 track 求交集,避免空集;
- `LLMPlanner`:调 LLM 给更细粒度规划,**安全过滤** target_tracks 必须在 available_tracks 内,且 system prompt 明令「不得出 1/0/N/A」。

Planner 的产物作为第一轮的 track/kind 过滤器传给 `HybridIndex.search`。

### 8.3 检索与合并

每轮:policy_index.search(policy_query) + case_index.search(evidence_query, case_id=…, allowed_tracks, content_kinds)。`_merge_hits` 按 chunk_id 去重合并,取分数高者,保留前 `limit` 条;新增的 chunk_id 记进 `added_*`。

### 8.4 Tier-3 Critic(`agent/critic.py`)

对合并后的合集做反思,产出 `CriticDecision`:

- `HeuristicCritic`(默认)四规则:① 同 `source_id` ≥3 次出现 → 低分副本 `weighted_down`(合并时跳过,证据仍可见);② 含反证关键词(fail/absent/expired…)但未标 contrary → flag;③ 含近答案字段(fully compliant/audit scenario…)→ flag(防 LLM 当 label);④ Element-1 缺注册范围锚点 → 记 missing;
- `LLMCritic`:LLM 评估,受 **30% drop 上限**约束(`maxItems = max(1, round(len*0.3))`),超限视为不可靠信号忽略。system prompt 明令「不得裁决合规」。

被 `weighted_down`/`drop` 的 chunk 从本轮合集移除,记进 `dropped_chunk_ids`。

### 8.5 RetrievalAgent(STOP/RETRIEVE,Gap 驱动)

`agent.decide(…)` 评估「上下文是否足够」,**只决定停止或继续检索,绝不裁决合规**:

- `HeuristicRetrievalAgent`(默认):`HeuristicContextAssessor` 检查 policy/evidence 是否齐全、是否有适用性条款、时间/留存维度、来源多样性,产出 gaps;有 gap 就 RETRIEVE 并改写 query,无 gap 就 STOP;
- `LLMRetrievalAgent`:LLM 评估,system prompt 强制「不把案例里的答案式文本当 label/ground truth;只在上下文足够时 STOP,否则给两条聚焦 query + 可选 track/kind 过滤」;
- `DisabledRetrievalAgent`:只做最小机械可用性门(policy/evidence 各有至少一条)。

`RetrievalAgentDecision` 有强 schema 约束:STOP 必须 `complete=true`,RETRIEVE 必须带非空 policy/evidence query——模型不能含糊其辞。

### 8.6 终止条件与 stop_reason

`stop_reason` 取 `complete` / `no_new_chunks` / `repeated_query` / `max_repairs` 之一:
- Agent 判 STOP 且上下文齐全 → `complete`;
- 一轮没新增任何 chunk → `no_new_chunks`;
- Agent 改写的 query 与历史重复 → `repeated_query`(防死循环);
- 达到 `max_repairs` → `max_repairs`。

还有一个硬闸:`rejected_stop`——Agent 想 STOP 但 policy/evidence 其实缺一条时,拒绝停止,补 gap 继续。

### 8.7 跨 case 污染硬阻断

`retrieve_for_checkpoint` 收尾检查:若 evidence 命中的 `chunk.case_id != 请求 case_id`,**立即 `RuntimeError`**。这是比 §7.6 更靠后的第二道防线,确保绝无跨 case 串味。整个循环每一轮的查询、候选 trace、Agent 决策、Critic 决策都写进 `RetrievalRound`,最终 `RetrievalBundle` 完整可复盘。

---

## 9. 阶段五:审计裁决 `audit_checkpoint`(`audit.py`)

### 9.1 AuditDecision schema(`models.py`)

```
applicability: APPLICABLE / NOT_APPLICABLE / UNKNOWN
verdict: 1 / 0 / N/A
regulatory_requirement, policy_citations[], supporting_evidence[],
contrary_evidence[], contradictions[], reasoning_summary, confidence,
retrieval_complete, review_flags[], shared_facts{}
```

强语义校验:`N/A` 必须 `applicability=NOT_APPLICABLE` 且有 policy 支撑;反之 `NOT_APPLICABLE` 必须 `N/A`。`shared_facts` 是该 CP 抽取的跨 CP 共享事实(如企业名、商品、日期),供一致性门比对。

### 9.2 审计 prompt 与污染证据规则

`build_audit_messages` 把 `checkpoint.model_dump()`(CP 全文)+ policy_hits + evidence_hits(带 `contamination_notice`)+ 检索状态拼成 user message。system prompt(`_AUDIT_SYSTEM`)对污染证据定了硬规则:

- 被标 `exclude_from_compliance_evidence` 的 chunk 默认当**反证**;
- 不得作为合规判定的**唯一**支持证据;
- 若某 CP 仅有的证据是污染的 → verdict `0` + `signature_foreign_evidence_only` flag;
- 污染证据里若含当前 CP 唯一可用的注册范围/商品/企业名/场所信息 → verdict `0`。

> ⚠ **已知约束(见 §18)**:`checkpoint.model_dump()`(含 CP 全文)进入了审计/Verifier/仲裁 prompt。文档据实记录。

### 9.3 模型响应强校验

`complete_json` 走 `json_schema` strict 模式;返回后 `AuditDecision.model_validate` 再过一遍 schema,且校验 `case_id`/`cp_id` 与检索身份一致,否则 raise。模型返回错 case 直接拦。

---

## 10. 阶段六:质量门(引用校验 + Verifier + 仲裁)

`process_audit_task`(`pipeline.py`)是单任务的完整质检链。

### 10.1 引用真实性校验 `validate_citations`(`quality.py`)

机械、确定、失败即阻断。逐条检查:
- 每个 policy_citation 必须在 `retrieval.policy_hits` 里存在;
- 每个 evidence_citation 必须在 `retrieval.evidence_hits` 里、且 `case_id` 与决策一致;
- `IMAGE_DESCRIPTION` 必须有 `derived_from`(不能拿孤立描述当证据);
- 污染 chunk 不得出现在 `supporting_evidence`(污染证据不能支持合规);
- 必须有至少一条 policy 引用与一条 evidence 引用;
- `COMPLIANT` 必须有 supporting_evidence。

这一步挡住「模型编造 chunk_id」「引用别家案例」「拿污染证据支持合规」等典型泄漏。

### 10.2 Verifier 独立复核 `verify_decision`

用**独立的 verifier 模型**(不同端点/模型)对决策做二次审查:每条法规要求是否有 policy 支撑、每个事实主张是否有 cited chunk 支撑、反证是否被忽略、N/A 是否有 applicability 支撑。返回 `PASS` / `FAIL` / `UNCERTAIN` + issues。**只复核不改判**。

### 10.3 仲裁触发 `should_arbitrate`

任一为真即触发仲裁:
- `confidence < 0.65`;
- `retrieval_complete == False`;
- 有 `review_flags`;
- 引用校验未过;
- Verifier 非 PASS;
- 有一致性 findings。

### 10.4 盲式仲裁 vs 升级仲裁

- **盲式**(`arbitration.tier=blind`,默认,`quality.arbitrate_checkpoint`):用 arbitrator 模型**重做一次审计**,比 verdict + applicability 是否一致。一致 → `ACCEPT_AGREEMENT`;不一致 → `REVIEW_DISAGREEMENT`。
- **升级**(`tier=escalated`,`agent/escalation.py`):盲式分歧后,若配了 `tiebreaker` 模型,做**第三次审计**,三个 verdict 多数票(≥2 一致)→ `ACCEPT_MAJORITY`;三模型全不一致 → `THREE_WAY_TIE`。`tiebreaker` 未配则静默降级回盲式(不报错)。

### 10.5 不收敛即阻断

仲裁后只有 `ACCEPT_AGREEMENT` / `ACCEPT_MAJORITY` 算收敛;否则 `BlockedTaskError`。收敛后还要再过一次 second 的引用校验 + Verifier(必须 PASS),任何一环不过都阻断。**阻断的任务状态置 `BLOCKED`,不会进提交**。

若未触发仲裁,则要求首次引用校验过 + Verifier PASS,否则同样阻断。

---

## 11. 阶段七:一致性门 `run_consistency_gate`(`pipeline.py`)

所有任务跑完后,按 case 聚合 `final/` 下的决策,用 `find_consistency_issues` 检查**同 case 跨 CP 的 `shared_facts`**:同一 `fact_key`(如 `_establishment_name`)在不同 CP 取了不同值 → 产 `ConsistencyFinding`,把涉及的 task 置 `BLOCKED`。

`find_signature_consistency_issues` 还会比对 `shared_facts._establishment_name` 与 manifest 的 `expected_establishment_name`(Track 1 预期名),不一致也告警。**只告警让仲裁复判,不硬改 verdict**。

装配前要求一致性报告 `finding_count == 0`,否则 `assemble_run_submission` 直接 raise。

---

## 12. 阶段八:提交装配 `assemble_submission`(`submission.py`)

多重门禁后装配:

1. `len(tasks) == 4100`(少一个都不行);
2. `unresolved_tasks == 0`(无 PENDING/RUNNING/BLOCKED/FAILED);
3. 一致性报告存在且 `finding_count == 0`;
4. manifest 恰好 100 case;
5. 模板必须是**仅表头**的官方模板(`max_row==1`),防误用已填充模板;
6. 表头严格等于 `["RE Number", "CP1", ..., "CP41"]`(42 列);
7. 每个 verdict ∈ `{1, 0, N/A}`;
8. 重复 RE Number 需主办方确认或显式 `--allow-unconfirmed-identifiers`。

装配后重载校验形状 `101×42`、表头未变、无空格/非法值,最后算 sha256。`SubmissionReport` 返回路径/行列/决策数/sha256/重复 RE 列表。

> **提交表形状 = 101 行(1 表头 + 100 案例)× 42 列(RE Number + CP1..CP41)。无 `submission_id` 列。**(简略版 `docs/ARCHITECTURE.md` 把这处写错了,见 §20。)

---

## 13. 工作流编排(`workflow.py`)

三档工作流,每档先过 `check_readiness` 就绪体检:

- **`prepare`**:manifest → ingest → index。产物落盘,不跑 LLM;
- **`pilot`**:按 `pilot_cases.json` 跑子集(case_ids × cp_ids),跑完审计 + 一致性门,产 `promotion_ready` 标志。`pilot_cases.json` 强校验(case_id 唯一 1–100、cp_ids 为 `ALL` 或合法列表、`task_count` 自洽);
- **`full`**:**必须先有已推广的 pilot 报告**(`status==COMPLETED && promotion_ready`),否则 `blocker: pilot_not_promoted`。然后跑全部 4100 任务 + 一致性门 + 装配。

`run`(CLI)是 prepare→audit→consistency→assemble 的单命令串联。`doctor` 按 stage 体检:路径存在性、MinerU、模型端点占位符检测、必需/可选模型 API key 是否设置。

**推广门的设计意图**:先用小成本子集验证检索/审计/门禁链路正常、一致性无冲突,再放全量,避免 4100 个任务跑完才发现系统性问题。

---

## 14. 任务持久化、并发与确定性

### 14.1 TaskStore 状态机(`state.py`)

每个 run 一份 `build/state/{run_id}-tasks.json`,4100 条 `AuditTask`。状态:`PENDING → RUNNING → COMPLETED` / `BLOCKED` / `FAILED`。`initialize` 幂等(已存在则返回 existing),`reset` 把 BLOCKED/FAILED 按 case/cp 过滤后重置为 PENDING(CLI `retry`)。线程安全(`RLock`)。

### 14.2 并发与隔离

`run_pending_tasks` 用 `ThreadPoolExecutor(max_workers)`(默认 pilot=2、full=4)并发跑 PENDING 任务。**任务级隔离**:单个任务抛异常只影响自己(`BlockedTaskError`→BLOCKED,其它→FAILED),不拖垮整批,错误信息持久化。`as_completed` 收集结果。

### 14.3 原子落盘 `atomic_write_json`

写临时文件 → `fsync` → `os.replace` 原子替换,排序键 + `ensure_ascii=False`。并发下不会写出半截 JSON。

### 14.4 模型调用缓存与 ledger(`llm.py`)

`CachedJsonClient` 包裹真实 client:按 `(client_name, model_metadata, system, user, schema)` 算 `request_hash`,命中则读 `build/cache/models/{name}/{hash}.json`,未命中调模型并写缓存。同时把每次调用(含 cache_hit 标志、完整 prompt 与响应)append 到 `build/logs/model-calls.jsonl` ledger。`model_metadata` 会过滤含 `key`/`token` 的字段,避免泄漏到 ledger。`temperature=0` + 缓存 → **同一配置下结果可复现**,也便于消融对比。

### 14.5 Agent 记忆(`agent/memory.py`)

- `CaseMemory`:单 case 跨 CP 累积 `shared_facts`/verdict/confidence,落盘 `build/memory/cases/{case_id}.json`。`process_audit_task` 在 final 落盘后调 `update` 写入;`facts_so_far()` 聚合当前 case 已有事实,**预留**给后续 CP 的 audit prompt 注入(当前未在 prompt 中接回,属预留基础设施);
- `FailureModeMemory`:`build/memory/failure_modes.jsonl`,按 `gap_signature` 滚动裁剪(每签名最多 100 条),`record_failure_mode()` 作为外部错误处理钩子暴露,当前未接入 happy path。

---

## 15. CLI 命令清单(`cli.py`)

```
manifest    建 100-case 清单
doctor      就绪体检(--stage prepare/pilot/full)
prepare     manifest + 解析 + 索引(--no-mineru)
ingest      仅解析(--case-id / --no-mineru)
index       仅建索引
retrieve    单 (case,cp) 检索冒烟(--case-id --cp-id)
audit       跑审计任务(--run-id,可限定 case/cp,--max-workers)
status      查任务状态
consistency 跑一致性门
assemble    装配提交表
pilot       跑试点 + 推广门(--pilot-file)
full        跑全量 4100 + 装配(需 pilot 已推广)
run         prepare→audit→consistency→assemble 单命令
retry       重置 BLOCKED/FAILED → PENDING
report      写本地验证报告
ablation    消融:list / run / report
```

---

## 16. 落盘产物结构

```
build/
├── manifests/cases.json        ← 100 case 清单 + 污染真值
├── parsed/
│   ├── checkpoints.json        ← CP1..CP41 定义
│   ├── policy.json             ← 法规 chunk
│   ├── cases/{case}/track-N.json
│   ├── images/{case}/          ← 抽出的图像
│   └── ingest-report.json
├── indexes/{policy,cases}.json ← 双索引
├── retrieval/{case}/{cp}.json  ← RetrievalBundle(含每轮 trace)
├── decisions/{case}/{cp}.json  ← 首次决策 + 引用校验
├── verification/{case}/{cp}.json
├── arbitration/{case}/{cp}.json
├── final/{case}/{cp}.json      ← 入提交的最终决策
├── consistency/{run_id}.json
├── state/{run_id}-tasks.json   ← TaskStore
├── memory/{cases/{case}.json, failure_modes.jsonl}
├── cache/models/{name}/*.json  ← 模型调用缓存
├── logs/model-calls.jsonl      ← 调用 ledger
├── runs/{pilot-001,full-…}.json← 工作流报告
└── submission.xlsx             ← 101×42 最终提交
```

---

## 17. 关键设计原则汇总

1. **幂等可重放**:阶段产物全落盘 + chunk_id 确定性 + 模型缓存,任意一阶段可单独重跑;
2. **多重门禁兜底**:引用校验(机械)→ Verifier(模型)→ 仲裁(模型)→ 一致性(机械),层层过滤 LLM 不确定性;
3. **flag_and_continue**:解析/审计的错误只 flag 不中断,失败任务隔离,不污染整体;
4. **污染物理隔离**:召回阶段剔除 `exclude_from_compliance_evidence`,审计阶段硬规则降权,跨 case 立即 raise——三道闸;
5. **职责切分**:Planner 只规划、Agent 只控检索完整性、Critic 只反思、Audit 只裁决、Verifier 只复核、Arbitrator 只仲裁——各层 system prompt 都明令「不得越权裁决 1/0/N/A」;
6. **优雅降级**:每个外部依赖都有兜底,零配置跑骨架,配置即升级;
7. **证据多样性**:Source-Aware MMR 强制多来源,为正反证据并存留空间;
8. **溯源**:每个 chunk → 源文件 sha256 + 位置,每个 citation → chunk_id 反查,审计可追溯。

---

## 18. 已知约束与红线

- **CP 全文进入模型上下文**:`build_initial_queries` 把 `checkpoint.text` 放进检索 query;`build_audit_messages` / `verify_decision` / `arbitrate_checkpoint` 把 `checkpoint.model_dump()` 放进 prompt。`checkingpoints_all_elements_onesheet.xlsx` 在赛题语义里是「检查点↔法规」的答案级映射,理论上不应进入 AI 输入。当前实现未做物理隔离,这是已知的待处理项(暂未改动);
- **配置占位符**:默认 `config.yaml` 所有端点为 `api.example.invalid` + `configure-*`,开箱只能跑骨架,真跑需在 `.env` 配真实 `FRECA_*_API_KEY` 与端点(`.env` 已被 `.gitignore` 排除,不会推送);
- **CaseMemory 注入未接回**:`facts_so_far()` 已实现但未注入后续 CP 的 audit prompt,跨 CP 事实累积目前仅落盘;
- **`failure_modes.jsonl` 未接入 happy path**:`record_failure_mode()` 仅作外部钩子暴露。

---

## 19. 消融实验(简述)

`ablation.py` 暴露 5 档检索变体,用于离线对比召回/融合/重排/选择/Agent 各组件的贡献:

| 变体 | recall | fusion | reranker | selector | agent | repair |
|---|---|---|---|---|---|---|
| `bm25_only` | bm25 | none | none | top_k | disabled | 0 |
| `vector_only` | vector | none | none | top_k | disabled | 0 |
| `weighted_hybrid` | hybrid | weighted | none | top_k | disabled | 0 |
| `rrf_reranker_no_mmr` | hybrid | rrf | rerank | top_k | disabled | 0 |
| `full_retrieval` | hybrid | rrf | rerank | mmr | base | base |

> 更细差异用 `config.yaml` 调,不开新变体。详见 `docs/ABLATION.md`。

---

## 20. 与简略版 `docs/ARCHITECTURE.md` 的差异

本文档为完整设计版。简略版 `docs/ARCHITECTURE.md` 有两处与代码不符,以本文档为准:

1. **提交列数**:简略版称「42 列(含 `submission_id` 等)」——实际为 `RE Number` + `CP1..CP41` 共 42 列,**无 `submission_id`**(`submission.py:13`、`submission.py:87`);
2. **案例数历史**:简略版提「96 案例(已迁移)」——当前代码强制 100 案例(`manifest.py:106`、`submission.py:50`),96 为已废历史口径。

如需统一,可删除简略版或修正上述两行。
