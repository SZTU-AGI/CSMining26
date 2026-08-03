# FRECA Task2 阶段汇报:跨 9 Case 系统性不合规发现

**日期**: 2026-08-03
**任务**: FRECA Task2(出口植物及植物产品合规审核,41 checkpoint × 100 farm case)
**规则依据**: Export Control (Plants and Plant Products) Rules 2021
**模型**: MiniMax-M3(配额:Token Plan 2056,与 MiniMax-2.7 共享,每日重置)

---

## 一、概览

本阶段(8/1 – 8/3)以**最小 token 成本**完成了方法效果验证与系统性不合规 checkpoint 的发现:

- 将实验从 **1 case 扩展到 9 case**,9 case × automatic_retrieval 全部 41/41 valid(共 369 次 verdict)。
- 识别出 **3 个最高置信系统性不合规 CP**(CP9 照明 / CP16 筛选去杂 / CP36 防替换),在所有适用 case 中全部判 0。
- 另有 10 个 CP 在 ≥4 case 判 0,共 **13 个系统性不合规 CP**。
- 总 token 消耗约 **4.9M**(automatic_retrieval 跨 9 case),相比"全方法 9 case"的估算 ~1.24B,**节省 99.6%**。
- 验证了 **"跨 case 单方法广撒网"** 方法能发现"单 case 多方法共识"漏掉的系统性问题(CP9 即是如此暴露)。

---

## 二、实验进展

| 阶段 | case 范围 | 方法 | 成果 |
|------|----------|------|------|
| 8/1 前 | case-001 | 7 方法全跑 | 41/41 完整,14 个共识不合规 CP |
| 8/1 | case-002 | 探针 | 验证 quota + 信号真实 |
| 8/1 – 8/2 | case-002/003/004 | automatic_retrieval 广撒网 | 4 case 跨 case 分析,CP9/CP16 4/4 全 0 |
| 8/2 – 8/3 | case-005/006/007/008/009 | automatic_retrieval 广撒网 | 9 case 全完整,CP9 升至 8/8 全 0 |

### 9 case automatic_retrieval 完整度

```
case-001 ~ case-009:各 41/41 valid(100%)
```

---

## 三、7 方法状态矩阵

| 方法 | 单位 | case-001 | case-002 | case-003 | 说明 |
|------|------|----------|----------|----------|------|
| case_full | 1 | ✅ 41/41 | ✅ | ✅ | 系统性偏宽松(2 case 80 合规/1 不合规),降权 |
| element_full | 4 | ✅ | ✅ | ✅ | 完整 |
| checkpoint_full | 41 | ✅ | ✅ | ✅ | 最严格(40 合规/22 不合规),参考金标准 |
| automatic_retrieval | 41 | ✅ | ✅ | ✅ | **性价比最高**,跨 case 广撒网主力 |
| stage_audit | ~123 | ✅ | 部分 | 部分 | 召回最高(75%),配额敏感 |
| agent_audit | ~68 | ✅ | 29/41 | 17/41 | case-003 待补 21 unit |
| verify_audit | ~42 | ✅ | 未跑 | 未跑 | 不幂等,需充足配额一次性跑 |

> case-001 是唯一 7 方法全完整的基线 case。case-002/003 的 agent_audit/verify_audit 受配额 429 间歇影响,待补。

---

## 四、核心发现:13 个系统性不合规 CP(9 case automatic_retrieval)

下表为 **≥4 case 判 0** 的 checkpoint,按判 0 数降序。"判 0/9" 为 9 case 中判不合规数;"N/A" 为材料不适用数。**剔除 N/A 后全 0 的为最高置信**。

