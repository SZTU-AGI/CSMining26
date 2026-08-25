from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from freca.ablation import (
    ABLATION_VARIANT_DESCRIPTIONS,
    ABLATION_VARIANT_NAMES,
    run_ablation_experiment,
    run_retrieval_judge_experiment,
    write_ablation_report,
)
from freca.config import PipelineConfig
from freca.evaluation import compare_reports, evaluate_run
from freca.direct_judge import DIRECT_JUDGE_METHODS, run_direct_judge_experiment
from freca.ledger.config import LedgerConfig
from freca.ledger.pipeline import run_ledger_gold_experiment
from freca.method_report import write_gold_html_report
from freca.methods import MethodRunLayout, compare_method_runs
from freca.models import TaskStatus
from freca.pipeline import (
    assemble_run_submission,
    build_hybrid_indexes,
    ingest_sources,
    retrieve_task_context,
    run_evidence_integrity_gate,
    run_consistency_gate,
    run_audit_tasks,
    write_manifest,
)
from freca.state import TaskStore
from freca.runtime import check_readiness
from freca.workflow import prepare_workflow, run_full_workflow, run_pilot_workflow
from freca.report import write_verification_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="freca", description="FRECA Task 2 audit pipeline")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("manifest", help="Build the 100-case source manifest")

    doctor = subparsers.add_parser("doctor", help="Check runtime readiness")
    doctor.add_argument("--stage", choices=("prepare", "pilot", "full"), default="pilot")

    retry = subparsers.add_parser("retry", help="Reset blocked or failed tasks to pending")
    retry.add_argument("--run-id", required=True)
    retry.add_argument(
        "--status",
        action="append",
        choices=(TaskStatus.BLOCKED.value, TaskStatus.FAILED.value),
    )
    retry.add_argument("--case-id", type=int, action="append")
    retry.add_argument("--cp-id", action="append")

    prepare = subparsers.add_parser(
        "prepare", help="Run manifest, parsing and index construction"
    )
    prepare.add_argument("--no-mineru", action="store_true")

    pilot = subparsers.add_parser("pilot", help="Run the configured pilot and promotion gate")
    pilot.add_argument("--pilot-file", type=Path, default=Path("pilot_cases.json"))
    pilot.add_argument("--run-id")
    pilot.add_argument("--max-workers", type=int, default=2)

    full = subparsers.add_parser("full", help="Run all 4,100 tasks and assemble output")
    full.add_argument("--run-id", required=True)
    full.add_argument("--max-workers", type=int, default=4)
    full.add_argument("--allow-unconfirmed-identifiers", action="store_true")
    full.add_argument("--pilot-report", type=Path)

    ingest = subparsers.add_parser("ingest", help="Parse policy and case evidence")
    ingest.add_argument("--case-id", type=int, action="append")
    ingest.add_argument("--no-mineru", action="store_true")

    subparsers.add_parser(
        "integrity",
        help="Run deterministic evidence-integrity checks over parsed case artifacts",
    )

    subparsers.add_parser("index", help="Build policy and case hybrid indexes")

    retrieve = subparsers.add_parser(
        "retrieve", help="Run retrieval only for one case and checking point"
    )
    retrieve.add_argument("--case-id", type=int, required=True)
    retrieve.add_argument("--cp-id", required=True)

    audit = subparsers.add_parser("audit", help="Run isolated case-by-CP audit tasks")
    audit.add_argument("--run-id", required=True)
    audit.add_argument("--case-id", type=int, action="append")
    audit.add_argument("--cp-id", action="append")
    audit.add_argument("--max-workers", type=int, default=4)

    status = subparsers.add_parser("status", help="Show durable audit task status")
    status.add_argument("--run-id", required=True)

    consistency = subparsers.add_parser(
        "consistency", help="Run Element-level shared-fact consistency checks"
    )
    consistency.add_argument("--run-id", required=True)

    assemble = subparsers.add_parser(
        "assemble", help="Assemble a submission after every quality gate passes"
    )
    assemble.add_argument("--run-id", required=True)
    assemble.add_argument("--output", type=Path)
    assemble.add_argument("--allow-unconfirmed-identifiers", action="store_true")

    run = subparsers.add_parser("run", help="Run manifest through candidate assembly")
    run.add_argument("--run-id", required=True)
    run.add_argument("--no-mineru", action="store_true")
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--allow-unconfirmed-identifiers", action="store_true")
    report = subparsers.add_parser("report", help="Write the local verification report")
    report.add_argument("--test-results", type=Path)

    ablation = subparsers.add_parser(
        "ablation", help="List, run or aggregate retrieval ablation experiments"
    )
    ablation_actions = ablation.add_subparsers(dest="ablation_action", required=True)
    ablation_actions.add_parser("list", help="List the built-in variants")
    ablation_run = ablation_actions.add_parser("run", help="Run selected task variants")
    ablation_run.add_argument("--experiment-id", required=True)
    ablation_run.add_argument(
        "--variant", action="append", choices=ABLATION_VARIANT_NAMES
    )
    ablation_run.add_argument("--case-id", type=int, action="append", required=True)
    ablation_run.add_argument("--cp-id", action="append", required=True)
    ablation_run.add_argument("--relevance-labels", type=Path)
    ablation_report = ablation_actions.add_parser(
        "report", help="Regenerate an experiment summary"
    )
    ablation_report.add_argument("--experiment-id", required=True)

    evaluation = subparsers.add_parser(
        "evaluation", help="Compare final verdicts with versioned Gold labels"
    )
    evaluation_actions = evaluation.add_subparsers(dest="evaluation_action", required=True)
    evaluation_run = evaluation_actions.add_parser(
        "run", help="Write one Gold comparison report"
    )
    evaluation_run.add_argument("--run-id", required=True)
    evaluation_run.add_argument(
        "--gold-labels", type=Path, default=Path("gold/consensus-v1.json")
    )
    evaluation_compare = evaluation_actions.add_parser(
        "compare", help="Rank saved Gold reports"
    )
    evaluation_compare.add_argument("--run-id", action="append", required=True)

    method = subparsers.add_parser("method", help="Run or evaluate isolated Gold methods")
    method_actions = method.add_subparsers(dest="method_action", required=True)
    method_evaluate = method_actions.add_parser(
        "evaluate", help="Evaluate one isolated method run"
    )
    method_evaluate.add_argument("--run-id", required=True)
    method_evaluate.add_argument(
        "--gold-labels", type=Path, default=Path("gold/consensus-v1.json")
    )
    method_retrieval = method_actions.add_parser(
        "retrieval", help="Run one retrieval variant through the shared Gold judge"
    )
    method_retrieval.add_argument("--run-id", required=True)
    method_retrieval.add_argument(
        "--variant", action="append", choices=ABLATION_VARIANT_NAMES, required=True
    )
    method_retrieval.add_argument(
        "--gold-labels", type=Path, default=Path("gold/consensus-v1.json")
    )
    method_retrieval.add_argument("--max-workers", type=int, default=1)
    method_direct = method_actions.add_parser(
        "direct", help="Run one approved direct LLM Gold judge"
    )
    method_direct.add_argument("--run-id", required=True)
    method_direct.add_argument("--method", choices=DIRECT_JUDGE_METHODS, required=True)
    method_direct.add_argument(
        "--gold-labels", type=Path, default=Path("gold/consensus-v1.json")
    )
    method_direct.add_argument("--max-workers", type=int, default=1)
    method_ledger = method_actions.add_parser(
        "ledger", help="Run the Ledger architecture on exact Gold task pairs"
    )
    method_ledger.add_argument("--run-id", required=True)
    method_ledger.add_argument(
        "--ledger-config", type=Path, default=Path("config.ledger.minimax.yaml")
    )
    method_ledger.add_argument(
        "--gold-labels", type=Path, default=Path("gold/consensus-v1.json")
    )
    method_ledger.add_argument("--max-workers", type=int, default=1)
    method_compare = method_actions.add_parser("compare", help="Rank evaluated Gold methods")
    method_compare.add_argument("--run-id", action="append", required=True)
    method_compare.add_argument("--output", type=Path)
    method_report = method_actions.add_parser("report", help="Write a static Gold method report")
    method_report.add_argument(
        "--comparison", type=Path, default=Path("build/method-comparison/gold-v1.json")
    )
    method_report.add_argument("--output", type=Path)
    return parser


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "method" and args.method_action == "ledger":
            summary = run_ledger_gold_experiment(
                LedgerConfig.from_yaml(args.ledger_config),
                run_id=args.run_id,
                gold_path=args.gold_labels,
                max_workers=args.max_workers,
            )
            _print(summary.model_dump())
            return 0 if summary.blocked == 0 and summary.failed == 0 else 2
        config = PipelineConfig.from_yaml(args.config)
        if args.command == "manifest":
            _print(write_manifest(config))
            return 0
        if args.command == "doctor":
            report = check_readiness(config, stage=args.stage)
            _print(report)
            return 0 if report["ready"] else 2
        if args.command == "retry":
            store = TaskStore(config.paths.build_dir / "state" / f"{args.run_id}-tasks.json")
            statuses = {
                TaskStatus(value)
                for value in (args.status or [TaskStatus.BLOCKED.value, TaskStatus.FAILED.value])
            }
            count = store.reset(
                statuses=statuses,
                case_ids=set(args.case_id) if args.case_id else None,
                cp_ids=set(args.cp_id) if args.cp_id else None,
            )
            _print({"run_id": args.run_id, "reset": count, "statuses": sorted(statuses)})
            return 0
        if args.command == "prepare":
            report = prepare_workflow(config, disable_mineru=args.no_mineru)
            _print(report)
            return 0 if report["status"] == "COMPLETED" else 2
        if args.command == "pilot":
            report = run_pilot_workflow(
                config,
                args.pilot_file,
                run_id=args.run_id,
                max_workers=args.max_workers,
            )
            _print(report)
            return 0 if report["status"] == "COMPLETED" else 2
        if args.command == "full":
            report = run_full_workflow(
                config,
                run_id=args.run_id,
                max_workers=args.max_workers,
                allow_unconfirmed_identifiers=args.allow_unconfirmed_identifiers,
                pilot_report_path=args.pilot_report,
            )
            _print(report)
            return 0 if report["status"] == "COMPLETED" else 2
        if args.command == "ingest":
            _print(
                ingest_sources(
                    config,
                    case_ids=set(args.case_id) if args.case_id else None,
                    disable_mineru=args.no_mineru,
                )
            )
            return 0
        if args.command == "integrity":
            _print(run_evidence_integrity_gate(config.paths.build_dir))
            return 0
        if args.command == "index":
            _print(build_hybrid_indexes(config))
            return 0
        if args.command == "retrieve":
            bundle = retrieve_task_context(
                config,
                case_id=args.case_id,
                cp_id=args.cp_id,
            )
            _print(
                {
                    "case_id": bundle.case_id,
                    "cp_id": bundle.cp_id,
                    "policy_hits": len(bundle.policy_hits),
                    "evidence_hits": len(bundle.evidence_hits),
                    "rounds": len(bundle.rounds),
                    "complete": bundle.complete,
                    "stop_reason": bundle.stop_reason,
                }
            )
            return 0
        if args.command == "audit":
            summary = run_audit_tasks(
                config,
                run_id=args.run_id,
                case_ids=args.case_id or range(1, 101),
                cp_ids=args.cp_id or [f"CP{index}" for index in range(1, 42)],
                max_workers=args.max_workers,
            )
            _print(summary.model_dump())
            return 0 if summary.blocked == 0 and summary.failed == 0 else 2
        if args.command == "status":
            store = TaskStore(config.paths.build_dir / "state" / f"{args.run_id}-tasks.json")
            tasks = store.all()
            counts = Counter(task.status.value for task in tasks)
            _print({"run_id": args.run_id, "total": len(tasks), "by_status": counts})
            return 0
        if args.command == "consistency":
            report = run_consistency_gate(config.paths.build_dir, run_id=args.run_id)
            _print(report)
            return 0 if report["finding_count"] == 0 else 2
        if args.command == "assemble":
            report = assemble_run_submission(
                config,
                run_id=args.run_id,
                allow_unconfirmed_identifiers=args.allow_unconfirmed_identifiers,
                output_path=args.output,
            )
            _print(report.model_dump(mode="json"))
            return 0
        if args.command == "run":
            _print(write_manifest(config))
            _print(
                ingest_sources(
                    config,
                    disable_mineru=args.no_mineru,
                )
            )
            _print(build_hybrid_indexes(config))
            summary = run_audit_tasks(
                config,
                run_id=args.run_id,
                max_workers=args.max_workers,
            )
            _print(summary.model_dump())
            if summary.blocked or summary.failed:
                return 2
            consistency_report = run_consistency_gate(
                config.paths.build_dir,
                run_id=args.run_id,
            )
            _print(consistency_report)
            if consistency_report["finding_count"]:
                return 2
            submission = assemble_run_submission(
                config,
                run_id=args.run_id,
                allow_unconfirmed_identifiers=args.allow_unconfirmed_identifiers,
            )
            _print(submission.model_dump(mode="json"))
            return 0
        if args.command == "report":
            _print(
                write_verification_report(
                    config,
                    test_results_path=args.test_results,
                )
            )
            return 0
        if args.command == "evaluation":
            if args.evaluation_action == "run":
                _print(
                    evaluate_run(
                        config.paths.build_dir,
                        run_id=args.run_id,
                        gold_path=args.gold_labels,
                    )
                )
                return 0
            _print(compare_reports(config.paths.build_dir, args.run_id))
            return 0
        if args.command == "method":
            if args.method_action == "evaluate":
                layout = MethodRunLayout(config.paths.build_dir, args.run_id)
                _print(
                    evaluate_run(
                        config.paths.build_dir,
                        run_id=args.run_id,
                        gold_path=args.gold_labels,
                        final_root=layout.final_dir,
                    )
                )
                return 0
            if args.method_action == "retrieval":
                summary = run_retrieval_judge_experiment(
                    config,
                    run_id=args.run_id,
                    variant_names=args.variant,
                    gold_path=args.gold_labels,
                    max_workers=args.max_workers,
                )
                _print(summary.model_dump())
                return 0 if summary.blocked == 0 and summary.failed == 0 else 2
            if args.method_action == "direct":
                summary = run_direct_judge_experiment(
                    config,
                    run_id=args.run_id,
                    method=args.method,
                    gold_path=args.gold_labels,
                    max_workers=args.max_workers,
                )
                _print(summary.model_dump())
                return 0 if summary.blocked == 0 and summary.failed == 0 else 2
            if args.method_action == "compare":
                _print(
                    compare_method_runs(
                        config.paths.build_dir,
                        args.run_id,
                        output_path=args.output,
                    )
                )
                return 0
            if args.method_action == "report":
                _print(
                    {
                        "output": str(
                            write_gold_html_report(
                                build_dir=config.paths.build_dir,
                                comparison_path=args.comparison,
                                output_path=args.output,
                            )
                        )
                    }
                )
                return 0
        if args.command == "ablation":
            if args.ablation_action == "list":
                _print(
                    [
                        {
                            "name": name,
                            "description": ABLATION_VARIANT_DESCRIPTIONS[name],
                        }
                        for name in ABLATION_VARIANT_NAMES
                    ]
                )
                return 0
            if args.ablation_action == "run":
                result = run_ablation_experiment(
                    config,
                    experiment_id=args.experiment_id,
                    variants=args.variant or ABLATION_VARIANT_NAMES,
                    case_ids=args.case_id,
                    cp_ids=args.cp_id,
                    relevance_labels_path=args.relevance_labels,
                )
                _print(result)
                return 0 if result.get("failed", 0) == 0 else 2
            result = write_ablation_report(
                config.paths.build_dir, args.experiment_id
            )
            _print(result)
            return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
