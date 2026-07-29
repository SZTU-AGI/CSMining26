# 本周技术进展(2026-07-28 ~ 2026-07-29)

## 项目背景

FRECA Task 2:100 个农场合规审计 case × 41 个检查点(CP1–CP41),共 4,100 个审计决策。
每个 case 含 9 份证据轨(注册表、HACCP、虫害控制记录、农场管理计划、场地平面图、卫生
计划、诱饵站地图、植物检疫安全程序、可追溯记录)和一份法规 PDF(Export Control (Plants
and Plant Products) Rules 2021)。比赛规则:不得在 prompt 里硬编码 CP 规则,推理必须
从官方材料来;提交按 Overall / CP / Element 三层准确率评测。

## 这周做的核心改动

把主路径从**检索 + 多 Agent 编排**(原架构:三路解析 → BM25+向量双索引 RRF 融合 →
planner 改写查询 → retrieval → 重排 → MMR → audit → verifier → 分级仲裁 → 提交门禁)
换成**直发 LLM 的实验架构**。原架构组件多,每多一个组件就多一个塞进隐式 CP 规则的口子;
新架构把"喂材料、收 verdict"作为唯一主路径,复杂检索/agent 只作为对照实验的脚手架。

## 实验方法(4 种)

| 方法 | 单元数/case | 输入材料 | 用途 |
|---|---|---|---|
| `case_full` | 1 | 全量 41 CP + 政策 + 9 份证据 | 正式首基线 |
| `element_full` | 4 | 同上但按 Element 分组出 | 评估输出批大小 |
| `checkpoint_full` | 41 | 单 CP + 全量材料 | 小样本上限对照 |
| `automatic_retrieval` | 1 | 用 CP 原文做通用 BM25,政策 / case 各选 12 段 | 检验检索净收益 |

四种方法的共同前提:**只发官方材料**(CP 原文、法规、当前 case 的 9 份证据、原图);
**不引入**人工 CP→规则映射、人工署名污染表(`signature_truth`)、异常 case 统一填 N/A
的旧策略、任何 gold label。

## 已实现的关键模块

### 规划层 (`freca.experiments.planning`)
- `build_execution_plan(method, case_id, checkpoints)` 把 CP 按方法切成
  `ExecutionUnit`,确定性、可复现。
- `select_cases(case_ids, limit=, only=)` 给高成本方法(`checkpoint_full` 41× 单 case)
  做抽样/白名单控制。

### 材料层 (`freca.experiments.materials`)
- `MaterialSnapshot`:`case_id` / `checkpoints` / `chunks` / `image_paths` /
  `track3_condition` / `input_sha256`(SHA-256 锁定输入可复现)。
- `build_material_snapshot`:做跨 case 校验(政策 chunk 不带 case_id,case chunk 必须归
  当前 case),产 `input_sha256`。
- `load_material_snapshot_from_parsed`:从 `build/parsed/` 读政策 + 当前 case 的 9 份
  JSON + 提取出来的原图路径。
- `select_automatic_retrieval_material`:通用 BM25 + 词重合度,无 CP 专属规则。
- `mask_audit_scenario`:Track 3 封面格 `A14` 的 `Audit scenario:` 近答案叙述在
  `track3_condition=masked` 下被替换为 `[REDACTED]`,保留标签与单元格结构。

### 模型层 (`freca.experiments.models`)
所有 IO 类型用 Pydantic `StrictModel` 强约束:`cp_id` 走 `^CP(1–41)$` 正则,
`input_sha256` 走 64-hex 校验,`verdict` ∈ {1, 0, N/A}。

### 提示与执行 (`prompts` / `runner` / `llm`)
- `SYSTEM_PROMPT`:声明"不要从答案化措辞推断 CP 规则""保留矛盾""只引用输入中的
  chunk/image id"。
- `build_prompt`:checkpoints + chunks + image_paths 序列化为 JSON 载荷,产
  `input_sha256` 和 `prompt_sha256`。
