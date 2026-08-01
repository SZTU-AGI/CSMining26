# Baseline 运行记录(2026-07-29 起,loop 自动跑)

> 本文档由 `/loop 30m` 自动维护,记录真实 API baseline 的进展、关键发现与配额状况。
> 机器可读记录见 `build/experiments/scoreboard.json` 与 `build/experiments/agreement.json`;
> 看板见 `build/experiments/scoreboard.html`。

## ⚠ 配额状况(已确认:日级重置,非 30 分钟)

MiniMax 账户 Token Plan 2056 是**账户级**速率限制,无法用 backoff 绕过(见
`memory/minimax-quota-is-account-tier.md`)。
- **2026-07-30 实测**:一次成功窗口(~20:11–20:24)后,连续 8 次 fire(>4 小时)仍 429。
- **2026-07-31 实测**:跨天后(00:1x)探测恢复 200。**重置周期是日级,不是 30 分钟。**
- 客户端的 5/10/20/40/60s backoff 能吸收**间歇性** 429(agent_audit 在 429 期间仍从 cp-000 推进到 cp-006),但持续 429 仍会中断密集方法。

## 已跑方法(case-001, track3=raw)

| 方法 | 调用数 | 状态 | 1/0/N-A | valid% |
|---|---|---|---|---|
| `case_full` | 1 | ✅ 完成 | 40/0/1 | 100% |
| `element_full` | 4 | ✅ 完成 | 40/1/0 | 100% |
| `checkpoint_full` | 41 | ✅ 完成 | 19/11/11 | 100% |
| `automatic_retrieval` | 41 | ✅ 完成 | 23/10/8 | 100% |
| `stage_audit` | ~123 | ⚠ 部分(31/41,429 中断 cp-031) | 23/8/0 | 75.6% |
| `agent_audit` | ~42 | ⚠ 部分(7/41,429 中断 cp-006) | 5/2/0 | 17.1% |
| `verify_audit` | ~42 | ❌ base 调用 429 失败 | 0/0/0 | 0% |

### 关键方法学发现

1. **`element_full` ≈ `case_full`(一致率 95.1%)**:都 40 合规,都 blanket-approve。
   element_full 按 Element 分组但仍灌全量材料,行为几乎等同 case_full,**无额外收益**。
2. **`checkpoint_full` 最均衡(19/11/11)**:11 不合规 + 11 N-A,是所有方法里最有区分度的。
   按 CP 粒度逐个判(即使带全量材料)给模型留出标 N-A 和挑刺的空间。
3. **`automatic_retrieval`(23/10/8)**第二均衡(RAG 筛选证据)。
4. `stage_audit`/`agent_audit` 因 429 中断,有效 CP 不足,分布仅供参考。

### 关键纠正:`element_full`/`checkpoint_full` 不是 context overflow

初次误判这两个方法因 prompt ~582k 字符超出 context 而失败。**实际是 429-retry-exhaustion**:
证据是 `stage_audit` 同尺寸(~582k)stage-1 在窗口早期成功;且 7-31 新窗口里 element_full
(4/4)和 checkpoint_full(41/41)**全部成功**。失败变量是配额时机,不是尺寸。详见
`memory/dense-context-methods-overflow.md`。

## 关键发现(无 silver,跨方法共识)

`scripts/agreement.py` 做跨方法一致性分析(无 ground truth 时,多方法一致判 0 是最可信的
真实问题候选)。**7 个共识不合规 CP,其中 3 个有 3 方法一致**:

| CP | 判 0 的方法数 | 方法 | 置信度 |
|---|---|---|---|
| **CP6** | 3 | agent_audit, checkpoint_full, stage_audit | 🔴 高 |
| **CP16** | 3 | automatic_retrieval, checkpoint_full, stage_audit | 🔴 高 |
| **CP36** | 3 | automatic_retrieval, checkpoint_full, element_full | 🔴 高 |
| CP19 | 2 | automatic_retrieval, checkpoint_full | 🟡 中 |
| CP23 | 2 | automatic_retrieval, stage_audit | 🟡 中 |
| CP30 | 2 | checkpoint_full, stage_audit | 🟡 中 |
| CP40 | 2 | automatic_retrieval, checkpoint_full | 🟡 中 |

**CP6 / CP16 / CP36 是最高可信度 finding**(3 个独立方法一致判不合规)。
另有 **32 个 blanket-approve 嫌疑 CP**(one-shot 判 1,更密方法判 0/N-A)。
待 `agent_audit`/`verify_audit` 跑全后,共识方法数会进一步增加。

## 已修复的 bug

`scoreboard.py` / `agreement.py` 原用 `rglob("result.json")` 会把 `agent_audit` 的中间产物
`cp-NNN/stage_audit/result.json` 和 `verify_audit` 的 `base/result.json` 也算进来,导致
agent_audit verdict 翻倍(14 而非 7)。已加过滤:排除父目录名为 `base` 或以 `stage` 开头
的 result.json,只保留最终 `cp-*/unit-*/verify-cp-*` 结果。

## 下次配额恢复(下一个日级窗口)时的策略

剩余未完成:`stage_audit` 的 cp-031..040(10 个)、`agent_audit` 的 cp-007..040(34 个)、
`verify_audit`(全部,~42 调用)。按成本从低到高跑:
```
python scripts/pilot_4x4.py --cases 1 \
  --methods verify_audit,agent_audit --inter-call-delay 1.5
python scripts/agreement.py && python scripts/scoreboard.py
```
(`element_full`/`checkpoint_full` 已完成,无需重跑;stage_audit 31/41 已够评分,暂不重跑。)
