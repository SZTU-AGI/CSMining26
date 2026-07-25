# FRECA Task 2 — 技术方向探究

> 版本：v3.0 · 2026-07-17
> 视角：除了"两阶段 Pipeline"，还有什么架构可以选？
> 目的：穷举可能的技术方向 → 评估优劣 → 推荐组合

---

## 0. 总览：8 大技术方向

| # | 方向 | 核心思想 | 复杂度 | 准确率 | 工程量 |
|---|---|---|---|---|---|
| 1 | **RAG + 单 LLM** | 政策切片 + 证据喂给 LLM | ⭐ | ⭐⭐ | ⭐ |
| 2 | **Tool-Use Agent** | LLM 调用工具（parser/searcher）迭代推理 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| 3 | **Multi-Agent Debate** | 多个 LLM 角色辩论投票 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 4 | **Knowledge Graph** | 构建 RE-CP-Rule 知识图谱 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 5 | **Constrained Decoding** | 用 schema 强制输出格式 + 推理 | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 6 | **Active Learning Loop** | 跑几轮 + 人工标注 + 微调 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 7 | **Hybrid: RAG + Tool-Use + KG** | 三者结合 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 8 | **Pure Prompt Engineering** | 反复调 prompt，不引入复杂架构 | ⭐ | ⭐⭐ | ⭐ |

---

## 1. 方向 1：RAG + 单 LLM（基准方案）

### 1.1 架构

```
[政策 PDF] → 切块 → 向量化 → ChromaDB
                                ↓
[9 份证据] → 索引 → 元数据 (case_id, track)
                                ↓
            [用户查询: "CP17 是否满足?"]
                                ↓
              检索: 政策相关段落 + 同 case 证据
                                ↓
                  [LLM 一次推理 → 裁决]
```

### 1.2 实现栈

- 向量化：`sentence-transformers` / `bge-large-zh-v1.5` / OpenAI Embedding
- 向量库：ChromaDB（轻量）/ Pinecone（云）/ FAISS（本地）
- 切块策略：按章节 / 按段落 / 按 token
- 检索：top-k = 5-10，多 query 检索

### 1.3 优劣

| 优势 | 劣势 |
|---|---|
| 实现简单 | 检索质量决定上限 |
| 上下文可控 | 多模态处理弱 |
| 调试容易 | 不擅长多跳推理 |

### 1.4 适合场景

MVP / 快速验证 / 数据规模小

---

## 2. 方向 2：Tool-Use Agent

### 2.1 架构

```
[LLM Agent]
  ↓ 决定调用工具
[Tool 1: docx_parser] → 提取证据段落
[Tool 2: policy_search] → 检索政策章节
[Tool 3: cross_check] → 跨文档对比
[Tool 4: scoring] → 计算 trust_score
  ↓
  自主迭代 3-10 步
  ↓
[最终裁决]
```

### 2.2 实现栈

- Agent 框架：LangChain / LangGraph / AutoGen / CrewAI
- 工具定义：每个工具是一个 Python 函数 + JSON schema
- ReAct / Plan-and-Execute 模式

### 2.3 优劣

| 优势 | 劣势 |
|---|---|
| 自主决策，可处理复杂情况 | 慢（多轮调用） |
| 工具组合灵活 | 调试困难 |
| 可解释性强（每步可追溯） | 可能陷入循环 |

### 2.4 适合场景

复杂决策 / 需要多次信息检索 / 异构证据

### 2.5 本任务的具体化

```python
# 工具集
@tool
def read_track(track_id: int) -> str:
    """读取指定 track 的证据内容"""

@tool
def search_policy(query: str, k: int = 3) -> list[str]:
    """在政策 PDF 中搜索相关章节"""

@tool
def check_cross_doc(topic: str) -> list[dict]:
    """检查同一 case 跨 track 的矛盾"""

@tool
def get_cp_definition(cp_id: str) -> str:
    """获取 CP 原文"""

# Agent 决策
agent = create_react_agent(llm, tools=[
    read_track, search_policy, check_cross_doc, get_cp_definition
])

# 一次 case 推理
result = agent.invoke({
    "input": f"审计 case {case_id} 的 CP {cp_id}",
    "evidence_paths": [...],
})
```

