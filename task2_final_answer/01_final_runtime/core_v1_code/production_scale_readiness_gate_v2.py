#!/usr/bin/env python3
"""Fail-closed paid-pilot and full-scale readiness gate for Production V2.

This gate consumes existing replay/live-gate reports only.  It calls no API,
does not fold outcomes, and does not target a preferred 1/0/N/A distribution.
Benchmark-fallback labels are counted separately from substantive labels and
can never serve as evidence of semantic reachability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SUBSTANTIVE_OUTCOMES = {
    "PROVEN_COMPLIANT",
    "PROVEN_NON_COMPLIANT",
    "PROVEN_NOT_APPLICABLE",
    "NOT_DEMONSTRATED",
    "CONFLICTING",
}
FALLBACK_FINALITIES = {
    "UNKNOWN_BENCHMARK_FALLBACK",
    "INSUFFICIENT_EVIDENCE_BENCHMARK_FALLBACK",
    "INTERPRETATION_CONFLICT_FALLBACK",
}


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bool(value: Any) -> bool:
    return value is True


def audit_replay(report: dict) -> dict:
    rows = report.get("coordinate_summaries") or []
    if not isinstance(rows, list):
        rows = []

    outcome_counts = Counter(
        str(row.get("v2_internal_outcome") or "MISSING")
        for row in rows
        if isinstance(row, dict)
    )
    folded_label_counts = Counter(
        str(row.get("v2_fold_label") or "MISSING")
        for row in rows
        if isinstance(row, dict)
    )
    fallback_label_counts = Counter(
        str(row.get("v2_fold_label") or "MISSING")
        for row in rows
        if isinstance(row, dict)
        and str(row.get("v2_fold_finality") or "") in FALLBACK_FINALITIES
    )
    substantive_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("v2_internal_outcome")) in SUBSTANTIVE_OUTCOMES
    ]
    substantive_label_counts = Counter(
        str(row.get("v2_fold_label") or "MISSING") for row in substantive_rows
    )

    coordinate_count = int(report.get("coordinate_count") or 0)
    compatible_count = int(report.get("compatible_count") or 0)
    checks = {
        "schema_valid": report.get("schema")
        == "freca-production-v2-semantic-reachability-report-v1",
        "inventory_nonempty_and_consistent": bool(rows)
        and len(rows) == coordinate_count
        and compatible_count == coordinate_count,
        "no_replay_or_system_blocks": int(
            report.get("replay_incompatible_count") or 0
        )
        == 0
        and int(report.get("system_block_count") or 0) == 0,
        "all_coordinate_hard_gates_pass": _bool(
            report.get("all_coordinate_hard_gates_pass")
        ),
        "all_round_executions_complete": _bool(
            report.get("all_round_executions_complete")
        ),
        "no_answer_or_historical_labels": report.get("answer_comparator_used")
        is False
        and report.get("historical_labels_used") is False,
        "real_substantive_witness_present": bool(substantive_rows),
        "not_all_compatible_coordinates_unknown": report.get(
            "all_compatible_real_coordinates_unknown"
        )
        is False,
    }
    safety_keys = {
        "schema_valid",
        "inventory_nonempty_and_consistent",
        "no_replay_or_system_blocks",
        "all_coordinate_hard_gates_pass",
        "all_round_executions_complete",
        "no_answer_or_historical_labels",
    }
    return {
        "checks": checks,
        "safety_pass": all(checks[k] for k in safety_keys),
        "semantic_witness_pass": checks["real_substantive_witness_present"]
        and checks["not_all_compatible_coordinates_unknown"],
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "folded_label_counts": dict(sorted(folded_label_counts.items())),
        "fallback_label_counts": dict(sorted(fallback_label_counts.items())),
        "substantive_label_counts": dict(
            sorted(substantive_label_counts.items())
        ),
        "substantive_coordinate_count": len(substantive_rows),
        "fallback_coordinate_count": sum(fallback_label_counts.values()),
    }


def audit_live_gate(report: dict) -> dict:
    totals = report.get("totals") or {}
    coordinates = report.get("coordinates") or []
    if not isinstance(totals, dict):
        totals = {}
    if not isinstance(coordinates, list):
        coordinates = []

    planned = int(report.get("sample_planned_count") or 0)
    completed = int(report.get("sample_completed_count") or 0)
    resolved = int(totals.get("resolved_decisive_goal_count") or 0)
    substantive = int(
        report.get("substantive_internal_outcome_change_count") or 0
    )
    checks = {
        "schema_valid": report.get("schema")
        == "freca-production-v2-phase5-live-gate-report-v1",
        "sample_complete": planned > 0
        and completed == planned
        and len(coordinates) == planned,
        "all_hard_gates_pass": _bool(report.get("all_hard_gates_pass")),
        "all_rounds_complete": _bool(report.get("all_rounds_complete")),
        "no_limit_violations": not (report.get("limit_violations") or []),
        "no_failed_calls": int(totals.get("failed_call_count") or 0) == 0,
        "no_answer_or_historical_labels": report.get("answer_comparator_used")
        is False
        and report.get("human_or_historical_labels_used") is False,
        "resolved_decisive_goal_present": resolved > 0,
        "substantive_outcome_benefit_present": substantive > 0,
        "primary_success_gate_pass": _bool(
            report.get("primary_success_gate_pass")
        ),
        "larger_paid_run_recommended": str(
            report.get("larger_paid_run_recommendation") or ""
        ).upper()
        == "GO",
    }
    safety_keys = {
        "schema_valid",
        "sample_complete",
        "all_hard_gates_pass",
        "all_rounds_complete",
        "no_limit_violations",
        "no_failed_calls",
        "no_answer_or_historical_labels",
    }
    benefit_keys = {
        "resolved_decisive_goal_present",
        "substantive_outcome_benefit_present",
        "primary_success_gate_pass",
        "larger_paid_run_recommended",
    }
    return {
        "checks": checks,
        "safety_pass": all(checks[k] for k in safety_keys),
        "benefit_pass": all(checks[k] for k in benefit_keys),
        "resolved_decisive_goal_count": resolved,
        "substantive_internal_outcome_change_count": substantive,
        "logical_model_call_count": int(
            report.get("logical_model_call_count") or 0
        ),
        "total_tokens": int(totals.get("total_tokens") or 0),
    }


def build_report(
    *,
    replay_path: Path,
    live_gate_path: Path,
    cost_budget_usd: float | None,
    budget_id: str | None,
) -> dict:
    replay = audit_replay(load_json(replay_path))
    live = audit_live_gate(load_json(live_gate_path))
    budget_frozen = (
        cost_budget_usd is not None
        and cost_budget_usd > 0
        and bool(str(budget_id or "").strip())
    )

    paid_pilot_checks = {
        "replay_safety_pass": replay["safety_pass"],
        "real_substantive_witness_present": replay["semantic_witness_pass"],
        "previous_live_gate_safety_pass": live["safety_pass"],
        "previous_live_gate_benefit_pass": live["benefit_pass"],
    }
    full_run_checks = {
        **paid_pilot_checks,
        "cost_budget_frozen": budget_frozen,
    }
    pilot_ready = all(paid_pilot_checks.values())
    full_ready = all(full_run_checks.values())

    blockers = []
    if not replay["semantic_witness_pass"]:
        blockers.append("NO_REAL_SUBSTANTIVE_OUTCOME_WITNESS")
    if not live["benefit_pass"]:
        blockers.append("LIVE_GATE_NO_DECISIVE_OR_OUTCOME_BENEFIT")
    if not budget_frozen:
        blockers.append("PAID_SCALE_COST_BUDGET_NOT_FROZEN")
    if not replay["safety_pass"] or not live["safety_pass"]:
        blockers.append("SAFETY_GATE_FAILURE")

    return {
        "schema": "freca-production-v2-scale-readiness-gate-v1",
        "ready_for_additional_paid_pilot": pilot_ready,
        "ready_for_full_4100": full_ready,
        "recommendation": "GO" if full_ready else "NO-GO",
        "blockers": sorted(set(blockers)),
        "checks": {
            "paid_pilot": paid_pilot_checks,
            "full_4100": full_run_checks,
        },
        "distribution_policy": {
            "preferred_label_distribution_enforced": False,
            "fallback_labels_counted_as_substantive": False,
            "note": "Counts are collapse diagnostics only; they are not label targets."
        },
        "replay": replay,
        "live_gate": live,
        "cost_budget": {
            "frozen": budget_frozen,
            "budget_id": budget_id,
            "maximum_usd": cost_budget_usd,
        },
        "inputs": {
            "replay_report": str(replay_path),
            "replay_report_sha256": sha256_file(replay_path),
            "live_gate_report": str(live_gate_path),
            "live_gate_report_sha256": sha256_file(live_gate_path),
        },
        "api_called": False,
        "answer_comparator_used": False,
        "upstream_artifacts_mutated": False,
    }


def self_test() -> None:
    replay = {
        "schema": "freca-production-v2-semantic-reachability-report-v1",
        "coordinate_count": 1,
        "compatible_count": 1,
        "replay_incompatible_count": 0,
        "system_block_count": 0,
        "all_coordinate_hard_gates_pass": True,
        "all_round_executions_complete": True,
        "answer_comparator_used": False,
        "historical_labels_used": False,
        "all_compatible_real_coordinates_unknown": True,
        "coordinate_summaries": [{
            "v2_internal_outcome": "UNKNOWN",
            "v2_fold_label": "0",
            "v2_fold_finality": "UNKNOWN_BENCHMARK_FALLBACK",
        }],
    }
    audited = audit_replay(replay)
    assert audited["safety_pass"] is True
    assert audited["semantic_witness_pass"] is False
    assert audited["fallback_label_counts"] == {"0": 1}
    assert audited["substantive_label_counts"] == {}

    replay["all_compatible_real_coordinates_unknown"] = False
    replay["coordinate_summaries"][0] = {
        "v2_internal_outcome": "PROVEN_COMPLIANT",
        "v2_fold_label": "1",
        "v2_fold_finality": "EVIDENCE_DEMONSTRATED",
    }
    audited = audit_replay(replay)
    assert audited["semantic_witness_pass"] is True
    assert audited["substantive_label_counts"] == {"1": 1}

    live = {
        "schema": "freca-production-v2-phase5-live-gate-report-v1",
        "sample_planned_count": 1,
        "sample_completed_count": 1,
        "coordinates": [{}],
        "all_hard_gates_pass": True,
        "all_rounds_complete": True,
        "limit_violations": [],
        "answer_comparator_used": False,
        "human_or_historical_labels_used": False,
        "logical_model_call_count": 1,
        "substantive_internal_outcome_change_count": 0,
        "primary_success_gate_pass": False,
        "larger_paid_run_recommendation": "NO-GO",
        "totals": {
            "failed_call_count": 0,
            "resolved_decisive_goal_count": 0,
            "total_tokens": 10,
        },
    }
    live_audit = audit_live_gate(live)
    assert live_audit["safety_pass"] is True
    assert live_audit["benefit_pass"] is False

    live["substantive_internal_outcome_change_count"] = 1
    live["primary_success_gate_pass"] = True
    live["larger_paid_run_recommendation"] = "GO"
    live["totals"]["resolved_decisive_goal_count"] = 1
    assert audit_live_gate(live)["benefit_pass"] is True

    print("production_scale_readiness_gate_v2 self-tests: PASS")
    print("  fallback 0 is not a substantive witness")
    print("  evidence-demonstrated 1 is a substantive witness")
    print("  safe but ineffective live gate remains NO-GO")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-report", type=Path)
    parser.add_argument("--live-gate-report", type=Path)
    parser.add_argument("--cost-budget-usd", type=float)
    parser.add_argument("--budget-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        if not args.replay_report and not args.live_gate_report and not args.output:
            return

    missing = [
        name
        for name, value in {
            "--replay-report": args.replay_report,
            "--live-gate-report": args.live_gate_report,
            "--output": args.output,
        }.items()
        if value is None
    ]
    if missing:
        parser.error("missing required arguments: " + ", ".join(missing))

    report = build_report(
        replay_path=args.replay_report,
        live_gate_path=args.live_gate_report,
        cost_budget_usd=args.cost_budget_usd,
        budget_id=args.budget_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("PAID PILOT READY:", report["ready_for_additional_paid_pilot"])
    print("FULL 4100 READY:", report["ready_for_full_4100"])
    print("RECOMMENDATION:", report["recommendation"])
    print("BLOCKERS:", ", ".join(report["blockers"]) or "NONE")
    print("Saved:", args.output)
    if not report["ready_for_full_4100"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
