# FRECA Task2 直连 LLM 审计实验 · 阶段总结

> 2026-08-01 · 给组织/导师的技术汇报。配套看板:`build/experiments/scoreboard.html`(纯 SVG 离线可看)。

## 1. 任务背景

FRECA Task2 的目标是对 **100 个农产品出口案例 × 41 个合规检查点(CP)** 做《出口管制(植物及植物产品)规则 2021》的合规判定。我们绕开了传统的"检索 + 审计任务"流水线,改为**直连长上下文 LLM(MiniMax-M3 / MiniMax-2.7)** 把官方规则材料与案例证据直接喂进模型,用 7 种不同的实验方法做 A/B 对比,看哪种方法最接近真实合规判定。

## 2. 七种实验方法(同一组 41 CP,不同上下文切分/推理深度)

| 方法 | 调用数/case | 严格度 | 设计 |
|---|---|---|---|
| `case_full` | 1 | 低 | 全部 41 CP 一次性提问(长上下文,模型自由发挥) |
| `element_full` | 4 | 低 | 按 4 个 Element 分块,每块约 10 CP |
| `checkpoint_full` | 41 | 中 | 每 CP 单独一次调用(独立判定,信息更聚焦) |
| `automatic_retrieval` | 41 | 中 | BM25+词面检索筛材料后再每 CP 判定(简单 RAG) |
| `verify_audit` | ~42 | 中 | base 一发判定 + 每 CP 无条件复查(自校验) |
| `agent_audit` | ~68 | 高 | stage_audit 之上 +6 条件触发模块(retrieval_repair / critic ×3 / verifier / arbitration) |
| `stage_audit` | ~123 | 最高 | 4 阶段:适用性 → 反向证据搜索 → 判定 → 整合(每 CP 4 个窄 prompt) |

详见 `docs/2026-08-01-method-pipelines.md`。

## 3. 已完成的基线(case-001,1/100)

截至 2026-08-01,**case-001 的 7 方法 41/41 全部跑完**(早先受 Token Plan 2056 配额窗口限制,stage_audit 补 10 个、agent_audit 补 4 个后完结)。

| 方法 | 合规(1) | 不合规(0) | N/A | token 消耗 |
|---|---|---|---|---|
| `case_full` | 40 | 0 | 1 | 179,790 |
| `element_full` | 40 | 1 | 0 | 694,316 |
| `checkpoint_full` | 19 | 11 | 11 | 6,990,557 |
| `automatic_retrieval` | 23 | 10 | 8 | 543,388 |
| `stage_audit` | 24 | 16 | 1 | — |
| `agent_audit` | 29 | 12 | 0 | — |
| `verify_audit` | 36 | 4 | 1 | — |

- **valid rate 100%**(所有调用都返回了合法结构化 JSON,citation 有效性 100%)
- 方法间严格度排序清晰:`case_full`(几乎全放行) < `verify_audit` < `element_full` < `automatic_retrieval` ≈ `agent_audit` < `checkpoint_full` < `stage_audit`(最严,16 个判 0)

## 4. 跨方法共识(无银标准的 ground-truth 代理)

由于没有人工标注的银标准,我们用**跨方法共识**(≥2 个独立方法都判 0)作为合规缺陷的代理信号。case-001 共识别出 **21 个共识不合规 CP**(≥2 方法),其中:

- **5 方法共识(最高置信)**:CP23
- **4 方法共识**:CP16 / CP22 / CP34 / CP36
- **3 方法共识**:CP5 / CP15 / CP30 / CP33 / CP37 / CP38 / CP40

最高置信 finding(CP16、CP36 在 checkpoint_full/automatic_retrieval/stage_audit/verify_audit 四个独立链路上同时判 0)的 CP 原因分析与引用见 `docs/2026-07-31-consensus-findings.md`。

### Agent 链路实际触发(case-001)

agent_audit 的 6 个条件触发模块在 case-001 上**只有 critic 被触发**(26/41 CP),其余 5 个模块 0 次。critic 翻转统计:1→0 翻转 3 次、0→1 翻转 1 次、22 次维持原判。说明在 case-001(偏合规案例)上 agent 链路主要起"复核"而非"修复"作用。

## 5. case-002 探针验证(2026-08-01)

为验证方法对**不同类型案例**的区分度,跑了 case-002 的轻方法:

| 方法 | 合规 | 不合规 | N/A |
|---|---|---|---|
| case_full(case-002) | 22 | **19** | 0 |
| element_full(case-002) | 36 | 3 | 2 |
| case_full(case-001 对照) | 40 | 0 | 1 |

**关键发现**:case-002 在 case_full(最宽松方法)下就被判出 **19 个不合规**,与 case-001 的"几乎全放行"形成鲜明对比 → 方法在不同案例上确实能区分合规/不合规,不是噪声。case-002 疑似"不合规案例"。

## 6. 扩展到 100 case 的成本估算

| 方法 | 100 case 调用数 | 单线程时长 |
|---|---|---|
| case_full | 100 | ~3 小时 |
| element_full | 400 | ~11 小时 |
| checkpoint_full | 4,100 | ~114 小时 |
| automatic_retrieval | 4,100 | ~114 小时 |
| verify_audit | ~4,200 | ~117 小时 |
| agent_audit | ~6,800 | ~189 小时 |
| stage_audit | ~12,300 | ~342 小时 |
| **合计** | **~28,000** | **~890 小时 ≈ 37 天** |

并发 4-8x 后约 5-9 天,仍受每日配额限制。**当前阶段停在 case-001 完整基线 + case-002 探针**,100 case 全量扩展待资源决策。

## 7. 工程产物

- **代码**:7 方法实现(`src/freca/experiments/`)、自改进 Harness 外环(`harness.py`)、看板聚合(`scripts/scoreboard.py`)、续跑/单 case 驱动(`scripts/resume_run.py` / `scripts/run_case.py`)
- **测试**:249 passed / 5 skipped(ReplayJsonClient 离线回放覆盖)
- **文档**:`docs/2026-07-31-consensus-findings.md`(共识 CP 详解)、`docs/2026-08-01-method-pipelines.md`(方法链路)
- **看板**:`build/experiments/scoreboard.html`(10 节纯 SVG:方法分布 / valid% / 共识 CP / 一致性热图 / token 成本 / CP×method 矩阵 / agent 链路 / 详情表)

## 8. 关键工程教训

1. **配额是账户级**:Token Plan 2056 的每日配额无法靠 backoff 绕过,429 重试耗尽后必须等窗口重置;M3 与 2.7 共享配额。
2. **续跑索引陷阱**:把"只跑缺失 unit"的子列表直接传给 plan runner 会从 0 重编号,**覆盖错误的 cp-NNN 目录**。修复:用 per-unit runner + 原始位置 index。
3. **result.json 同名 shadow**:agent_audit 内部复用 stage_audit(写 `cp-NNN/stage_audit/result.json`),verify_audit 写 `base/result.json`,这些中间产物与最终 `cp-NNN/result.json` 同名。矩阵提取若用 `rglob` 会取错(把 critic 前的 base verdict 当 final)。修复:`_unit_dirs_with_results()` 排除 `base`/`stage*` 子目录 + 过滤空骨架。
4. **方法间严格度天然分层**:case_full 倾向全放行,stage_audit 最严。无银标准时,跨方法共识(尤其 ≥3 方法)是较可信的合规缺陷代理。
