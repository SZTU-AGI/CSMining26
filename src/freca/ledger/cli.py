"""CLI for the ledger architecture — ``python -m freca.ledger``.

Deliberately separate from ``freca.cli`` so switching architectures never means
editing code:

* legacy   ``freca --config config.yaml full --run-id r1``
* ledger   ``python -m freca.ledger --config config.ledger.yaml run --run-id r1``

Both read the same ``build/`` directory. The ledger stack only ever writes
under ``build/ledger/``, so the two can be run back to back and diffed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from freca.models import TaskStatus

from freca.ledger.config import LedgerConfig
from freca.ledger.gates import evaluate_gates, summarize_gate
from freca.ledger.pipeline import (
    ALL_CP_IDS,
    assemble_ledger_submission,
    build_fact_ledgers,
    build_rubrics,
    make_store,
    run_baseline,
    run_ledger_tasks,
    run_ledger_workflow,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m freca.ledger",
        description="FRECA Task 2 — structured fact ledger + runtime regulatory rubric",
    )
    parser.add_argument("--config", type=Path, default=Path("config.ledger.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("describe", help="Show the resolved ledger configuration")

    facts = sub.add_parser("facts", help="Stage A — build per-case fact ledgers")
    facts.add_argument("--case-id", type=int, action="append")
    facts.add_argument("--force", action="store_true", help="Rebuild cached ledgers")
    facts.add_argument("--max-workers", type=int)

    rubrics = sub.add_parser("rubrics", help="Stage B — build per-CP regulatory rubrics")
    rubrics.add_argument("--cp-id", action="append")
    rubrics.add_argument("--max-workers", type=int)

    audit = sub.add_parser("audit", help="Stages C-E — pack, adjudicate, gate, review")
    audit.add_argument("--run-id", required=True)
    audit.add_argument("--case-id", type=int, action="append")
    audit.add_argument("--cp-id", action="append")
    audit.add_argument("--max-workers", type=int, default=4)

    gates = sub.add_parser("gates", help="Re-run gates over stored outcomes (read-only)")
    gates.add_argument("--case-id", type=int, action="append")
    gates.add_argument("--cp-id", action="append")

    baseline = sub.add_parser("baseline", help="§8 artifact classification for a run")
    baseline.add_argument("--run-id", required=True)
    baseline.add_argument("--no-legacy", action="store_true")

    inspect = sub.add_parser("inspect", help="Show one case×CP outcome in full")
    inspect.add_argument("--case-id", type=int, required=True)
    inspect.add_argument("--cp-id", required=True)

    status = sub.add_parser("status", help="Durable task status for a run")
    status.add_argument("--run-id", required=True)

    retry = sub.add_parser("retry", help="Reset blocked or failed tasks to pending")
    retry.add_argument("--run-id", required=True)
    retry.add_argument(
        "--status",
        action="append",
        choices=(TaskStatus.BLOCKED.value, TaskStatus.FAILED.value),
    )
    retry.add_argument("--case-id", type=int, action="append")
    retry.add_argument("--cp-id", action="append")

    assemble = sub.add_parser("assemble", help="Write a submission workbook")
    assemble.add_argument("--run-id", required=True)
    assemble.add_argument("--output", type=Path)
    assemble.add_argument("--allow-unconfirmed-identifiers", action="store_true")

    run = sub.add_parser("run", help="Stage A through E plus the §8 report")
    run.add_argument("--run-id", required=True)
    run.add_argument("--case-id", type=int, action="append")
    run.add_argument("--cp-id", action="append")
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--force-facts", action="store_true")
    run.add_argument("--assemble", action="store_true")
    run.add_argument("--allow-unconfirmed-identifiers", action="store_true")

    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _gates_report(config: LedgerConfig, case_ids, cp_ids) -> dict:
    store = make_store(config)
    wanted_cases = set(case_ids) if case_ids else None
    wanted_cps = set(cp_ids) if cp_ids else None

    rubrics: dict[str, object] = {}
    ledgers: dict[int, object] = {}
    rows = []
    codes: Counter[str] = Counter()
    failed = 0

    for outcome in store.iter_outcomes():
        if wanted_cases is not None and outcome.case_id not in wanted_cases:
            continue
        if wanted_cps is not None and outcome.cp_id not in wanted_cps:
            continue
        if outcome.cp_id not in rubrics:
            rubrics[outcome.cp_id] = store.read_rubric(outcome.cp_id)
        if outcome.case_id not in ledgers:
            ledgers[outcome.case_id] = (
                store.read_ledger(outcome.case_id)
                if store.has_ledger(outcome.case_id)
                else None
            )
        pack = store.read_pack(outcome.case_id, outcome.cp_id)
        report = evaluate_gates(
            decision=outcome.final,
            pack=pack,
            rubric=rubrics[outcome.cp_id],  # type: ignore[arg-type]
            ledger=ledgers[outcome.case_id],  # type: ignore[arg-type]
            config=config.ledger.adjudication,
        )
        if not report.passed:
            failed += 1
        for finding in report.findings:
            codes[finding.code] += 1
        rows.append(
            {
                "case_id": outcome.case_id,
                "cp_id": outcome.cp_id,
                "verdict": outcome.final.verdict.value,
                **summarize_gate(report),
            }
        )

    rows.sort(key=lambda item: -float(item["review_priority"]))
    return {
        "examined": len(rows),
        "failed": failed,
        "findings": dict(codes.most_common()),
        "top_review_priority": rows[:20],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = LedgerConfig.from_yaml(args.config)

        if args.command == "describe":
            _print(config.describe())
            return 0

        if args.command == "facts":
            _print(
                build_fact_ledgers(
                    config,
                    case_ids=args.case_id or None,
                    force=args.force,
                    max_workers=args.max_workers,
                )
            )
            return 0

        if args.command == "rubrics":
            report = build_rubrics(
                config,
                cp_ids=args.cp_id or None,
                max_workers=args.max_workers,
            )
            _print(report)
            return 0 if report["built"] else 2

        if args.command == "audit":
            summary = run_ledger_tasks(
                config,
                run_id=args.run_id,
                case_ids=args.case_id or range(1, 101),
                cp_ids=args.cp_id or ALL_CP_IDS,
                max_workers=args.max_workers,
            )
            _print(summary.model_dump())
            return 0 if summary.blocked == 0 and summary.failed == 0 else 2

        if args.command == "gates":
            report = _gates_report(config, args.case_id, args.cp_id)
            _print(report)
            return 0 if report["failed"] == 0 else 2

        if args.command == "baseline":
            report = run_baseline(
                config,
                run_id=args.run_id,
                include_legacy=not args.no_legacy,
            )
            payload = report.model_dump(mode="json")
            payload["silver"].pop("entries", None)
            _print(payload)
            return 0

        if args.command == "inspect":
            store = make_store(config)
            outcome = store.read_outcome(args.case_id, args.cp_id)
            _print(outcome.model_dump(mode="json"))
            return 0

        if args.command == "status":
            store = make_store(config)
            tasks = store.task_store(args.run_id).all()
            counts = Counter(task.status.value for task in tasks)
            _print({"run_id": args.run_id, "total": len(tasks), "by_status": counts})
            return 0

        if args.command == "retry":
            store = make_store(config)
            statuses = {
                TaskStatus(value)
                for value in (
                    args.status or [TaskStatus.BLOCKED.value, TaskStatus.FAILED.value]
                )
            }
            count = store.task_store(args.run_id).reset(
                statuses=statuses,
                case_ids=set(args.case_id) if args.case_id else None,
                cp_ids=set(args.cp_id) if args.cp_id else None,
            )
            _print({"run_id": args.run_id, "reset": count, "statuses": sorted(statuses)})
            return 0

        if args.command == "assemble":
            report = assemble_ledger_submission(
                config,
                run_id=args.run_id,
                output_path=args.output,
                allow_unconfirmed_identifiers=args.allow_unconfirmed_identifiers,
            )
            _print(report.model_dump(mode="json"))
            return 0

        if args.command == "run":
            report = run_ledger_workflow(
                config,
                run_id=args.run_id,
                case_ids=args.case_id or None,
                cp_ids=args.cp_id or None,
                max_workers=args.max_workers,
                force_facts=args.force_facts,
                assemble=args.assemble,
                allow_unconfirmed_identifiers=args.allow_unconfirmed_identifiers,
            )
            _print(report)
            return 0 if report.get("status") == "COMPLETED" else 2

    except (FileNotFoundError, ValueError, RuntimeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
