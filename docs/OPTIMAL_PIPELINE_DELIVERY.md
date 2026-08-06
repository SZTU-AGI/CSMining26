# FRECA Task2 最优链路交付:架构、评分准则与对抗审查

> **状态**:设计完成 + 代码落地(16 模块 + 测试);仅 Stage A 跑过 3 个 case,Stage B–E 零实证。对抗审查发现的两个 P1 漏洞(判0 复核歧视、生产候选无门禁)已于 2026-08-06 修复并补单元测试(全量 292 passed)。
> **日期**:2026-08-06
> **范围**:本文件是对"最优链路"(ledger 架构)的交付评审,覆盖架构设计、评分准则(scoring)、以及一次聚焦判定链路的对抗审查。架构全貌见 [`LEDGER_ARCHITECTURE.md`](LEDGER_ARCHITECTURE.md) 与 [`ARCHITECTURE_DESIGN.md`](ARCHITECTURE_DESIGN.md);放弃多方法投票的依据见 [`STRUCTURED_RUBRIC_AUDIT_PROPOSAL.md`](STRUCTURED_RUBRIC_AUDIT_PROPOSAL.md)。本文件不重复 those,只补"评分准则细解 + 对抗漏洞"。

---

## 0. 一句话结论

最优链路 = **每案一次结构化事实抽取 → 每 CP 运行时从法规推导 rubric → 基于 fact pack 单次裁决 → 仅在证据不足/冲突时条件复核**,评分用 5 个**独立、无总分**的维度只做证据质量与复核排序,**绝不**决定 verdict。设计哲学高度自洽,但本次对抗审查发现 4 个实质问题(其中 1 个架构级、1 个评分偏差),且整条判定链路**尚无任何真实 verdict 验证过**。**两个 P1 漏洞(评分对判0 的复核歧视、生产候选缺门禁)已修复并补测试**;P0(零实证)、P2(凑引用)、P3(次要)仍待处理,见 §5/§7。

---

## 1. 当前实际进展(真相,非印象)

| 链路 | 产物 | 状态 |
|---|---|---|
| **Ledger(最优链路)** | `build/ledger/facts/{001,002,003}.json` + `.trace.json` | **仅 Stage A 事实抽取,3 个 case,deterministic 模式(模型端点未接,见 §6),零 verdict** |
| Ledger Stage B–E | — | `build/ledger/` 下只有 `facts/`,**无 rubric/outcomes/decisions**,从未运行 |
| 7 种实验方法(主仓) | `build/experiments/case_full`(1 case)、`pilot`(4 case 抽样) | 其余 6 方法无 summary |
| `automatic_retrieval` case-001~009 | 8/3 文档 [`cross-case-systemic-cps.md`](../../reports/analysis/2026-08-03-cross-case-systemic-cps.md) 引用 369 verdict | **主仓 `build/experiments/` 未见**,可能在 `.worktrees/freca-evidence-audit`,待确认 |
| Gate 0 确定性诊断 | `build/diagnostics/contamination_full100.json`、`user_signature_truth.json` | **100 case 全跑过** |
| 人工金标 | `manual_audit/digests/*`(200 文件)、顶层 `case*_judgments.json` + `reports/gold_standard.xlsx` | **100 case 全有,4100 判定,已交叉验证一致** |

**含义**:评分准则与裁决逻辑目前是**纯设计**,所有阈值/权重为先验值,零实证校准。对抗审查因此只能查逻辑一致性,查不了实证表现(见 §5 漏洞 4)。

---

## 2. 候选方向(团队分工参考)

用户提到"每人负责一个方向"。当前可辨识的方向:

| # | 方向 | 角色 | 数据支撑 |
|---|---|---|---|
| 1 | **Ledger 主路径**(本文件主题) | 新主路径、交付候选来源 | 3 case 事实抽取 |
| 2 | **Automatic retrieval 检索基线** | 检索基线对照、最省 token(13k/call) | case-001~009(369 verdict,在 worktree) |
| 3 | **Cross-case 差异性判别** | 判"哪些 CP 系统性不合规"vs 偶发,与单 case 共识互补 | 已发现 CP9/16/36 等 13 个系统性 CP |
| 4 | 多方法共识投票 | 已弃当真值,留作稳定性信号 | case-001/002 |
| 5 | Gate 0 确定性证据完整性 | 纯机械、不调 LLM、材料质量门 | 100 case 全跑 |

方向 2/3 有真实 verdict 数据(在 worktree),方向 1 是新主路径但只跑了前 3 步。

---

## 3. 最优链路架构(五阶段 A–E)

