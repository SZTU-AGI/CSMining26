# 这周做了什么(给自己看的回顾)

7/28 - 7/29,Freca Task 2 这边基本就做了一件事:**把主路径从 agent/检索链换成"直发 LLM"**。

## 起点

之前仓库里已经搭了一套挺重的流水线:docx/xlsx/pdf 三路解析,BM25 + 向量双索引 + RRF,
再过一遍 planner / retrieval / critic,最后走 verifier + 仲裁,提交前还有 4100 行 verdict
一致性门禁。`signature_truth` 那张人工署名污染表也在用。整套架构组件多,每多一个
组件就多一个能塞进隐式 CP 规则的口子。

比赛规则明文禁止把"CP 要求什么"这种硬编码写进提示词,Agent 链天然容易触线。我当时
跟用户聊下来的判断是:**该发给 LLM 的就发给 LLM**——法规原文、CP 原文、当前 case
的 9 份证据,这些合法、完整、可以直接送达,根本不需要中间再加一层检索/agent 编排。
所以转向的本质是反过度工程,把主路径简化到"喂材料、收 verdict"。

## 这两天搭起来的东西

新分支 `feature/direct-llm-experiments`,13 个 commit,核心是一组 `freca experiment`
子命令,目前已经能跑的是 `plan` / `materialize` / `cases`,`run` 还没接上。

规划层做了一件事:把 41 个 CP 按方法切成 `ExecutionUnit`。`case_full` 一个 case 一个调用,
`element_full` 4 个,`checkpoint_full` 41 个,`automatic_retrieval` 用 CP 原文做通用 BM25
从政策 / 当前 case 各选 12 段——不带任何 CP 专属规则或人工源映射。这四种方法是为了回答
"切小输出单元到底有没有收益""自动检索净收益是不是正"这两个问题,不是预设答案。

材料层做了一件关键的事:`MaterialSnapshot` 把政策 + 当前 case 的 chunks + 原图路径 +
CP 一起打包,产 SHA-256,跨 case 校验(政策 chunk 不能带 case_id,case chunk 必须归当前 case)。
多模态链路也写了——`complete_json_with_images` 会把官方原图 base64 内联进同一结构化请求。
代码写完测试都过了,但**实测 898 个证据文件里 0 张图**(docx 内嵌媒体 0、xlsx 图形 0、
独立图片 0),Farm Site Plan 和 Bait Station Map 都是纯文本 + 表格。所以多模态链路是
已测试但对本数据集空转的脚手架,我之前跟用户说的"能识图"在 API 层成立、在数据层不成立,
已经在 README / SOLUTION / 设计文档里把这点更正了。

提示与执行层:`SYSTEM_PROMPT` 显式说"不要从答案化措辞推断 CP 规则""保留矛盾"
"只引用输入中的 chunk/image id",然后把 checkpoints + chunks + image_paths 序列化成
JSON 载荷(可控大小、键名稳定),产出 input_sha256 和 prompt_sha256。验证层只校验 verdict
域 ∈ {1, 0, N/A}、引用 id 属于当前快照、verdict 数组完整、reason 非空——**不会用程序
规则覆盖模型 verdict**,也不假装能验证推理内容。

评估层就一个函数 `compare_to_reference`,算 silver_agreement,字段命名刻意避开
"accuracy",明文说这是"和冻结银标的一致率,不是官方准确率"。

## 今天修 / 更正的几件事

写完第一版分支推上去之后我回头查了真实数据,发现两件原来没说清楚的事:

**Track 3 "Audit scenario" 是近答案叙述**。100/100 个 case 的 Track 3 封面格 `A14`
用白话写明合规姿态:case 1 是 `Fully compliant - comprehensive grain storage facility...`,
case 5 是 `Active insect infestation ... not pest-free at audit`,case 50 是
`New establishment registered Oct 2024 ... <2 years of records`。这相当于模型看一句话
直接抄答案。原版代码原样发,等于在利用泄漏而不自知。今早加了 `track3_condition` 维度:
`masked` 把叙述替换为 `[REDACTED]`,保留 `Audit scenario:` 标签和 `| B14=<BLANK>` 单元格
结构;`raw` 保留原貌。两种条件 input_sha256 不同,可以同方法各跑一次对照,真正量化泄漏
影响。实测 case 1/5/50/100 四个真实样本全部干净脱敏。

**checkpoint_full 全量跑不动**。case 1 的 case_full 实测 57.9 万字符 ≈ 16.5 万 token,
checkpoint_full 把同一份材料重复 41 次/case,全量约 6.7 亿 token,成本上不可行。
加了 `select_cases(limit=, only=)` 和 `experiment cases --method X --limit N`,
checkpoint_full 可以限定抽样,case_full 仍全量。

**图像:数据里真没有**(上面已经说过)。我没去造一个提取出 0 张图的轮子,改文档即可。

## 还差什么

两件没动,等指令:

- **`experiment run` 是死桩**。即便加 `--allow-live-model` 仍然返回
  `BLOCKED: materialized experiment execution is not configured`。`run_execution`
  本身能用(replay client 测过),但 CLI 没接上,所以现在还没有"按方法 × scope ×
  track3 条件跑遍 case、把 verdict 写入 submission.xlsx"的编排器。这是**从脚手架到
  能交差的最后一公里**。
- **冻结银标参考根本没生成**。`compare_to_reference` 只是把两个结果对比,但生成银标
  的那一步不存在。银标必须与候选在相同的 track3_condition 下生成,否则一致率度量的是
  "双方都读了泄漏"而不是"双方都对"。

成本 / 延迟 / token / 失败率度量现在也没做,设计文档里列了但代码没加。

## 仓库状态

- 私有仓库 `passionworkeer/freca-task2-audit`,分支 `feature/direct-llm-experiments`,
  最新 commit `790f5cf`,已推到 origin。
- `main` 上一个 chore commit(把 `.worktrees/` 和 brainstorm 文件加进 `.gitignore`),
  也已推。
- 没有 `.env` 或凭据被 git track;原始比赛数据(`extracted/`、`*.pdf`、压缩包)
  已 gitignore;`config.yaml` 里只有 API base_url 占位符和 `api_key_env` 名,不是值。
- 测试:149 passed / 5 skipped。5 个 skip 是依赖本地 33MB 原始资料的集成测试,worktree
  里没数据,合理 skip。
- 提交风格延续之前的 `feat:` / `docs:` / `test:` 前缀,中文正文。

## 一句话

主路径已经换到直发 LLM 实验架构,Track 3 泄漏和数据无图像的失真都改了,**离交差还差
一个真实运行编排器和一个银标生成器**。