"""污染证据场景的回归测试。

覆盖:

* ``freca.signatures.SignatureTruthLoader`` 解析用户表 → ``ContaminatedCaseIndex``;
* ``build_manifest`` 注入污染 Track,把 flag 与 ``contaminated_tracks`` 字段写进 ``CaseRecord``;
* ``HybridIndex.search`` 不把污染 chunk 放进 ``evidence_hits``,但写到 trace;
* ``annotate_chunks`` 给污染 chunk 加 ``exclude_from_compliance_evidence``;
* ``validate_citations`` 拒绝用污染 chunk 当 supporting;
* ``find_signature_consistency_issues`` 在 manifest ``expected_establishment_name``
  与 ``shared_facts[_establishment_name]`` 不一致时产一致性告警。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from freca.config import (
    FusionMode,
    RecallMode,
    RerankerMode,
    RetrievalConfig,
    SelectorMode,
)
from freca.index import HybridIndex
from freca.models import (
    Applicability,
    AuditDecision,
    CaseRecord,
    ContentKind,
    EvidenceChunk,
    RetrievalBundle,
    RetrievalHit,
    RetrievalRound,
    SourceLocation,
    SourceRecord,
    SourceType,
    Verdict,
)
from freca.quality import (
    find_signature_consistency_issues,
    validate_citations,
)
from freca.signatures import (
    ContaminatedCaseIndex,
    SignatureTruthLoader,
    annotate_chunks,
    merge_into_case_record,
)


def _build_truth() -> dict[str, ContaminatedCaseIndex]:
    index = ContaminatedCaseIndex(
        re_number="RE-NSW-2020-0088",
        expected_name="Gunnedah Grain Exports Pty Ltd",
    )
    index.contaminated[2] = "foreign_farm"
    index.contaminated[3] = "foreign_farm"
    index.contaminated[9] = "foreign_farm"
    return {index.re_number: index}


def test_signature_truth_loader_parses_xlsx(tmp_path: Path) -> None:
    """xlsx 不在仓库中,直接在 here-doc 里手写一份最小 zip 并解析。"""
    import zipfile

    xlsx = tmp_path / "truth.xlsx"
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="S1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        '<row><c t="inlineStr"><is><t>case_id</t></is></c>'
        '<c t="inlineStr"><is><t>track</t></is></c>'
        '<c t="inlineStr"><is><t>filename</t></is></c>'
        '<c t="inlineStr"><is><t>actual</t></is></c>'
        '<c t="inlineStr"><is><t>expected</t></is></c>'
        '<c t="inlineStr"><is><t>relation</t></is></c></row>'
        '<row><c t="inlineStr"><is><t>RE-X-2020-0001</t></is></c>'
        '<c t="inlineStr"><is><t>T1_Registration</t></is></c>'
        '<c t="inlineStr"><is><t>1.docx</t></is></c>'
        '<c t="inlineStr"><is><t>Self</t></is></c>'
        '<c t="inlineStr"><is><t>Self</t></is></c>'
        '<c t="inlineStr"><is><t>一致</t></is></c></row>'
        '<row><c t="inlineStr"><is><t>RE-X-2020-0001</t></is></c>'
        '<c t="inlineStr"><is><t>T3_Pest</t></is></c>'
        '<c t="inlineStr"><is><t>3.xlsx</t></is></c>'
        '<c t="inlineStr"><is><t>Other Farm</t></is></c>'
        '<c t="inlineStr"><is><t>Self</t></is></c>'
        '<c t="inlineStr"><is><t>外农</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(xlsx, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    truth = SignatureTruthLoader().load(xlsx)
    assert "RE-X-2020-0001" in truth
    index = truth["RE-X-2020-0001"]
    assert index.expected_name == "Self"
    assert index.contaminated == {3: "foreign_farm"}
    assert index.is_foreign is True


def test_merge_into_case_record_adds_contaminated_tracks() -> None:
    case = CaseRecord(
        case_id=74,
        re_number="RE-NSW-2020-0088",
        sources=[
            SourceRecord(
                source_id="case-074-t1",
                case_id=74,
                track=1,
                re_number="RE-NSW-2020-0088",
                path=Path("/tmp/1.docx"),
                source_type=SourceType.DOCX,
                sha256="a" * 64,
            )
        ],
    )
    truth = _build_truth()
    updated = merge_into_case_record(case, truth)
    assert updated.contaminated_tracks == {2: "foreign_farm", 3: "foreign_farm", 9: "foreign_farm"}
    assert any(flag.startswith("track_contaminated:") for flag in updated.flags)
    assert updated.expected_establishment_name == "Gunnedah Grain Exports Pty Ltd"


def test_annotate_chunks_marks_contaminated_tracks() -> None:
    case = CaseRecord(
        case_id=74,
        re_number="RE-NSW-2020-0088",
        sources=[],
        contaminated_tracks={3: "foreign_farm"},
    )
    chunks = [
        EvidenceChunk(
            chunk_id="case-074-t3-1",
            case_id=74,
            track=3,
            source_id="case-074-t3",
            source_file="3.xlsx",
            source_type=SourceType.XLSX,
            location=SourceLocation(),
            content="foreign pest log",
            content_kind=ContentKind.TABLE,
            parser_name="test",
            parser_version="1",
            source_sha256="b" * 64,
        ),
        EvidenceChunk(
            chunk_id="case-074-t1-1",
            case_id=74,
            track=1,
            source_id="case-074-t1",
            source_file="1.docx",
            source_type=SourceType.DOCX,
            location=SourceLocation(),
            content="gunnedah registration",
            content_kind=ContentKind.PARAGRAPH,
            parser_name="test",
            parser_version="1",
            source_sha256="a" * 64,
        ),
    ]
    annotated = annotate_chunks(chunks, case)
    assert "exclude_from_compliance_evidence" in annotated[0].flags
    assert "exclude_from_compliance_evidence" not in annotated[1].flags
    assert annotated[0].metadata["track_contamination_relation"] == "foreign_farm"


def test_hybrid_index_excludes_contaminated_chunks_from_hits() -> None:
    chunks = [
        EvidenceChunk(
            chunk_id="case-074-t3-contam",
            case_id=74,
            track=3,
            source_id="case-074-t3",
            source_file="3.xlsx",
            source_type=SourceType.XLSX,
            location=SourceLocation(),
            content="Sunrise Canola pest log entry",
            content_kind=ContentKind.TABLE,
            parser_name="test",
            parser_version="1",
            source_sha256="b" * 64,
            flags=["exclude_from_compliance_evidence"],
        ),
        EvidenceChunk(
            chunk_id="case-074-t6-clean",
            case_id=74,
            track=6,
            source_id="case-074-t6",
            source_file="6.docx",
            source_type=SourceType.DOCX,
            location=SourceLocation(),
            content="Gunnedah hygiene procedure for grain storage",
            content_kind=ContentKind.PARAGRAPH,
            parser_name="test",
            parser_version="1",
            source_sha256="a" * 64,
        ),
    ]
    idx = HybridIndex(chunks, scope="case")
    trace: list[dict] = []
    cfg = RetrievalConfig(
        recall_mode=RecallMode.HYBRID,
        fusion_mode=FusionMode.RRF,
        reranker_mode=RerankerMode.LEXICAL,
        selector_mode=SelectorMode.TOP_K,
        candidate_limit=10,
    )
    hits = idx.search(
        "gunnedah pest storage procedure",
        case_id=74,
        limit=10,
        config=cfg,
        trace_sink=trace,
    )
    assert [hit.chunk.chunk_id for hit in hits] == ["case-074-t6-clean"]
    assert any(
        entry.get("reason") == "contaminated_excluded_evidence"
        for entry in trace
    )


def test_validate_citations_rejects_contaminated_support() -> None:
    contaminated = EvidenceChunk(
        chunk_id="case-074-t3-contam",
        case_id=74,
        track=3,
        source_id="case-074-t3",
        source_file="3.xlsx",
        source_type=SourceType.XLSX,
        location=SourceLocation(),
        content="Sunrise Canola pest log entry",
        content_kind=ContentKind.TABLE,
        parser_name="test",
        parser_version="1",
        source_sha256="b" * 64,
        flags=["exclude_from_compliance_evidence"],
    )
    hit = RetrievalHit(chunk=contaminated, score=1.0, rank=1, score_trace={})
    bundle = RetrievalBundle(
        case_id=74,
        cp_id="CP20",
        policy_hits=[],
        evidence_hits=[hit],
        rounds=[],
        complete=True,
        stop_reason="complete",
    )
    decision = AuditDecision(
        case_id=74,
        cp_id="CP20",
        applicability=Applicability.APPLICABLE,
        regulatory_requirement="Pest control must be documented by the registered establishment.",
        policy_citations=["policy:p001:s4-7A"],
        supporting_evidence=["case-074-t3-contam"],
        contrary_evidence=[],
        contradictions=[],
        verdict=Verdict.COMPLIANT,
        reasoning_summary="The pest log is present.",
        confidence=0.6,
        retrieval_complete=True,
    )
    result = validate_citations(decision, bundle)
    assert not result.passed
    assert any("contaminated" in err for err in result.errors)


def test_find_signature_consistency_issues_detects_name_conflict() -> None:
    case = CaseRecord(
        case_id=35,
        re_number="RE-WA-2021-0077",
        sources=[],
        metadata={"expected_establishment_name": "Goldfields Grain Storage"},
    )
    decisions = [
        AuditDecision(
            case_id=35,
            cp_id="CP1",
            applicability=Applicability.APPLICABLE,
            regulatory_requirement="r",
            policy_citations=["policy:p1"],
            supporting_evidence=["case-035-t1-1"],
            contrary_evidence=[],
            contradictions=[],
            verdict=Verdict.COMPLIANT,
            reasoning_summary="ok",
            confidence=0.7,
            retrieval_complete=True,
            shared_facts={"_establishment_name": "Mekong Fresh Produce"},
        ),
        AuditDecision(
            case_id=35,
            cp_id="CP17",
            applicability=Applicability.APPLICABLE,
            regulatory_requirement="r",
            policy_citations=["policy:p1"],
            supporting_evidence=["case-035-t3-1"],
            contrary_evidence=[],
            contradictions=[],
            verdict=Verdict.NON_COMPLIANT,
            reasoning_summary="nope",
            confidence=0.5,
            retrieval_complete=True,
            shared_facts={"_registered_commodity": "wheat"},
        ),
    ]
    findings = find_signature_consistency_issues(decisions, case)
    keys = {finding.fact_key for finding in findings}
    assert "_establishment_name_vs_case" in keys
