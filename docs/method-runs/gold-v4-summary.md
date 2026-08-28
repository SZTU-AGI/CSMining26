# Gold v4 双 rubric 口径对比结果

## 评测边界

- Gold：`gold/consensus-v2.json` 中 37 条 confirmed case×CP 标签（v1 的 34 条 + 8/26 表新增 3 条：065/CP24=1、065/CP26=0、074/CP26=0），并补记 RE 注册号字段。
- 模型：MiniMax-M3；Gold verdict 与人工共识理由没有进入模型提示词。
- 运行：只覆盖 case 023、035、038、065、074 的已确认 CP；没有运行 369 或 4,100 项。
- 资格门槛：coverage ≥ 90%，终态失败率 ≤ 10%。
- 消融变量：仅 Stage B rubric 来源——PDF 检索派生（`rubric-v1`，现状）vs 人工评分标准（`FRECA_41CP_评分标准_最终合并版_材料并入.xlsx`，`rubric-curated-v1`，伪 chunk `curated:CPn` 前置、全文豁免截断）。检索链路、Stage A/C/D/E 配置逐字节相同。

## 结果

| 运行 | rubric 来源 |  critic | 一致率 | 覆盖率 | 终态失败率 | 调用数 | 资格 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `ledger-na-gate-gold-v4` | PDF 派生 | 无 | **31/37 (83.8%)** | 100.0% | 0.0% | 111 | **并列冠军** |
| `ledger-curated-conflict-critic-gold-v4` | 人工评分标准 | 有 | **31/37 (83.8%)** | 100.0% | 0.0% | 108 | 并列冠军 |
| `ledger-conflict-critic-gold-v4` | PDF 派生 | 有 | 28/37 (75.7%) | 100.0% | 0.0% | 111 | 合格 |
| `ledger-curated-na-gate-gold-v4` | 人工评分标准 | 无 | 24/37 (64.9%) | 100.0% | 0.0% | 100 | 合格 |

两条冠军 run 的一致率并列，但错误集不同：共同错 3 条（023/CP12、065/CP12、065/CP24），各自独有 3 条（na-gate：065/CP15、065/CP35、074/CP12；curated-critic：023/CP35、035/CP12、074/CP14）。

`ledger-curated-conflict-critic-gold-v4` 有 1 个 CP33 rubric 降级（模型 JSON 解析失败回退），其余三条 run 零降级。

## 关键发现：rubric 来源与 critic 存在交互

- PDF 臂：加 conflict-critic 从 83.8% **降**到 75.7%（-8.1pp，9 处不一致）。
- 人工评分标准臂：加 conflict-critic 从 64.9% **升**到 83.8%（+18.9pp）。

即 critic 的效果依赖 rubric 口径：人工评分标准单独作为 rubric 主源时判得过松/过紧（最差 CP：CP12 1/5、CP35 1/4、CP26 1/2），critic 的独立复核恰好纠正了这批偏差；而 PDF 派生 rubric 下 critic 反而引入新错判。

## 新增 3 条标签（8/26 表）各 run 表现

| 任务 | Gold | na-gate | PDF+critic | curated | curated+critic |
| --- | --- | --- | --- | --- | --- |
| 065/CP24 | 1 | ✗ 0 | ✓ 1 | ✗ 0 | ✗ 0 |
| 065/CP26 | 0 | ✓ 0 | ✗ 1 | ✗ 1 | ✓ 0 |
| 074/CP26 | 0 | ✓ 0 | ✓ 0 | ✓ 0 | ✓ 0 |

只有 na-gate 与 curated+critic 在这 3 条上各对 2 条；无 run 全对。

## v3 历史对照（口径不同，仅供参考）

v3 为 34 条分母、13 个 run 的历史对比：冠军 `ledger-na-gate-gold-v2` = `ledger-conflict-critic-v3` = 27/34 (79.4%)。本次 v4 为 37 条分母的全新 run，一致率不可与 79.4% 直接换算比较；PDF 臂 na-gate 在新分母下 31/37，方向向好。

## 结论与建议

1. **正式方案维持 `ledger-na-gate`（PDF 派生 rubric）**：并列最高一致率、部件最少（无 critic）、v2 起即冠军、零降级；人工评分标准 + critic 组合虽然打平，但叠加了两个改动点且含 1 个降级 rubric，稳健性弱。
2. 人工评分标准（8/26 最终合并版）**不作为 rubric 主源**：单独使用显著劣化（-18.9pp）。它更适合作为复核/交叉验证信号或未来 reviewer 的对照材料。
3. CP12（5 条仅对 1 条）与 CP35（4 条仅对 1 条）是 na-gate 当前最集中的误差来源，与 v2 期结论一致，属跨口径顽固项，应作为下一轮提示词/证据原子性改进的靶点。

## 产物

- `build/method-comparison/gold-v4.json`（仅含 4 条 v4 run）
- `build/reports/gold-v4-method-selection.html`
- `build/evaluation/ledger-na-gate-gold-v4.json`
- `build/evaluation/ledger-conflict-critic-gold-v4.json`
- `build/evaluation/ledger-curated-na-gate-gold-v4.json`
- `build/evaluation/ledger-curated-conflict-critic-gold-v4.json`
- 抽查记录：`ledger-curated-na-gate-gold-v4/ledger/rubrics/CP26.json` 含 `curated:CP26`、snippets 全文 3,177 字（>1,800 不截断）、`rubric_version=rubric-curated-v1`；PDF 臂同名文件仍为 `rubric-v1` 且只引 `policy-rules-2021_*` chunk——消融单变量成立。
