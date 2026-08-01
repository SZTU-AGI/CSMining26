# 共识 Finding 记录(case-001,无 silver,跨方法一致)

> 由 `scripts/agreement.py` 锁定的共识不合规 CP,配以各方法原始 reason + citation。
> 无 ground truth 时,**多个独立方法一致判不合规**是最可信的真实问题候选。
> 数据来源:`build/experiments/**/result.json`(final verdict,已排除 stage_audit/base 中间产物)。
> CP 定义见 `build/parsed/checkpoints.json`;引用 ID 对应政策 PDF 页或 case 证据段。

**最近更新:2026-08-01** —— 七方法 baseline 全部跑完(41/41 valid 100%),共识由 12 升至 **14 个**,CP23/CP30/CP34/CP37 升至 3 方法,新增 CP38/CP27。

---

## ⚠ 数据完整性说明(2026-07-31 修正 + 08-01 补全)

本文档早期版本基于**错位数据**:`scripts/resume_run.py` 早期版本裁剪 plan_units 后传给 `run_*_plan` runner,后者用 `enumerate` index 写 `cp-NNN`,导致补跑的 CP 被写到错误目录(覆盖已完成项)。修正:
1. `resume_run.py` 改用单 unit runner(`run_*_unit`)+ **原始** index 定位 `cp-NNN`(见 memory `resume-run-index-corruption`)。
2. 错位数据按 `cp_id` 字段重新归位(stage_audit 10 个、agent_audit 14 个)。
3. 修正后 CP6/CP3 **退出**共识(原"3 方法/2 方法"是错位把别的 CP 当成了它们);CP36 升至 4 方法。
4. 2026-08-01 用修好的 `resume_run.py` 补完剩余 14 CP(stage_audit CP1..CP10 用 M3,agent_audit CP38..CP41 用 2.7),**七方法 41/41 全完整**。

**提取注意**:agent_audit 内部复用 stage_audit(写 `cp-NNN/stage_audit/result.json`,是 critic 前的 base),verify_audit 写 `base/result.json`。统计 final verdict 时必须排除这些中间产物(`scoreboard.py::_unit_dirs_with_results` 已处理:跳过 `base`/`stage*` 目录)。

---

## 方法分布速览(case-001, track3=raw,七方法完整)

| 方法 | 1/0/N-A | valid% | 调用数/案例 | 备注 |
|---|---|---|---|---|
| case_full | 40/0/1 | 100% | 1 | blanket-approve,无区分度 |
| element_full | 40/1/0 | 100% | 4 | ≈ case_full(一致率 95%) |
| checkpoint_full | 19/11/11 | 100% | 41 | **最有区分度**,11 N/A |
| automatic_retrieval | 23/10/8 | 100% | 41 | RAG,第二均衡 |
| stage_audit | 24/16/1 | 100% | ~123 | 4 阶段,**最严格**(16 判 0) |
| agent_audit | 29/12/0 | 100% | ~68 | stage + 条件 critic,12 判 0 |
| verify_audit | 36/4/1 | 100% | ~42 | base + 无条件复查,4 翻转 1->0 |

**关键观察**:方法的"严格度"与调用粒度/阶段数正相关 —— one-shot(case/element_full)几乎全放行;分块到 CP(checkpoint_full)和分阶段(stage_audit)显著更敢判 0。这是 blanket-approve 偏差随上下文细化而减弱的直接证据。

---

## Agent 链路实际效果(case-001,agent_audit)

agent_audit = stage_audit(4 阶段) + 6 条件触发的 module(详见 `docs/2026-08-01-method-pipelines.md`)。case-001 实际触发情况:

| 维度 | 数值 |
|---|---|
| 触发 module 的 CP | **26 / 41** |
| 唯一触发的 module | **critic**(其余 5 个 module 0 触发) |
| critic 触发条件 | condition 3 `conflict`(stage-3 contradictions 非空) |
| critic 翻转 1→0(合规→不合规) | **3 个 CP**(更严格) |
| critic 翻转 0→1(不合规→合规) | 1 个 CP |
| critic 维持原判 | 22 个 CP(14 维持 1,8 维持 0) |
| final_resolution | 26 REVIEWED + 15 ACCEPT(未触发即接受 base) |

