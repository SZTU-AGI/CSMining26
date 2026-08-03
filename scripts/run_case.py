"""Per-case driver: run all 7 methods for one case id.

Smoke probe: ``PYTHONPATH=src python scripts/run_case.py --case-id 2``
Full sweep:  ``PYTHONPATH=src python scripts/run_case.py --case-id 2 --methods case_full,element_full``

Methods map:
- case_full / element_full / checkpoint_full / automatic_retrieval / verify_audit -> orchestrator.run_experiment
- stage_audit / agent_audit -> resume_run per-unit runners (positional index fix)

Re-runs only units missing from disk (idempotent on re-invocation).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from freca.config import PipelineConfig
from freca.cp import load_checkpoints
from freca.experiments.agent_audit import run_agent_audit_unit
from freca.experiments.materials import load_material_snapshot_from_parsed
from freca.experiments.models import ExperimentMethod, Track3Condition
from freca.experiments.orchestrator import materialize_for_unit, run_experiment
from freca.experiments.planning import build_execution_plan
from freca.experiments.runner import run_execution
from freca.experiments.stage_audit import run_stage_audit_unit
from freca.experiments.verify_audit import run_verify_audit_unit
from freca.llm import build_audit_client
from freca.env_loader import apply_env_file, find_env_file
from freca.state import atomic_write_json


class _PacingClient:
    """Wrap an LLM client to insert a fixed delay between every call.

    Mirrors the throttle used in scripts/resume_run.py to spread traffic.
    """

    def __init__(self, inner, *, delay: float) -> None:
        self._inner = inner
        self._delay = delay
        self.last_usage = None

    def __getattr__(self, name: str):
        target = getattr(self._inner, name)
        if not callable(target):
            return target

        def wrapped(*args, **kwargs):
            result = target(*args, **kwargs)
            self.last_usage = getattr(self._inner, "last_usage", None)
            if self._delay > 0:
                time.sleep(self._delay)
            return result

        return wrapped


PLAN_RUNNERS = {
    ExperimentMethod.STAGE_AUDIT: run_stage_audit_unit,
    ExperimentMethod.AGENT_AUDIT: run_agent_audit_unit,
}

# One LLM call per plan unit via run_execution, persisted to unit-NNN/result.json.
# Idempotent: re-runs skip already-valid units so we never burn quota twice.
SINGLE_SHOT_METHODS = {
    ExperimentMethod.CASE_FULL,
    ExperimentMethod.ELEMENT_FULL,
    ExperimentMethod.CHECKPOINT_FULL,
    ExperimentMethod.AUTOMATIC_RETRIEVAL,
}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", type=int, required=True)
    parser.add_argument(
        "--methods",
        default=",".join(m.value for m in ExperimentMethod),
        help="comma-separated method names (default: all 7)",
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--artifact-root", type=Path, default=Path("build/experiments"))
    parser.add_argument("--inter-call-delay", type=float, default=1.5)
    parser.add_argument("--model", default="MiniMax-M3")
    parser.add_argument("--uncertainty-threshold", type=float, default=0.5)
    args = parser.parse_args(argv)

    env_file = find_env_file()
    if env_file is not None:
        apply_env_file(env_file)
    os.environ.setdefault("FRECA_AUDIT_BASE_URL", "https://api.minimaxi.com/anthropic")

    methods = [ExperimentMethod(v) for v in args.methods.split(",") if v.strip()]
    config = PipelineConfig.from_yaml(args.config)
    checkpoints = load_checkpoints(config.paths.checkpoints_xlsx)
    object.__setattr__(
        config.models,
        "audit",
        config.models.audit.model_copy(update={"model": args.model}),
    )
    client = _PacingClient(build_audit_client(config.models.audit), delay=args.inter_call_delay)
    parsed_dir = config.paths.build_dir / "parsed"

    summary: list[dict] = []
    for method in methods:
        started = time.monotonic()
        plan = build_execution_plan(method, case_id=args.case_id, checkpoints=checkpoints)

        if method in PLAN_RUNNERS:
            case_dir = args.artifact_root / method.value / f"case-{args.case_id:03d}" / "track3-raw"
            missing = []
            for idx, _unit in enumerate(plan.units):
                ud = case_dir / f"cp-{idx:03d}"
                rf = ud / "result.json"
                if not rf.exists():
                    missing.append(idx)
                    continue
                try:
                    data = json.loads(rf.read_text(encoding="utf-8"))
                except Exception:
                    missing.append(idx)
                    continue
                if not data.get("valid"):
                    missing.append(idx)
            material = load_material_snapshot_from_parsed(
                parsed_dir=parsed_dir,
                case_id=args.case_id,
                checkpoints=list(checkpoints),
                track3_condition=Track3Condition.RAW,
            )
            unit_runner = PLAN_RUNNERS[method]
            ran = 0
            valid = 0
            for original_idx in missing:
                unit = plan.units[original_idx]
                unit_dir = case_dir / f"cp-{original_idx:03d}"
                kwargs: dict = {
                    "unit": unit,
                    "material": material,
                    "client": client,
                    "artifact_dir": unit_dir,
                }
                if method == ExperimentMethod.AGENT_AUDIT:
                    kwargs["uncertainty_threshold"] = args.uncertainty_threshold
                result = unit_runner(**kwargs)
                ran += 1
                if result.valid:
                    valid += 1
                print(
                    json.dumps({"cp": f"cp-{original_idx:03d}", "valid": result.valid}, ensure_ascii=False),
                    flush=True,
                )
            elapsed = time.monotonic() - started
            summary.append(
                {
                    "method": method.value,
                    "case_id": args.case_id,
                    "missing_found": len(missing),
                    "ran": ran,
                    "valid": valid,
                    "elapsed_seconds": round(elapsed, 2),
                }
            )
        elif method in SINGLE_SHOT_METHODS:
            case_dir = args.artifact_root / method.value / f"case-{args.case_id:03d}" / "track3-raw"
            base_material = load_material_snapshot_from_parsed(
                parsed_dir=parsed_dir,
                case_id=args.case_id,
                checkpoints=list(checkpoints),
                track3_condition=Track3Condition.RAW,
            )
            ran = valid = verdicts_total = 0
            for idx, unit in enumerate(plan.units):
                unit_dir = case_dir / f"unit-{idx:03d}"
                rf = unit_dir / "result.json"
                if rf.exists():
                    try:
                        data = json.loads(rf.read_text(encoding="utf-8"))
                    except Exception:
                        data = {}
                    if data.get("valid") and data.get("verdicts"):
                        verdicts_total += len(data["verdicts"])
                        continue  # already valid, skip to save quota
                mat = materialize_for_unit(
                    parsed_dir=parsed_dir,
                    case_id=args.case_id,
                    checkpoints=checkpoints,
                    unit_method=method,
                    unit_checkpoint_ids=unit.checkpoint_ids,
                    track3_condition=Track3Condition.RAW,
                )
                result = run_execution(unit=unit, material=mat, client=client, artifact_dir=unit_dir)
                ran += 1
                if result.valid:
                    valid += 1
                    verdicts_total += len(result.verdicts)
                print(
                    json.dumps({"unit": f"unit-{idx:03d}", "valid": result.valid}, ensure_ascii=False),
                    flush=True,
                )
            atomic_write_json(
                args.artifact_root / method.value / "summary.json",
                {
                    "method": method.value,
                    "case_id": args.case_id,
                    "track3_condition": "raw",
                    "units_total": len(plan.units),
                    "units_valid": sum(
                        1
                        for idx in range(len(plan.units))
                        if (case_dir / f"unit-{idx:03d}" / "result.json").exists()
                    ),
                    "verdicts_total": verdicts_total,
                },
            )
            elapsed = time.monotonic() - started
            summary.append(
                {
                    "method": method.value,
                    "case_id": args.case_id,
                    "ran": ran,
                    "valid": valid,
                    "verdicts_total": verdicts_total,
                    "elapsed_seconds": round(elapsed, 2),
                }
            )
        else:
            # VERIFY_AUDIT: base + unconditional per-CP verify (run_verify_audit_unit
            # does not skip existing results, but verify_audit is rarely partially
            # run since one case is a single base + 41 atomic verify calls).
            results = run_experiment(
                plan=plan,
                checkpoints=checkpoints,
                parsed_dir=parsed_dir,
                track3_condition=Track3Condition.RAW,
                client=client,
                artifact_root=args.artifact_root,
                uncertainty_threshold=args.uncertainty_threshold,
            )
            elapsed = time.monotonic() - started
            summary.append(
                {
                    "method": method.value,
                    "case_id": args.case_id,
                    "units_total": len(results),
                    "valid": sum(1 for r in results if r.valid),
                    "verdicts_total": sum(len(r.verdicts) for r in results),
                    "elapsed_seconds": round(elapsed, 2),
                }
            )
        print(json.dumps(summary[-1], ensure_ascii=False), flush=True)

    print(json.dumps({"case_id": args.case_id, "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())