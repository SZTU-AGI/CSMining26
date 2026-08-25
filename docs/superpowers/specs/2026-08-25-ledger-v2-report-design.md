# Ledger v2 与 Gold 汇报设计

## 目标

在不把 Gold 标签、案例编号或 CP 编号写入规则或提示词的前提下，修复 Ledger 对 `N/A` 的不合法放行，重跑相同 34 条 Gold；同时生成一份可离线打开的 HTML 汇报，记录 v1 全部方法的可比结果和 v2 的结论。

## 已知问题

`normalize_decision` 目前只要模型输出 `N/A`、带有政策引用和适用性说明，就把其适用性强制改写为 `NOT_APPLICABLE`。这覆盖了模型原本输出的 `UNKNOWN`：也就是说，“主体不一致”“材料不足”“无法确认筛选是否进行”被错误地包装成法律上的不适用。

这违反了团队共识：`N/A` 只能由明确的法规非适用支撑；对象主体不一致或不能证明条件满足应为 `0` 并保留质量标记。

## 方案比较

1. **规范化硬门（采用）**：仅允许原始 applicability 已为 `NOT_APPLICABLE` 的 `N/A`；否则映射为 `0`，保留原始 `UNKNOWN` 和新增原因标记。它是模型无关、可审计的契约修复，不改变检索、事实账本或引用校验。
2. 提示词加强：明确要求模型将不确定性判为 `0`。这会降低错误率，但仍无法阻止格式正确却语义错误的 `N/A`。
3. 主体一致性预过滤：先筛掉身份不符事实再裁决。它对证据纯度有价值，但会改变证据召回，难以将这轮提升准确归因于单一改动。

本轮只实现方案 1；方案 2/3 留作 v2 若未达到 80% agreement 时的后续实验。

## 设计

### Ledger v2

- 在 `freca.ledger.adjudicate.normalize_decision` 中检查原始 `applicability`。
- 当 verdict 为 `N/A` 而原始适用性不是 `NOT_APPLICABLE`，输出 `0`、保留 `UNKNOWN` 或 `APPLICABLE`，并记录 `na_withdrawn_nonlegal_applicability`。
- 仍要求合法 `N/A` 具有政策引用与非空适用性说明。
- 不改变 gate 的“发现问题但不重写 verdict”原则；该规则属于模型响应到 `LedgerDecision` 的语义规范化。
- 以新 `run_id=ledger-na-gate-gold-v2` 跑完整 34 条 Gold，所有产物隔离保存并由统一比较器重新排名。

### HTML 汇报

创建一个无外部依赖的静态页面 `build/reports/gold-v1-method-selection.html`：

- 顶部展示 Gold 范围、评测口径和唯一合格的 v1 基线。
- 方法总览表：agreement、coverage、终态失败率、资格状态和结论。
- Ledger 误差诊断：按 CP 展示 v1 表现，并突出 `N/A` 语义漏洞及可验证的预期影响。
- v2 区域预留实际运行结果；页面由本地 JSON 报告生成，避免手工抄写指标。
- 明确标注 Gold 仅用于离线评分，且本轮未运行 369/4,100 项。

## 验收

1. 单元测试证明 `UNKNOWN + N/A` 与 `APPLICABLE + N/A` 均归一为 `0`，合法 `NOT_APPLICABLE + N/A` 不变。
2. 原有 Ledger 裁决、gate 与 review 测试通过。
3. v2 只创建 34 个确认的 case×CP 任务，且不覆盖 v1 产物。
4. HTML 可在本地直接打开，数据来自已落盘评测报告；页面显示 v1 全部八个方法及 v2 结果。
