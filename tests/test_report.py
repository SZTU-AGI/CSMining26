from pathlib import Path

from freca.config import ModelEndpointConfig, ModelsConfig, PathsConfig, PipelineConfig
from freca.report import write_verification_report
from freca.state import atomic_write_json


def test_verification_report_summarizes_local_evidence_and_external_blockers(
    tmp_path: Path,
) -> None:
    build = tmp_path / "build"
    atomic_write_json(
        build / "manifests" / "cases.json",
        {"cases": [{"case_id": case_id} for case_id in range(1, 101)], "source_count": 898},
    )
    atomic_write_json(
        build / "parsed" / "ingest-report.json",
        {
            "source_count": 898,
            "policy_chunks": 132,
            "total_chunks": 25252,
            "failures": [],
            "mineru_used": False,
            "vision_descriptions_enabled": False,
        },
    )
    atomic_write_json(
        build / "indexes" / "index-report.json",
        {"policy_chunks": 132, "case_chunks": 25120, "embedding_provider": "local_hashing_fallback"},
    )
    atomic_write_json(
        build / "retrieval-smoke" / "035" / "CP17.json",
        {
            "case_id": 35,
            "cp_id": "CP17",
            "complete": True,
            "evidence_hits": [{"chunk": {"case_id": 35}}],
        },
    )
    test_xml = build / "test-results.xml"
    test_xml.parent.mkdir(parents=True, exist_ok=True)
    test_xml.write_text(
        '<testsuites><testsuite name="pytest" tests="45" failures="0" errors="0" skipped="0" time="1.0"/></testsuites>',
        encoding="utf-8",
    )
    endpoint = ModelEndpointConfig(
        base_url="https://example.invalid/v1",
        model="model",
        api_key_env="FRECA_MISSING_TEST_KEY",
    )
    config = PipelineConfig(
        paths=PathsConfig(
            cases_root=tmp_path,
            policy_pdf=tmp_path / "policy.pdf",
            checkpoints_xlsx=tmp_path / "cp.xlsx",
            submission_template=tmp_path / "submission.xlsx",
            build_dir=build,
        ),
        models=ModelsConfig(audit=endpoint),
    )

    report = write_verification_report(config, test_results_path=test_xml)

    assert report["verified"]["cases"] == 100
    assert report["verified"]["sources"] == 898
    assert report["verified"]["cross_case_retrieval_hits"] == 0
    assert report["tests"]["passed"] == 45
    assert "model_credentials" in report["unresolved_blockers"]
    assert "organizer_submission_identifiers" not in report["unresolved_blockers"]
    assert report["data_quality_policy"] == "flag_and_continue"
    assert "organizer_submission_identifiers" in report["data_quality_risks"]
    assert report["runtime"]["mineru_mode"] == "disabled"
    assert (build / "verification_report.json").exists()