---

## 3. 方向 3：Multi-Agent Debate（多角色辩论）

### 3.1 架构

```
            ┌────────────────┐
            │  Auditor (主)   │
            └───────┬────────┘
                    ↓ 给出初步意见
        ┌───────────┴───────────┐
        ↓                       ↓
  ┌──────────┐            ┌──────────┐
  │Proponent │            │ Opponent │
  │ (支持合规)│            │(支持违规)│
  └────┬─────┘            └────┬─────┘
       └───────────┬───────────┘
                   ↓ 辩论 3 轮
            ┌────────────┐
            │   Judge    │
            │ (终审)     │
            └─────┬──────┘
                  ↓
            [最终裁决]
```

### 3.2 实现栈

- 框架：AutoGen（CrewAI 也可，但 AutoGen 更成熟）
- 角色定义：System prompt 区分
- 辩论协议：每轮 1-3 句话，限制 token

### 3.3 优劣

| 优势 | 劣势 |
|---|---|
| 准确率高（多个视角） | 极慢（3-5x 调用） |
| 偏见少 | 成本高（3-5x token） |
| 可解释性强 | 角色设计复杂 |

### 3.4 适合场景

高价值决策 / 准确率优先 / 成本不敏感

### 3.5 本任务的可行性

**不太适合**——96 case × 41 CP = 3,936 任务，每个任务跑 3 角色辩论 = 11,808 次调用，成本 3-5x。但**可选用于 LOW trust_score 的 case**（占少数）。

---

## 4. 方向 4：Knowledge Graph（知识图谱）

### 4.1 架构

```
                   ┌─────────────────┐
                   │ Policy PDF      │
                   │  + 41 CP 定义   │
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │  LLM 抽取       │
                   │  "Rule 4-2 包含 │
                   │   C1, C2, C3   │
                   │   对应 CP8-10" │
                   └────────┬────────┘
                            ↓
        ┌──────────────────────────────────┐
        │  Knowledge Graph                 │
        │  - RE (注册机构)                  │
        │  - CP (检查点)                    │
        │  - Rule (政策条款)                │
        │  - Evidence (证据)                │
        │  - Status (合规状态)              │
        │  关系: requires, satisfies, etc.  │
        └──────────────┬───────────────────┘
                       ↓
              [Graph Reasoning]
                  SPARQL / Cypher
                       ↓
              [最终裁决]
```

### 4.2 实现栈

- 图库：Neo4j / NetworkX（轻量）
- 抽取：LLM + schema 引导
- 推理：Cypher / SPARQL / 程序化

### 4.3 优劣

| 优势 | 劣势 |
|---|---|
| 推理可解释 | 抽取成本高 |
| 支持复杂查询 | 图构建需要时间 |
| 知识沉淀 | 更新困难 |

### 4.4 风险

**赛题明令禁止"硬编码 CP 规则到 prompt"**——KG 抽取本质上是把规则结构化，存在被裁定违规的风险（详见 DATA_ANALYSIS_REPORT.md 问题 7）。

### 4.5 本任务的可行性

**低推荐**——如果组委会裁定 KG 抽取 = 硬编码，整个方案作废。除非组委会明确说 OK，否则不建议走这条路线。

---

## 5. 方向 5：Constrained Decoding（约束解码）

### 5.1 架构

```
[LLM] → [输出层加约束] → 必须是 "1" / "0" / "N/A" 之一
                              ↓
                    [无效 token 概率归零]
                              ↓
                      [合法裁决]
```

### 5.2 实现栈

- Outlines / Guidance / SGLang
- 自定义 tokenizer mask
- JSON schema 约束

### 5.3 优劣

| 优势 | 劣势 |
|---|---|
| 输出格式 100% 合规 | 不能控制推理过程 |
| 无需 retry | 不能解决"推理错"问题 |
| 速度快 | 多模态支持弱 |

### 5.4 本任务的可行性

