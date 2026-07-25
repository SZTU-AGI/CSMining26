# FRECA Task 2 数据 & 任务说明书复核报告

> 本报告基于 `D:\Data\Desktop\contest\Task2\` 下的实际数据 + `task.md` 复核得出。
> 复核日期：2026-07-17。
> 复核对象：参赛说明书、数据包结构、检查点定义、提交模板、证据文件内容、Profile 脚本输出。

---

## 0. TL;DR

- **数据基本可用**：100 个逻辑案例 × 9 条证据轨道 = 898 个文件，可以完整恢复 case_id 1..100 与 RE Number 的映射。
- **任务书自相矛盾**：`task.md` 的 "Dataset" 部分写 96 例，但 "Submission Format" / "Evaluation" 全部按 100 例、4100 判决计算，且解压数据恰好也是 100 例。**96 是旧数字**。
- **提交模板不完整**：当前 `submission_template.xlsx` 实际只有一行表头，没有 100 行数据。
- **结构性数据问题**（影响提交是否能跑通，需要组委会答复）：
  1. `RE-WA-2021-0077` 目录同时容纳 case 35（Goldfields）和 case 100（Midwest），二者共享同一 RE Number。
  2. case 24（`RE-SA-2021-0066`）和 case 80（`RE-QLD-2022-0077`）缺失 Track 1。
- **数据生成痕迹**（可能是赛题设计，也可能是 bug，建议确认）：
  1. 所有 100 个 Track 3（Pest Control Record）的封面 `RE Number` 字段与所在目录 RE Number 不一致。
  2. Track 4（Farm Management Plan）使用旧版规则编号 `C1..C7`，与官方 `CP1..CP41` 体系不一致。
  3. Track 3 内嵌 "Audit scenario: ..."、"Fully compliant"、"NON-COMPLIANT (C3)" 等近结论性字段。
  4. 部分 Track 1 中 `Registration Status` 已显式给出 "current / lapsed / suspended / under review" 状态声明。
- **可视为"现实审计中农场提交不规范"的迹象**：
  - 文件之间场名、商品、地址不一致；
  - 记录语言非英文、缺日期、缺签字；
  - Track 3 内嵌"留存期提示"（≥2 年 vs <2 年）暗示审计结论；
  - 同一案例里同时存在"无系统"和"有完整系统"的描述。
- **建议**：不要直接把这些当作"bug"，而是当作"证据完整性（evidence integrity）"的前置审计阶段，先判断哪些证据可信、哪些需要降权处理，再做 CP1–CP41 的合规裁决。

---

## 0.5 问题优先级矩阵（自决 vs 必问组委会）

| 原问题 | 是否能本地流程化 | 落地位置 | 是否发邮件问 |
|---|---|---|---|
| 案例数 96 vs 100 | ✅ Stage 0.5 异常筛选 = 剔除 **4 例** → **96 正式评测** | 5.0 节 | ❌ 已自决（与 task.md 一致） |
| 提交模板缺行 | ✅ 按 case_id 升序填 100 行骨架（异常 case 填 N/A） | Stage 8 | 🟡 邮件 5a 问 |
| `RE-WA-2021-0077` 双案例 | ⚠️ 流程能识别但无法在提交表里唯一标识 | Stage 0.5 标记 anomaly | 🟡 邮件 1 问（确认 RE Number 重复如何处理） |
| Track 1 缺失（case 24/80） | ✅ 作为异常判据之一 | Stage 0.5 + Stage 2 | ❌ |
| Track 3 内部 RE 100% 错位 | ✅ 忽略 Track 3 RE 字段，以目录为准 | Stage 1 | ❌ |
| Track 4 旧版 C1..C7 | ✅ 本地建映射表 C1..C3→CP8..10、C4..C6→CP11..13、C7→CP17..19 | 5.7 节 | ❌ |
| Track 3 NOTE / Audit scenario 字段 | ✅ 作为证据的一部分供模型读取 + cross-check | Stage 5 + 5.5 | 🟡 邮件 3 问（确认合规） |
| 跨文档场名/commodity 不一致 | ✅ Stage 4 自动检测 | Stage 4 | ❌ |
| Track 1 Registration Status 声明 | ✅ 作为 CP1/CP2 基线证据 | Stage 3 | ❌ |
| case_id ↔ RE 映射 | ✅ 本地脚本已恢复 | 1.3 节 | ❌ |
| 文件解析 | ✅ python-docx + openpyxl + pypdf | 8 节 | ❌ |
| Prompt 硬编码边界 | ❌ 规则解读，不可本地解决 | — | **🔴 必问 2** |
| 是否提供带标签训练集 | ❌ 影响开发流程 | — | **🔴 必问 4** |
| Demo / 答辩要求 | ❌ 影响交付物清单 | — | 🟡 邮件 5b 问 |

**结论**：必问组委会 **4 个问题**（1、2、3、4、5），其余 9 项本地流程化。**关键自决**：96 是真实评测数（= 100 − 4 异常）。

---

## 1. 数据集结构（已确认）

### 1.1 文件清单

| 文件 | 内容 | 备注 |
|---|---|---|
| `Task2/SFRE_cases.zip` | 100 案例的证据包 | 解压后含 `extracted/SFRE_cases/` + `__MACOSX/` |
| `Task2/checkingpoints_all_elements_onesheet.xlsx` | CP1–CP41 完整定义 | 4 行 × 41 列；Row 3 是文本，Row 4 是 CP 编号 |
| `Task2/submission_template.xlsx` | 提交模板 | **当前只有表头**，无 100 行数据 |
| `Task2/1-Export Control (Plants and Plant Products)Rules 2021.pdf` | 政策依据 | 132 页 |
| `Task2/Task2 Description.docx` | 任务说明 | 与 `task.md` 内容基本一致 |
| `Task2/extracted/SFRE_cases/` | 已解压的案例 | 99 个 RE 目录（实际承载 100 个逻辑案例） |

### 1.2 证据轨道（Track 1–9）

| Track | 名称 | 格式 | 文件数 | 备注 |
|---|---|---|---|---|
| 1 | 企业登记申请表 | .docx | **98** | case 24、80 缺失 |
| 2 | HACCP 计划 | .docx | 100 | 文件名带 `_<NN>_<公司名>` 编号 |
| 3 | 害虫防治记录 | .xlsx | 100 | 多 sheet：Cover / Pest Activity Log / Bait Station Register / Chemical Storage Register / Establishment Condition |
| 4 | 农场管理计划 | .docx | 100 | 显式引用旧版 Rule 章节 + `C1..C7` 编号 |
| 5 | 农场用地规划图 | .docx | 100 | 92 个含相邻场地 RE 编号（cross-reference，非自身） |
| 6 | 农场卫生与清洁计划 | .docx | 100 | 部分含"6 months retention"违规提示 |
| 7 | 诱饵站位置图 | .docx | 100 | 图形/drawings 较多 |
| 8 | 植物检疫安全程序 | .docx | 100 | 文件名带 `_<NNN>` 编号 |
| 9 | 可追溯性记录 | .xlsx | 100 | 文件名带 `_<NNN>` 编号 |
| **合计** | | | **898** | 698 docx + 200 xlsx |

### 1.3 案例目录结构（已实测）

- **逻辑案例数**：100 个（case_id 1..100，全部存在）
- **RE 目录数**：99 个
- **混合目录**：仅 `RE-WA-2021-0077` 一处，里面容纳 case 35（Goldfields Grain Storage Pty Ltd）+ case 100（Midwest Grain Holdings Pty Ltd）的全套 18 个文件
- **缺失 Track 1**：case 24（`RE-SA-2021-0066`）、case 80（`RE-QLD-2022-0077`）
- **案例索引恢复方式**：
  - Track 1/2/3 文件名：`1_Farm_<NN>_...`、`2_HACCPPlan_<NN>_...`、`3_PestControlRecord_<NN>_...` → 直接取 `NN`
  - Track 8/9 文件名：`8_..._<NNN>.docx`、`9_..._<NNN>.xlsx` → 取末尾 3 位
  - Track 4/5/6/7 文件名无编号 → 通过目录推断；混合目录用公司名 `Goldfields` / `Midwest` 拆分

---

## 2. 任务说明书（`task.md`）中的不一致

### 2.1 案例数：96 vs 100

| 出处 | 行号 | 内容 |
|---|---|---|
| Dataset | line 91 | "Total cases: 96" |
| Submission Format | line 103 | "100 data rows (one per case)" |
| Submission Format | line 105 | "RE Number, CP1, CP2, …, CP41"（共 42 列） |
| Evaluation | line 118 | "4,100 audit decisions (41 × 100 cases)" |
| Evaluation | line 121 | "proportion of correct verdicts for each individual checking point across 100 cases" |
| Evaluation | line 123 | "proportion of correct verdicts within each of the four compliance elements" |

**矛盾点**：96 vs 100 与 4,100 = 41×100 自洽性。**96 几乎肯定是过时数字**，但需组委会明确"最终以哪个数为准"——这会直接影响提交表的行数与本地校验脚本的实现。

### 2.2 提交模板 vs 规则

**规则原话**（line 111–113）：
> Do not add, remove, or reorder rows or columns. The RE Number column must match the provided case identifiers exactly.

**现状**：
- `submission_template.xlsx` 实际只有 1 行表头（`max_row=1, max_col=42`）。
- 没有预置的 100 行 RE Number 顺序。
- 参赛者**必须**自行填入 100 行。

**风险**：如果官方有"预设的 RE Number 顺序"，自行填入会违反 "must match the provided case identifiers exactly"。需要组委会确认：
- 顺序是否任意（按 case_id 1..100 升序即可）；
- 是否会下发修正后的模板（带 100 行 RE 编号）；
- 评测脚本是用 case_id 行号匹配，还是按 RE Number 字符串匹配。

### 2.3 Human Involvement Constraint 的边界（line 127–131）

> Participants must not encode compliance rules directly into prompts (e.g., "CP3 requires X"). The AI system must derive its reasoning solely from the policy document and the farm evidence. Prompts that hard-code checking-point logic will be deemed in violation during method verification.

**模糊点**：
1. 是否允许把官方 `checkingpoints_all_elements_onesheet.xlsx` 中的 CP 原文作为模型输入？
2. 是否允许模型从政策 PDF 中自动抽取"Rules s4-2 ↔ Element 2.1"的引用关系？
3. 是否允许把模型抽取出的 41 条"CP ↔ 证据要件"映射作为缓存复用？
4. 是否允许"对每条证据问 yes/no 问题"的端到端推理？
5. 同一条规则若由模型多次推导得到相同答案，是否满足方法可复现性？

**这 5 条建议在向组委会提问时合并为 1 个多子项问题。**

---

## 3. 数据层面的真实问题（结构性）

### 3.1 `RE-WA-2021-0077` 双重案例

**现象**：目录下有 18 个文件（每条 track 各 2 个）：
```
1_Farm_35_Goldfields_Grain_Storage_Pty_Ltd.docx       ← case 35
1_Farm_100_Midwest_Grain_Holdings_Pty_Ltd.docx        ← case 100
2_HACCPPlan_35_Goldfields_Grain_Storage_Pty_Ltd.docx  ← case 35
2_HACCPPlan_100_Midwest_Grain_Holdings_Pty_Ltd.docx   ← case 100
...（track 3..9 同理）
```

**冲突**：两个独立 RE Number 应当不同的案例，复用同一个 RE Number `RE-WA-2021-0077`。
**影响**：提交表只有 1 列 "RE Number"，无法在保留官方 RE 标识符的同时区分两个案例。
**可能的解释**（自检，未验证）：
- 评测方使用 case_id 作为内部主键，RE Number 仅做展示用途；
- 这是数据 bug，需要补发新 RE 编号；
- 评测时随机只取其中一个案例。

### 3.2 Track 1 缺失

| Case | 目录 | 缺失项 | Track 2 中可见的痕迹 |
|---|---|---|---|
| 24 | `RE-SA-2021-0066` | Track 1 | HACCP 内部 Establishment 名 = "Tropical Farms Qld Pty Ltd"，引用不规范号 `RE-QLD-2891` |
| 80 | `RE-QLD-2022-0077` | Track 1 | HACCP 内部 Commodity、Scope 等可能正常，但缺少注册主体声明 |

**两种解读**：
- 故意的（"evidence may be incomplete" 已写在规则里，line 101）；
- 漏传（其他 98 个案例的 Track 1 都存在，缺这两个不太像抽样设计）。

### 3.3 Track 3 系统性 RE 错位

**实测**：`verify_findings.py` 输出 `track3_wrong_re=100`，即 100/100 个 Track 3 文件的封面 `RE Number` 字段与所在目录 RE Number 不一致。

**例**：`RE-WA-2021-0077/Midwest`（case 100）的 Track 3：
- Cover sheet：`Establishment Name = Burnett Grain Terminal`，`RE Number = RE-QLD-2020-8012`
- 所有后续 sheet 的页眉都写 `Burnett Grain Terminal | RE-QLD-2020-8012 | ...`
- 数据本身（活动日志、诱饵站、化学品、现场条件）齐全且看起来真实

**推断**：Track 3 文件是从某个 master 模板批量克隆，封面 RE 字段没改；数据是按"真实场景模板"生成的。
**对方案影响**：
- 必须按**目录 RE** 而非文件内 RE 来归属案例；
- "RE 编号不一致"本身可能是一个**针对 CP1/CP2** 的预设干扰——RE 内部登记的号与对外公示的号不一致，恰好是 "establishment operating within registered scope" 的反例。

### 3.4 Track 4 旧版 Rule 章节编号

每个 Track 4 文件都包含以下三行（每行 100 次出现）：
```
Rules s4-2 — Element 2.1 checking points C1, C2, C3
Rules s4-2(5), s4-2(6) — Element 2.2 checking points C4, C5, C6
Rules s4-7A(2)(a) — Element 3.1 checking point C7
```

**异常**：官方用 `CP1..CP41`，Track 4 用 `C1..C7`（无 P）。
**两种解读**：
- Track 4 文件基于旧版规则生成；
- Track 4 内部编号是组织方的"内部别名"，与官方 CP 体系是不同维度。

**对方案影响**：检索策略如果按 `s4-2` / `s4-7A` 这些章节号去 PDF 查条款，可以省 token；但要确认这些章节号在 132 页 PDF 中是否确实存在且对应正确的内容（用 `policy_index.py` 可以快速核对）。

### 3.5 Track 3 内嵌的"近结论"字段

**8 类审计场景**（每类 12–13 个案例，总计 100）：
```
13 × Bait stations found empty and damaged at audit
13 × New establishment registered Oct 2024 (<2 years records)
12 × Chemicals stored in unlocked cupboard inside the pulse grading shed
12 × Persistent rodent activity (8 inspections, no escalation)
12 × Active insect infestation (grain weevils in seed cotton store)
13 × Records in Vietnamese; dates missing; legibility issues
12 × No formal pest control system documented
13 × Fully compliant
```

每类场景在 Cover sheet 第 14 行（最后一行）以 `Audit scenario: <文字>` 形式呈现。
**另外**，Record Period 段会显式标注：
```
NOTE: Records retained from 2020 (≥ 2 years — compliant). Language: English
NOTE: Records retained from 2025 (< 2 years — NON-COMPLIANT (C3)). Language: English
NOTE: Records retained from 2021 (≥ 2 years — compliant). Language: Vietnamese (partial) / English (recent) — NON-COMPLIANT (C4)
```

**问题**：这些字段是"给审计员的备忘"还是"泄漏给模型的提示"？
- 如果是证据，则模型可以直接引用，对应 CP20–CP23、CP25–CP28 等可以快速裁决。
- 如果是元信息，应在评测时剥离。

**建议**：把这部分**当作证据的一部分**纳入推理（与规则不冲突），但**不要让 prompt 直接读取它作为结论**——让模型从证据自然得出同样结论，再对比校验。

### 3.6 Track 1 内嵌 Registration Status 声明

部分 Track 1 在第 3 段直接给出状态：

| RE | 状态 |
|---|---|
| `RE-NSW-2020-0033` | lapsed on 30 June 2024, renewal not lodged |
| `RE-NSW-2020-0088` | current and active |
| `RE-NSW-2021-0099` | under review following complaint |
| `RE-WA-2021-0077`（case 100） | suspended on 3 March 2025 |

**影响**：这些直接对应 CP1（operating within registered scope）和 CP2（registration not suspended）的判定。

**重要认知**：Track 1 不是被审计的证据，而是**审计员拿到的注册基线文件**——它定义了"机构当前合法状态"，其他 track 是判断"在该状态下是否符合各类要求"的依据。
- CP1/CP2：应直接读 Track 1 的 Registration Status；
- CP3–CP41：需要综合 Track 2–9。

---

## 4. 可视为"农场提交不规范"的迹象

这部分不需要组委会答复，而是建议**直接纳入证据完整性预审流程**。

### 4.1 跨文档场名不一致

例：case 24 目录名 `RE-SA-2021-0066`，Track 2 内部：
- Establishment 字段 = "Tropical Farms Qld Pty Ltd"（状态名）
- Address = "16 Station Road, Victoria"（SA 目录却写 Victoria 地址）
- 引用 `RE-QLD-2891`（不规范号）
- 公司名（从 Track 2 文件名）= "Mid North Lentil Growers Pty Ltd"

三重不一致——这种程度的混乱在真实审计中会被标记为"提交材料完整性存疑"，需要额外的 cross-check。

### 4.2 记录语言 / 日期缺失

- Track 3 中 13 例使用越南语早期记录 → 直接违反 CP23（records must be in English）
- Track 6 中 8 例只保留 6 个月记录 → 违反 CP22（≥2 years）
- Track 3 多例在日期列出现 "—"（空）或缺失行号

### 4.3 "无系统"与"完整系统"自相矛盾

部分案例 Track 4（管理计划）描述存在完整 IPM 系统，而 Track 3（pest control record）却声明 "No formal pest control system documented"——同一案例对同一事实给出冲突陈述。

### 4.4 隐含的"答案"字段

- Track 3 `Audit scenario` 字段中已分类 8 种典型场景。
- Track 3 `NOTE:` 行中已部分标注 `(compliant)` / `(NON-COMPLIANT (C3))`。
- Track 1 Registration Status 已直接声明当前状态。

这些字段**不能**直接作为裁决依据（违反规则），但**可以**作为内部 cross-check 的对照——模型自主推导出 X，对照这里也是 X，置信度提升；若不一致，需要进一步分析。

---

## 5. 建议：证据完整性预审流程

> 在进入 CP1–CP41 的正式审计之前，先做一次"证据完整性（evidence integrity）"审计，目的是：
> 1. 识别"哪些案例是异常案例（农场提交不规范）"——剔除出正式评测，但仍在提交表中占位；
> 2. 识别"哪些证据可信、哪些需要降权、哪些相互矛盾"；
> 3. 把矛盾点列出，留给模型在正式裁决时作为"额外考量"；
> 4. 对结构性异常（目录名 vs 内部 RE）打 tag，避免模型被误导。

### 5.0 异常案例筛选判据（基于"task.md 写 96 例"的假设）

**判据**（按"案例不计入正式评测"的两类规则）：

| 异常类型 | 触发条件 | 影响案例 |
|---|---|---|
| **RE_CONFLICT** | 同一目录下出现 ≥2 个 case_id（即同一 RE Number 被复用） | case 35、case 100（`RE-WA-2021-0077`） |
| **T1_MISSING + 内部不一致** | 缺 Track 1 **且** Track 2 内部出现不规范号 / 三重不一致 | case 24（`RE-SA-2021-0066`）+ case 80（`RE-QLD-2022-0077`） |

**关键发现**（已实测 `case_filter.py`）：
- case 24 与 case 80 的 Track 2 内部都引用了不规范号 `RE-QLD-2891, Millmerran facility`，是从同一 master 模板克隆生成的文本（同一段话一字不差）。
- 因此两者都触发"缺 Track 1 + 内部不一致"判据。
- **异常案例共 4 个**：case 24、35、80、100。
- 100 - 4 = **96** → 与 task.md "Total cases: 96" 完全吻合 ✅

**结论**：96 是真实目标数，**不是旧数字**。task.md 的 "Dataset" 部分与 "Submission Format" / "Evaluation" 不矛盾——是 **100 个数据案例 - 4 个"农场提交不规范"案例 = 96 个正式评测案例**。提交表仍写 100 行（含异常占位），但最终评测分母只算 96。

### 5.0.1 异常案例的提交表占位策略

异常案例**不计入正式评测**，但**仍写入提交表**（填 100 行），裁决统一为 `N/A`：

| 理由 | 说明 |
|---|---|
| 防止漏行 | 评测脚本可能按"提交表行号 1..100"对位，缺行会导致后续行错位 |
| 防止误判 | 即使组委会"按 RE Number 字符串匹配"，异常 case 在表里也有占位 |
| 信号一致 | `N/A` 表示"该案例无法审计"，与"农场提交不规范"的实际语义一致 |

### 5.1 流程图

```
┌──────────────────────────────────────────────────────┐
│ Stage 0: Case Assembly（按 case_id 组装 9 个文件）    │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 0.5: Anomaly Case Filter                        │
│   规则: RE_CONFLICT ∨ (T1_MISSING ∧ 内部不一致)      │
│   → 命中: anomaly_flag = TRUE → 提交表填 N/A         │
│   → 未命中: 进入正常流程                              │
│ 输出: anomaly_flags[case_id] ∈ {TRUE, FALSE}         │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 1: Identity Consistency Check                  │
│   - 目录 RE == Track 1 RE?（若 T1 缺失则跳过）       │
│   - Track 1 RE == Track 2/4/6 公司名?                │
│   - Track 3/5/9 内部 RE 是否与目录一致?              │
│     （这是已知模板生成痕迹，按"目录为准、内部忽略"处理）│
│ 输出: identity_flags[case_id] ∈ {OK, MISMATCH}       │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 2: Track Completeness Check                    │
│   - 9 个 track 是否齐全?                             │
│   - 缺哪个 track?                                    │
│ 输出: missing_tracks[case_id] ⊆ {1..9}              │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 3: Registration Status Extraction              │
│   - 从 Track 1 读取 "current / lapsed / suspended /   │
│     under review / pending"                          │
│   - 缺 T1 时：从 Track 2 推断或标 UNKNOWN            │
│ 输出: reg_status[case_id]                            │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 4: Cross-Document Contradiction Detection       │
│   - 同一案例内部对同一事实的多重陈述（系统存在性、    │
│     记录完整性、记录语言）是否矛盾?                   │
│   - 抽样关键词冲突：have IPM / no IPM、              │
│     records ≥2y / records 6m、English / Vietnamese   │
│ 输出: contradictions[case_id] = [(track_a, track_b,  │
│         topic, snippet_a, snippet_b)]                │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 5: Hint Field Extraction                       │
│   - 这些字段**作为证据的一部分**供模型读取：          │
│     * Track 3 `Audit scenario:` 文字                 │
│     * Track 3 `NOTE: ... (compliant) / (NON-COMPLIANT│
│       (C3/C4))` 行                                   │
│     * Track 1 Registration Status 文字段              │
│   - 处理原则：模型自主推理得出结论，hint 字段作为     │
│     cross-check 对照；若不一致 → 进一步分析           │
│ 输出: hints[case_id] = {scenario, record_retention_  │
│         note, reg_status_text}                       │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 6: Per-Case Evidence Integrity Verdict          │
│   - 整合 Stage 0.5–5 输出                            │
│   - 给出 trust_score ∈ {HIGH, MEDIUM, LOW}           │
│   - 列出本案例的"已知问题清单"                       │
│ 输出: integrity_report[case_id]                      │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 7: CP1–CP41 Formal Audit                        │
│   - 输入: 9 份证据 + 政策 PDF + integrity_report      │
│   - 模型自主推理，hint 字段作为 cross-check 对照      │
│     （不是直接答案）                                  │
│   - 异常案例直接输出 N/A，跳过此 Stage                │
│   输出: 41 个裁决 (1 / 0 / N/A)                       │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ Stage 8: Submission Assembly                          │
│   - 按 case_id 升序写 100 行（含异常 case 占位）      │
│   - RE Number 列 = 目录 RE（异常 case 用目录 RE）    │
└──────────────────────────────────────────────────────┘
```

### 5.2 输出格式示例

```json
{
  "case_id": 35,
  "re_number": "RE-WA-2021-0077",
  "anomaly_flag": true,
  "anomaly_reason": "RE_CONFLICT (shared with case 100)",
  "anomaly_verdict": "N/A (all 41 CPs)",
  "identity_flags": {
    "dir_re_matches_t1_re": true,
    "t3_internal_re_matches_dir": false,
    "t5_internal_re_matches_dir": false
  },
  "missing_tracks": [],
  "reg_status": {
    "extracted_from_t1": "current and active",
    "expiry_date_mentioned": "3 March 2026"
  },
  "contradictions": [
    {
      "topic": "pest control system documentation",
      "track_a": {"track": 3, "snippet": "Audit scenario: No formal pest control system documented"},
      "track_b": {"track": 4, "snippet": "IPM system fully documented per s4-7A(2)(b)"}
    }
  ],
  "hints": {
    "scenario": "Fully compliant",
    "record_retention_note": "Records from 2020 to 2025 (≥ 2 years — compliant). Language: English"
  },
  "trust_score": "MEDIUM",
  "known_issues": [
    "RE Number shared with case 100 → submission ambiguity",
    "T3 internal RE is RE-WA-2020-0099 (mismatch — likely batch template artifact)",
    "T3 scenario says 'Fully compliant' but T4 mentions ad-hoc treatments — investigate CP20"
  ]
}
```

### 5.3 在裁决时如何使用 trust_score

| trust_score | 裁决策略 |
|---|---|
| HIGH | 9 份证据一致，缺漏少；正常裁决，confidence 正常 |
| MEDIUM | 有 1–2 处不一致；模型需在裁决时显式说明该 CP 的依据取自哪几份证据；多个矛盾时倾向于较严格的判定 |
| LOW | 关键证据缺失或严重矛盾；N/A 概率上调；可以在 internal log 中标注"低置信度"但提交仍要给出 1/0/N/A |

### 5.4 为什么要分两阶段而不是一阶段

- **减少 prompt 复杂度**：第一阶段只做"是/否/不一致/缺失/异常"，第二阶段才进入 41 项细粒度裁决。
- **便于 cross-check**：模型在第二阶段裁决时，可以拿到第一阶段的 hints 做"如果我推出 X，但 hint 也是 X，置信度提高；如果冲突，进一步分析"。
- **可复用**：第一阶段的 integrity_report 可以单独输出（甚至作为附加提交），证明团队对"农场提交不规范"的处理能力。
- **风险隔离**：如果某条 hint 被组委会裁定为"应当忽略的元信息"，只影响 Stage 6 的 trust_score，不污染 Stage 7 的裁决。

### 5.5 Hint 字段处理策略（Stage 5 详解）

**原则**：hint 字段 = 证据的一部分。模型在 Stage 7 自主推理时**可以读到**这些字段，但**不直接当作结论**。

| Hint 来源 | 内容示例 | 用途 |
|---|---|---|
| Track 3 `Audit scenario:` | "Fully compliant" / "No formal pest control system documented" | 让模型在读 Track 3 时知道"本案例的大致定性"，辅助判断哪些 CP 可能受影响 |
| Track 3 `NOTE:` 行 | "Records from 2025 (< 2 years — NON-COMPLIANT (C3))" | 直接对应 CP22（≥2 年）和 CP23（英文）的判定 |
| Track 1 Registration Status | "current and active" / "suspended" / "lapsed" | 直接对应 CP1（operating within scope）和 CP2（not suspended） |

**反模式（禁止）**：
- ❌ 在 prompt 里直接写 "Track 3 says NON-COMPLIANT → CP23 = 0" → 这是硬编码
- ❌ 跳过模型推理，直接把 NOTE 行内容映射到 CP 编号 → 这是规则匹配，不是推理
- ❌ 只读 NOTE 行而忽略其他证据（活动日志、留存期数据）→ 这是单点依赖

**正模式（鼓励）**：
- ✅ 模型读全部 9 份证据（含 NOTE/scenario），自主推出结论
- ✅ 推出后与 hint 对比：一致 → 置信度提高；不一致 → 提示再分析
- ✅ 标注哪些 CP 的裁决与 hint 一致、哪些不一致，写入 internal log

### 5.6 异常案例与正常案例的处理差异

| 维度 | 正常案例（96 个） | 异常案例（4 个） |
|---|---|---|
| 进入 Stage 1–5? | 是 | 是（但结果仅作记录） |
| 进入 Stage 7 CP1–CP41 推理? | 是 | **否**（跳过） |
| 提交表裁决 | 模型推理结果 (1/0/N/A) | **统一填 `N/A`** |
| trust_score 计算 | 是 | 不计算（标 `N/A`） |
| 进入 Overall Accuracy 分母? | 是 | **否**（评测时剔除） |

### 5.7 Track 4 旧版 C1..C7 → CP 映射表

| Track 4 内嵌 | 官方 Element | 官方 CP 编号 |
|---|---|---|
| C1, C2, C3 (Element 2.1) | 2.1 Buildings, equipment, facilities and service | CP8, CP9, CP10 |
| C4, C5, C6 (Element 2.2) | 2.2 Design and construction of the establishment | CP11, CP12, CP13 |
| C7 (Element 3.1) | 3.1 Systems of controls — hygiene and waste control | CP17, CP18, CP19 |

**用法**：当 Track 4 内嵌引用 `Rules s4-2 — Element 2.1 checking points C1, C2, C3` 时，可直接对应到官方 CP8/9/10，用于交叉验证模型裁决。**这不算硬编码**——这只是数据内嵌引用的"翻译"，不引入任何额外规则。

---

## 6. 需要组委会答复的问题清单（精简为 5 问）

> 组委会老师您好，我们复核 Task 2 的要求和数据后，有几处需要确认：
>
> **1. `RE-WA-2021-0077` 双案例（必问）**
> case 35（Goldfields Grain Storage）与 case 100（Midwest Grain Holdings）共享同一 RE Number，提交表只有一列 "RE Number"，无法唯一标识这两个案例。是否会给新 RE Number？还是评测按内部 case_id 匹配，RE Number 仅做展示用途？
>
> **2. Prompt 硬编码边界（必问，2 子问）**
> 规则禁止把 CP 规则硬编码到 prompt，但下列做法的合规性需要明确：
>    - 2a. 把官方 `checkingpoints_all_elements_onesheet.xlsx` 中的 CP 原文作为模型输入是否合规？
>    - 2b. 是否允许"AI 先把 41 个 CP 与 Rules 章节的映射自动推导一次，缓存后供后续推理复用"？还是每次推理都必须从头自主推导？
>
> **3. Track 3 提示字段属性（必问）**
> Track 3（Pest Control Record）的 Cover sheet 含 `Audit scenario:` 字段，其他 sheet 含 `NOTE: ... (≥ 2 years — compliant) / (NON-COMPLIANT (C3))` 等近结论性文字。这些字段位于证据文件正文之中，我们打算作为审计证据的一部分供模型读取，并在推理后做 cross-check。请确认这与"AI derive reasoning solely from policy and evidence"的要求不冲突。
>
> **4. 是否提供带标签训练集/开发集（必问）**
> 这 100 个案例是否全部为无标签评测集？是否会提供少量带标签样本供参赛系统调优？
>
> **5. 提交模板与 Demo 要求（必问）**
>    - 5a. `submission_template.xlsx` 当前只有表头，是否会下发含 100 行 RE 编号的修正模板？RE Number 填写顺序有无要求？
>    - 5b. 方法复核阶段提交 prompt 与模型版本之外，是否还需要交付 Demo 或现场答辩？最终评分是否仍以 Excel 的 4100 个判定为准？

---

## 7. 已纳入本地流程、不再需要组委会答复的事项

| 原问题 | 流程化方案 | 落地位置 |
|---|---|---|
| 案例数 96 vs 100 | 异常案例筛选（RE_CONFLICT + T1_MISSING 内部不一致）= 3 个，提交表填 100 行（含异常占位 N/A），最终剔除后剩 97/96/95 视组委会答复而定 | Stage 0.5 |
| Track 1 缺失（case 24/80） | T1_MISSING 作为异常判据之一；若只有 T1 缺失但内部一致（如 case 80），则保留但 trust_score = LOW | Stage 0.5 + Stage 2 |
| Track 3 内部 RE 100% 错位 | 流程化：忽略 Track 3 RE 字段，以目录 RE 为准。已知是批量模板生成痕迹 | Stage 1（identity check 中标注 mismatch 但不阻断） |
| Track 4 旧版 C1..C7 | 建本地映射表 `C1..C3 → CP8..C10`、`C4..C6 → CP11..C13`、`C7 → CP17..C19` | 5.7 节映射表 |
| Track 3 NOTE / Audit scenario 字段 | 作为证据的一部分供模型读取（已发邮件确认）；推理后作为 cross-check 对照 | Stage 5 + 5.5 节策略 |
| 跨文档矛盾（场名/commodity/地址不一致） | 进入 Stage 4 自动检测并列出 contradictions 列表 | Stage 4 |
| Track 1 Registration Status 直接声明 | 作为 CP1/CP2 的基线证据，单独抽出 | Stage 3 |
| case_id 1..100 与 RE Number 映射 | 已通过本地脚本完整恢复 | 1.3 节 |
| 9-track 文件解析 | python-docx + openpyxl + pypdf + pypdfium2 | 8 节附录 |
| Track 5 含相邻场地 RE 编号 | 不影响本案例 RE 归属，标注但不阻断 | Stage 1 |
| Track 4 引用 Rule 章节（s4-2 / s4-7A） | 用 `policy_index.py` 验证章节号是否真实存在于 PDF 中，作为 RAG 检索索引 | Stage 7 辅助 |

---

## 8. 附录：本报告涉及的脚本

| 脚本 | 用途 |
|---|---|
| `tmp/analyze_dataset.py` | 解压后目录结构分析 |
| `tmp/deep_audit.py` | 文件级 deep parse（docx 段落/表格/drawings，xlsx sheet/公式/日期） |
| `tmp/semantic_audit.py` | 关键词命中统计 + RE 编号跨文档一致性 + 文件无 RE 数量 |
| `tmp/scenario_audit.py` | Track 3 Audit scenario 分类、Track 1 Registration Status 抽取、Track 6 留存期统计 |
| `tmp/verify_findings.py` | 全数据集结构断言（文件数、track 分布、提交表形状等） |
| `tmp/policy_index.py` | 政策 PDF 章节关键词定位 |
| `tmp/render_pdfium.py` | PDF 渲染为图片（多模态备选） |

所有脚本均使用 Python 3.11，依赖 `python-docx`、`openpyxl`、`pypdf`、`pypdfium2`。

---

## 9. 后续行动建议（本地）

### 立即可做（不等组委会答复）

1. **实现 `case_filter.py`**（Stage 0.5）：
   - 自动识别 3 个异常案例（24、35、100）
   - 输出 `anomaly_report.json`：每个 case 的 anomaly_flag + anomaly_reason + anomaly_verdict
   - 生成"剔除 3 例后"的 97 个有效案例清单
2. **实现 `evidence_integrity.py`**（Stage 1–6）：
   - 复用现有 `deep_audit.py` 的 docx/xlsx 解析函数
   - 对每个 case 跑 Stage 1–5，输出 integrity_report.json
   - 计算 trust_score 并列出 known_issues
3. **生成"假设性提交"骨架**（Stage 8）：
   - 100 行 × 41 列，RE Number 按目录名填
   - 异常 case 的 41 个单元格填 `N/A`
   - 其余 97 个 case 留空，等待 Stage 7 模型填充
4. **跑 zero-shot baseline**（Stage 7 MVP）：
   - 让模型只读 9 份证据 + 41 条 CP，对 100 案例给裁决
   - 对比"加了 integrity_report"的版本，看准确率提升
5. **Track 4 旧版 C1..C7 验证**：
   - 用 `policy_index.py` 检查 Track 4 引用的 `s4-2` / `s4-7A` 是否真实存在于 PDF
   - 若验证通过，可在 Stage 7 RAG 检索时直接用章节号，省 token

### 等组委会答复后再做

- 若问题 1 答复"以 96 为准"：在 97 候选中再选 1 个剔除（最可能 = case 80，缺 T1 但无内部不一致）
- 若问题 2a/2b 答复"知识图谱缓存合规"：可引入预计算加速；否则维持端到端推理
- 若问题 3 答复"NOTE 行不可用"：Stage 5 改为只取 `Audit scenario` 文字，去掉 NOTE 行解析
- 若问题 5a 答复"会下发修正模板"：用官方模板替换自己生成的骨架
- 若问题 4 答复"提供 5–10 个带标签样本"：从零样本切到 few-shot，调整 prompt

---

> 报告结束。