| CP | Element | 章节 | 官方条款(原文) | 判0/9 | N/A | 置信 |
|----|---------|------|----------------|-------|-----|------|
| **CP9** | E2 | 2.1 Buildings/equipment/facilities | There is adequate **lighting** for the export operations being conducted. | 8 | 1 | **最高** |
| **CP16** | E2 | 2.4 Screening | ensure that any large **contaminants** are removed from the plants or plant products | 7 | 2 | **最高** |
| **CP36** | E4 | 4.2 Phytosanitary security | minimising the risk of **substitution** (switching of goods) | 7 | 2 | **最高** |
| CP13 | E2 | 2.2 Design and construction | designed/constructed/maintained to provide disposal of all waste material (solids & liquids) efficiently & hygienically | 6 | 0 | 高 |
| CP40 | E4 | 4.3 Record keeping – traceability | Records must: be in English; include date; be accurate, legible, auditable | 6 | 0 | 高 |
| CP41 | E4 | 4.4 Importing country requirements | all importing country requirements … must be met | 6 | 2 | 高 |
| CP14 | E2 | 2.3 Inspection areas | inspection areas/equipment/facilities designed for sample collection, inspection, analysis by Authorised Officer | 5 | 3 | 中高 |
| CP29 | E4 | 4.1 Traceability & integrity | from the property (if any) from which they were transferred to receival into the establishment | 5 | 3 | 中高 |
| CP33 | E4 | 4.1 Traceability & integrity | a product flow chart demonstrating movement & maintenance of product integrity | 5 | 0 | 中高 |
| CP34 | E4 | 4.1 Traceability & integrity | system of controls to manage goods that fail inspection (blending, rejections, treatments, representations) | 5 | 2 | 中高 |
| CP4 | E1 | 1.2 Plans and specifications | all important features of registered area & adjoining sites (buildings, facilities, services) | 4 | 2 | 中高 |
| CP23 | E3 | 3.3 Record keeping – hygiene/waste/pest | Records must: be in English; include date; be accurate, legible, auditable | 4 | 4 | 中高 |
| CP31 | E4 | 4.1 Traceability & integrity | to the next premises which they are transferred to | 4 | 4 | 中高 |

### 9-case 逐案分布(3 个最高置信 CP)

```
CP    c1 c2 c3 c4 c5 c6 c7 c8 c9  #0 N/A
CP9    0  0  0  0  0 N/A  0  0  0   8   1   <- 8/8 有效 case 全 0(最强)
CP16   0  0  0  0  0  0  0 N/A N/A  7   2   <- 7/7 有效 case 全 0
CP36   0  0  0 N/A 0  0 N/A 0  0   7   2   <- 7/7 有效 case 全 0
```

---

## 五、3 个最高置信 CP 深度解读

三个系统性判 0 的 CP 集中在 **phytosanitary 控制的物理/流程环节**:

1. **CP9 照明(adequate lighting)** -- 8/8 有效 case 全 0。farm 案例材料系统性未提供"出口操作场所照明充足"的证据。
2. **CP16 筛选去杂(contaminants removed)** -- 7/7 有效 case 全 0。系统性未提供"移除植物/产品中大污染物(筛选)"的证据。
3. **CP36 防替换(minimising substitution)** -- 7/7 有效 case 全 0。系统性未提供"防止货物被调换/替换"的 phyto 安全控制证据。

**两种可能根因**(需人工/深挖确认):

- **(A) farm 实际未落实** -- 真不合规,应作为高优先级整改项上报;
- **(B) farm 已落实但材料未记录** -- 材料缺口,应补佐证材料后再审。

无论哪种,这三个 CP 都应作为**跨 case 系统性 finding** 上报,而非单 case 偶发问题。Element-4(追溯与 phyto 安全)整体偏弱:CP29/31/33/34/36/40/41 七个 CP 系统性判 0,提示该 Element 的控制体系在 farm 层面普遍未落实或未记录。

---

## 六、方法论价值:跨 case 广撒网 vs 多方法共识

本阶段验证了两种分析模式的互补性:

| 模式 | 回答的问题 | 优势 | 局限 |
|------|-----------|------|------|
| **单 case 多方法共识** | "该 case 是否合规?哪些 CP 不合规?" | 多视角交叉,单 case 结论可靠 | 单 case 视角,看不出系统性 |
| **跨 case 单方法广撒网**(本阶段) | "哪些 CP 系统性不合规?" | token 极省,暴露跨 case 一致性 | 单方法召回有限,需高一致性才可信 |

