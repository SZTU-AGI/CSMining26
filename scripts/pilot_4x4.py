"""Pilot comparison driver: run 4 methods × N cases against the live MiniMax endpoint.

Usage:
    PYTHONPATH=src FRECA_AUDIT_BASE_URL=https://api.minimaxi.com/anthropic \\
        FRECA_AUDIT_MODEL=MiniMax-M3 \\
        python scripts/pilot_4x4.py --cases 1,5,50,100 --config /path/to/config.yaml
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path

WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(WORKTREE_SRC))

from freca.config import PipelineConfig
from freca.cp import load_checkpoints
from freca.env_loader import find_env_file, apply_env_file
from freca.experiments.models import ExperimentMethod, Track3Condition
from freca.experiments.orchestrator import run_experiment
from freca.experiments.planning import build_execution_plan
from freca.llm import build_audit_client


METHODS: tuple[ExperimentMethod, ...] = (
    ExperimentMethod.CASE_FULL,
    ExperimentMethod.ELEMENT_FULL,
    ExperimentMethod.CHECKPOINT_FULL,
    ExperimentMethod.AUTOMATIC_RETRIEVAL,
)


def _summarize(result_paths: list[Path]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for path in result_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "path": str(path),
                "verdicts": len(data.get("verdicts", [])),
                "valid": data.get("valid", False),
                "errors": list(data.get("errors", [])),
            }
        )
    return {
        "total": len(rows),
        "valid_count": sum(1 for row in rows if row["valid"]),
        "verdict_count": sum(row["verdicts"] for row in rows),
        "rows": rows,
    }


def _run_case(
    *,
    case_id: int,
    method: ExperimentMethod,
    checkpoints: Sequence[object],
    parsed_dir: Path,
    client: object,
    artifact_root: Path,
    track3_condition: Track3Condition,
) -> dict[str, object]:
    plan = build_execution_plan(method, case_id=case_id, checkpoints=checkpoints)
    case_root = artifact_root / f"case-{case_id:03d}"
    started = time.monotonic()
    results = run_experiment(
        plan=plan,
        checkpoints=checkpoints,
        parsed_dir=parsed_dir,
        track3_condition=track3_condition,
        client=client,
        artifact_root=case_root,
    )
    elapsed = time.monotonic() - started
    return {
        "method": method.value,
        "case_id": case_id,
        "track3_condition": track3_condition.value,
        "units_total": len(results),
        "units_valid": sum(1 for result in results if result.valid),
        "verdicts_total": sum(len(result.verdicts) for result in results),
        "elapsed_seconds": round(elapsed, 2),
    }


class _PacingClient:
    """Wrap a JsonChatClient and pause briefly after every call.

    MiniMax enforces a per-minute request quota; sequential checkpoint_full
    units (41 calls in a tight loop) trip 429s that the long-backoff retry
    then has to recover from. A small inter-call delay keeps us under quota
    in the first place. The wrapper is transparent to the runner.
    """

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default="1,5,50,100",
        help="comma-separated case ids",
    )
    parser.add_argument(
        "--track3",
        choices=[condition.value for condition in Track3Condition],
        default=Track3Condition.RAW.value,
    )
    parser.add_argument(
        "--methods",
        default=",".join(method.value for method in METHODS),
        help="comma-separated methods (default: all four)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("build/experiments/pilot"),
    )
    parser.add_argument(
        "--inter-call-delay",
        type=float,
        default=1.5,
        help="seconds to pause between API calls (pacing to avoid 429s)",
    )
    args = parser.parse_args()

    env_file = find_env_file()
    if env_file is not None:
        apply_env_file(env_file)

    case_ids = [int(value) for value in args.cases.split(",") if value.strip()]
    methods = [ExperimentMethod(value) for value in args.methods.split(",") if value.strip()]
    track3_condition = Track3Condition(args.track3)
    config = PipelineConfig.from_yaml(args.config)
    checkpoints = load_checkpoints(config.paths.checkpoints_xlsx)
    client = _PacingClient(build_audit_client(config.models.audit), delay=args.inter_call_delay)
    parsed_dir = config.paths.build_dir / "parsed"

    args.artifact_root.mkdir(parents=True, exist_ok=True)

    summary: list[dict[str, object]] = []
    elapsed_per_method: dict[str, list[float]] = {method.value: [] for method in methods}
    for case_id in case_ids:
        for method in methods:
            entry = _run_case(
                case_id=case_id,
                method=method,
                checkpoints=checkpoints,
                parsed_dir=parsed_dir,
                client=client,
                artifact_root=args.artifact_root,
                track3_condition=track3_condition,
            )
            elapsed_per_method[method.value].append(
                float(entry["elapsed_seconds"])
            )
            print(json.dumps(entry, ensure_ascii=False))
            summary.append(entry)

    aggregate = {
        "cases": case_ids,
        "methods": [method.value for method in methods],
        "track3_condition": track3_condition.value,
        "elapsed_seconds_per_method": {
            method: {
                "mean": (
                    round(statistics.mean(values), 2) if values else None
                ),
                "stdev": (
                    round(statistics.stdev(values), 2) if len(values) > 1 else 0.0
                ),
                "samples": len(values),
            }
            for method, values in elapsed_per_method.items()
        },
        "runs": summary,
    }
    aggregate_path = args.artifact_root / "pilot-summary.json"
    aggregate_path.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summary": str(aggregate_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())