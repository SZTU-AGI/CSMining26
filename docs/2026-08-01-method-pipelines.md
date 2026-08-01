# 实验方法推理链路(Pipelines)说明

> FRECA Task2 的 7 个审计方法,按"调用粒度 + 阶段数"递增排列。
> 每个方法对同一组官方材料(政策 PDF + case 9 证据轨道)独立判定 41 个 CP。
> 本文档讲清每个方法的**推理链路**,重点拆解 stage_audit 的 4 阶段与 agent_audit 的 6 条件触发链路。
> 数据来源:`src/freca/experiments/*.py` + case-001 实测 trace。

**最近更新:2026-08-01**(七方法 41/41 完整后的链路实测)。

---

## 方法谱系(复杂度↑ = 严格度↑ = 成本↑)

```
one-shot ──> 分块 ──> 检索 ──> 复查 ──> 多阶段 ──> 条件 agent
case_full   element_   auto_     verify_   stage_     agent_
(1 call)    full       retrieval audit     audit      audit
            (4)        (41)      (~42)     (~123)     (~68)
```

| 方法 | 粒度 | 阶段 | 调用/案例 | 严格度 | 区分度 |
|---|---|---|---|---|---|
| case_full | 整案 | 1 | 1 | 低 | 无(40/0/1) |
| element_full | 4 Element | 1 | 4 | 低 | 无(40/1/0) |
| checkpoint_full | 41 CP | 1 | 41 | 中 | **高**(19/11/11) |
| automatic_retrieval | 41 CP+RAG | 1 | 41 | 中 | 高(23/10/8) |
| verify_audit | 41 CP | 2(base+verify) | ~42 | 中高 | 中(36/4/1) |
| stage_audit | 41 CP | 4 | ~123 | **高** | **高**(24/16/1) |
| agent_audit | 41 CP | 4+条件 | ~68 | 高 | 高(29/12/0) |

---

## A. case_full -- 整案一次性判定

**链路**:把整案全部材料 + 41 CP 一次性塞进 prompt,模型一次调用产出 41 个 verdict。
**优点**:1 call,最便宜。
**弱点**:**blanket-approve 偏差** -- 模型在超长上下文里倾向于"没看到失败证据 = 合规",case-001 得 40/0/1(几乎全放行),无区分度。与 element_full 一致率 95%(两路 one-shot 互验,但同样偏宽)。

## B. element_full -- 按 4 Element 分块

**链路**:按 4 个 Element(场所身份/产品处理/文档/追溯)各一次调用,每次判定该 Element 下的 CP(共 4 call)。
**效果**:≈ case_full(40/1/0),分块未显著提升严格度,因每块仍是 one-shot 且上下文仍很大。

## C. checkpoint_full -- 按 41 CP 分块

**链路**:每个 CP 单独一次调用(41 call),prompt 只含该 CP 的规则 + 全案材料。
**效果**:**最有区分度**(19/11/11)。把上下文从"整案"聚焦到"单 CP 规则"后,模型更敢判 0 和 N/A。11 个 N/A 说明模型在单 CP 粒度下更诚实承认"该 CP 不适用"。

## D. automatic_retrieval -- BM25 + 词法 RAG

**链路**:每个 CP 先用 BM25 + 词法匹配从材料里**检索**最相关的政策段/证据段,只把检索到的片段(而非全量材料)塞进 prompt 判定(41 call)。
**效果**:23/10/8,第二均衡。RAG 既降低了上下文长度,又把模型注意力锚定到相关证据,严格度介于 one-shot 与分阶段之间。

---

## E. stage_audit -- 4 阶段直接 LLM(最严格)

**链路**(每 CP 走 4 阶段,~3 call/CP):

```
stage-1 适用性 ──APPLICABLE──> stage-3 判定(1/0)──┐
   │                                              ├──> stage-4 整合 final verdict
   └─NOT_APPLICABLE─> stage-2 反证搜索 ──找到适用 track─> 升 stage-3
                                          └─无─> 维持 N/A
```

| 阶段 | 何时跑 | 输出 | 作用 |
|---|---|---|---|
| **stage-1 适用性** | 总是 | APPLICABLE / NOT_APPLICABLE | **先承诺适用性,再找证据** -- 消除"无失败证据=合规"的 blanket-approve 根源 |
| **stage-2 反证搜索** | 仅 stage-1=N/A 时 | 找到适用 track / 维持 N/A | 给 N/A 一次"翻案"机会:强制模型尝试找反证 |
| **stage-3 判定** | 仅 applicable 时 | 1/0 + reason + citations + contradictions | 核心 1/0 判定,附带**矛盾证据数组** |
| **stage-4 整合** | 总是 | ExperimentVerdict | 拼成下游统一的 verdict 形状 |

**关键设计**:stage-1 强制先判适用性,是 stage_audit 比 one-shot 严格的根本原因 -- 模型不能在没决定"该 CP 是否适用"前就放行。

**case-001 实测**:40 APPLICABLE + 1 N/A;**33/41 CP 的 stage-3 含 contradictions**(支持与反证共存)-- 这是 agent_audit critic 触发的基础。

---

## F. verify_audit -- base + 无条件复查

**链路**:先做一次 base 判定(同 checkpoint_full 粒度),再对**每个 CP 无条件**复查一次(~42 call)。
**与 agent_audit 区别**:verify_audit **无条件**复查所有 CP;agent_audit **仅低置信/有矛盾时**触发复查。
**case-001 实测**:36/4/1。base 的 6 个 FAIL 中 **4 个真翻转 1->0**(CP2/CP15/CP16/CP27),CP37 复查返回非枚举值 "Present" 保留 base N/A。复查净效果偏向严格(4 个 1->0)。

