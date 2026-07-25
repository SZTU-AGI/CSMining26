from __future__ import annotations

from pathlib import Path

from freca.cli import build_parser, main
from freca.config import (
    ModelEndpointConfig,
    ModelsConfig,
    PathsConfig,
    PipelineConfig,
    RetrievalAgentMode,
    RetrievalConfig,
)
from freca.models import AuditTask, TaskStatus
from freca.runtime import check_readiness
from freca.state import TaskStore


def _pipeline_config(tmp_path: Path) -> PipelineConfig:
    for name in ("cases",):
        (tmp_path / name).mkdir()
    for name in ("policy.pdf", "cp.xlsx", "submission.xlsx"):
        (tmp_path / name).write_bytes(b"fixture")
    endpoint = ModelEndpointConfig(
        base_url="https://models.example/v1",
        model="model",
        api_key_env="FRECA_RUNTIME_TEST_KEY",
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


def test_readiness_reports_missing_model_key_without_exposing_secret(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("FRECA_RUNTIME_TEST_KEY", raising=False)

    report = check_readiness(_pipeline_config(tmp_path), stage="pilot")

    assert report["ready"] is False
    issue = next(item for item in report["checks"] if item["name"] == "model:audit")
    assert issue["status"] == "ERROR"
    assert "FRECA_RUNTIME_TEST_KEY" in issue["detail"]


def test_task_store_selectively_resets_blocked_and_failed_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    store.initialize(
        [
            AuditTask(
                task_id=f"run:case-001:{cp}",
                run_id="run",
                case_id=1,
                cp_id=cp,
                status=status,
                error="old error",
            )
            for cp, status in (
                ("CP1", TaskStatus.BLOCKED),
                ("CP2", TaskStatus.FAILED),
                ("CP3", TaskStatus.COMPLETED),
            )
        ]
    )

    reset = store.reset(
        statuses={TaskStatus.BLOCKED, TaskStatus.FAILED},
        cp_ids={"CP1"},
    )

    assert reset == 1
    assert store.get("run:case-001:CP1").status == TaskStatus.PENDING
    assert store.get("run:case-001:CP1").error is None
    assert store.get("run:case-001:CP2").status == TaskStatus.FAILED
    assert store.get("run:case-001:CP3").status == TaskStatus.COMPLETED


def test_cli_exposes_doctor_and_retry_commands() -> None:
    help_text = build_parser().format_help()

    assert "doctor" in help_text
    assert "retry" in help_text


def test_cli_exposes_all_ablation_operations() -> None:
    parser = build_parser()

    assert parser.parse_args(["ablation", "list"]).ablation_action == "list"
    run = parser.parse_args(
        [
            "ablation",
            "run",
            "--experiment-id",
            "smoke",
            "--variant",
            "bm25_only",
            "--case-id",
            "1",
            "--cp-id",
            "CP1",
        ]
    )
    assert run.ablation_action == "run"
    assert parser.parse_args(
        ["ablation", "report", "--experiment-id", "smoke"]
    ).ablation_action == "report"


def test_readiness_requires_endpoint_for_active_llm_retrieval_agent(tmp_path: Path) -> None:
    config = _pipeline_config(tmp_path).model_copy(
        update={
            "retrieval": RetrievalConfig(agent_mode=RetrievalAgentMode.LLM),
        }
    )

    report = check_readiness(config, stage="pilot")

    issue = next(
        item for item in report["checks"] if item["name"] == "model:retrieval_agent"
    )
    assert issue["status"] == "ERROR"
