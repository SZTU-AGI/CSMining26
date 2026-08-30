# FRECA-GRACE V5 交接说明(2026-08-30)

这份包 = 你导出的 34 个流水线文件 + 13 个校验工具 + 1 个参考核心测试替身。
**不含任何 API key、`.env`、案例数据或运行结果**(已做密钥扫描,零命中)。

一句话状态:**代码可以跑了,但方案有三个决策还没定,而其中两个要靠跑冒烟拿数据才能定。**

---

## 一、我动了你的哪些文件(只有 3 个)

其余 31 个文件与你导出的**逐字节相同**。合并时以你的版本为基线重放我的改动,
你在 `freca_core_v1.py` 里加的 HTTP 错误处理 + bearer 脱敏**完整保留**。

### 1. `freca_core_v1.py` — temperature 在 thinking 分支也钉到 0

原来只有 `else` 分支设 `temperature=0.0`,`thinking=True` 的调用(契约编译
`compile_cp`)**完全没设**,走服务端默认值。

赛规原文:「Submissions that cannot be reproduced from the declared prompt and
model will be disqualified.」任何一条调用留着未固定的解码参数都是资格风险。
契约会落盘并按 sha256 进任务指纹,所以**给定契约的运行是可复现的,但从零重建
契约不是**。

逃生阀:`FRECA_UNPIN_THINKING_TEMPERATURE=1` 恢复旧行为(万一服务端不接受
`temperature` 与 `reasoning_effort` 并存)。

### 2. `production_runner_v1.py` — 接线 N/A 反查 + 补指纹(5 处)

**这是最重要的一处。** `build_fold_gate_report` 接受 `na_countercheck` 参数,
不传时 `na_countercheck_passed` 恒为 False,而 `build_outcome_and_fold`
**从来没传过**。实测(用真 `build_outcome_and_fold`):

| 上游 root_states | 不传 countercheck | 传 countercheck |
|---|---|---|
| `non_applicability_state=TRUE` | **`0`** (UNKNOWN_BENCHMARK_FALLBACK) | **`N/A`** (RULE_FIXED_NA) |

**即三个标签里有一个结构性出不来**,上游判"不适用"的坐标一律被答成 0。

修复放在新模块 `na_countercheck_v1.py`,反查只用**已派生的 root_states**,
零新增模型调用、零 prompt 改动(避开"禁止把 CP 逻辑硬编码进 prompt"的红线)。

**默认关闭**,`FRECA_ENABLE_NA_COUNTERCHECK=1` 开启。为什么默认关闭见第三节。

另外补了两项进指纹:`na_countercheck_v1.py` 进 `RUNTIME_FILES`,开关状态进
`task_input_fingerprint` 的 payload。**不补这两项的话**,关着跑一次再开着重跑
同一个 run-dir,任务会全部命中缓存,开关看起来完全无效——最误导的失败方式。

顺带更正了 `--no-repair` 的帮助文字:原文写「stop after initial Layer-7 before
Fold」,与代码不符(fold 在 repair 循环**之后**,照常执行)。

### 3. `run_production_v2_full.sh` — 全量闸门改为按变量推导路径

你加的 `SMOKE_TAG` 很好(不同 provider 的冒烟能并存)。但全量闸门写死了
`production_run_v2_smoke_6_zhipu_v2_replay`,而冒烟路径用
`${SMOKE_COUNT}_${SMOKE_TAG}`——**两个轴都会踩空**,而且报错说的是
"冒烟缺失或 NO-GO",指向错误的原因。

现在按同样变量推导,并把「文件不存在」和「判 NO-GO」分开报:

```
Full run blocked: no smoke analysis at
  .../production_run_v2_smoke_8_ds_v1_replay/smoke_analysis.json
Run the smoke with the same tag and case count first:
  FRECA_SMOKE_TAG=ds_v1 bash run_production_v2_full.sh smoke 8
```

`FRECA_FULL_GATE_SMOKE_COUNT` 可覆盖默认的 6。

### ⚠ 行尾

我用 Python 改写文件曾把 LF 变成 CRLF。`sha256_file` 读的是**字节**,
行尾一变所有文件哈希都变 → 每个任务指纹都变 → **主机上的续跑缓存全部失效,
变成全量重跑**。已全部统一回 LF,包内 0 个 CRLF。你合并时也注意这点。

