# Track 内污染证据的抗噪处理

> 本文件定位为深度操作手册,记录 Track 内污染证据的现象、解决方案、关键代码位置与接下来优化方向。
> 实施口径与决策见 `../DECISIONS.md` 第 7 条;架构层意义见 `../SOLUTION.md` 第 17 节。

## 1. 现象:污染是结构性的,不是例外

用户调研表 `文件署名整理表_v2(1).xlsx` 表明 **100 / 100** 个案例的 Track 1 之外都有 ≥1 个 Track 的证据指向别家农场。具体分布:

| 污染 Track 数 | 案例数 |
|---|---|
| 1 | 4 |
| 2 | 34 |
| 3 | 35 |
| 4 | 5 |
| 6 | 4 |
| 7 | 8 |
| 8 | 8 |
| 9 | 2(高度污染,case 35 / 100 共用 RE) |

### 1.1 数据本身的现象(用户调研表确认)

| Track | 别家农场案例数 |
|---|---|
| T1 Registration | 1 |
| T2 HACCP | 97 |
| T3 Pest Control | 50 |
| T4 Management | 34 |
| T5 Site Plan | 25 |
| T6 Hygiene | 22 |
| T7 Bait Station Map | 21 |
| T8 Phytosanitary | 21 |
| T9 Traceability | 79 |

最严重的两类:

1. **T2 HACCP 接近全员污染(97/100)**:HACCP 计划几乎都是别家农场的;
2. **T9 Traceability 高污染(79/100)**:可追溯记录被替换。

干净 case(0 污染 Track)在 100 中为 0。**这意味着没有 case 能"全部 Track 自给自足"**,任何依赖污染 Track 的检查点必须能识别并正确处理外来证据。

### 1.2 你看到的特定案例对照

下面是交叉验证的样本(用户表 vs `build/parsed/cases/{case_id}/track-{n}.json` 解析产物),证明这不是偶发:

- **case 74 / RE-NSW-2020-0088**(Gunnedah Grain Exports):T2 = Bathurst Plains / T3 = Sunrise Canola / T9 = Kookaburra Agri,其余 Track 都是本场。
- **case 35 / 100 / RE-WA-2021-0077**:T1-T8 全污染,T9 一致;共用 RE Number 但分属两个逻辑案例。
- **case 80 / RE-QLD-2022-0077**:即使 T1 缺失,T3 = Darling River Citrus Exports,T4-T8 = Condamine Valley Grain Co;基本是混合目录。

### 1.3 数据可信度

用户表 `文件署名整理表_v2(1).xlsx` 共 898 行(每案 × 9 Track 减去 2 个缺 Track 1),已与 `build/parsed/cases/` 解析文本做反向校验,**两者在 Track 3 内嵌别家 RE Number 的分布 90%+ 一致**。`build/diagnostics/user_signature_truth_with_case_id.json` 是 ground truth 与 case_id 的反查结果。

## 2. 为什么这是个真问题

不识别外来农场证据时,会有什么后果:

- **污染 Track 变成"假合规证据"**:若模型见到 Track 3 的别家农场记录,可能误把别家的 pest log 当本家证据判 `1`。
- **依赖污染 Track 的 CP 全判错**:CP20-21 主要看 Track 3,共 50 case 的 Track 3 被污染,若不识别,这 50 × 2 = 100 个 CP 全部风险被错判。
- **依赖 Track 9 的 CP 也高风险**:CP38-40 主要看 Track 9,79 case 被污染。
- **CP1 本身可能也判错**:case 35 / 100 的 Track 1 也被污染,所有依赖"注册信息"的 CP 都得重新审视。

组委会没有以"整案 N/A"承担这种污染,而是把 Track 1 单独保留为本场基线,意味着**模型必须能识别签名差异、做出正确判断**。这正是 anti-noise 能力的考点。

## 3. 解决方案(已经落地)

### 3.1 4 层防线

