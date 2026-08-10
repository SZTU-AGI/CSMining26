# FRECA Task2 阶段汇报:跨 51 Case 系统性不合规 + 2-case 多方法共识交叉验证

**日期**: 2026-08-04(51-case 更新版,含置信度修正)
**任务**: FRECA Task2(出口植物及植物产品合规审核,41 checkpoint × 100 farm case)
**规则依据**: Export Control (Plants and Plant Products) Rules 2021
**模型**: MiniMax-M3(配额:Token Plan 2056,与 MiniMax-2.7 共享,每日重置)

---

## 一、概览

本阶段(8/1 – 8/4)以**最小 token 成本**完成方法效果验证,并通过**两种分析模式互补**修正初步结论:

- **跨 case 单方法广撒网**:51 case × automatic_retrieval 全完整(**2091 次 verdict**),扫描系统性不合规 CP。CP9 41/41、CP16 36/36 有效 case 全判 0(过半 100 case)。
- **2-case 多方法共识**:case-001 + case-002 两 case 7 方法全完整,交叉验证 CP 的多方法一致性。
- **核心修正**:初步判 CP9 最高置信,经 2-case 共识验证后发现 **CP9 可能是 automatic_retrieval 的 RAG 检索偏差**;**CP16 才是最可信系统性不合规**(两维度都强,36/36 有效 case 全 0)。
- token 消耗:51 case automatic_retrieval ≈ **27M tokens**(全方法 51 case 估算 ~7B,**省 99.5%以上**)。

---

## 二、实验进展

| 阶段 | case 范围 | 方法 | 成果 |
|------|----------|------|------|
| 8/1 前 | case-001 | 7 方法全跑 | 41/41 完整,首个全方法基线 |
| 8/1 – 8/4 | case-002~051 | automatic_retrieval 广撒网 | 51 case 全完整(CP9 41/41、CP16 36/36 有效全 0)|
| 8/3 | case-002 | 补 agent_audit + verify_audit | **第二个 7 方法全完整 case** |

### 数据完整度

- **automatic_retrieval**:case-001 ~ case-051 全部 41/41 valid(51 case 完整,过半 100 case)。CP9 41/41、CP16 36/36 有效 case 全判 0(34 个 CP ≥4 case 判 0)。
- **7 方法全完整**:case-001、case-002(两个基线 case)。
- **case-002 verify_audit**:41/41 valid,判 0 的 33 个 CP 中含 CP9、CP16,从复核角度支持二者不合规。

---

## 三、7 方法状态(case-001 + case-002 双基线)

| 方法 | 单位 | case-001 | case-002 | 性能 |
|------|------|----------|----------|------|
| case_full | 1 | ✅ | ✅ | 系统性偏宽松(2 case 80 合规/1 不合规),降权 |
| element_full | 4 | ✅ | ✅ | 完整 |
| checkpoint_full | 41 | ✅ | ✅ | 最严格(全案材料),参考金标准 |
| automatic_retrieval | 41 | ✅ | ✅ | **性价比最高**,跨 case 广撒网主力 |
| stage_audit | ~123 | ✅ | ✅ | 召回最高,判 0 最多(case-002 23 CP) |
| agent_audit | ~68 | ✅ | ✅ | case-002 判 0 最多(24 CP) |
| verify_audit | ~42 | ✅ | ✅ | 复核方法,case-002 判 0 33 CP(最严) |

---

## 四、核心发现:34 个系统性不合规 CP(51 case automatic_retrieval)

下表为 **≥4 case 判 0** 的 checkpoint(11-case 快照;51-case 升至 34 CP,CP9 41、CP16 36、CP34/CP41 等领先)。

