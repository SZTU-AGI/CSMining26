# 本周总结(2026-07-28 ~ 2026-07-29):从检索/Agent 链转向直发 LLM 实验架构

> 范围:Freca Task 2(100 cases × 41 CPs = 4,100 审计决策)。本周的核心动作是
> **把主路径从检索 + 多 Agent 链(planner / retrieval / critic / verifier / 仲裁)
> 换成"把官方材料直接喂给 LLM"的实验架构**——一个明确反"过度工程"的转向。

---

## 1. 起点:之前的 Agent 链

提交前仓库里已经有一套重型流水线:

- **解析**:`docx` / `xlsx` / `pdf` 三路解析,产出 `EvidenceChunk`(kind 涵盖 paragraph /
  heading / table / image / image_description)。
- **双索引**:BM25 + 向量,RRF 融合。
- **检索层**:planner(查询改写)→ retrieval → 重排 → MMR 选择,带 source-aware
  同源惩罚。
- **审计层**:基于系统提示 `_AUDIT_SYSTEM` 让模型判断,要求给出 verdict 数组 + 引用
  chunk/image id。
- **引用校验**:要求每个 verdict 至少引用一个有效 chunk_id。
- **Verifier + 仲裁**:分级置信度阈值、不一致时触发仲裁。
- **提交门禁**:4,100 行 verdict 一致性检查。

辅助组件还包括 `signature_truth`(`文件署名整理表_v2(1).xlsx`,人工署名污染表),
旧策略"96 个 case 整案填 N/A,4 个异常 case 弃审"已正式废弃(SOLUTION.md)。

## 2. 转向的触发与论据

用户的判断:**"比赛里面不说是有些东西是不能发给 LLM 的吗,那我就把哪些东西发过去,
那这样他审核出来的正确率应该是比较高的。"**

把它工程化后得到几个结论:

- 比赛规则明文禁止把"CP 要求什么""某字段出现就判 0"这类**人工 CP→规则映射**写进
  提示词。检索/Agent 链天然容易渗进这类隐式规则。
- 法规 PDF + 当前 case 的 9 份证据是合法、完整、可直接送达的材料。
- 所以**全量官方材料直发**必须是第一条基线;Agent、RRF、Reranker、多模型仲裁等复杂
  组件不再进入主路径,只作为对照实验的脚手架。
- 真实数据测算单 case 全量材料约 5–6 万字符,法规约 25.6 万字符;不能机械地为每个 CP
  重复塞全量(4,100 次超长调用),因此需要 4 种调用粒度来回答"切小输出单元"和"自动
  检索"到底有没有收益。

由此定下 4 种实验方法(M0–M3):

| 方法 | 单元数 / case | 区别 |
|---|---|---|
| `case_full` | 1 | 全量材料 + 41 CP 一次出 |
| `element_full` | 4 | 按 Element 分组,材料不裁 |
| `checkpoint_full` | 41 | 单 CP 一次,材料不裁 |
| `automatic_retrieval` | 1 | 用 CP 原文做通用 BM25,从政策/case 各选 12 段 |

## 3. 边界与纪律(写进设计文档与代码注释)

| 可发 | 不可发 |
|---|---|
| CP 原文(题目) | 人工 CP→规则映射 |
| 法规原文 | 旧版 C1→CP 人工对应 |
| 当前 case 的 9 份证据 | 异常案例统一 N/A 的旧策略 |
| 表格、图像、原文里的 Audit scenario / NON-COMPLIANT 字段 | 外部人工整理出的署名污染真值表 `signature_truth` |
|  | 任何 gold label / 答案表 |

银标(`compare_to_reference` 的输入)由独立 LLM 用**同一份官方材料**一次性生成、
版本冻结;**只用于相对比较,不能称为真实准确率**。

## 4. 已交付的代码

分支:`feature/direct-llm-experiments`(13 个 commit,已推 origin)。

### 4.1 规划层 — `src/freca/experiments/planning.py`

