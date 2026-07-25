# 已确认的实施决策

更新日期：2026-07-20

1. Python 环境使用项目目录下的 `.venv`，不修改系统或共享 Python 环境。
2. 不安装本地 MinerU。当前保留页码的 PyMuPDF 结果用于工程验证；后续提供云端参数后接入 MinerU API，并重新生成法规解析产物和索引。
3. 审计、Verifier、仲裁、查询改写、Embedding 和视觉模型均通过 `config.yaml` 配置；密钥只从环境变量读取，不写入仓库或日志。
4. 先运行 `pilot_cases.json` 中的 9 个案例、全部 41 个 CP，共 369 个任务。通过门禁后再运行剩余案例。
5. 96/100 口径冲突、case 24/80 缺 Track 1、case 35/100 共用 RE Number、Track 3 内部编号错位和模板仅有表头，统一按数据质量问题处理：首次清洗时显式标记，但不筛除案例、不修正原始证据、不自动填 `N/A`，并继续进入检索和逐 CP 审计。
6. 数据质量问题不阻断解析、索引、检索和审计；正式提交文件仍受 4,100 项完整性、引用、Verifier、一致性和候选表结构门禁约束。
7. **Track 内污染证据处理**：用户调研表（`文件署名整理表_v2(1).xlsx`）表明多数 case 的 Track 3/5/9 携带别家农场的 establishment name 与 RE Number。处理口径：
   - 通过 `paths.signature_truth_xlsx` 把调研表接入构建管线，落到 `CaseRecord.contaminated_tracks`；
   - 解析阶段通过 `freca.signatures.annotate_chunks` 给污染 Track 的 chunk 标记 `exclude_from_compliance_evidence`；
   - `HybridIndex.search` 默认不把这类 chunk 放进 `evidence_hits`，但保留 `trace` 记录供审计模型与 Verifier 透明可见；
   - 裁决 prompt 增加污染语义：不能把污染 chunk 当 sole supporting；若该 CP 唯一可引用证据是污染 chunk → 自动判 `0` 并加 `signature_foreign_evidence_only` flag；
   - `validate_citations` 拒绝污染 chunk 进入 `supporting_evidence`；
   - `find_signature_consistency_issues` 检查 `shared_facts[_establishment_name]` 与 `CaseRecord.expected_establishment_name`（从 Track 1 抽出）是否冲突，触发仲裁。

## MinerU 云端接入边界

接入时优先采用官方 `mineru-open-sdk`，通过 `MINERU_TOKEN` 环境变量提供 token。若提供的是自建 `mineru-api` 地址，则使用 `/file_parse` 远程接口。两种模式现已通过统一适配器归一为带页码的 `content_list`，切换时只修改 `config.yaml`。

## 污染证据汇总数据来源

`build/diagnostics/user_signature_truth_with_case_id.json` 是 ground truth，反查到 case_id 后用于：

* `build/manifests/cases.json` 的 `contaminated_tracks` 与 `flags`；
* `build/parsed/ingest-report.json` 的 `contaminated_chunk_counts` 与 `signature_summary`；
* 每条候选裁决的 `shared_facts` 与 `review_flags`。

诊断脚本：再跑一遍可直接调 `freca.signatures.SignatureTruthLoader().load(path)`。