```mermaid
flowchart LR
    A[用户调研表<br/>文件署名整理表_v2(1).xlsx] --> B[签名解析器<br/>SignatureTruthLoader]
    B --> C[Case Manifest<br/>contaminated_tracks]
    C --> D[解析层<br/>annotate_chunks]
    D --> E[EvidenceChunk<br/>exclude_from_compliance_evidence]
    E --> F[HybridIndex 检索<br/>隔离污染 chunk]
    F --> G[审计模型<br/>看污染 notice]
    G --> H[裁决 schema<br/>不能 sole-support]
    H --> I[一致性检查<br/>_establishment_name]
    I --> J[仲裁]
```

### 3.2 第 1 层:污染识别(`freca.signatures`)

文件:`src/freca/signatures.py`,接口:

```python
loader = SignatureTruthLoader()
truth  = loader.load(xlsx_path)              # dict[re_number, ContaminatedCaseIndex]

manifest = build_manifest(cases_root, signature_truth=truth)
# 在 manifest 阶段把污染索引写到 CaseRecord.contaminated_tracks 与 flags
```

`ContaminatedCaseIndex` 包含每案 `contaminated: dict[track_number, relation]`(`foreign_farm` / `supplier` / `signature_mismatch`)与 `expected_name`。

### 3.3 第 2 层:解析层加污染 flag(`annotate_chunks`)

文件:`src/freca/signatures.py:annotate_chunks`,在 `pipeline.py:ingest_sources` 调用:

```python
if case.contaminated_tracks and source.track in case.contaminated_tracks:
    chunks = annotate_chunks(chunks, case)
```

它给污染 Track 的每个 chunk 加:

- `flags: ["track_contaminated:N:relation", "exclude_from_compliance_evidence"]`
- `metadata.track_contamination_relation: relation`

### 3.4 第 3 层:检索隔离(`HybridIndex.search`)

文件:`src/freca/index/store.py:HybridIndex.search`,签名新增 `include_excluded_evidence=False`:

```python
contaminated_subset = [c for c in subset if "exclude_from_compliance_evidence" in c.flags]
eligible_subset     = [c for c in subset if "exclude_from_compliance_evidence" not in c.flags]
# evidence_hits <- eligible_subset only
# trace_sink   <- {reason: contaminated_excluded_evidence}
```

调用方`pipeline.retrieve_for_checkpoint` 默认走 `include_excluded_evidence=False`,所以评审模型与 `validate_citations` 都看不到污染 chunk 当 supporting。

### 3.5 第 4 层:裁决语义(`freca.audit + quality`)

- `audit.py`:`_AUDIT_SYSTEM` 指令明确"不可把污染 chunk 当 sole supporting";`_format_hits` 在 content 前加 `CONTAMINATED_EVIDENCE — not the registered establishment; do not cite as supporting.` 标记。
- `quality.py:validate_citations`:拒绝 `supporting_evidence` 列表包含污染 chunk,报错并抛 `BlockedTaskError`。
- `quality.py:find_signature_consistency_issues`:对比 `shared_facts[_establishment_name]` 与 `CaseRecord.expected_establishment_name`,冲突时产出 `ConsistencyFinding` 并通过 `should_arbitrate` 拉起仲裁。

### 3.6 关键代码索引

| 层 | 模块 | 主要函数 |
|---|---|---|
| 识别 | `freca.signatures` | `SignatureTruthLoader.load`, `merge_into_case_record`, `annotate_chunks` |
| 配置 | `freca.config` | `PathsConfig.signature_truth_xlsx`,`PipelineConfig.from_yaml` 兼容 `Path \| None` |
| 整合 | `freca.pipeline` | `write_manifest(signature_truth=...)`, `ingest_sources`(`case.contaminated_tracks` → `annotate_chunks`) |
| 检索 | `freca.index.store` | `HybridIndex.search(include_excluded_evidence=...)` |
| 裁决 | `freca.audit` | `_AUDIT_SYSTEM`, `_format_hits`, `build_audit_messages` |
| 引用 | `freca.quality` | `validate_citations`(拒绝污染 supporting),`find_signature_consistency_issues` |
| 模型 | `freca.models` | `CaseRecord.contaminated_tracks`, `expected_establishment_name`, `foreign_contaminated_tracks` |
| 测试 | `tests/test_signature_contamination.py` | 6 个污染场景测试 + 1 个 xlsx 自构造解析测试 |

### 3.7 产物 Schema 增量

