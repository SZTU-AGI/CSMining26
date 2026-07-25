from pathlib import Path

import pytest

from freca.config import PipelineConfig
from freca.models import (
    Applicability,
    AuditDecision,
    CaseRecord,
    SourceRecord,
    SourceType,
    Verdict,
)


def test_case_id_is_primary_key_and_re_number_can_repeat(tmp_path: Path) -> None:
    source_35 = SourceRecord(
        source_id="case-035-t1",
        case_id=35,
        track=1,
        re_number="RE-WA-2021-0077",
        path=tmp_path / "35.docx",
        source_type=SourceType.DOCX,
        sha256="a" * 64,
    )
    source_100 = source_35.model_copy(
        update={"source_id": "case-100-t1", "case_id": 100, "path": tmp_path / "100.docx"}
    )

    case_35 = CaseRecord(case_id=35, re_number="RE-WA-2021-0077", sources=[source_35])
    case_100 = CaseRecord(case_id=100, re_number="RE-WA-2021-0077", sources=[source_100])

    assert case_35.case_id != case_100.case_id
    assert case_35.re_number == case_100.re_number


def test_na_requires_not_applicable_and_policy_support() -> None:
    with pytest.raises(ValueError, match="N/A requires NOT_APPLICABLE"):
        AuditDecision(
            case_id=1,
            cp_id="CP1",
            applicability=Applicability.UNKNOWN,
            regulatory_requirement="Registration evidence must be assessed.",
            policy_citations=["policy:p001:block-01"],
            supporting_evidence=[],
            contrary_evidence=[],
            contradictions=[],
            verdict=Verdict.NOT_APPLICABLE,
            reasoning_summary="The parser failed.",
            confidence=0.2,
            retrieval_complete=False,
            review_flags=["parser_failed"],
        )


def test_config_resolves_relative_paths_and_references_secret_by_name(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
paths:
  cases_root: data/cases
  policy_pdf: data/policy.pdf
  checkpoints_xlsx: data/cp.xlsx
  submission_template: data/submission.xlsx
  build_dir: build
models:
  audit:
    base_url: https://example.invalid/v1
    model: audit-model
    api_key_env: FRECA_AUDIT_API_KEY
""".strip(),
        encoding="utf-8",
    )

    config = PipelineConfig.from_yaml(config_path)

    assert config.paths.cases_root == tmp_path / "data" / "cases"
    assert config.paths.build_dir == tmp_path / "build"
    assert config.models.audit.api_key_env == "FRECA_AUDIT_API_KEY"
    assert "api_key" not in config.models.audit.model_dump()