```
9 份材料 ──[A 抽取]──▶ fact ledger(每案一次,只述事实不下结论,可回链原文)
                                  │
CP 原文 ──[检索法规]──▶ [B rubric]─┤  每 CP 运行时从法规推导,带引用,按哈希缓存
                                  ▼
                        [C selection]──▶ fact pack(~28 facts + 全部 contradiction)
                                  │       每个 fact 记录被哪条 criterion 拉入及原因
                                  ▼
                        [D adjudicate]──▶ 1/0/N/A + 双引用(模型裁决,模块不算 verdict)
                                  │
                        [E review]────▶ 仅触发条件复核(紧凑上下文)
                                  ▼
                        scorecard(5 维,无总分)+ review_priority(triage)
```

**贯穿全链的防泄漏/防作弊设计**:

- **A 抽取**(`extraction.py`):`require_verbatim_match`——引用在自家 chunk 找不到就标记不信任;`drop_answer_like_facts`——Track 3 的 `Audit scenario`/`NOTE: NON-COMPLIANT` 当作者元数据丢弃(竞赛红线 §3)。
- **B rubric**(`rubric.py:7-20`):只用官方 CP 文本做检索种子;每个 criterion **强制引用 ≥1 检索到的 policy chunk**,`CheckpointRubric` 拒绝验证不在检索上下文的引用;文件内不含任何 `CP→verdict` 映射;prompt 禁止写检索文本未说的阈值。**rubric 是 per-CP 不 per-case**(需求不随 case 变),按 `(CP 文本, 检索 chunk, generator, prompt 版本)` 哈希缓存以支持复现。
- **C selection**(`selection.py:17-24`):adjudicator **看不到 9 份原文**,只看 ~28 facts 的 pack;没拉到事实的 criterion 报为 `uncovered_criteria`(不静默消失),让门能区分"法规满足"和"没看";routing 是 lexical deterministic,只决定"读什么"不决定"判什么"。
- **D adjudicate**(`adjudicate.py:9-25`):**双引用强制**(1/0 必须有 ≥1 政策引用 + ≥1 本案 fact 引用,不在 rubric/pack 的引用先剔除再校验);**N/A 是法律结论不是搪塞**(必须 `NOT_APPLICABLE` + 政策适用性解释,"没检索到/材料不全"不能产 N/A);answer-like fact 移出支持集;别家农场(污染)证据**不能单独**支持 1。模块开头声明:"verdict is the model's, this module does not compute it, no rule of the form 'criterion X satisfied ⇒ 1'"。
- **E review**:仅 rubric 缺法规依据/缺关键事实/同主题矛盾/理由与标签不符/低置信时触发,用紧凑片段不重发全案。

---

## 4. 评分准则设计(`scoring.py`,核心交付物)

### 4.1 哲学:5 维独立,**刻意无总分**(PROPOSAL §6)

> 法规审计含**否决式事实**——一条缺失的虫害记录就能否决一个其他维度全好的 case。所以"80 分即合规"不合法。

`EvidenceScorecard` **故意不暴露 `total`**。这些数只能:
- 表达 verdict 背后的证据好坏;
- 决定是否需要独立复核;
- 在复核预算有限时排复核队列。

**绝不能**:求和/平均成一个数;与阈值比较产 1/0/N/A。

### 4.2 五个维度(全 `[0,1]`)

| 维度 | 函数 | 测什么 | 关键细节 |
|---|---|---|---|
| **D1 法规覆盖** | `_regulatory_coverage` | verdict 引用是否覆盖 rubric 的政策条款 | = 命中 rubric criteria 的政策引用占比;例外/时间条款专项加权(0.75/0.25);**rubric 降级则压 ≤0.5**(`scoring.py:120-122`) |
| **D2 支持覆盖** | `_support_coverage` | 本案 fact 是否触及 rubric 关键条件 | criterion 带引用 fact=1.0,纯推理=0.4,`not_evidenced`=0;N/A 时只评估适用性 criteria |
| **D3 反证强度** | `_contrary_strength` | 指向"不合规"的证据强度 | **高分≠坏**——专为把"判1但反证强"的拉去复核;违反 criteria +0.2~0.6、反证 fact +0.2、BLOCKER 矛盾 +0.25、missing tracks +≤0.2 |
| **D4 引用质量** | `_citation_quality` | 双引用是否可解析/可追溯/verbatim | 政策引用 known 占比 0.3 + fact 可解析 0.3 + 可追溯 0.2 + verbatim 0.1 + reasoning/N/A 解释 0.1;repair flags 每个 −0.1 |
| **D5 证据完整性** | `_evidence_integrity` | 缺件/污染/解析失败 | 从 1.0 起扣:missing tracks ≤−0.3、空账本 −0.4、污染 fact −0.25×share、verbatim 缺失 −0.2×share、answer-like −0.1、BLOCKER −0.25、空 pack −0.35、`adjudication_blocked` −0.5 |

