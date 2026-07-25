# farm-case-analysis — 项目说明与文件命名规范

## 项目背景
FRECA（Farm Registered Establishment Compliance Audit）合规审计竞赛：
对 100 个农场 case × 41 个检查点（CP1–CP41）输出 `1 / 0 / N/A`，共 4100 次判决。

## 目录结构
```
farm-case-analysis/
├── backend/       # Python 后端代码（检索 + 判决流水线）← 主要维护目录
├── frontend/      # （预留，可视化 / 调试界面）
├── docs/          # （预留，方案、报告、评审材料）
└── outputs/       # （预留，最终 submission 归档）
```

## 文件命名规范
1. **代码（Python）**：`snake_case.py`，模块名清晰表意，如 `pdf_parser.py`、`hybrid_retriever.py`。
2. **目录**：全小写，多级用 `/`，如 `src/retrieval/`。
3. **配置**：统一 `config.yaml`（YAML），不写 `.py` 配置。
4. **常量 / 枚举**：代码内 `UPPER_SNAKE_CASE`。
5. **数据 / 产物（data/ 下）**：`<对象>_<用途>.<ext>`
   - 索引缓存：`rules_clauses.json`、`RE-NSW-2020-0033_chunks.json`
   - 验证集：`validation_set_v1.xlsx`
   - 提交：`submission_v1.xlsx`
6. **日志**：`logs/YYYYMMDD_HHMM_<环节>.log`
7. **case 编号**：保留数据源命名 `RE-<州>-<年>-<编号>`，不得改写。
8. **红线语义**：`checkingpoints_all_elements_onesheet.xlsx`（=CP↔法规条款映射，即规则本身）**严禁进入任何 AI 输入**；代码/文件名中不得出现 `cp_rule`/`checkpoint_*` 之类的「可喂入语料」命名。
9. **文件名禁用空格 / 中文**（脚本、模块）；说明文档可用中文。

## 约束红线（最高优先级）
- `checkingpoints` 表不得进 prompt / query / 3-shot / 语料。
- 推理仅来自「法规 PDF + 农场证据」。
- 提交须含 exact prompt + 模型名，可复现。