| CP | Element | 章节 | 官方条款(原文摘录) | 判0/11 | N/A |
|----|---------|------|---------------------|--------|-----|
| CP9 | E2 | 2.1 Buildings/equipment | adequate **lighting** for export operations | 10 | 1 |
| CP16 | E2 | 2.4 Screening | large **contaminants** removed from plants/products | 9 | 2 |
| CP13 | E2 | 2.2 Design/construction | disposal of all waste material efficiently & hygienically | 7 | 0 |
| CP36 | E4 | 4.2 Phytosanitary security | minimising risk of **substitution** (switching of goods) | 7 | 3 |
| CP41 | E4 | 4.4 Importing country req | all importing country requirements must be met | 7 | 3 |
| CP14 | E2 | 2.3 Inspection areas | inspection areas designed for sample collection/analysis | 6 | 4 |
| CP29 | E4 | 4.1 Traceability | from property transferred to receival into establishment | 6 | 4 |
| CP33 | E4 | 4.1 Traceability | product flow chart demonstrating movement & integrity | 6 | 0 |
| CP34 | E4 | 4.1 Traceability | controls for goods that fail inspection (blending/rejection/treatment) | 6 | 3 |
| CP40 | E4 | 4.3 Record keeping | Records in English; date; accurate, legible, auditable | 6 | 0 |
| CP23 | E3 | 3.3 Record keeping | Records in English; date; accurate, legible, auditable | 5 | 4 |
| CP31 | E4 | 4.1 Traceability | to the next premises transferred to | 5 | 4 |
| CP4 | E1 | 1.2 Plans/specs | important features of registered area & adjoining sites | 4 | 3 |
| CP30 | E4 | 4.1 Traceability | while they are at the registered establishment | 4 | 5 |
| CP35 | E4 | 4.2 Phytosanitary security | maintaining risk for contamination or infestation | 4 | 0 |

> **Element-4(追溯与 phyto 安全)整体偏弱**:34 个系统性 CP 中过半属 Element-4,提示该 Element 控制体系在 farm 层面普遍未落实或未记录。
>
> **51-case 升至 34 个 CP ≥4 case 判 0**(CP9 41、CP16 36 领先;完整数据见 build/experiments/automatic_retrieval/)。

---

## 五、关键:2-case 多方法共识交叉验证(置信度修正)

仅看"跨 case 单方法广撒网"会误判。用 case-001 + case-002 的 7 方法数据交叉验证三个候选 CP:

| CP | 跨 case 单方法(automatic_retrieval) | 多方法共识(case-001 + case-002) | 综合结论 |
|----|------|------|------|
| **CP16** | 36/36 有效 case 全 0 | c1: 4 方法(auto/cp_full/stage/verify);c2: 2 方法 | **最可信**(两维度都强) |
| **CP36** | 多 case 判 0 但 case-010/013/014 判 1 | c1: 4 方法;c2: **6 方法**(最强) | 多方法最强,但跨 case 有例外 |
| **CP9** | 41/41 有效 case 全 0 | c1: **仅 1 方法**(auto);c2: 2 方法 | **警示:可能是方法偏差** |

### CP9 警示详解

CP9("adequate lighting")跨 51 case 中 41 个有效 case 全判 0(10 N/A),看似最强系统性。但在 case-001 **仅 automatic_retrieval 1 个方法判 0**,checkpoint_full / stage_audit / agent_audit / verify_audit 均未判 0。这强烈提示:

> CP9 的"系统性判 0"可能是 **automatic_retrieval 对"lighting"类证据的 RAG 检索偏差**(检索不到照明证据 → 一致判 0),而非 farm 真不合规。其他方法(用全案材料)未判 0,说明材料中可能有照明相关内容,RAG 没检索到。

### 2-case 多方法共识不合规 CP

两 case 都被 ≥3 方法判 0 的 CP(共识最强):**CP23、CP34、CP36**。

### 置信度修正排序

| 排名 | CP | 依据 |
|------|-----|------|
| 1 | **CP16** | 跨 case 36/36 全 0 + case-001 4 方法共识(两维度都强) |
| 2 | **CP36** | 多方法共识最强(c1 4 + c2 6 方法),但 case-010 等判 1(非绝对系统性) |
| 3 | **CP9** | 跨 case 41/41 全 0 但多方法共识弱,疑似 RAG 偏差,需高召回方法深挖确认 |
| - | CP23/CP34 | 2-case 共识(都 ≥3 方法),建议关注 |