---

## 二、怎么跑

### 先跑一键自测(零 API,约 1 分钟)

```bash
cd code
bash run_all_self_tests.sh --with-shim
```

应该看到 `ALL 16 CHECKS PASSED`,最后一项是
`PASS production_runner_v1 imports (shim core)`。

**关于 `mas_harness_v1` 那一行:** 包里不含案例数据,所以它会退回合成案卷,
并明确打印:

```
mas_harness_v1 self-tests: PASS (2 SYNTHETIC cases - no dumps under ...,
so the dump parser was NOT exercised)
```

这是**刻意的**:退回而不声明,就等于宣称 dump 解析器被检验过而其实没有。
在有 `eval/case_dumps/` 的机器上跑,同一行会变成
`PASS (3 real cases, 27 tracks)`,那时解析器才真的被测到。
用 `--case-dumps <路径>` 指定位置。

### 再跑冒烟

```bash
bash run_production_v2_full.sh smoke 6
```

### 然后跑校验闸门(零 API,读产物)

```bash
bash validate_run_v1.sh smoke \
  results_v2/production_run_v1_smoke_6_zhipu_v2 \
  results_v2/production_run_v2_smoke_6_zhipu_v2_replay \
  246
```

七步依次是:结构探针 → 输出合规 → 零值来源 → N/A 可达性 → N/A 触发面 →
H7 支撑定位 → 证据覆盖率。**最便宜的排在最前**,结构漂移会在下游门被信任之前
就被抓住。

---

## 三、三个还没定的决策

### ① N/A 开关(要靠冒烟数据定,别靠论证)

开启会把一批 0 变成 N/A。判错就是净损失,收益率无法先验测量。

**别用 615 格金标当依据**——那份已被判定不可信。可用的结构性证据:
41 条 CP 里有 10 条自带条件从句(`where applicable` / `if any` / `where
required`:CP1/6/7/14/26/29/34/37/38/41),这是 N/A 的合理落点,占比 24.4%。

**怎么定:** 冒烟跑完(**开关保持关闭**),看第 5 步的输出:

```
would flip to N/A   <n>  <share>
concentration       <c>  vs random baseline 0.2439
```

- `concentration` 显著高于 0.2439 → 机制在跟条件措辞走,可以开
- 接近或低于 → 越权,别开

`na_trigger_surface_v1.py` 从**关着跑的运行**里就能零成本反算,不用为了这个
数专门开着跑一次。

### ② 蜕变测试里 repair 开还是关

**生产全量运行是开着 repair 的。** 关着测,H1–H8 建立的关系不适用于实际提交的
配置;开着测,每个坐标多烧几轮模型调用。这是成本与有效性的权衡,所以
`rerun_adapter_v1.make_rerun` 的 `repair_enabled` **做成了必填参数**,没有默认值
——任何默认都会有人踩错。取值记录在每条观测里。

### ③ FRECA 跑出来的结果和现有定版是什么关系

现有定版 `submission_task2_v5R_dupRE.xlsx`(md5 `69de12fdd5a1`)用的是
`t2_v5r.js`——单次 prompt + 确定性身份事实层,**不是这套流水线**。

它当初是**在 615 格金标上比出来的**。那份金标现在不可信,**选型依据本身没了**。
所以如果 FRECA 跑出一份不同的答案,目前**没有任何仪器能在两者之间做选择**。

**这个判据要在跑全量之前定,不能跑完再想**——否则会有两份提交和一个无法回答的
问题。

---

## 四、两个已测的结论(都是零 API 测出来的)

### 检索层不是 v7 那个瓶颈 —— 我自己的假设被推翻

我先按 `retrieval_top_k=12` 推断 FRECA 继承了 v7 的信息瓶颈(ledger 只覆盖
6.6–7.0% 的实质 span,93% 案卷从未到达判定者)。**推错了。**

`top_k` 不是约束项。真正的边界在 `retrieve_requirement_candidates` 内部:
`lexical_candidate_limit=40`、support/attack 上下文帽各 `max(24, top_k)`、
外加 `:P`/`:R` 的 ±1 邻接扩展。

真实测量(真解析器 + 真 BM25 + 真检索函数,4 案例 × 41 CP = 164 次检索):