---

## G. agent_audit -- stage_audit + 条件触发 agent 链路(重点)

**链路**:base = stage_audit(4 阶段)。base 完成后,评估 6 个触发条件,命中则触发对应 module(每个 module 一次 LLM call)。**干净高置信的 CP 不触发任何 module**,直接 ACCEPT base verdict。

```
stage_audit(4阶段) ──> base verdict
                          │
                          ▼
                   _evaluate_triggers
                          │
        ┌─────────────────┼─────────────────┬──────────────┐
        ▼                 ▼                 ▼              ▼
   condition 1       condition 3        condition 4/5   condition 6
   initial_na        conflict           low_conf /      cross_cp_
                                        missing_cite    inconsistent
        │                 │                 │              │
        ▼                 ▼                 ▼              ▼
  retrieval_repair     critic           verifier       arbitration
        │                 │                 │              │
        └─────────────────┴────修/翻 verdict─┴──────────────┘
                          │
                          ▼
                   final verdict + AgentTrace
```

### 6 个触发条件 + 对应 module

| # | 条件 | 含义 | 触发 module | 作用 |
|---|---|---|---|---|
| 1 | `initial_na` | stage-1=N/A 且 stage-2 升级到 1/0 | retrieval_repair | 重跑反证搜索,带升级上下文确认 |
| 2 | `retrieval_gap` | stage-2 无任何 evidence_citation | verifier(拓宽) | 拓宽检索再判 |
| 3 | `conflict` | stage-3 contradictions 非空(支持+反证共存) | **critic** | 批判性复查,可能翻 verdict |
| 4 | `low_confidence` | uncertainty > 0.5(默认阈值) | verifier | 复查低置信判定 |
| 5 | `missing_citation` | verdict 无 citation_ids | verifier | 补引用再判 |
| 6 | `cross_cp_inconsistent` | 同 case 不同 CP 间事实冲突 | arbitration | 仲裁跨 CP 矛盾 |

### case-001 实际触发统计(41 CP)

| 维度 | 数值 |
|---|---|
| 触发 module 的 CP | **26 / 41**(其余 15 直接 ACCEPT base) |
| 触发的 module | **仅 critic**(条件 3 conflict) |
| 其余 5 module(retrieval_repair/verifier×3/arbitration) | **0 触发** |
| critic 翻转 1->0(合规->不合规) | **3 个 CP** |
| critic 翻转 0->1(不合规->合规) | 1 个 CP |
| critic 维持原判 | 22(14 维持 1,8 维持 0) |
| final_resolution | 26 REVIEWED + 15 ACCEPT |

**解读**:
- case-001 是"证据高矛盾"案例(33/41 CP 有 contradictions),所以 condition 3 几乎必然触发 critic。
- **critic 净效果偏严格**(3 个 1->0 vs 1 个 0->1):在矛盾证据上,agent 链路倾向于把"疑似合规"翻成"不合规"。
- 其余 5 module 0 触发,说明 case-001 没出现 N/A 误升、缺引用、低置信、跨 CP 冲突 -- **触发条件设计偏窄,真实案例主要命中 conflict 一路**。这也提示:若案例类型变化(如多 N/A、多缺引用),其他 module 才会发挥作用。
- 3 个 critic 1->0 翻转中包含 CP23(升 3 方法共识),说明 agent 链路对共识有实质贡献。

### AgentTrace 字段(每 CP 落盘)

`result.json` 的 `agent_trace` 记录:`fired_modules`(每个含 module/trigger/verdict_before/verdict_after/extra_calls)+ `final_resolution`(ACCEPT/REVIEWED)+ `extra_calls`。可完整回放 agent 决策链。

---

## 链路对比小结

| 维度 | one-shot(case/element) | 分块(cp_full) | RAG(auto) | 复查(verify) | 多阶段(stage) | 条件 agent |
|---|---|---|---|---|---|---|
| 调用数 | 1-4 | 41 | 41 | ~42 | ~123 | ~68 |
| 先判适用性 | ✗ | ✗ | ✗ | ✗ | **✓** | **✓** |
| 反证搜索 | ✗ | ✗ | ✗ | ✗ | **✓** | **✓** |
| 复查/批判 | ✗ | ✗ | ✗ | **无条件** | ✗ | **条件触发** |
| case-001 判 0 数 | 0-1 | 11 | 10 | 4 | **16** | 12 |
| blanket-approve 风险 | 高 | 中 | 中 | 中 | 低 | 低 |

**结论**:严格度随"先承诺适用性 + 反证搜索 + 复查"的引入而提升。stage_audit 最严(16 判 0),agent_audit 用条件触发在 stage 基础上进一步翻 3 个 1->0,且调用数(~68)远低于 stage_audit(~123) -- **条件 agent 在成本与严格度间取得平衡**。

---

## 关联

- 共识 finding 详见 `docs/2026-07-31-consensus-findings.md`
- 架构总览详见 `docs/ARCHITECTURE.md`
- agent 触发条件源码:`src/freca/experiments/agent_audit.py`(docstring + `_evaluate_triggers`)
- stage 4 阶段源码:`src/freca/experiments/stage_audit.py`
- 看板(含链路图 + CP×method 矩阵):`build/experiments/scoreboard.html`
