from __future__ import annotations

import os
import importlib.util
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from freca.config import PipelineConfig
from freca.state import atomic_write_json, read_json


def _read_or(path: Path, default):
    return read_json(path) if path.exists() else default


def _test_summary(path: Path) -> dict:
    if not path.exists():
        return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "available": False}
    root = ET.parse(path).getroot()
    suites = [root] if "tests" in root.attrib else list(root.findall(".//testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failed = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    return {
        "passed": tests - failed - errors - skipped,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "available": True,
    }


def write_verification_report(
    config: PipelineConfig,
    *,
    test_results_path: Path | None = None,
) -> dict:
    build = config.paths.build_dir
    manifest = _read_or(build / "manifests" / "cases.json", {"cases": [], "source_count": 0})
    ingest = _read_or(build / "parsed" / "ingest-report.json", {})
    index = _read_or(build / "indexes" / "index-report.json", {})
    wrong_case = 0
    retrieval_count = 0
    for path in sorted((build / "retrieval-smoke").glob("*/CP*.json")):
        retrieval = read_json(path)
        retrieval_count += 1
        wrong_case += sum(
            hit.get("chunk", {}).get("case_id") != retrieval.get("case_id")
            for hit in retrieval.get("evidence_hits", [])
        )

    endpoints = {
        name: endpoint
        for name, endpoint in config.models.model_dump().items()
        if endpoint is not None
    }
    missing_key_envs = sorted(
        endpoint["api_key_env"]
        for endpoint in endpoints.values()
        if not os.environ.get(endpoint["api_key_env"])
    )
    unresolved = {}
    data_quality_risks = {
        "organizer_submission_identifiers": {
            "policy": "flag_and_continue",
            "items": [
                "The task wording mentions both 96 and 100 cases.",
                "Cases 24 and 80 are missing Track 1.",
                "Cases 35 and 100 share an RE Number.",
                "Track 3 contains embedded RE Numbers that differ from case provenance.",
                "The submission template contains a header but no prefilled case rows.",
            ],
            "effect": (
                "All 100 logical cases continue through parsing, indexing, retrieval, and audit. "
                "The risks remain visible in provenance and do not create synthetic N/A verdicts."
            ),
            "observed_during_ingest": ingest.get("data_quality", {}),
        }
    }
    if not ingest.get("mineru_used"):
        unresolved["cloud_mineru_configuration"] = (
            f"MinerU mode is {config.mineru.mode.value}; the current policy artifact uses "
            "the page-preserving PyMuPDF fallback. Configure cloud_sdk/remote_api and rerun "
            "prepare before model audit."
        )
    if not ingest.get("vision_descriptions_enabled"):
        unresolved["vision_model"] = "Images are retained, but neutral VLM descriptions were not generated."
    if index.get("embedding_provider") == "local_hashing_fallback":
        unresolved["semantic_embedding"] = "Indexes use local hashing vectors until a semantic embedding endpoint is configured."
    if missing_key_envs:
        unresolved["model_credentials"] = {
            "missing_environment_variables": missing_key_envs,
            "effect": "Audit, verifier, query rewriting, vision, semantic embedding, and arbitration calls remain blocked as applicable.",
        }

    tests_path = test_results_path or (build / "test-results.xml")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verified": {
            "cases": len(manifest.get("cases", [])),
            "sources": manifest.get("source_count", ingest.get("source_count", 0)),
            "policy_chunks": index.get("policy_chunks", ingest.get("policy_chunks", 0)),
            "case_chunks": index.get("case_chunks", 0),
            "total_chunks": ingest.get("total_chunks", 0),
            "parse_failures": len(ingest.get("failures", [])),
            "retrieval_smoke_tasks": retrieval_count,
            "cross_case_retrieval_hits": wrong_case,
        },
        "tests": _test_summary(tests_path),
        "data_quality_policy": "flag_and_continue",
        "data_quality_risks": data_quality_risks,
        "runtime": {
            "mineru_mode": config.mineru.mode.value,
            "mineru_sdk_installed": importlib.util.find_spec("mineru") is not None,
            "embedding_provider": index.get("embedding_provider"),
        },
        "unresolved_blockers": unresolved,
        "rerun_commands": [
            ".\\.venv\\Scripts\\python.exe -m pytest -q --junitxml=build/test-results-runtime.xml",
            ".\\.venv\\Scripts\\python.exe -m freca.cli --config config.yaml doctor --stage prepare",
            ".\\.venv\\Scripts\\python.exe -m freca.cli --config config.yaml prepare",
            ".\\.venv\\Scripts\\python.exe -m freca.cli --config config.yaml doctor --stage pilot",
            ".\\.venv\\Scripts\\python.exe -m freca.cli --config config.yaml pilot --pilot-file pilot_cases.json --max-workers 2",
            ".\\.venv\\Scripts\\python.exe -m freca.cli --config config.yaml full --run-id full-001 --max-workers 4 --allow-unconfirmed-identifiers",
        ],
    }
    atomic_write_json(build / "verification_report.json", report)
    return report
