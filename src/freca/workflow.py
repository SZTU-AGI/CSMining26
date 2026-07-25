from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from freca.config import PipelineConfig
from freca.pipeline import (
    assemble_run_submission,
    build_hybrid_indexes,
    ingest_sources,
    run_audit_tasks,
    run_consistency_gate,
    write_manifest,
)
from freca.runtime import check_readiness
from freca.state import atomic_write_json, read_json


class PilotSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    case_ids: list[int]
    cp_ids: list[str]
    task_count: int


def load_pilot_spec(path: Path) -> PilotSpec:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cp_ids = raw.get("cp_ids")
    if cp_ids == "ALL":
        cp_ids = [f"CP{index}" for index in range(1, 42)]
    if not isinstance(cp_ids, list) or not cp_ids:
        raise ValueError("pilot cp_ids must be ALL or a non-empty list")
    case_ids = raw.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids:
        raise ValueError("pilot case_ids must be a non-empty list")
    if len(case_ids) != len(set(case_ids)) or any(
        not isinstance(case_id, int) or not 1 <= case_id <= 100 for case_id in case_ids
    ):
        raise ValueError("pilot case_ids must be unique integers from 1 to 100")
    expected_cp_ids = {f"CP{index}" for index in range(1, 42)}
    if len(cp_ids) != len(set(cp_ids)) or any(cp_id not in expected_cp_ids for cp_id in cp_ids):
        raise ValueError("pilot cp_ids contain duplicates or unknown checking points")
    expected_count = len(case_ids) * len(cp_ids)
    if raw.get("task_count") != expected_count:
        raise ValueError(
            f"pilot task_count is {raw.get('task_count')}, expected {expected_count}"
        )
    return PilotSpec(
        run_id=raw["run_id"],
        case_ids=case_ids,
        cp_ids=cp_ids,
        task_count=expected_count,
    )


def _write_run_report(config: PipelineConfig, name: str, report: dict) -> dict:
    atomic_write_json(config.paths.build_dir / "runs" / f"{name}.json", report)
    return report


def prepare_workflow(config: PipelineConfig, *, disable_mineru: bool = False) -> dict:
    readiness = check_readiness(config, stage="prepare")
    if not readiness["ready"]:
        return _write_run_report(
            config,
            "prepare",
            {"workflow": "prepare", "status": "BLOCKED", "readiness": readiness},
        )
    manifest = write_manifest(config)
    ingest = ingest_sources(config, disable_mineru=disable_mineru)
    if ingest["failures"]:
        return _write_run_report(
            config,
            "prepare",
            {
                "workflow": "prepare",
                "status": "FAILED",
                "readiness": readiness,
                "manifest": manifest,
                "ingest": ingest,
            },
        )
    index = build_hybrid_indexes(config)
    return _write_run_report(
        config,
        "prepare",
        {
            "workflow": "prepare",
            "status": "COMPLETED",
            "readiness": readiness,
            "manifest": manifest,
            "ingest": ingest,
            "index": index,
        },
    )


def run_pilot_workflow(
    config: PipelineConfig,
    pilot_path: Path,
    *,
    run_id: str | None = None,
    max_workers: int = 2,
) -> dict:
    spec = load_pilot_spec(pilot_path)
    selected_run_id = run_id or spec.run_id
    readiness = check_readiness(config, stage="pilot")
    report: dict = {
        "workflow": "pilot",
        "run_id": selected_run_id,
        "case_ids": spec.case_ids,
        "cp_ids": spec.cp_ids,
        "task_count": spec.task_count,
        "readiness": readiness,
    }
    if not readiness["ready"]:
        report["status"] = "BLOCKED"
        return _write_run_report(config, selected_run_id, report)
    summary = run_audit_tasks(
        config,
        run_id=selected_run_id,
        case_ids=spec.case_ids,
        cp_ids=spec.cp_ids,
        max_workers=max_workers,
    )
    report["audit"] = summary.model_dump(mode="json")
    if summary.blocked or summary.failed or summary.pending or summary.running:
        report["status"] = "BLOCKED"
        report["promotion_ready"] = False
        return _write_run_report(config, selected_run_id, report)
    consistency = run_consistency_gate(config.paths.build_dir, run_id=selected_run_id)
    report["consistency"] = consistency
    report["promotion_ready"] = consistency["finding_count"] == 0
    report["status"] = "COMPLETED" if report["promotion_ready"] else "BLOCKED"
    return _write_run_report(config, selected_run_id, report)


def run_full_workflow(
    config: PipelineConfig,
    *,
    run_id: str,
    max_workers: int = 4,
    allow_unconfirmed_identifiers: bool = False,
    pilot_report_path: Path | None = None,
) -> dict:
    readiness = check_readiness(config, stage="full")
    report: dict = {"workflow": "full", "run_id": run_id, "readiness": readiness}
    if not readiness["ready"]:
        report["status"] = "BLOCKED"
        return _write_run_report(config, run_id, report)
    promotion_path = pilot_report_path or (
        config.paths.build_dir / "runs" / "pilot-001.json"
    )
    if not promotion_path.exists():
        report.update(
            {
                "status": "BLOCKED",
                "blocker": "pilot_not_promoted",
                "pilot_report": str(promotion_path),
            }
        )
        return _write_run_report(config, run_id, report)
    pilot_report = read_json(promotion_path)
    if pilot_report.get("status") != "COMPLETED" or not pilot_report.get(
        "promotion_ready"
    ):
        report.update(
            {
                "status": "BLOCKED",
                "blocker": "pilot_not_promoted",
                "pilot_report": str(promotion_path),
            }
        )
        return _write_run_report(config, run_id, report)
    report["pilot_report"] = str(promotion_path)
    summary = run_audit_tasks(config, run_id=run_id, max_workers=max_workers)
    report["audit"] = summary.model_dump(mode="json")
    if summary.total != 4100 or summary.blocked or summary.failed or summary.pending or summary.running:
        report["status"] = "BLOCKED"
        return _write_run_report(config, run_id, report)
    consistency = run_consistency_gate(config.paths.build_dir, run_id=run_id)
    report["consistency"] = consistency
    if consistency["finding_count"]:
        report["status"] = "BLOCKED"
        return _write_run_report(config, run_id, report)
    submission = assemble_run_submission(
        config,
        run_id=run_id,
        allow_unconfirmed_identifiers=allow_unconfirmed_identifiers,
    )
    report["submission"] = submission.model_dump(mode="json")
    report["status"] = "COMPLETED"
    return _write_run_report(config, run_id, report)
