# FRECA 多方法直接 LLM 审计实验设计

## 目标

以官方 CP、法规和当前农场案例原始证据为唯一模型输入来源，建立可复现的多方法实验框架。框架比较不同上下文组织方式，而不是把复杂检索、Agent 或人工规则固化进正式审计路径。

## 不可违反的边界

- CP 原文从官方检查点 Excel 原样读取；不得在提示词中改写或补充 CP 合规规则。
- 模型只看官方政策、当前 case 的官方九份证据及其原始图像；不得输入外部人工标签、`signature_truth`、旧版 C1 到 CP 映射、异常案例填值策略或字段到裁决的映射。
- `case_id` 是唯一案例键。文件归属、文件 SHA-256、页码/工作表/单元格和图像路径必须被记录，但这些元数据不得产生裁决。
- 每次模型调用保存模型标识、提示词、输入快照哈希、响应、时间和方法配置。凭据只读自环境变量且绝不落盘。
- LLM 生成的 silver 参考只能衡量候选方法的相对一致性，不能表述为真实准确率或官方成绩。

## 共享材料底座

`freca.experiments.materials` 从已有的 manifest、法规解析、检查点和案例解析产物建立 `MaterialSnapshot`：官方法规来源片段及页码、当前 case 的原始文本/表格/来源定位、DOCX 保留的原始图像、CP 原文和全部输入文件 SHA-256。

材料底座只验证输入是否来自官方文件、是否属于当前 case、是否可读取；不会过滤冲突证据，也不会添加合规结论。

## 实验方法

| 方法 | 调用单元 | 上下文 | 用途 |
|---|---|---|---|
| `case_full` | 1 case / 1 调用 | 全部 CP、法规、九份证据和图像 | 正式首基线 |
| `element_full` | 1 case / 4 调用 | 当前 Element 的 CP；其余材料不裁剪 | 评估输出批大小 |
| `checkpoint_full` | 1 case / 41 调用 | 一个 CP；其余材料不裁剪 | 小样本上限对照 |
| `automatic_retrieval` | 由 CP 原文生成的调用单元 | 通用 BM25 自动选择法规与当前 case 文本 | 检验检索的净收益 |

图像是横切开关：`text_only` 不附图，`multimodal` 附官方原始图片。两种条件共享相同提示词、CP 和文本材料。

> **数据现状(2026-07-28 实测):** FRECA 100 个 case 的 898 个证据文件中**没有任何图像**(0 个 docx 内嵌媒体、0 个 xlsx 图形、无独立图片文件);"Farm Site Plan"和"Bait Station Map"也是纯文本+表格。因此 `multimodal` 开关对本数据集为空转,`text_only` vs `multimodal` 对照不适用。多模态发送链路已实现并测试,但 `image_paths` 实际为空。

> **Track 3 近答案字段:** 每个 case 的 Track 3 封面格 `A14=Audit scenario: <叙述>` 用白话写明合规姿态(如"Fully compliant"、"Active insect infestation... not pest-free")。`materialize --track3 raw|masked` 提供两种条件:`masked` 仅把该叙述替换为 `[REDACTED]`,保留 `Audit scenario:` 标签与单元格结构。同一方法在 raw 与 masked 下各跑一次即可量化泄漏影响,而不是静默利用。silver 参考也须在相同条件下生成,否则一致率度量的是"双方都读了泄漏"。

## 模型请求和输出

提示词固定声明审计角色、允许值 `1/0/N/A`、需要根据给定官方材料独立推理、不得把矛盾证据静默修正。它不包含 CP 专属规则或人工证据映射。

每个输出单元为 JSON，含 `case_id`、方法、CP 判决数组、每项 verdict、简短理由、引用的原始 chunk/image id 和不确定性。程序只校验 JSON 结构、判决域、引用属于当前输入快照；不会以程序规则覆盖模型 verdict。

## 评价与选择

冻结 `reference` 方法、模型和提示词后，生成单独的 LLM silver 参考。候选方法只与该冻结参考比较，输出 verdict agreement（总计、按 CP、按 Element）、格式有效率、当前 case 引用率、可定位引用率、相同配置重跑一致性、token、调用次数、延迟、失败率及 text-only/multimodal 差异。

报告显式标注“silver agreement，不是官方准确率”。选择命令仅根据冻结报告选择胜出方法；提交仍要求 100 个 case 和 4,100 个合法 verdict。

## CLI 与制品

新增 `freca experiment` 命令组：`materialize`、`plan`、`run`、`reference`、`compare` 和 `select`。所有制品位于 `build/experiments/{experiment_id}/`，按方法和 run id 分隔。原有重型 `pipeline` 命令保留兼容，但不是新实验命令的依赖。

## 验证

每个新增模块先用自包含 fixture 写失败测试，再实现最小逻辑。测试覆盖允许输入边界、四种方法调用计划、无 CP 规则提示词、跨 case 引用拒绝、冻结 reference 比较、稳定排序和 CLI 参数。缺失原始比赛资料的 worktree 中，依赖原始资料的旧集成测试标记为 skipped；完整资料环境仍执行它们。