| | chunk 覆盖 | 字符覆盖 |
|---|---|---|
| 单坐标(中位) | 10.68% | **21.07%** |
| 41 条 CP 合并 | 60.70% | **69.09%** |
| v7 ledger | — | 6.6–7.0% |

案卷真实规模:每案 ~541 chunk / ~49,000 字符。敏感性检验:查询从 18 词缩到
6 词,字符覆盖 22.31% → 17.49%,**仍是 v7 的约 2.5 倍**,不是长查询的假象。

复现:
```bash
python retrieval_ceiling_probe_v1.py \
  --evidence-root <annotation_packet/evidence> \
  --cp-text <eval/checking_points_41.txt>
```

**但这只排除了一个解释。** 判定层读的是 **alignments 不是 retrievals**,
身份门和对齐器还会各收窄一次,那两步都要模型、离线测不了。
冒烟跑完后 `validate_run_v1.sh` 第 7 步会给出这个数。

### 蜕变测试对"声称不足"几乎是盲的

实测:

| 退化系统 | `hard_mas_pass` |
|---|---|
| 恒答 0 | **True**(H1/H2/H3a/H3b/H6 全过,H4/H5/H7 弃权) |
| 恒答 1 | False(H3a 失败) |

判定器全是「若原先是 1,则变换后不得仍为 1」这种形式——**一个从不说 1 的系统
全部空洞满足**。这套东西擅长抓过度声称。

而 FRECA 是合取链(适用性成立 ∧ 每条决定性要求达标 ∧ 无反驳 ∧ 无攻击),
实测每一种失败都产出 0(`UNKNOWN`→0、`CONFLICTING`→0、`NOT_DEMONSTRATED`→0)。
**它的风险方向恰好是这套测试看不见的那一侧。** 用的时候要知道这一点。

---

## 五、校验工具清单

| 文件 | 作用 | 需要运行产物? |
|---|---|---|
| `run_all_self_tests.sh` | 一键跑全部自测 | 否 |
| `validate_run_v1.sh` | 七步校验闸门 | 是 |
| `schema_probe_v1.py` | 字段是否在(把静默归零变成显式点名) | 是 |
| `submission_composition_gate_v1.py` | 输出是否退化(硬闸) | 是 |
| `zero_provenance_report_v1.py` | 零值是"被证否"还是"没结论" | 是 |
| `fold_finality_v1.py` | finality 分类单一来源 + 从 fold policy 自动核对完备性 | 否 |
| `na_countercheck_v1.py` | N/A 反查(默认关闭) | 否 |
| `na_reachability_check_v1.py` | N/A 分支是否可达 | 否 |
| `na_trigger_surface_v1.py` | N/A 会落在哪些 CP(从关着跑的运行反算) | 是 |
| `support_locator_export_v1.py` | H7 的唯一支撑定位 | 是 |
| `mas_harness_v1.py` | 蜕变测试 H1–H8 + 诊断 D1–D4 | 需 rerun |
| `rerun_adapter_v1.py` | 把 harness 接到真 `run_task` | 需 rerun |
| `run_invariants_v1.py` | 确定性 + N/A 可达性 | 需 rerun |
| `evidence_coverage_probe_v1.py` | 判定层实际看到多少案卷 | 是 |
| `retrieval_ceiling_probe_v1.py` | 检索面上界(离线) | 否 |

`testing/reference_core_shim/` 是**测试专用**的参考核心替身,让本机能
`import production_runner_v1`。**绝不能用它跑要保留的结果**——
`decide_search_route` 是 `action_gate_v1_1:1224` 那条交叉校验的对手方,
自己写就变成循环论证,等于静默移除一道安全网。详见该目录的 README。
需 `FRECA_REFERENCE_CORE_SHIM_OK=1` 才加载,不设直接抛 `ImportError`。

---

## 六、给写方法说明的人:一处错

`答辩材料/方法说明/methodology_task2.md` §1.4:

> Our submitted output predicts **41.90% zeros**, which sits just under that line.

41.90% 是**一率**不是零率。定版 `submission_task2_v5R_dupRE.xlsx` 实际是
2382 个 0 / 1718 个 1 = **零率 58.10%**、一率 41.90%。

盈亏平衡线是「官方真值中 ≥42.45% 为非合规」。按 41.90% 说「just under that
line」;按真实零率 58.10% 则是**远高于**该线。**这个赌注分析的方向反了。**
