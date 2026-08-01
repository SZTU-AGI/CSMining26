"""Resume helper: re-run only the unit indices whose result.json is missing.

Both STAGE_AUDIT and AGENT_AUDIT write ``cp-{index:03d}/result.json`` where
``index`` is the *positional* index in the plan_units list passed to their
``run_*_plan`` runners. That means a naive "pass only the missing units" call
would renumber them from 0 and **overwrite the wrong cp-NNN dirs** - a real bug
that corrupted an earlier resume attempt.

This script avoids that by calling the per-unit runners
(``run_stage_audit_unit`` / ``run_agent_audit_unit``) directly and passing the
*original* positional index as ``artifact_dir`` so each re-run lands at the
correct ``cp-NNN`` path. It also recomputes the per-method ``summary.json`` so
the scoreboard reflects the now-complete run.

Usage:
    PYTHONPATH=src python scripts/resume_run.py --cases 1 \\
        --methods stage_audit,agent_audit --model MiniMax-M3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(WORKTREE_SRC))

from freca.config import PipelineConfig
from freca.cp import load_checkpoints
from freca.env_loader import find_env_file, apply_env_file
from freca.experiments.agent_audit import run_agent_audit_unit
from freca.experiments.materials import load_material_snapshot_from_parsed
from freca.experiments.models import ExperimentMethod, Track3Condition
from freca.experiments.planning import build_execution_plan
from freca.experiments.stage_audit import run_stage_audit_unit
from freca.llm import build_audit_client
from freca.state import atomic_write_json

UNIT_RUNNERS = {
    ExperimentMethod.STAGE_AUDIT: run_stage_audit_unit,
    ExperimentMethod.AGENT_AUDIT: run_agent_audit_unit,
}


class _PacingClient:
    """Pause briefly after every call (see scripts/pilot_4x4.py)."""

    def __init__(self, inner: object, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    def complete_json(self, **kwargs: object) -> dict[str, object]:
        try:
            return self._inner.complete_json(**kwargs)  # type: ignore[attr-defined]
        finally:
            if self._delay:
                time.sleep(self._delay)

    def complete_json_with_images(self, **kwargs: object) -> dict[str, object]:
        try:
            return self._inner.complete_json_with_images(**kwargs)  # type: ignore[attr-defined]
        finally:
            if self._delay:
                time.sleep(self._delay)

    @property
    def last_usage(self) -> dict[str, int] | None:
        return getattr(self._inner, "last_usage", None)


def _missing_indices(method: ExperimentMethod, case_id: int, total: int, root: Path) -> list[int]:
    """Return positional indices whose cp-NNN/result.json is missing."""
    case_dir = root / method.value / f"case-{case_id:03d}" / "track3-raw"
    missing: list[int] = []
    for idx in range(total):
        if not (case_dir / f"cp-{idx:03d}" / "result.json").exists():
            missing.append(idx)
    return missing


def _rewrite_summary(method: ExperimentMethod, case_id: int, root: Path) -> None:
    """Recompute summary.json from the on-disk result.json files."""
    case_dir = root / method.value / f"case-{case_id:03d}" / "track3-raw"
    valid = 0
    verdicts = 0
    total = 0
    for cp_dir in sorted(case_dir.glob("cp-*")):
        rj = cp_dir / "result.json"
        if not rj.exists():
            continue
        total += 1
        data = json.loads(rj.read_text(encoding="utf-8"))
        if data.get("valid"):
            valid += 1
        verdicts += len(data.get("verdicts", []))
    summary = {
        "method": method.value,
        "case_id": case_id,
        "track3_condition": Track3Condition.RAW.value,
        "units_total": total,
        "units_valid": valid,
        "verdicts_total": verdicts,
    }
    atomic_write_json(root / method.value / "summary.json", summary)


def _run_resume(
    *,
    method: ExperimentMethod,
    case_id: int,
    checkpoints: object,
    parsed_dir: Path,
    client: _PacingClient,
    artifact_root: Path,
    uncertainty_threshold: float,
    limit: int = 0,
) -> dict[str, object]:
    plan = build_execution_plan(method, case_id=case_id, checkpoints=checkpoints)
    case_dir = artifact_root / method.value / f"case-{case_id:03d}" / "track3-raw"
    missing = _missing_indices(method, case_id, total=len(plan.units), root=artifact_root)
    if limit > 0:
        missing = missing[:limit]
    if not missing:
        _rewrite_summary(method, case_id, artifact_root)
        return {"method": method.value, "case_id": case_id, "missing": [], "ran": 0}

    material = load_material_snapshot_from_parsed(
        parsed_dir=parsed_dir,
        case_id=case_id,
        checkpoints=list(checkpoints),
        track3_condition=Track3Condition.RAW,
    )
    runner = UNIT_RUNNERS[method]
    started = time.monotonic()
    results: list = []
    for original_idx in missing:
        unit = plan.units[original_idx]
        # Critical: use the ORIGINAL index, not enumerate(), so the re-run lands
        # at cp-<original_idx> and never collides with already-completed units.
        unit_dir = case_dir / f"cp-{original_idx:03d}"
        kwargs: dict = {
            "unit": unit,
            "material": material,
            "client": client,
            "artifact_dir": unit_dir,
        }
        if method == ExperimentMethod.AGENT_AUDIT:
            kwargs["uncertainty_threshold"] = uncertainty_threshold
        result = runner(**kwargs)
        results.append(result)
        print(
            json.dumps(
                {"cp": f"cp-{original_idx:03d}", "valid": result.valid},
                ensure_ascii=False,
            ),
            flush=True,
        )
    elapsed = time.monotonic() - started
    _rewrite_summary(method, case_id, artifact_root)
    return {
        "method": method.value,
        "case_id": case_id,
        "missing": missing,
        "ran": len(results),
        "valid": sum(1 for r in results if r.valid),
        "elapsed_seconds": round(elapsed, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="1", help="comma-separated case ids")
    parser.add_argument(
        "--methods",
        default="stage_audit,agent_audit",
        help="comma-separated methods to resume (default: stage_audit,agent_audit)",
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("build/experiments"),
    )
    parser.add_argument(
        "--inter-call-delay",
        type=float,
        default=1.5,
        help="seconds between API calls (pacing to avoid 429s)",
    )
    parser.add_argument(
        "--model",
        default="MiniMax-M3",
        help="MiniMax model name to override config.yaml's audit.model",
    )
    parser.add_argument(
        "--uncertainty-threshold",
        type=float,
        default=0.5,
        help="AGENT_AUDIT low-confidence trigger threshold",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after resuming this many units (0 = all missing); useful for smoke-testing one CP",
    )
    args = parser.parse_args()

    env_file = find_env_file()
    if env_file is not None:
        apply_env_file(env_file)
    os.environ.setdefault("FRECA_AUDIT_BASE_URL", "https://api.minimaxi.com/anthropic")

    case_ids = [int(v) for v in args.cases.split(",") if v.strip()]
    methods = [ExperimentMethod(v) for v in args.methods.split(",") if v.strip()]
    for m in methods:
        if m not in UNIT_RUNNERS:
            raise SystemExit(f"resume not implemented for method {m.value}")

    config = PipelineConfig.from_yaml(args.config)
    checkpoints = load_checkpoints(config.paths.checkpoints_xlsx)
    object.__setattr__(
        config.models,
        "audit",
        config.models.audit.model_copy(update={"model": args.model}),
    )
    client = _PacingClient(build_audit_client(config.models.audit), delay=args.inter_call_delay)
    parsed_dir = config.paths.build_dir / "parsed"

    for method in methods:
        for case_id in case_ids:
            entry = _run_resume(
                method=method,
                case_id=case_id,
                checkpoints=checkpoints,
                parsed_dir=parsed_dir,
                client=client,
                artifact_root=args.artifact_root,
                uncertainty_threshold=args.uncertainty_threshold,
                limit=args.limit,
            )
            print(json.dumps(entry, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())