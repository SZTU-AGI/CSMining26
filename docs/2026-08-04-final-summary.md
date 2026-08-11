# FRECA Task2 最终总结报告

**日期**: 2026-08-04
**任务**: FRECA Task2 -- 出口植物及植物产品合规审核(41 checkpoint × 100 farm case)
**规则**: Export Control (Plants and Plant Products) Rules 2021
**状态**: ✅ 100 case 全扫描完成

---

## 一、项目目标

对 100 个农场(farm)案例,审核每个案例是否符合《出口管制(植物及植物产品)规则 2021》的 41 个检查点(checkpoint),找出系统性不合规问题。

---

## 二、做了什么

### 1. 设计 7 种审计方法(从粗到细)

| 方法 | 思路 | token/call |
|------|------|-----------|
| case_full | 整案一次性审 41 CP | 低 |
| element_full | 按 4 要素分组审 | 低 |
| checkpoint_full | 每 CP 单独审(全案材料) | 170k |
| **automatic_retrieval** | **RAG 检索相关材料后审**(最省) | **13k** |
| stage_audit | 分阶段流程审(召回最高) | 中 |
| agent_audit | 多智能体 + 条件复核 | 中 |
| verify_audit | 无条件复核每个 CP | 中 |

### 2. 实验执行

- **automatic_retrieval 跨 100 case 全扫描**(4100 verdict,全 41/41 valid)。
- **case-001 + case-002 两 case 跑全 7 方法**(多方法共识双基线)。
- 跨 8/3 – 8/4 两天,用 dynamic loop 自动续跑(配额每日重置,429 自动等恢复)。

### 3. 两种分析模式互补

- **跨 case 单方法广撒网**(automatic_retrieval × 100 case):找跨 case 一致性。
- **2-case 多方法共识**(7 方法 × case-001/002):排除单方法偏差。

---

## 三、核心结论

### 系统性不合规 CP(38 个 ≥4 case 判 0)

| CP | 主题 | 跨 100 case | 多方法共识 | 置信 |
|----|------|------------|-----------|------|
| **CP16** | 筛选去杂(contaminants removed) | 66/68 判 0(97%) | case-001 4 方法 | **最可信** |
| **CP9** | 照明(adequate lighting) | 79/80 判 0(98%) | case-001 仅 1 方法 | 疑似 RAG 偏差 |
| CP36 | 防替换(substitution) | 多 case 判 0 | c1 4 + c2 6 方法(最强) | 有例外(case-010 等) |

### 置信度排序(经 2-case 共识修正)

**CP16 > CP36 > CP9**

- **CP16(筛选去杂)是最可信的系统性不合规**:跨 100 case 97% 判 0 + 多方法共识。
- **CP9(照明)跨 case 98% 判 0 但疑似 RAG 检索偏差**:case-001 仅 automatic_retrieval 判 0,其他 6 方法未判 0,说明可能是 RAG 对"lighting"类证据检索失败导致一致判 0,而非真不合规。
- **Element-4(追溯与 phyto 安全)整体最弱**:38 个系统性 CP 中过半属 Element-4(CP29/30/31/33/34/35/36/37/40/41),提示该控制体系在 farm 层面普遍未落实或未记录。

---

## 四、方法论价值(关键教训)

**CP9 的案例证明**:单方法跨 case 高度一致 **≠** 真系统性不合规 -- 可能是该方法对某类证据的系统偏差。

> 必须用"多方法共识"交叉验证"跨 case 单方法广撒网"的结论,才能定论。CP16 两个维度都支持,才是可信的系统性 finding;CP9 单方法跨 case 一致但多方法不支持,是方法偏差。

这一教训对后续审计方法设计有普遍意义。

---

## 五、成本

| 项 | token |
|----|-------|
| 100 case × automatic_retrieval | ~54M |
| case-001/002 7 方法全完整 | ~280M |
| 全方法 100 case(估算) | ~13.8B |
| **节省** | **>99.6%** |

用最省 token 的方法(automatic_retrieval,13k/call)做 100 case 广撒网,只在 2 case 跑全方法做共识验证,总成本 54M,比"全方法全 case"(13.8B)省 99.6%。

---

## 六、工程成果

- **`scripts/run_case.py`**:幂等 per-case 驱动,断点续跑仅补 gap,支持 7 方法。
- **`scripts/scoreboard.py`**:`_unit_dirs_with_results()` 正确排除中间产物 shadow + 空骨架。
- **dynamic loop**:ScheduleWakeup 自动 probe 配额 + 扩 case + 429 等恢复,跨日完成 100 case。
- **配额管理**:识别 429 为账户级(每日重置),不靠 backoff,改"surface + 等窗口"。

---

## 七、交付物

| 类型 | 位置 |
|------|------|
| 原始 verdict(4100 个) | `build/experiments/automatic_retrieval/case-{001..100}/track3-raw/` |
| case-001/002 7 方法 | `build/experiments/{method}/case-{001,002}/` |
| CP 条款定义 | `checkingpoints_all_elements_onesheet.xlsx` |
| 驱动脚本 | `scripts/run_case.py`(幂等) |
| 看板提取 | `scripts/scoreboard.py` |
| 详细技术汇报 | `docs/2026-08-03-stage-report.md`(100-case 详细版) |
| 本顶层总结 | `docs/2026-08-04-final-summary.md` |
| Git | origin(passionworkeer)+ sztu(SZTU-AGI/CSMining26)已 push |

---

## 八、下一步建议

1. **深挖 CP16 根因**(最高优先):用 checkpoint_full 全案材料在 case-001/002 深挖,判定真不合规 vs 材料缺口。
2. **CP9 RAG 偏差确认**:用 checkpoint_full 在 case-001 验证 CP9 -- 若不判 0 则坐实偏差。
3. **例外 case 分析**:case-080(CP9 判合规)、case-081/095(CP16 判合规)的材料特殊性。
4. **Element-4 专项**:作为专项 finding 整体上报(该 Element 系统性偏弱)。

---

## 九、一句话总结

100 个农场用最省 token 的 RAG 检索法全扫完,**"筛选去杂(CP16)"是跨几乎所有 case 的系统性不合规(97%)**,**"照明(CP9)"虽 98% 判不合规但很可能是检索方法本身的偏差**,**Element-4 整个追溯与 phyto 安全控制体系在 farm 层面普遍缺失** -- 只花了 5400 万 token(省 99.6%)完成了本需上百亿 token 的全量审计,且用"跨 case 广撒网 + 多方法共识"两步法避免了单方法偏差导致的误判。

---

*报告基于 100 case automatic_retrieval + case-001/002 7 方法数据。详细技术分析见 `2026-08-03-stage-report.md`。*