- `build_execution_plan(method, case_id, checkpoints)` 把 41 个 CP 按方法切成
  `ExecutionUnit`,确定性、可复现。
- 新增 `select_cases(case_ids, limit=, only=)` —— 给高成本方法(`checkpoint_full`
  41× 单 case)做抽样/白名单控制。

### 4.2 材料层 — `src/freca/experiments/materials.py`

- `MaterialSnapshot`:case_id / checkpoints / chunks / image_paths / track3_condition
  / input_sha256。
- `build_material_snapshot`:做跨 case 校验(政策 chunk 不能带 case_id,case chunk 必须
  归属当前 case),产 SHA-256。
- `load_material_snapshot_from_parsed`:从 `build/parsed/` 读政策 + 当前 case 的 9 份
  JSON + 提取出来的原图路径。
- `select_automatic_retrieval_material`:通用 BM25 + 词重合度,**不含 CP 专属规则或人
  工源映射**。
- **`mask_audit_scenario(content)`**:今天新加的 `Track3Condition.MASKED` 变换——
  正则 `(Audit scenario:\s*).*?(?=\s*\|\s*[A-Z]+\d+=|$)` 把 Track 3 封面格 A14 的
  近答案叙述替换为 `[REDACTED]`,保留 `Audit scenario:` 标签与 `| B14=<BLANK>`
  单元格结构。raw vs masked 各自得到不同的 `input_sha256`,可以直接对照。

### 4.3 模型层 — `src/freca/experiments/models.py`

`ExperimentMethod`、`Track3Condition`、`ExecutionUnit`/`ExecutionPlan` /
`MaterialSnapshot` / `PromptEnvelope` / `ExecutionResult` / `SilverComparison`
全部用 Pydantic `StrictModel` 强约束,`cp_id` 走 `^CP(1–41)$` 正则,`input_sha256`
走 64-hex 校验。

### 4.4 提示与执行 — `prompts.py` / `runner.py` / `llm.py`

- `SYSTEM_PROMPT` 显式声明"不要从答案化措辞推断 CP 规则""保留矛盾""只引用输入中的
  chunk/image id"。
- `build_prompt`:把 checkpoints + chunks + image_paths 序列化为 JSON 载荷(可控大小、
  键名稳定),产出 `input_sha256` + `prompt_sha256`。
- `validate_response`:校验 verdict 数组、引用 id 属于当前快照、verdict 域 ∈ {1,0,N/A}、
  至少 1 个引用、有 reason。
- `OpenAICompatibleJsonClient.complete_json_with_images`:base64 内联官方原图,
  同一结构化请求里支持图文混排;`ReplayJsonClient` 提供测试用的回放客户端,
  仓库测试不发真实请求。

### 4.5 评估 — `evaluation.py`

`compare_to_reference(candidate, reference)` 只报告 `silver_agreement`,**显式不命名
为 accuracy**,绝不与官方 ground truth 混淆。

### 4.6 CLI — `cli.py`

`freca experiment plan | materialize | cases | run`,目前：

- `plan` / `materialize` / `cases`:完全 provider-free,只产制品到
  `build/experiments/{method}/case-NNN/` 或 `build/experiments/plans/`。
- `run`:当前是**死桩** —— 即便加 `--allow-live-model` 仍返回
  `BLOCKED: materialized experiment execution is not configured`。这是已知缺口,
  是从脚手架到能出 submission 的最后一公里。

## 5. 今天(2026-07-29)的三件事

| 项 | 处置 |
|---|---|
| Track 3 Audit-scenario masking | 已实现,真数据 4/4 干净脱敏 |
| checkpoint_full 范围 / 抽样 | 已实现,`select_cases` + `experiment cases --limit N` |
| 图像提取 | **事实更正,不造轮子**:实测 898 个证据文件**0 张图**(docx 0、xlsx 0、独立图 0);Farm Site Plan / Bait Station Map 是纯文本 + 表格。已更正 README/SOLUTION/设计文档里"附原始图像"的说法,多模态链路保留为已测试但对本数据集空转的脚手架 |