- **`build/manifests/cases.json`**:新增 `contaminated_tracks` 与 `metadata.expected_establishment_name`,`flags` 出现 `track_contaminated:N:relation` 与 `signature_foreign`。
- **`build/parsed/cases/{case_id}/track-N.json`**:污染 Track 的 chunk 携带 `exclude_from_compliance_evidence` flag 与 `metadata.track_contamination_relation`。
- **`build/retrieval/{case_id}/{cp_id}.json`**:污染 chunk 在 `evidence_candidate_trace` 出现 `{"selected": false, "reason": "contaminated_excluded_evidence"}`。
- **`build/consistency/{run_id}.json`**:新增 `_establishment_name_vs_case` finding 触发仲裁。

## 4. 合规边界(与赛规对齐)

| 允许 | 禁止 |
|---|---|
| 用 Track 1 内的 establishment name 作为本场基线 | 把别家农场的证据当本场证据 |
| 隔离污染 chunk,但保留 trace 可追溯 | 隐式删除污染证据或整案 N/A |
| 在裁决 prompt 中给出污染 notice | 把污染识别规则"硬编码"成 CP 专属规则 |
| 触发仲裁以二次复核 | 用模型置信度数字机械覆盖裁决 |

> 本方案的核心合规点是:**污染发现不指向 1/0/N/A 中任意一种确定值,而是降低置信度 → 触发仲裁 → 二次裁决**。这一步保留人类监督空间,不擅自代打。

## 5. 接下来的优化方向

### 5.1 自动污染检测(脱离 ground truth)

当前依赖 `文件署名整理表_v2(1).xlsx` 作为 ground truth。如果未来数据集扩充:

- 用 LLM 抽取每 Track 内的 establishment name / RE Number;
- 与 Track 1 的预期名对比,自动识别 `foreign_farm`;
- 把识别结果(LLM 抽取 + diff)落盘,作为可重跑的 ground truth。

这块不在当前 v3.1 路线里,因为:
- 当前 ground truth 已经覆盖 100 / 100 case;
- 自动抽取引入新模型依赖、增加合规章风险;
- 时间盒内做的话,值得但优先级低于 LLM 接入。

### 5.2 跨 Track 业务字段冲突检测

用户指出的"同一 case 内不同 Track 写不同商品 / 面积"(杏仁 vs 杂粮 vs 葡萄的现象)的处理,可以扩展 `find_signature_consistency_issues` 探测:

- `_registered_commodity`(本家的注册作物):从 Track 1 抽出,与其它 Track 的 establishment name 提作物冲突;
- `_registered_area_ha`(注册面积):同样逻辑。

当前已经埋点 `_establishment_name`,扩展字段时只需要:
- 在 `AuditDecision.shared_facts` 里多塞两个 key;
- 在 `find_signature_consistency_issues` 复制一行同样的 case-vs-decision 检测。

### 5.3 与未来 Agent 修复衔接

LLM 检索 Agent 的 `target_tracks` 限制可与污染 Track 强挂钩:

- 当污染 Track 是某 CP 的关键证据类型时,Agent 应拒绝查询它(`disabled` 模式仍能 recall 其它 Track);
- 缺口的报告里增加 `contaminated_track_x` 让 Agent 知道为什么 evidence 为空;
- 这块当前没改,等模型接入后第一轮 pilot 看到 evidence empty 的 case 再调 prompt。

### 5.4 Trace 接口稳定性

`evidence_candidate_trace` 新增 `contaminated_excluded_evidence` 是非破坏性增量(其它字段不变)。

- `RetrievalHit` 没改 schema;
- `RetrievalRound.policy_candidate_trace / evidence_candidate_trace` 仅多了 `reason` 字段;
- 现有 21 个 retrieval / pipeline 测试未触发 schema 验证,所以无需做兼容性 shim。

## 6. 一句话总结

> 我们用 4 层防线把"Track 内别家农场的证据"在管线每一段都识别、隔离、可追溯,但把最终判断权完整留给 LLM 审计与仲裁,既不擅自整案填 N/A 也不抹掉原始证据,同时通过 `validate_citations` 与一致性检查保证 LLM 不能用污染证据误判合规。