**解读**:case-001 是一个"证据有大量矛盾(33/41 CP 有 contradictions)"的案例,所以 critic 几乎必然触发。critic 的净效果是**偏向严格**(3 个 1→0 vs 1 个 0→1),即 agent 链路在矛盾证据上倾向于把"疑似合规"翻成"不合规"。其余 5 个 module(retrieval_repair / verifier×3 / arbitration)未触发,说明 case-001 没出现 N/A 误升、缺引用、低置信、跨 CP 冲突等情形 —— 触发条件设计偏窄,真实案例主要命中 conflict 一路。

---

## 共识不合规 CP 总览(14 个)

| CP | 判 0 方法数 | 方法 | 置信度 |
|---|---|---|---|
| **CP16** | 4 | automatic_retrieval, checkpoint_full, stage_audit, verify_audit | 🔴🔴 最高 |
| **CP36** | 4 | automatic_retrieval, checkpoint_full, element_full, stage_audit | 🔴🔴 最高 |
| **CP23** | 3 | automatic_retrieval, agent_audit, stage_audit | 🔴 高(升) |
| **CP30** | 3 | agent_audit, checkpoint_full, stage_audit | 🔴 高(升,潜在最严重) |
| **CP34** | 3 | agent_audit, checkpoint_full, stage_audit | 🔴 高(升) |
| **CP37** | 3 | agent_audit, automatic_retrieval, stage_audit | 🔴 高(升) |
| CP15 | 2 | stage_audit, verify_audit | 🟡 中 |
| CP19 | 2 | automatic_retrieval, checkpoint_full | 🟡 中 |
| CP20 | 2 | agent_audit, checkpoint_full | 🟡 中 |
| CP21 | 2 | agent_audit, stage_audit | 🟡 中 |
| CP22 | 2 | agent_audit, checkpoint_full | 🟡 中 |
| CP27 | 2 | agent_audit, verify_audit | 🟡 中 |
| CP33 | 2 | agent_audit, stage_audit | 🟡 中 |
| CP38 | 2 | agent_audit, stage_audit(新) | 🟡 中 |
| CP40 | 2 | automatic_retrieval, checkpoint_full | 🟡 中 |

> 注:CP6、CP3 在错位修正后**退出**共识(原先因数据错位被误判)。

---

## 🔴🔴 4 方法最高置信

### CP16 —— 筛分去除大污染物(4 方法一致判 0)

**要求**:筛分作业须确保从植物/植物产品中去除大型污染物(政策 4-9(2))。
**分歧**:判 0 的(4 个)指出案例讨论了一般清洁/卫生(旋转筛清杂机、吸气机等),但**无任何证据明确说明筛分过程确保大污染物去除** —— "有设备描述"但未与"去除大污染物"要求显式挂钩。verify_audit 复查把 CP16 从 base 的 1 翻成了 0。判 1 的(case_full/element_full)把清杂/吸气设备视为已满足。
**关键引用**:`policy-rules-2021_page-0049`、`case-001-t6_paragraph-0036`、`case-001-t2_table-002`。
**结论**:证据具体性缺口。**4 方法一致(含无条件复查),置信度最高,基本锁定为真实 finding。**

### CP36 —— 替换(掉包)风险控制(4 方法一致判 0)

**要求**:须有书面控制措施最小化替换(掉包)风险(Element-4 §4.2)。
**分歧**(最有说服力):**所有方法(含判 1 的 case_full)都注意到** `case-001-t8_paragraph-0012` 写着 "Substitution controls rely on **single-operator checks** for selected late-shift dispatches"。判 0 的把"依赖单一操作员检查"解读为**控制弱点**,element_full 原文称 "a weakness... rather than demonstrating minimisation of substitution risk"。判 1 的 case_full 用更广控制(班末核对、封条核验)覆盖但仍承认该弱点。
**关键引用**:`case-001-t8_paragraph-0012`、`case-001-t8_paragraph-0005`(部分批次重贴标签)。
**结论**:替换控制依赖单一操作员检查 = 文件化控制弱点;另有重贴标签线索。**4 方法一致,与 CP16 并列最高置信度。**

---

## 🔴 3 方法高置信

### CP23 —— 记录须英文/注明日期/可审计(automatic_retrieval, agent_audit, stage_audit)