## 6. 对抗性审查(独立 + 两个 agent)的关键发现

按严重度:

1. **Track 3 "Audit scenario" 近答案叙述**(已修)。100/100 case 在 Track 3 封面格
   `A14` 用白话写明合规姿态,如 `Fully compliant`、`Active insect infestation ... not pest-free at audit`、`New establishment registered Oct 2024 ... <2 years of records`。原样直发会让模型"读一句话直接抄答案",且银标若在 raw 下生成,`silver_agreement` 会同时被这层污染稀释。今早已加 raw/masked 维度。
2. **数据无图像**(已修)。898 文件 0 张图,见上。
3. **prompt 规模**:case 1 实测 `case_full` 一次约 **57.9 万字符 ≈ 16.5 万 token**;
   `checkpoint_full` 把同一份材料重复 41 次/case,全量约 **6.7 亿 token**,
   不可行,需要抽样控制(已加)。
4. **`experiment run` 是死桩**(未修,见 §7)。
5. **银标参考根本没生成**(未修,见 §7)。
6. **`compare_to_reference` 不能验证"推理内容"**,只校验 verdict 域与引用存在;
   模型可以回吐一个不真支持结论的 reason 但引用了真实 chunk_id。
7. **检索 `select_automatic_retrieval_material` 忽略 image_paths 裁剪**,检索选的
   是 chunk 不是图(本数据集无图,实际无影响)。
8. **`VERDICT_SCHEMA`** 与 `ExperimentVerdict` 字段不完全对齐,模型的 schema 错误会
   滑过结构校验。
9. **`ExecutionResult` 不带 `image_paths`**,事后无法区分 text-only vs multimodal
   跑。
10. **代码风格**:引用 `int(checkpoint.cp_id[2:])` 假设 CP 前缀固定;空 verdict 列表
    走错误分支略绕。CLAUDE.md 的"最小代码"原则下,部分 `_invalid_result` 错误消息
    截断了 pydantic 全部错误,定位不友好。

## 7. 已知缺口(下一步)

- **真实运行编排器**:`experiment run` 死桩。`run_execution` 已有 replay-tested 实现,
  但 CLI 没接上;没有"按方法 × scope × track3 条件跑遍 case、把 verdict 写入
  submission.xlsx"的编排器。这是**从脚手架到能交差的关键缺口**。
- **冻结银标生成**:`compare_to_reference` 只是对比函数,生成银标的那一步不存在。
  银标必须与候选在相同 `track3_condition` 下生成,否则一致率测的是"双方都读了泄漏"。
- **成本 / 延迟 / token / 失败率度量**:`ExecutionResult` 现在不带这些字段,
  设计文档列了未实现。
- **最终参赛模型未定**:用户提过"能识图"但实测数据无图;现在候选是 text-only 的
  多模态模型(因为接口支持图文,即便本批没图)。

## 8. 仓库卫生

- 私有仓库 `passionworkeer/freca-task2-audit`。
- 无 `.env`、无凭据被 git track;`config.yaml` 含 API base_url 占位符与 `api_key_env`
  名(env 变量名,非值)。
- 原始比赛数据(`extracted/`、`SFRE_cases.zip`、`*.pdf`、`*.xlsx` 部分)已 gitignore。
- 提交风格:`feat:` / `docs:` / `test:` 前缀,中文正文,conventional-commits 习惯。
- 测试:`149 passed / 5 skipped`(5 个跳过的是依赖本地 33MB 原始资料的集成测试,
  工作树里没有,合理 skip)。

## 9. 一句话状态

主路径已从"agent 链"换成"直发 LLM + 4 种方法对照"的实验脚手架;Track 3 泄漏
与文档失真已修;**还差真实运行编排器 + 银标生成才能交差**,这两件没动,等指令。