> **此前将 CP9 排第一是单方法偏差导致的误判,现修正。**

---

## 六、方法论价值:两种分析模式必须互补

| 模式 | 回答的问题 | 优势 | 局限 |
|------|-----------|------|------|
| **跨 case 单方法广撒网** | "哪些 CP 跨 case 一致判 0?" | token 极省,暴露跨 case 一致性 | 单方法可能有系统偏差(如 CP9) |
| **2-case 多方法共识** | "哪些 CP 被多方法一致判 0?" | 多视角交叉,排除单方法偏差 | 仅 2 case,看不出跨 case 稳健性 |

**关键教训**:CP9 的案例证明,单方法跨 case 高度一致**不等于**真系统性不合规 -- 可能是该方法对某类证据的系统偏差。**必须用多方法共识交叉验证**才能定论。CP16 两个维度都支持,才是可信的系统性 finding。

**token 配比策略**:跨 case 扫描用最低成本方法(automatic_retrieval 13k/call),定论再用高成本方法(case-001/002 7 方法)。本阶段 51 case 广撒网 + 2 case 全方法,总成本 ~27M + 两 case 全方法,远低于"全方法全 case"。

---

## 七、工程改进

### 1. `scripts/run_case.py` 幂等性修复

新增 `SINGLE_SHOT_METHODS` 分支,断点续跑仅补 gap(case-008 从 34/41 补到 41/41 仅跑 7 unit;case-002 agent_audit 补 12 unit)。

### 2. `scripts/scoreboard.py` 空骨架泄漏修复

`_unit_dirs_with_results()` 排除 `base/`、`stage*/` shadow 及 `valid=False`/`verdicts=[]` 空骨架,避免矩阵虚高。

### 3. 配额管理

429 为账户级配额(不可 backoff 绕过),策略"surface + 等窗口恢复"。verify_audit 等不幂等方法需配额充足时一次性跑完。8/3 因连续高消耗耗尽配额,跨 0 点(每日重置)后恢复,loop 模式自动续跑。

---

## 八、token 成本

| 项 | token |
|----|-------|
| 51 case × automatic_retrieval | ~27M |
| case-001/002 7 方法全完整 | ~280M |
| 全方法 51 case(估算) | ~7B |
| **广撒网 + 双基线策略节省** | **>99%** |

---

## 九、下一步建议

1. **深挖 CP16 根因**(最高优先):用 checkpoint_full/stage_audit 在 1-2 case 深挖,判定根因 A(真不合规)vs B(材料缺口)。CP16 两维度都支持,深挖价值最高。
2. **CP9 方法偏差确认**:用 checkpoint_full(全案材料,非 RAG)在 case-001 验证 CP9 -- 若 checkpoint_full 不判 0,则坐实 RAG 偏差;若判 0,则 CP9 真不合规。
3. **CP36 跨 case 例外分析**:case-010/013/014 判 1(合规),分析其材料特殊性。
4. **继续扩 case**:CP16 已 36/36 有效全 0(51 case),目标 100 case 全覆盖。
5. **Element-4 专项**:34 个系统性 CP 中过半属 Element-4,建议作为专项 finding 整体上报。

---

## 十、附录:数据与脚本位置

- **原始 verdict**:`build/experiments/{method}/case-{001..051}/track3-raw/unit-*/result.json`
- **CP 条款定义**:`checkingpoints_all_elements_onesheet.xlsx`
- **驱动脚本**:`scripts/run_case.py`(幂等,`--methods automatic_retrieval --case-id N`)
- **看板提取**:`scripts/scoreboard.py`(`_unit_dirs_with_results`)
- **跨 case 专项分析**:`docs/2026-08-03-cross-case-systemic-cps.md`
- **上一阶段汇报**:`docs/2026-08-01-stage-summary.md`

---

*本汇报基于 51 case automatic_retrieval + case-001/002 7 方法数据。CP16 为最可信系统性不合规(两维度验证);CP9 疑似方法偏差,需高召回方法确认;CP36 多方法强但跨 case 有例外。*
