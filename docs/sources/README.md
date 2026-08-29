# 源数据归档

本目录归档**比赛期间外部提供/产出的关键源材料**，便于团队完整溯源。这些文件在流水线运行时**不直接被读取**——正天的评分标准已通过 `src/freca/ledger/criteria.py` 解析后落地为 `curated:` 伪 chunk；其余材料仅作背景参考与人工核验。

| 文件 | 来源 | 用途 |
|---|---|---|
| `FRECA_41CP_评分标准_最终合并版_材料并入.xlsx` | **正天整理**（8/26 最终合并版） | Stage B curated rubric 口径的输入（v4 消融的评测目标） |
| `CP结果汇总_全部CP证据链_首表含RE注册号(1).xlsx` | 团队共识表（8/26） | `gold/consensus-v2.json` 中 RE 注册号字段的来源；新增 3 条标签的依据 |
| `12个逻辑结构代表CP_五案例三证据链总表_符号逐行说明版.xlsx` | 团队 | 12 个逻辑结构 CP 的代表样本与符号说明（背景参考） |
| `FRECA打标流水线_原理与流程.html` | 团队 HTML 文档 | deepseek v6 流水线的原理与流程（用于解读 81% 一致率的对照线） |
| `矩阵_判定结果_051719(1).xlsx` | **王博 deepseek v6** 流水线产物 | 100×41 完整矩阵，作为另一条对照线（v6 81% 一致率） |

## 与本仓代码的对应关系

- `FRECA_41CP_评分标准_最终合并版_材料并入.xlsx` —— `src/freca/ledger/criteria.py`（`CriteriaTable.load`）
- `CP结果汇总_*` —— `gold/consensus-v2.json` 的 `re_number` 字段与 `cp24/26` 新增三条
- `FRECA打标流水线_原理与流程.html` —— 误差分析报告 §4 中关于 deepseek v6 的对照说明
- `矩阵_判定结果_*` —— 计划中的“与王博 81% 对账”步骤的输入（待决策）

## 不在仓的相关文件

父目录 `D:/Data/Desktop/contest (2)/` 下原始文件已在本目录备份；本地 `.worktrees/`、`.venv/`、`build/`（406 MB 运行产物）按 `.gitignore` 屏蔽不入仓。