### 4.3 `review_priority`(`scoring.py:365-398`)

triage 数 `[0,1]`,**极性相反**(证据弱=高分=先复核),**明确禁止阈值化成 verdict**。weakness 组合:

```
0.28·(1−D4) + 0.22·(1−D2) + 0.20·(1−D5) + 0.15·(1−D1)
+ contrary 极性随标签翻转:判1 → +0.15·contrary;判0 → +0.15·(1−contrary)   ← §5 漏洞 2 已修
+ 0.10·(threshold − confidence)  若低置信
+ ≤0.25·(0.12·error_count) + ≤0.10·(0.02·trigger_count)
```

---

## 5. 对抗审查发现

审查覆盖 Stage B/C/D/E 的判定链路(rubric、selection、adjudicate、scoring)。严重度:🔴 架构/偏差级,🟡 弱点级,⚪ 次要。

### 🔴 漏洞 1(架构级):verdict 不靠评分兜底  ·  ✅ 已修复(P1)

**现象**:`scoring.py:14-17` 明确"must never be compared against a threshold to produce 1/0/N/A";`adjudicate.py:22-25` 明确"verdict is the model's, this module does not compute it"。

**后果**:评分只是**事后信号**,只影响"先复核谁",**不阻断任何 verdict**。一个引用完整但判定逻辑错的 verdict 能直接进生产候选。最终质量天花板 = adjudication 模型质量 + review 兜底。若 review 预算紧或 review 模型与主裁决同源偏宽,bad verdict 漏出。

**建议**:(a) 给 review_priority 设硬阈值门禁——超过阈值的 verdict 在缺 review 时**不得**进入 production_candidate,只能进 silver/evidence_integrity 类;(b) 或对 D2≤阈值 且 verdict=1 的组合强制 review(覆盖不足却判合规是最高风险组合)。

**✅ 修复(2026-08-06)**:采纳建议 (a)。`baseline.build_production_candidate` 增 held_back 门禁——`review_priority >= BaselineConfig.production_priority_threshold`(默认 0.5,可配)且 `not outcome.reviewed` 的 verdict 计入 `held_back_items` / `held_back_examples`,**不**进入 `submittable_items`;已复核项(review 已确认或推翻 primary)放行;`items` 仍记全量以保留可追溯性。补测试 `test_production_candidate_holds_back_high_priority_unreviewed_items`、`test_production_candidate_threshold_is_configurable`。门禁是**事后闸门**,不改变"评分不决定 verdict"的契约(评分仍只排序、不阻断),只是把高风险未复核项挡在"可提交子集"之外。

### 🔴 漏洞 2(评分偏差,最阴险):`review_priority` 对判0的复核歧视  ·  ✅ 已修复(P1)

**现象**:`scoring.py:388-391`——verdict=COMPLIANT 时 `+0.15·contrary`,否则(含 NON_COMPLIANT/N/A)`+0.05·contrary`。

**后果**:意图是防"假合规"(判1但有反证→优先复核)。但副作用:**过度判0(假阴性,农场其实合规却被判不合规)系统性获得更低复核优先级、更难被发现**。竞赛是对称准确率(overall accuracy),假阴性与假阳性同等扣分,这个不对称会让"偏保守判0"的错误长期隐蔽,尤其当模型本身有判0倾向时。

**建议**:contrary_strength 的复核加权改为对称,或对"判0 但 D2/D4 高(证据其实充分却判不合规)"也设触发——假阴性应与假阳性同等可见。

**✅ 修复(2026-08-06)**:`scoring.review_priority` 判0 分支由 `+0.05·contrary` 改为 `+0.15·(1−contrary)`——判0 时 contrary 越**弱**越可疑(判了不合规却缺不合规证据 = 假阴性特征),与判1 的 `+0.15·contrary`(contrary 越强越可疑 = 假合规)极性对称,假阴性与假阳性同等进入复核队列。

> **踩坑**:第一版曾用 `support_coverage` 作判0 信号(`+0.15·support`),被测试 `test_contradictory_evidence_raises_priority_for_both_labels` 当场打回——`support_coverage` 已在通用项 `0.22·(1−support)` 中以**相反极性**出现(低 support = 证据弱 = 要复核),复用会自抵消(实测 strong_support 的判0 反而比 weak_support 复核优先级更低)。正确信号是让 contrary 维度对两标签方向极性翻转:它不在通用项里,天然不冲突。测试现锁定"判1 怕 contrary 强、判0 怕 contrary 弱"。

### 🟡 漏洞 3:dual citation 门禁可被"凑引用"绕过