**作为辅助方案**——和方向 1 或方向 2 搭配，用 constrained decoding 防止输出格式错误，但推理仍交给 LLM。

---

## 6. 方向 6：Active Learning Loop（主动学习）

### 6.1 架构

```
[初始 LLM] → 跑 10 个 case → 人工标注
       ↓
[用这 10 个微调 LoRA] → 跑下一个 10 个 → 人工标注
       ↓
[继续微调] → 重复直到 96 个
       ↓
[最终模型] → 跑全 96 个
```

### 6.2 实现栈

- LoRA: PEFT + bitsandbytes
- 基础模型：Qwen2.5-72B / Llama-3.3-70B
- 训练框架：HuggingFace Transformers + Accelerate

### 6.3 优劣

| 优势 | 劣势 |
|---|---|
| 准确率最高 | 需要标注数据 |
| 推理快（本地） | 训练成本高 |
| 可解释（模型本身） | 需要 GPU 集群 |

### 6.4 本任务的可行性

**最不确定**——组委会明确说"不会提供带标签训练集"（待确认）。即使提供，10-20 个样本微调 70B 模型至少需要 A100 × 4 跑 1 天。

**不推荐**——投入产出比低。除非组委会提供大量标注样本。

---

## 7. 方向 7：Hybrid（RAG + Tool-Use + KG）

### 7.1 架构

```
┌─────────────────────────────────────────────┐
│  Layer 1: KG（离线构建）                     │
│  - RE → CP → Rule 三元组                    │
│  - 预计算"该 case 应该审计哪些 CP"             │
└──────────────┬──────────────────────────────┘
               ↓ 提供检索起点
┌─────────────────────────────────────────────┐
│  Layer 2: RAG（在线检索）                    │
│  - 政策 PDF 章节检索                          │
│  - 同 case 证据段落检索                       │
└──────────────┬──────────────────────────────┘
               ↓ 提供候选证据
┌─────────────────────────────────────────────┐
│  Layer 3: Tool-Use Agent（推理）             │
│  - 用工具读证据                              │
│  - 跨文档对比                                │
│  - 调用 KG 推理                              │
│  - 综合裁决                                  │
└─────────────────────────────────────────────┘
```

### 7.2 优劣

| 优势 | 劣势 |
|---|---|
| 各取所长 | 实现复杂 |
| 准确率最高 | 调试困难 |
| 可解释性中等 | 成本中等-高 |

### 7.3 本任务的可行性

**过度设计**——96 case 跑一次，没必要搭 KG。**仅在 RAG 不够用时考虑升级**。

---

## 8. 方向 8：Pure Prompt Engineering

### 8.1 架构

```
[精心设计的 prompt]
  ↓
[LLM 一次调用 → 41 个裁决]
```

### 8.2 优劣

| 优势 | 劣势 |
|---|---|
| 实现最简 | 准确率上限低 |
| 调试最快 | 不可扩展 |
| 成本最低 | 受 prompt 影响大 |

### 8.3 本任务的可行性

**作为 baseline / MVP**——先用纯 prompt 跑通，看准确率。如果够用，就不用复杂架构。

---

## 9. 各方向详细对比矩阵

| 维度 | 1 RAG | 2 Tool-Use | 3 Debate | 4 KG | 5 Constrained | 6 Active | 7 Hybrid | 8 Pure Prompt |
|---|---|---|---|---|---|---|---|---|
| **准确率** | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| **速度** | ⭐⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| **成本** | ⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐ |
| **可解释** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| **可调试** | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐⭐ |
| **可扩展** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐ |
| **合规风险** | 低 | 低 | 低 | **高** | 低 | 中 | 高 | 低 |
| **工程量** | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐ |

---

## 10. 组合策略（推荐）

不是单一方向，而是**组合**：

### 10.1 MVP 阶段（Phase 1-2）

**方向 8（Pure Prompt）+ 方向 1（RAG）**

- 用 RAG 检索政策 + 证据
- 单 prompt 推理 41 个裁决
- 跑通流程，建立 baseline

### 10.2 优化阶段（Phase 3）

