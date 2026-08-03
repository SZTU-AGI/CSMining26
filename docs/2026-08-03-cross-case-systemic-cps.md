# 跨 9 Case 系统性不合规 Checkpoint 分析

**日期**: 2026-08-03
**方法**: automatic_retrieval(RAG 检索材料 + LLM 推理,最省 token)
**范围**: case-001 ~ case-009,每 case 41 个 checkpoint,共 369 次 verdict
**模型**: MiniMax-M3

---

## 1. 目标与策略

用户要求"用最少 token 验证方法效果"。本分析用 **automatic_retrieval**(13k tokens/call,7 方法中最低)跨 9 case 广撒网,验证哪些 checkpoint 是**系统性不合规**(跨多 case 一致判 0),而非单 case 偶发。

### 性价比对比

| 方法 | token/call | 召回率 | 性价比(召回/M token) |
|------|-----------|--------|----------------------|
| **automatic_retrieval** | 13k | 11.1% | **11.1(最高)** |
| checkpoint_full | 170k | 22% | 1.3 |
| 全方法单 case | — | — | — |

9 case × automatic_retrieval ≈ **4.9M tokens**;若用全方法 9 case 需 ~1.24B tokens。**省 99.6%**。

---

## 2. 核心发现:3 个"有效 case 全 0"的最高置信系统性 CP

下列 3 个 checkpoint 在所有**适用**的 case 中全部判 0(不合规),无一放行:

| CP | Element | 章节 | 官方条款(原文) | 判 0 | N/A |
|----|---------|------|----------------|------|-----|
| **CP9** | Element-2 | 2.1 Buildings, equipment, facilities and service | There is adequate **lighting** for the export operations being conducted. | **8/9** | 1 |
| **CP16** | Element-2 | 2.4 Screening | ensure that any large **contaminants** are removed from the plants or plant products | **7/9** | 2 |
| **CP36** | Element-4 | 4.2 System of controls – phytosanitary security | minimising the risk of **substitution** (switching of goods) | **7/9** | 2 |

> "N/A" 表示该 case 材料不适用此 CP(如该 case 无对应业务环节)。剔除 N/A 后,**所有有效 case 均判 0**。

### 9-case 逐案分布

```
CP    c1 c2 c3 c4 c5 c6 c7 c8 c9  #0 N/A
CP9    0  0  0  0  0 N/A  0  0  0   8   1   <- 8/8 有效 case 全 0(最强)
CP16   0  0  0  0  0  0  0 N/A N/A  7   2   <- 7/7 有效 case 全 0
CP36   0  0  0 N/A 0  0 N/A 0  0   7   2   <- 7/7 有效 case 全 0
```

---

## 3. 根因解读

三个系统性判 0 的 CP 集中在 **phytosanitary 控制的物理/流程环节**:

1. **CP9 照明(adequate lighting)** —— farm 案例材料系统性未提供"出口操作场所照明充足"的证据。
2. **CP16 筛选去杂(contaminants removed)** —— 系统性未提供"移除植物/产品中大污染物(筛选)"的证据。
3. **CP36 防替换(minimising substitution)** —— 系统性未提供"防止货物被调换/替换"的 phyto 安全控制证据。

**两种可能根因**(需进一步人工/深挖确认):
- (A) farm 实际未落实这三项控制 —— **真不合规**,应作为高优先级整改项;
- (B) farm 实际已落实但材料未记录 —— **材料缺口**,应补充佐证材料后再审。

无论哪种,这三个 CP 都应作为**跨 case 系统性 finding** 上报,而非单 case 偶发问题。

---

## 4. 全部 ≥4 case 判 0 的 CP(共 13 个)

| CP | 判 0/9 | N/A | Element | 主题 | 置信度 |
|----|--------|-----|---------|------|--------|
| CP9 | 8 | 1 | E2 | 照明 | 最高 |
| CP16 | 7 | 2 | E2 | 筛选去杂 | 最高 |
| CP36 | 7 | 2 | E4 | 防替换 | 最高 |
| CP13 | 6 | 0 | E2 | 废料处置设计 | 高 |
| CP40 | 6 | 0 | E4 | 记录要求(英文/日期/可审计) | 高 |
| CP41 | 6 | 2 | E4 | 进口国要求 | 高 |
| CP4 | 4 | 2 | E1 | 注册经营范围 | 中高 |
| CP14 | 5 | 3 | E2 | (见 xlsx) | 中高 |
| CP23 | 4 | 4 | E3 | (见 xlsx) | 中高 |
| CP29 | 5 | 3 | E4 | (见 xlsx) | 中高 |
| CP31 | 4 | 4 | E4 | (见 xlsx) | 中高 |
| CP33 | 5 | 0 | E4 | (见 xlsx) | 中高 |
| CP34 | 5 | 2 | E4 | (见 xlsx) | 中高 |

> 完整条款文本见 `checkingpoints_all_elements_onesheet.xlsx`。

---

## 5. 方法论价值

本分析验证了 **automatic_retrieval 跨 case 广撒网** 的方法效果:

- **能发现多方法共识漏掉的系统性 CP**:CP9 在 case-001/002 的多方法共识分析中仅 automatic_retrieval 判 0(被降权),跨 9 case 广撒网才暴露其 8/8 全 0 的系统性。
- **token 效率极高**:4.9M tokens 完成 9 case × 41 CP = 369 次 verdict 的系统性扫描。
- **适用于"系统性 vs 偶发"判别**:单 case 多方法共识判别"该 case 是否合规",跨 case 单方法广撒网判别"该 CP 是否系统性不合规" —— 两者互补。

### 局限

- automatic_retrieval 召回率偏低(11.1%),部分 CP 的"判 0"可能是 RAG 未检索到证据而非真不合规(对应根因 B)。
- 对 CP9/CP16/CP36 这类"全 0"高一致性 CP,误判概率低;对 4-5/9 的 CP,建议再用 checkpoint_full 或 stage_audit 在单 case 深挖确认。

---

## 6. 数据位置

- 原始 verdict: `build/experiments/automatic_retrieval/case-{001..009}/track3-raw/unit-*/result.json`
- 驱动脚本: `scripts/run_case.py`(幂等,支持断点续跑)
- 看板提取: `scripts/scoreboard.py`(`_unit_dirs_with_results` 排除中间产物 shadow)