**现象**:`adjudicate._clean_ids`(`adjudicate.py:149+`)只验证引用 id 在 rubric/pack 内合法,**不验证引用与 verdict 的因果强度**。

**后果**:模型可从 pack 抓一条相关但非决定性的 fact 满足"有 fact 引用",再给与证据强弱不符的判定(如 CP9 照明:引一条"有窗户"的 fact 判1)。D2 能部分捕捉(fact 是否触及关键 condition)但 D2 只降 review_priority、不阻断。

**建议**:对"verdict=1 但 D2<阈值"或"verdict=0 但被判 violated 的 criteria 无 fact 支撑"的组合,在裁决阶段就拒收并强制重裁,而非仅打 flag。

### 🟡 漏洞 4:零实证验证

**现象**:`build/ledger/` 只有 facts,Stage B–E 从未运行;D1–D5 权重、`confidence_threshold=0.65`、contrary 的 0.15/0.05 全是先验 magic number。

**后果**:无法判断这些阈值/权重是否合理。例如 0.65 置信阈值、review_priority 的 0.28/0.22/... 加权都未经任何 verdict 分布校准。

**建议**:接模型后先用金标 case 跑 10–20 个 case×CP,看 5 维分布、review_priority 与"金标错判"的相关性,据此校准。**这是交付前最该补的一步。**

### ⚪ 次要观察

- **selection 漏召回路径**:`selection.py` 用 lexical token 重叠 + topic 分类做 routing。关键 fact 若用同义不同词表述("lighting" vs "illumination"、"bait station" vs "rodent trap"),可能不进 pack → 该 criterion `not_evidenced` → 裁决可能当"缺证据→判0"而非"没找到"。这与 cross-case 文档里 automatic_retrieval 召回率仅 11% 是同一类病。`uncovered_criteria` 不静默消失是好的,但它进 `integrity_notes` 在 D5 最多只扣 0.15。
- **污染证据双刃**:`include_contaminated=true`(config)把别家农场材料留进 pack,初衷是让裁决者"看见并拒绝",但反向风险是裁决者被污染证据说服。"不能**单独**支持1"的约束在"1 条弱本土 fact + 1 条强污染 fact"组合下形式上可绕过。
- **rubric 降级联动不全**:降级 rubric 压 D1≤0.5,但 D2/D4 不联动,降级路径下 scorecard 可能虚高。降级产物本不应进 production_candidate,需上游门拦。

---

## 6. 交付前置条件(阻塞性)

1. **模型端点未接**:`config.ledger.yaml:57-92` 中 `audit/verifier/arbitrator/embedding` 的 `base_url` 全是 `https://api.example.invalid/v1`、model 全是 `configure-*-model`、`FRECA_*_API_KEY` 未填。当前 extraction 走 `llm_with_fallback` 的 deterministic 分支(故能产 3 case facts),但 Stage B/D 需要 LLM,**不接模型整条主链跑不动**。
2. **legacy 视图缺失**:`method_from_legacy_finals`(`baseline.py:115`)读 `build/final/*/CP*.json`,但 `build/final/` 不存在 → silver 一致性集的"第二个独立证据视图"目前为空。
3. **金标已就绪**:`reports/gold_standard.xlsx`(100 case × 41 CP,已交叉验证)可直接用于 §5 漏洞 4 的校准实验。

---

## 7. 若决定修漏洞的建议优先级

| 优先级 | 漏洞 | 改动量 | 说明 |
|---|---|---|---|
| ✅ P1 | 2(判0复核歧视) | 已修 | `scoring.py` 判0 分支改 `+0.15·(1−contrary)`,与判1 极性对称;补对称测试 |
| ✅ P1 | 1(verdict 不兜底) | 已修 | `baseline.py` 加 held_back 门禁,阈值 `production_priority_threshold` 可配;补测试 |
| P0 | 4(零实证) | 小(运行)+ 中(校准) | 不修则其他漏洞都无法量化;接模型跑 10–20 case×CP,用 `reports/gold_standard.xlsx` 校准 |
| P2 | 3(凑引用) | 中 | adjudicate 加因果强度校验,可能需改 schema |
| P3 | 次要观察 | 中 | selection 加同义词/语义路由;污染证据策略复核 |

---

## 8. 审查范围声明

本次审查**覆盖**:`rubric.py`、`selection.py`、`adjudicate.py`、`scoring.py` 的判定链路逻辑,及 `config.ledger.yaml` 配置。
本次审查**未覆盖**:`pipeline.py` 的阶段编排实现、`review.py` 的复核实现、`gates.py` 的质量门实现(仅读 docstring)、`extraction.py` 的 LLM 抽取质量、以及**任何实证运行**(因 Stage B–E 未跑)。这些待接模型跑通后补审。