**+ 方向 5（Constrained Decoding）**

- 防止输出格式错误
- 减少 retry

### 10.3 高级优化（Phase 4，可选）

**+ 方向 2（Tool-Use）**

- 仅为 LOW trust_score 的 case 启用
- 自主决定读哪些证据、查哪些政策
- 用 multi-agent debate 处理最难的 case

### 10.4 不推荐的方向

| 方向 | 不推荐理由 |
|---|---|
| 3 Multi-Agent Debate（全员） | 成本 3-5x，96 case 跑不起 |
| 4 Knowledge Graph | 合规风险高 |
| 6 Active Learning | 需要标注数据 + GPU 集群 |
| 7 Hybrid | 96 case 过度设计 |

---

## 11. 关键技术细节深挖

### 11.1 RAG 切块策略对比

| 策略 | 切法 | 适合 |
|---|---|---|
| 按章节 | "Chapter 4" 一段 | 政策文件 |
| 按段落 | 每段一个 chunk | 长文档 |
| 按 token | 每 500 token 一段 | 通用 |
| 按语义 | embedding 聚类 | 复杂文档 |
| 滑动窗口 | 500 token + 100 重叠 | 检索友好 |

**本任务推荐**：政策 PDF 按章节切（Chapter → Section → Subsection），证据按 track 切（每个 track 独立 chunk）。

### 11.2 向量化模型选择

| 模型 | 维度 | 性能 | 速度 | 备注 |
|---|---|---|---|---|
| OpenAI text-embedding-3-large | 3072 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | 闭源，最准 |
| bge-large-zh-v1.5 | 1024 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 中文最强 |
| bge-large-en-v1.5 | 1024 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 英文最强 |
| m3-embedding | 1024 | ⭐⭐⭐⭐ | ⭐⭐⭐ | 多语言 |
| all-MiniLM-L6-v2 | 384 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 轻量 |

**本任务推荐**：bge-large-en-v1.5（数据是英文）。

### 11.3 检索策略对比

| 策略 | 原理 | 适合 |
|---|---|---|
| Top-k cosine | 取最相似的 k 个 | 简单查询 |
| MMR | 多样性 + 相关性 | 多角度查询 |
| HyDE | 用假设性回答做检索 | 抽象查询 |
| Multi-query | 多个角度查询合并 | 复杂查询 |
| Re-ranking | 检索后再排序 | 高精度需求 |

**本任务推荐**：Top-k + Re-ranking（用 bge-reranker-large）。

### 11.4 Prompt 缓存策略

| 缓存什么 | 节省多少 | 备注 |
|---|---|---|
| System prompt | 每次调用都省 | Anthropic 自动 |
| CP 定义 | 41 次调用共享 | Anthropic cache |
| 政策切片 | 同 CP 多次调用共享 | 自实现 |
| 证据 | 不缓存（每个 case 不同） | — |

**本任务推荐**：把不变的 part（system + policy + CP 定义）做 prefix cache，可变 part（evidence）放后面。Anthropic 缓存命中可省 90% 成本。

### 11.5 并发与限流

| 维度 | 建议值 | 理由 |
|---|---|---|
| 全局并发 | 16-32 | 平衡速度与 rate limit |
| 单 case 内 | 串行（41 CP 顺序） | 简单 |
| case 间 | 并行 | 96 case 不依赖 |
| Rate limit | 看 API 文档 | Anthropic: 60 RPM |

### 11.6 错误恢复策略

| 错误 | 恢复 |
|---|---|
| 网络超时 | 指数退避 3 次 |
| 限流 (429) | 退避到下一个时间窗 |
| 输出格式错 | 重试 1 次 + 改 prompt |
| 内容审核拒绝 | 重试 1 次 + 改 prompt |
| 完全失败 | 死信队列 + 人工 review |

---

## 12. 新方案：差异化思考

除了上述方向，还有一些**非主流但可能有效**的思路：

### 12.1 思路 A：模板化裁决（Template-Based）

不用 LLM 推理，而是**用正则 + 关键词 + 规则**做裁决：