- `OpenAICompatibleJsonClient.complete_json_with_images`:官方原图 base64 内联到同一
  结构化请求,支持图文混排。
- `validate_response`:校验 verdict 数组、引用 id 属于当前快照、verdict 域、reason 非空。
  不会用程序规则覆盖模型 verdict。

### 评估 (`evaluation`)
`compare_to_reference(candidate, reference)` 输出 `silver_agreement`,字段命名刻意避开
"accuracy",明文标注这是"和冻结银标的一致率,不是官方准确率"。冻结银标由独立 LLM
用同一份官方材料一次性生成,版本冻结;**仅用于相对比较**。

### CLI (`freca experiment`)
- `plan` / `materialize` / `cases`:provider-free,产制品到
  `build/experiments/{method}/case-NNN/` 或 `build/experiments/plans/`。
- `run`:live-model 调用入口,目前仍由 `--allow-live-model` 显式门禁;真实运行编排器
  待接。

## 本周具体改动

### 新增(本周全部 test-first)
- `Track3Condition` 枚举(`raw` / `masked`)与对应 `MaterialSnapshot` 字段,
  `build_material_snapshot` / `load_material_snapshot_from_parsed` /
  `select_automatic_retrieval_material` 全部透传该参数,`input_sha256` 随条件变化,
  raw 与 masked 可直接对照。
- `mask_audit_scenario(content)`:正则
  `(Audit scenario:\s*).*?(?=\s*\|\s*[A-Z]+\d+=|$)` 替换为
  `\1[REDACTED]`,保留标签与单元格结构。实测 case 1 / 5 / 50 / 100 真实 Track 3
  内容全部干净脱敏。
- `select_cases`:纯函数,`limit` 限定抽样,`only` 给显式子集并按全集排序校验未知 id。
- CLI `--track3 {raw,masked}` 接入 `materialize`,新增 `experiment cases --method X
  --limit N|--cases 1,5,9` 子命令。

### 修正
- 文档更正:FRECA 数据集 898 个证据文件实测**无任何图像**(docx 内嵌媒体 0、xlsx 图形 0、
  独立图片 0);Farm Site Plan 与 Bait Station Map 是纯文本 + 表格。多模态发送链路已
  实现并测试,但 `image_paths` 对本数据集实际为空,`text_only vs multimodal` 对照不适用。
  README / SOLUTION / 设计文档据此更正。

## 性能与成本

- 单 case 实测(`case_full`,case 1):57.9 万字符 ≈ 16.5 万 token(387 个 chunk,大头
  是 367 KB 政策 JSON)。
- `checkpoint_full` 把同一份材料重复 41 次/case,全量约 6.7 亿 token,不可行,需
  `select_cases` 抽样控制。
- `automatic_retrieval` 降到 ~1.3 万 token(24 chunk),材料差异 12× 是 M3 设计本意。

## 测试与仓库

- 149 passed / 5 skipped;5 个 skip 是依赖本地 33 MB 原始资料的集成测试,worktree 无
  数据时合理 skip。
- 分支 `feature/direct-llm-experiments`,13 个 commit,最新 `2f5f839`,已推 origin。
- 主分支 `main` 一个 chore commit(`.gitignore` 增 `.worktrees/`、brainstorm 文件),
  也已推。
- 无 `.env` / 凭据被 git track;原始比赛数据(`extracted/`、`*.pdf`、压缩包)已 gitignore。
- 提交风格:`feat:` / `docs:` / `test:` 前缀,中文正文,conventional-commits 习惯。

## 待办

- **真实运行编排器**:`experiment run` 当前是死桩,`run_execution` 本身可用(replay
  client 测过),但 CLI 没接上;缺"按方法 × scope × track3 条件跑遍 case、写
  submission.xlsx"的编排。
- **冻结银标生成**:`compare_to_reference` 只是对比,生成银标那一步不存在。银标必须与
  候选在相同 `track3_condition` 下生成,否则一致率测的是"双方都读了泄漏"。
- 成本 / 延迟 / token / 失败率度量未实现,设计文档列了但代码未加。