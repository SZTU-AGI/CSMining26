# Gold v2 方法选择结果

## 评测边界

- Gold：`gold/consensus-v1.json` 中 34 条 confirmed case×CP 标签。
- 模型：MiniMax-M3；Gold verdict 和人工共识理由没有进入模型提示词。
- 运行：只覆盖 case 023、035、038、065、074 的已确认 CP；没有运行 369 或 4,100 项。
- 资格门槛：coverage ≥ 90%，终态失败率 ≤ 10%。

## 结果

| 运行 | 设计 | 一致率 | 覆盖率 | 终态失败率 | 资格 |
| --- | --- | ---: | ---: | ---: | --- |
| `ledger-na-gate-gold-v2` | 原始 applicability 不是 `NOT_APPLICABLE` 时撤回 `N/A` 并判 `0` | **27/34 (79.4%)** | 100.0% | 0.0% | **冠军** |
| `ledger-review-always-gold-v2` | 在 N/A 硬门上对每项强制独立复核 | 26/34 (76.5%) | 100.0% | 0.0% | 合格 |
| `ledger-evidence-expanded-gold-v2` | 在 N/A 硬门上扩至 42 个事实并排除污染事实 | 24/34 (70.6%) | 100.0% | 0.0% | 合格 |
| `ledger-gold-v1` | 原始 Ledger 基线 | 24/34 (70.6%) | 100.0% | 0.0% | 合格 |

所有检索和直连 LLM 对照均未同时满足覆盖率与终态失败率门槛；其最佳 agreement 是 63.3%，但 coverage 为 88.2%、终态失败率为 11.8%。

## 结论

`ledger-na-gate-gold-v2` 是唯一应进入下一阶段的候选。它相对 v1 提升 3 条、8.8 个百分点，且不引入案例、CP 或 Gold 标签特判。提升来自修正一个语义契约漏洞：原模型输出 `UNKNOWN + N/A` 时，旧归一化器错误地把 `UNKNOWN` 改写为法律上的 `NOT_APPLICABLE`；v2 保留原适用性并把不合法 N/A 规范化为 `0`。

全量复核没有超过硬门单改，扩大证据包也没有改善结果，因此不建议把它们叠加到下一轮默认链路。

## 剩余误差方向

- CP12：模型对局部卫生问题、缺少描述性事实和整体场所合规之间的界限仍偏保守。
- CP14：模型容易把具体检查区设计事实判为充分，或因主体矛盾过度否定其原子独立性。
- CP15：虽然不合法 N/A 已被清除，筛选措施对“大污染物风险”和“移除”的证明阈值仍有错判。

下一轮应先以这三类错误构造不含 Gold 答案的通用提示词和证据原子性改进，再只重跑新的隔离 v3 Gold run；在 Gold agreement 进一步稳定前，不扩展到 369 项。

## 产物

- `build/method-comparison/gold-v2.json`
- `build/reports/gold-method-selection.html`
- `build/evaluation/ledger-na-gate-gold-v2.json`
- `build/evaluation/ledger-review-always-gold-v2.json`
- `build/evaluation/ledger-evidence-expanded-gold-v2.json`