**关键例证 -- CP9 的暴露过程**:
- 在 case-001/002 的多方法共识分析中,CP9 仅 automatic_retrieval 判 0(单方法),被降权处理,**未进入共识不合规名单**。
- 跨 9 case automatic_retrieval 广撒网后,CP9 在 8/8 有效 case 全 0,一致性极高,**确认为系统性不合规**。
- 这说明单 case 多方法共识会漏掉"只被低召回方法捕获但跨 case 高度一致"的系统性问题;跨 case 广撒网正好补足。

**token 效率**:automatic_retrieval 13k tokens/call(7 方法最低),性价比 11.1 召回/M token,是 checkpoint_full(1.3)的 8.5 倍。跨 case 扫描用最低成本方法,深挖再用高成本方法,是最优配比。

---

## 七、工程改进

### 1. `scripts/run_case.py` 幂等性修复

**问题**:早期版本对 case_full / element_full / checkpoint_full / automatic_retrieval / verify_audit 走 `run_experiment` 会**全量重跑**,断点续跑时浪费配额。

**修复**:新增 `SINGLE_SHOT_METHODS` 分支,手动检测每个 unit 的 `result.json`(`valid && verdicts` 非空则跳过),仅对缺失 unit 调 `run_execution`。断点续跑只补 gap,不重跑。

**成效**:case-008 从 34/41 补到 41/41 仅跑 7 个 unit(62s);case-007 补 2 个 unit(14s)。

### 2. `scripts/scoreboard.py` 空骨架泄漏修复

**问题**:`_unit_dirs_with_results()` 把 verify_audit 的中间产物 `case-NNN-unit-NNN/result.json`(空骨架,`valid=False, verdicts=[]`)误计为 final unit,导致矩阵提取虚高。

**修复**:排除 `base/`、`stage*/` 同名子目录 shadow,并过滤 `valid=False` 或 `verdicts=[]` 的骨架。

### 3. 配额管理

- 识别 429 为**账户级配额**(Token Plan 2056),无法靠 backoff 绕过;策略改为"surface + stop,等窗口恢复"。
- 配额呈间歇窗口式恢复(每波 ~25-30 calls 后 429),agent_audit/verify_audit 等多 call 方法在配额紧张时失败率高,改为"等充足配额一次性跑完",避免半途反复 429 retry 浪费。

---

## 八、token 成本与配额

| 项 | token |
|----|-------|
| 9 case × automatic_retrieval | ~4.9M |
| 全方法 9 case(估算) | ~1.24B |
| **节省** | **99.6%** |

配额现状:MiniMax-M3 + MiniMax-2.7 共享 Token Plan 2056,每日重置。8/3 当下配额可用。

---

## 九、下一步建议

按优先级排序:

1. **深挖 CP9/CP16/CP36 根因**(高价值):用 checkpoint_full 或 stage_audit 在 1-2 个 case 深挖,判定根因 A(真不合规)vs B(材料缺口)。这决定 finding 的定性。
2. **补全 case-002/003 的 agent_audit/verify_audit**:凑齐第二个 7 方法全完整 case,做多 case 多方法共识交叉验证。
3. **继续扩 case-010+ automatic_retrieval**:巩固 CP9 的 8/8(目标 10+ case 全 0)。边际收益递减,可选。
4. **commit + push 到组织**:9 case 数据 + 本汇报 + run_case.py/scoreboard.py 修复 + 记忆更新,推到 origin + sztu(CSMining26)。
5. **Element-4 专项**:该 Element 7 个 CP 系统性判 0,建议作为专项 finding 整体上报。

---

## 十、附录:数据与脚本位置

- **原始 verdict**:`build/experiments/automatic_retrieval/case-{001..009}/track3-raw/unit-*/result.json`
- **CP 条款定义**:`checkingpoints_all_elements_onesheet.xlsx`
- **驱动脚本**:`scripts/run_case.py`(幂等,`--methods automatic_retrieval --case-id N`)
- **看板提取**:`scripts/scoreboard.py`(`_unit_dirs_with_results`)
- **跨 case 分析文档**:`docs/2026-08-03-cross-case-systemic-cps.md`
- **上一阶段汇报**:`docs/2026-08-01-stage-summary.md`

---

*本汇报基于 9 case automatic_retrieval 实验数据。CP9/CP16/CP36 的"有效 case 全 0"一致性为最高置信结论;其余 10 个 CP 的定性建议用高召回方法在单 case 深挖确认。*
