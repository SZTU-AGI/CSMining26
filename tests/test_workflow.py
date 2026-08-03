from __future__ import annotations

import json
from pathlib import Path

import pytest

from freca.cli import build_parser
from freca.config import ModelEndpointConfig, ModelsConfig, PathsConfig, PipelineConfig
from freca.models import PipelineRunSummary
from freca.workflow import load_pilot_spec, prepare_workflow, run_full_workflow, run_pilot_workflow


def _config(tmp_path: Path) -> PipelineConfig:
    endpoint = ModelEndpointConfig(
        base_url="https://models.example/v1",
        model="model",
        api_key_env="KEY",
    )
    return PipelineConfig(
        paths=PathsConfig(
            cases_root=tmp_path / "cases",
            policy_pdf=tmp_path / "policy.pdf",
            checkpoints_xlsx=tmp_path / "cp.xlsx",
            submission_template=tmp_path / "submission.xlsx",
            build_dir=tmp_path / "build",
        ),
        models=ModelsConfig(audit=endpoint, verifier=endpoint, arbitrator=endpoint),
    )


def test_loads_project_pilot_as_exactly_369_tasks() -> None:
    path = Path(__file__).parents[1] / "pilot_cases.json"

    spec = load_pilot_spec(path)

    assert len(spec.case_ids) == 9
    assert len(spec.cp_ids) == 41
    assert spec.task_count == 369


def test_rejects_incorrect_pilot_task_count(tmp_path: Path) -> None:
    path = tmp_path / "pilot.json"
    path.write_text(
        json.dumps({"run_id": "bad", "case_ids": [1], "cp_ids": ["CP1"], "task_count": 2}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="task_count"):
        load_pilot_spec(path)


def test_pilot_stops_before_model_calls_when_doctor_is_not_ready(
    tmp_path: Path, monkeypatch
) -> None:
    called = False

    def audit(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "freca.workflow.check_readiness",
        lambda config, stage: {"stage": stage, "ready": False, "checks": []},
    )
    monkeypatch.setattr("freca.workflow.run_audit_tasks", audit)

    report = run_pilot_workflow(
        _config(tmp_path), Path(__file__).parents[1] / "pilot_cases.json"
    )

    assert report["status"] == "BLOCKED"
    assert called is False


def test_prepare_runs_integrity_gate_after_successful_ingest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "freca.workflow.check_readiness",
        lambda config, stage: {"stage": stage, "ready": True, "checks": []},
    )
    monkeypatch.setattr("freca.workflow.write_manifest", lambda config: {"cases": 100})
    monkeypatch.setattr("freca.workflow.ingest_sources", lambda config, **kwargs: {"failures": []})
    monkeypatch.setattr(
        "freca.workflow.run_evidence_integrity_gate",
        lambda build_dir: {"summary": {"BLOCKER": 2, "REVIEW": 3, "PASS": 95}},
    )
    monkeypatch.setattr("freca.workflow.build_hybrid_indexes", lambda config: {"policy": 132})

    report = prepare_workflow(_config(tmp_path))

    assert report["status"] == "COMPLETED"
    assert report["integrity"]["summary"]["BLOCKER"] == 2


def test_full_workflow_assembles_only_after_audit_and_consistency_pass(
    tmp_path: Path, monkeypatch
) -> None:
    pilot_report = tmp_path / "build" / "runs" / "pilot-001.json"
    pilot_report.parent.mkdir(parents=True)
    pilot_report.write_text(
        json.dumps({"status": "COMPLETED", "promotion_ready": True}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "freca.workflow.check_readiness",
        lambda config, stage: {"stage": stage, "ready": True, "checks": []},
    )
    monkeypatch.setattr(
        "freca.workflow.run_audit_tasks",
        lambda *args, **kwargs: PipelineRunSummary(
            total=4100, pending=0, running=0, completed=4100, blocked=0, failed=0
        ),
    )
    monkeypatch.setattr(
        "freca.workflow.run_consistency_gate",
        lambda *args, **kwargs: {"finding_count": 0},
    )

    class Submission:
        def model_dump(self, mode="json"):
            return {"output_path": "submission.xlsx"}

    monkeypatch.setattr(
        "freca.workflow.assemble_run_submission", lambda *args, **kwargs: Submission()
    )

    report = run_full_workflow(_config(tmp_path), run_id="full-001")

    assert report["status"] == "COMPLETED"
    assert report["audit"]["total"] == 4100
    assert report["submission"]["output_path"] == "submission.xlsx"


def test_full_workflow_requires_successful_pilot_promotion(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "freca.workflow.check_readiness",
        lambda config, stage: {"stage": stage, "ready": True, "checks": []},
    )

    report = run_full_workflow(_config(tmp_path), run_id="full-001")

    assert report["status"] == "BLOCKED"
    assert report["blocker"] == "pilot_not_promoted"


def test_cli_exposes_prepare_pilot_and_full_workflows() -> None:
    help_text = build_parser().format_help()

    assert "prepare" in help_text
    assert "pilot" in help_text
    assert "full" in help_text