```python
def audit_cp22(evidence):
    # CP22: 至少 2 年记录
    for track in [3, 6, 9]:
        text = read_evidence(track)
        if "retain" in text.lower():
            years = extract_years(text)
            if max(years) - min(years) >= 2:
                return "1"
            else:
                return "0"
    return "N/A"
```

| 优势 | 劣势 |
|---|---|
| 极快 | 不能处理复杂情况 |
| 可解释 | 维护成本高 |
| 合规 | 覆盖率低 |

**评估**：可用于简单 CP（CP22/CP23），复杂 CP 还是 LLM。

### 12.2 思路 B：Few-Shot + Self-Consistency（无 Agent）

```
[3-5 个示例] → [LLM 推理] → [输出] → [重复 3-5 次] → [投票]
```

| 优势 | 劣势 |
|---|---|
| 比 zero-shot 准 | 成本 3-5x |
| 实现简单 | 偶发问题 |

**评估**：作为 ensemble 策略，比 debate 简单，比纯推理准。

### 12.3 思路 C：Calibration（置信度校准）

```
[LLM 输出] + [置信度] → [校准] → [最终裁决 + 标记低置信度]
```

| 优势 | 劣势 |
|---|---|
| 区分容易/难 case | 校准需要验证集 |
| 聚焦人工 review | 不能直接提升准确率 |

**评估**：作为辅助，提升可观测性。

### 12.4 思路 D：知识蒸馏（Distillation）

```
[大模型（如 Opus）] → [跑全 96 case] → [产生训练数据]
       ↓
[小模型（如 Haiku）] → [微调] → [本地推理]
```

| 优势 | 劣势 |
|---|---|
| 推理成本降 10-50x | 需要标注数据 |
| 可本地部署 | 训练成本高 |

**评估**：如果有时间 + 算力，可考虑长期方案。

### 12.5 思路 E：规则+LLM 混合

```
[简单 CP] → [规则] → [直接裁决]
[复杂 CP] → [LLM]  → [推理裁决]
[冲突 CP] → [LLM debate] → [投票]
```

| 优势 | 劣势 |
|---|---|
| 各取所长 | 规则设计成本 |
| 成本最优 | 复杂度高 |

**评估**：推荐作为 Phase 3 优化。

---

## 13. 实施路线图（推荐）

```
Week 1 (5 days):
  Day 1: Pipeline 基础设施 + config + state
  Day 2: Stage 0 (ingest) + Stage 0.5 (filter)
  Day 3: Stage 1-6 (integrity)
  Day 4: Stage 7 prompt + RAG + 1 case pilot
  Day 5: Stage 7 全量 + Stage 8 + sanity check

Week 2 (3 days, optional):
  Day 6: Constrained decoding 集成
  Day 7: Tool-Use agent for LOW cases
  Day 8: Self-consistency 调优 + 最终交付

Week 3 (5 days, optional):
  Day 9-13: 知识蒸馏 / 主动学习（如果有标注数据）
```

---

## 14. 我的最终推荐

**Week 1**：MVP 跑通
- 架构：Pipeline + Checkpoint（方案 β）
- 推理：方向 1 RAG + 方向 5 Constrained Decoding
- 多模态：原生图像输入
- Prompt：Jinja2 模板 + cache

**Week 2（如果 MVP 准确率不够）**：
- LOW trust_score 的 case → 升级到方向 2 Tool-Use
- 关键 CP（如 CP22/CP23）→ 启用 self-consistency

**不推荐**：
- Knowledge Graph（合规风险）
- Active Learning（成本太高）
- Hybrid（过度设计）

---

## 15. 给您的问题

请告诉我：

1. **是否接受 RAG + Constrained Decoding 作为 MVP 方案**？还是想要更激进（如 Tool-Use）？
2. **是否接受"分阶段升级"思路**（先 MVP，再迭代）？还是一步到位？
3. **是否需要我详细设计某个具体方向**（如 LangGraph agent 的具体工具集）？
4. **是否要我把推荐方案更新到 SOLUTION.md** 作为最终架构？