**要求**:记录须为英文、含行动日期、准确清晰可审计(政策 11-2)。
**分歧**:**stage_audit 指出 Bait Station Register(case-001-t3)缺少检查/维护日期列**(仅一次 "Last Inspected"),与"记录须注明日期"矛盾。agent_audit 经 critic 把 base 的 1 翻成 0(1→0 翻转之一)。case_full 自己也提到"手写批注部分不清"但仍判 1。
**关键引用**:`case-001-t3_sheet-bait-station-register`、`policy-rules-2021_page-0108`。
**实质**:诱饵站登记表缺日期列 = 具体记录缺陷。

### CP30 —— 在注册场所期间的追溯(agent_audit, checkpoint_full, stage_audit)⚠ 潜在最严重

**要求**:在注册场所期间须能追溯植物/植物产品(4-7B(b))。
**分歧**:判 1 的举 Product Movement Log;判 0 的抓**场所身份不匹配**。**stage_audit 指出:t9 的追溯证据属于另一场所(Mallee Exports),t2 引用 RE-WA-3051(非本 case 的 RE-WA-2021-0041)** —— 追溯记录疑似张冠李戴。agent_audit 进一步抓到 Pest Control Record 内嵌 RE 编号也属别处。checkpoint_full 亦称无明确覆盖"在场所期间"的追溯系统。automatic_retrieval 判 N/A。
**关键引用**:`case-001-t9_sheet-2-product-movement-log`、`case-001-t2_paragraph-0022/0023`、`case-001-t3_sheet-cover`、`case-001-t8_paragraph-0009`。
**实质**:**场所身份不匹配** —— 追溯记录疑似张冠李戴,**潜在最严重 finding**(若属实,整案追溯链断裂)。

### CP34 —— failed goods 控制体系(agent_audit, checkpoint_full, stage_audit)

**要求**:须有 failed goods(检验不合格品)的控制体系,含 blending/rejection/treatment(政策 4-9(3))。
**分歧**:checkpoint_full 称无证据描述 failed goods 控制系统。agent_audit 抓到 Rejected/Failed Goods Log(sheet 6)**仅 1 条且手写不清**,且 Failed Goods Management Procedure(`case-001-t2_paragraph-0026`)允许的 blending **不被 s4-9(3) 允许**。stage_audit 综合指出 ad-hoc 班末重贴标签、缺 rejected-goods 记录、不合格品被接受放行。
**关键引用**:`case-001-t9_sheet-6-rejected-goods-log`、`case-001-t2_paragraph-0026`、`case-001-t8_paragraph-0005/0007/0009`。
**实质**:failed goods 控制零散、blending 违规、记录残缺。

### CP37 —— 包装要求(agent_audit, automatic_retrieval, stage_audit)

**要求**:出口须有文件化包装要求(适用性/清洁/强度/进口国要求,政策 4-11)。
**分歧**:三方法一致称**无证据呈现文件化包装要求**。t8 Phytosanitary Security Procedure 仅称"包装 QC 记录偶附于 shift summary",无实质包装规范。
**关键引用**:`policy-rules-2021_page-0049`、`case-001-t8_paragraph-0013/0014`。
**实质**:包装要求缺失(仅有附挂 QC 记录的提及)。

---

## 🟡 2 方法中置信(待补强)

### CP15 —— 筛分大污染物(stage_audit, verify_audit)
verify_audit 复查把 CP15 从 base 1 翻成 0,与 stage_audit 一致。与 CP16 同 Element-2(筛分),待其他方法印证升 3 方法。

### CP19 —— 控制体系对实际出口作业类型有效(automatic_retrieval, checkpoint_full)
判 1 的举检查记录(2025-03-10 检查、季度内审)为效;判 0 的要"**证明对实际作业类型有效**"而非仅有制度描述。checkpoint_full 指出制度文档针对 "Wheat export operations" 未证明对实际作业有效,且多处 **RE 编号不匹配**。
**引用**:`case-001-t6_paragraph-0007/0009`、`policy-rules-2021_page-0048`。

### CP20 —— 害虫控制系统存在(agent_audit, checkpoint_full)
agent_audit 抓到 Pest Activity Log **归另一场所**(GrainGuard Storage Services, RE-NSW-2019-0441, NSW),且 Bait Station Register 列 18 站但 Map 仅 13 站。checkpoint_full 亦指出 pest 证据 RE 编号不一致(RE-NSW-2019-0441 / RE-WA-3051)。
**引用**:`case-001-t2_paragraph-0021`、`case-001-t3_sheet-cover`、`case-001-t3_sheet-pest-activity-log`。
**实质**:害虫控制证据张冠李戴 + 站点数量矛盾(与 CP30 共性)。

