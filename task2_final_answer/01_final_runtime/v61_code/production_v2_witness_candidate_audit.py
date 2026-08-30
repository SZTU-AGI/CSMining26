#!/usr/bin/env python3
"""Rank cached real-coordinate witness candidates without model/API calls.

The audit never changes evidence, contracts, outcomes, or labels.  A cached
witness is eligible only when every decisive requirement already has a direct
directional basis and a fully disposed opposite-direction candidate universe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import coverage_v1
import proof_standard_v1_1 as proof_standard


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def direct_basis_rows(
    *,
    alignments: list[dict],
    requirement_id: str,
    direction: str,
) -> list[dict]:
    relation = "SUPPORT" if direction == "SUPPORT" else "ATTACK"
    rows = [
        row
        for row in alignments
        if str(row.get("requirement_id")) == requirement_id
        and row.get("relation") == relation
        and row.get("argument_admission_channel") == "DIRECT"
        and row.get("identity_decisive_proof_eligible") is True
    ]
    if direction == "ATTACK":
        rows = [row for row in rows if proof_standard._is_explicit_adverse_fact(row)]
    return rows


def quote_preview(row: dict) -> str:
    fact = row.get("fact_candidate") or {}
    quote = str(row.get("exact_quote") or fact.get("quote") or "")
    return " ".join(quote.split())[:320]


def replay_blockers(replay_report: dict) -> dict[tuple[str, str], list[str]]:
    out = {}
    for row in replay_report.get("coordinate_summaries", []) or []:
        if not isinstance(row, dict):
            continue
        blockers = row.get("v2_final_blockers") or []
        flat = []
        if isinstance(blockers, dict):
            for value in blockers.values():
                if isinstance(value, list):
                    flat.extend(map(str, value))
                elif value:
                    flat.append(str(value))
        elif isinstance(blockers, list):
            flat.extend(map(str, blockers))
        out[(str(row.get("case_uid")), str(row.get("cp_id")))] = sorted(set(flat))
    return out


def audit_coordinate(path: Path, blockers: list[str]) -> dict:
    rr = load_json(path)
    alignments = rr.get("alignments") or []
    requirements = {
        str(row.get("requirement_id")): row
        for row in (rr.get("evidence_requirement_plan") or {}).get(
            "requirements", []
        )
        if isinstance(row, dict)
    }
    decisive_ids = sorted(
        rid
        for rid, row in requirements.items()
        if row.get("decisiveness") == "DECISIVE"
    )

    traces = {}
    for trace in rr.get("retrieval_traces", []) or []:
        if not isinstance(trace, dict):
            continue
        rid = str(trace.get("requirement_id"))
        direction = str(trace.get("direction"))
        disposition = coverage_v1.candidate_disposition(
            trace=trace,
            alignments=alignments,
        )
        traces[(rid, direction)] = {
            "candidate_universe_count": int(
                disposition.get("candidate_universe_count") or 0
            ),
            "unassessed_count": len(
                disposition.get("universe_unassessed_candidate_ids", []) or []
            ),
            "basis_rows": direct_basis_rows(
                alignments=alignments,
                requirement_id=rid,
                direction=direction,
            ),
        }

    routes = []
    decisive_route_present = {}
    for rid in decisive_ids:
        decisive_route_present[rid] = False
        for direction, opposite in (("SUPPORT", "ATTACK"), ("ATTACK", "SUPPORT")):
            own = traces.get((rid, direction))
            counter = traces.get((rid, opposite))
            if not own or not counter or not own["basis_rows"]:
                continue
            complete = counter["unassessed_count"] == 0
            decisive_route_present[rid] = decisive_route_present[rid] or complete
            routes.append(
                {
                    "requirement_id": rid,
                    "direction": direction,
                    "direct_basis_count": len(own["basis_rows"]),
                    "basis_quote_previews": [
                        quote_preview(row) for row in own["basis_rows"][:3]
                    ],
                    "countercheck_direction": opposite,
                    "countercheck_candidate_universe_count": counter[
                        "candidate_universe_count"
                    ],
                    "countercheck_unassessed_count": counter["unassessed_count"],
                    "cached_countercheck_complete": complete,
                }
            )

    cached_witness = bool(decisive_ids) and all(decisive_route_present.values())
    best_remaining = min(
        (row["countercheck_unassessed_count"] for row in routes),
        default=None,
    )
    return {
        "case_uid": str(rr.get("case_uid") or path.parents[2].name),
        "case_id": rr.get("case_id"),
        "cp_id": str(rr.get("cp_id") or path.parents[1].name),
        "requirement_result_path": str(path),
        "requirement_result_sha256": sha256_file(path),
        "decisive_requirement_ids": decisive_ids,
        "candidate_routes": routes,
        "best_countercheck_unassessed_count": best_remaining,
        "cached_witness_eligible": cached_witness,
        "replay_blockers": blockers,
        "temporal_blocked": any("TEMPORAL" in value for value in blockers),
        "reliability_blocked": any("RELIABILITY" in value for value in blockers),
    }


def build_report(
    *,
    task_root: Path,
    replay_report_path: Path,
    excluded_cp_ids: set[str],
) -> dict:
    replay = load_json(replay_report_path)
    blocker_index = replay_blockers(replay)
    rows = []
    for path in sorted(task_root.glob("shard-*/tasks/case-*/CP*/initial/requirement_result.json")):
        cp_id = path.parents[1].name
        case_uid = path.parents[2].name
        if cp_id in excluded_cp_ids:
            continue
        decision = path.parents[1] / "decision.json"
        if not decision.exists():
            continue
        rows.append(
            audit_coordinate(
                path,
                blocker_index.get((case_uid, cp_id), []),
            )
        )

    routes = [
        {**route, "case_uid": row["case_uid"], "case_id": row["case_id"],
         "cp_id": row["cp_id"], "temporal_blocked": row["temporal_blocked"],
         "reliability_blocked": row["reliability_blocked"],
         "replay_blockers": row["replay_blockers"]}
        for row in rows
        for route in row["candidate_routes"]
    ]
    positive = sorted(
        (row for row in routes if row["direction"] == "SUPPORT"),
        key=lambda row: (
            row["countercheck_unassessed_count"], row["case_uid"], row["cp_id"]
        ),
    )
    adverse = sorted(
        (row for row in routes if row["direction"] == "ATTACK"),
        key=lambda row: (
            row["countercheck_unassessed_count"], row["case_uid"], row["cp_id"]
        ),
    )
    cached = [row for row in rows if row["cached_witness_eligible"]]
    return {
        "schema": "freca-production-v2-witness-candidate-audit-v1",
        "method": {
            "api_called": False,
            "answer_comparator_used": False,
            "historical_labels_used": False,
            "contracts_or_evidence_mutated": False,
        },
        "inventory": {
            "completed_coordinate_count": len(rows),
            "excluded_cp_ids": sorted(excluded_cp_ids),
            "cached_witness_eligible_count": len(cached),
            "positive_route_count": len(positive),
            "explicit_adverse_route_count": len(adverse),
        },
        "cached_witness_eligible_coordinates": cached,
        "best_positive_routes": positive[:20],
        "best_explicit_adverse_routes": adverse[:20],
        "conclusion": (
            "CACHED_ZERO_API_WITNESS_AVAILABLE"
            if cached
            else "NO_CACHED_ZERO_API_WITNESS"
        ),
        "next_action": (
            "RUN_CACHED_WITNESS_REPLAY"
            if cached
            else "PREREGISTER_BOUNDED_COUNTERCHECK_AND_RESOLVE_TYPED_GATE_INPUTS"
        ),
        "replay_report": {
            "path": str(replay_report_path),
            "sha256": sha256_file(replay_report_path),
        },
    }


def self_test() -> None:
    alignment = {
        "requirement_id": "ER1",
        "relation": "SUPPORT",
        "argument_admission_channel": "DIRECT",
        "identity_decisive_proof_eligible": True,
        "exact_quote": "Observed feature",
    }
    assert direct_basis_rows(
        alignments=[alignment], requirement_id="ER1", direction="SUPPORT"
    ) == [alignment]
    assert direct_basis_rows(
        alignments=[alignment], requirement_id="ER1", direction="ATTACK"
    ) == []
    print("production_v2_witness_candidate_audit self-tests: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task-root", type=Path, default=Path("results_v2/production_run_v1_shards")
    )
    parser.add_argument("--replay-report", type=Path)
    parser.add_argument("--exclude-cp", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        if args.replay_report is None and args.output is None:
            return
    if args.replay_report is None or args.output is None:
        parser.error("--replay-report and --output are required")

    report = build_report(
        task_root=args.task_root,
        replay_report_path=args.replay_report,
        excluded_cp_ids=set(map(str, args.exclude_cp)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("COMPLETED COORDINATES:", report["inventory"]["completed_coordinate_count"])
    print("CACHED WITNESSES:", report["inventory"]["cached_witness_eligible_count"])
    print("CONCLUSION:", report["conclusion"])
    print("NEXT ACTION:", report["next_action"])
    print("Saved:", args.output)


if __name__ == "__main__":
    main()