### CP21 —— 害虫控制有效性(agent_audit, stage_audit)
bait station 多处缺陷:BS-07/BS-10 "Map reference pending"/"Loose anchor"、BS-05 water ingress、角落碎屑积压待清、重复鼠害且响应延迟。
**引用**:`case-001-t7_table-002`、`case-001-t7_paragraph-0005/0011`。

### CP22 —— 记录留存 2 年(agent_audit, checkpoint_full)
Record Archive Register 仅 "Jan 2025 - current",且标注 **"Legacy index migration in progress"** + **"Rejected-goods attachments partly offsite"**,未满足 2 年留存。
**引用**:`case-001-t9_sheet-7-record-archive-register`、`case-001-t1_paragraph-0053`、`policy-rules-2021_page-0110`。

### CP27 —— 化学品存储(agent_audit, verify_audit)
化学品存储登记表列 4 种化学品均存于"专用上锁化学品仓,距粮棚 15m",G 列确认"No — stored off grain",但证据未证明对所有化学品均满足要求。
**引用**:`case-001-t3_sheet-chemical-storage-register`。

### CP33 —— 产品流程图(agent_audit, stage_audit)
t8 Phytosanitary Security Procedure **未含完整产品流程图**,且显式称"flow chart omits one alternate transfer path"(省略一条备用转移路径)。
**引用**:`case-001-t8_paragraph-0005/0006/0007/0008`、`case-001-t1_paragraph-0027`。

### CP38 —— 追溯记录留存(agent_audit, stage_audit,新)
追溯记录(receival/movement/dispatch、treatment、rejected goods)在 Archive Register 标注 "Legacy index migration in progress" + "Rejected-goods attachments partly offsite",记录未完整留存且现场不可得。覆盖率仅 Jan 2025 - current,未满足 ≥2 年。
**引用**:`case-001-t9_sheet-7-record-archive-register`、`case-001-t9_sheet-6-rejected-goods-log`、`case-001-t9_sheet-1-receival-register`。

### CP40 —— 记录英文/日期/准确/可审计(automatic_retrieval, checkpoint_full)
文档含**不一致、嵌入 RE 编号不匹配、空白签字栏、Sanitation 记录空白日期栏**。automatic_retrieval 称仅有"保留 2 年"一般陈述,无语言/日期/准确性证据。
**引用**:`case-001-t6_table-005`、`case-001-t9_sheet-7-record-archive-register`、`policy-rules-2021_page-0108`。

---

## 跨 CP 共性线索(值得单独追踪)

1. **RE 编号不匹配 / 张冠李戴**:CP19/CP20/CP30/CP40 多个方法提到案例内嵌入的注册编号(如 RE-WA-3051、RE-NSW-2019-0441 vs 本案 RE-WA-2021-0041)不一致 —— 疑似同一批证据被多个 case 复用或场所错配。与 CP20(Pest Log 属 GrainGuard)、CP30(t9 属 Mallee Exports)相互印证,**这是跨 CP 最严重的系统性线索**。
2. **记录留存缺口**:CP22/CP38 的 "Legacy index migration in progress" + "Rejected-goods attachments partly offsite" 反复出现 —— 记录未完整留存/现场不可得。
3. **记录日期/签字缺失**:CP23(诱饵站登记表无日期列)、CP40(空白日期/签字栏)—— 记录可审计性短板。
4. **"有描述但未证明有效/适用"**:CP16/CP19/CP37 都是"描述了设备/制度/包装,但未显式证明满足特定要求" —— 模型按 CP 字面要求逐条核对时更易抓到这类缺口(这是 checkpoint_full/stage_audit 比一刀切方法更严的原因)。

---

## 下一步

- **CP16、CP36 已 4 方法一致,基本确认为真实 finding(并列最高置信度)**。
- **CP30 的场所不匹配若被 silver 印证,优先级最高**(潜在数据/证据错配,整案追溯链可能断裂)。
- 3 方法共识(CP23/CP30/CP34/37)已较稳,2 方法待 silver 或第 4 方法印证。
- 待有 silver(异常报告 / 人工标签)后,用 `anchored_correct/anchored_total` 校验上述共识是否与 ground truth 一致。
- **多 case 扩展**:case-001 已完整,可扩展到 case-002..100 验证共识模式是否复现(RE 编号错配可能是 batch 级问题)。轻方法(case_full 1 call)可低成本跨 case 预览。
