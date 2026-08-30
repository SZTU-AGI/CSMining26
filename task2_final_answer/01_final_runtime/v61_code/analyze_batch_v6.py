#!/usr/bin/env python3
"""Strict zero-API analysis for a completed V6 initial batch."""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path

import production_runner_v2 as runner
import evidence_nature_v1 as evidence_nature
from semantic_replay_v6_1 import replay_requirement_result
from witness_funnel_v6 import analyze_root


RE_BY_CASE = {
    "case-023": "RE-NSW-2020-0144",
    "case-035": "RE-WA-2021-0077",
    "case-038": "RE-NSW-2021-0177",
    "case-065": "RE-NSW-2021-0222",
    "case-074": "RE-NSW-2020-0088",
}
EXPECTED_CPS = {"CP1", "CP12", "CP15", "CP26", "CP35"}


def analyze(run_root: Path, contracts: Path) -> dict:
    rows = []
    for rr_path in sorted(run_root.glob("tasks/case-*/CP*/initial/requirement_result.json")):
        case_uid = rr_path.parents[2].name
        cp_id = rr_path.parents[1].name
        rr_original = json.loads(rr_path.read_text(encoding="utf-8"))
        # V6.1 analyzer always replays persisted model relations through the
        # current deterministic semantic gate.  Otherwise an old initial batch
        # keeps stale CORROBORATIVE->DIRECT admissions and stale predicate
        # profiles even after the code has been fixed.  This is zero-API and
        # does not add evidence.
        rr, semantic_replay = replay_requirement_result(rr_original)
        contract = json.loads((contracts / f"{cp_id}.json").read_text(encoding="utf-8"))
        root = runner.build_layer7_v2(requirement_result=rr, contract=contract)
        outcome, fold = runner.build_outcome_and_fold(root, contract)
        funnel = analyze_root(
            root,
            chunks_path=run_root / "cases" / case_uid / "evidence_chunks.json",
        )
        coverage_rows = (root.get("coverage") or {}).get("need_reports", []) or []
        unassessed = sum(
            len(row.get("unassessed_candidate_ids", []) or []) for row in coverage_rows
        )
        counterchecks_complete = sum(
            1 for row in coverage_rows if row.get("countercheck_complete") is True
        )

        requirements = {
            str(row["requirement_id"]): row
            for row in (rr.get("evidence_requirement_plan") or {}).get("requirements", [])
        }
        stale_need_ids = []
        retrieval_profile_mismatches = []
        for trace in rr_original.get("retrieval_traces", []) or []:
            rid = str(trace.get("requirement_id"))
            requirement = requirements.get(rid)
            if not requirement:
                continue
            expected = sorted(
                evidence_nature.infer_requirement_predicate_profile(requirement).get(
                    "target_kinds", []
                )
            )
            observed_targets = sorted(
                ((trace.get("typed_fact_scan") or {}).get("target_kinds") or [])
            )
            if expected != observed_targets:
                stale_need_ids.append(str(trace.get("need_id")))
                retrieval_profile_mismatches.append({
                    "need_id": trace.get("need_id"),
                    "requirement_id": rid,
                    "old_target_kinds": observed_targets,
                    "v6_1_target_kinds": expected,
                })

        rows.append({
            "case_uid": case_uid,
            "re_id": RE_BY_CASE.get(case_uid),
            "cp_id": cp_id,
            "semantic_replay_validation_failures": semantic_replay.get(
                "validation_failure_count", 0
            ),
            "typed_retrieval_profile_stale": bool(stale_need_ids),
            "stale_retrieval_need_ids": sorted(set(stale_need_ids)),
            "retrieval_profile_mismatches": retrieval_profile_mismatches,
            "internal_outcome": outcome.get("common_internal_outcome"),
            "fold_label": fold.get("label"),
            "fold_finality": fold.get("finality"),
            "retrieved_candidate_count": funnel["retrieved_candidate_count"],
            "aligned_source_count": funnel["aligned_source_count"],
            "direct_truth_bearing_source_count":
                funnel["direct_truth_bearing_source_count"],
            "accepted_decisive_basis_count":
                funnel["accepted_decisive_basis_count"],
            "decisive_requirements_with_accepted_direction_count":
                funnel["decisive_requirements_with_accepted_direction_count"],
            "unassessed_candidate_count": unassessed,
            "counterchecks_complete": counterchecks_complete,
            "open_goal_count": len((root.get("open_goals") or {}).get("goals", []) or []),
            "proof_failure_codes": funnel["blockers"]["proof_failure_codes"],
            "non_executable_terminal_codes":
                funnel["blockers"]["non_executable_terminal_codes"],
        })

    expected = {(case, cp) for case in RE_BY_CASE for cp in EXPECTED_CPS}
    observed = {(row["case_uid"], row["cp_id"]) for row in rows}
    missing = sorted(f"{case}/{cp}" for case, cp in expected - observed)
    extra = sorted(f"{case}/{cp}" for case, cp in observed - expected)
    if missing or extra or len(rows) != 25:
        raise RuntimeError(
            f"STRICT_COORDINATE_MISMATCH rows={len(rows)} missing={missing} extra={extra}"
        )

    blocker_counts = collections.Counter(
        code for row in rows for code in row["proof_failure_codes"]
    )
    terminal_counts = collections.Counter(
        code for row in rows for code in row["non_executable_terminal_codes"]
    )
    outcome_counts = collections.Counter(row["internal_outcome"] for row in rows)
    label_counts = collections.Counter(row["fold_label"] for row in rows)

    by_cp = []
    for cp_id in sorted(EXPECTED_CPS, key=lambda value: int(value[2:])):
        group = [row for row in rows if row["cp_id"] == cp_id]
        by_cp.append({
            "cp_id": cp_id,
            "coordinates": len(group),
            "truth_bearing_mean": statistics.mean(
                row["direct_truth_bearing_source_count"] for row in group
            ),
            "truth_bearing_max": max(
                row["direct_truth_bearing_source_count"] for row in group
            ),
            "aligned_mean": statistics.mean(row["aligned_source_count"] for row in group),
            "unassessed_mean": statistics.mean(
                row["unassessed_candidate_count"] for row in group
            ),
            "accepted_decisive_basis_total": sum(
                row["accepted_decisive_basis_count"] for row in group
            ),
            "stale_typed_retrieval_coordinates": sum(
                1 for row in group if row["typed_retrieval_profile_stale"]
            ),
        })

    ranking = sorted(
        rows,
        key=lambda row: (
            -row["accepted_decisive_basis_count"],
            -row["direct_truth_bearing_source_count"],
            row["unassessed_candidate_count"],
            row["case_uid"],
            int(row["cp_id"][2:]),
        ),
    )
    # V6.1.1: bounded repair targets unresolved coordinates, not already-settled
    # coordinates.  An UNKNOWN with zero accepted decisive bases can still be
    # repairable: FIND_SUPPORT/FIND_ATTACK may align the next candidate batch.
    # Conversely, PROVEN_COMPLIANT/CONFLICTING coordinates should not be
    # selected merely because they already have decisive bases.
    repair_admissible = [
        row for row in ranking
        if row["internal_outcome"] == "UNKNOWN"
        and not row["non_executable_terminal_codes"]
        and not row["typed_retrieval_profile_stale"]
        and row["open_goal_count"] > 0
        and (
            row["unassessed_candidate_count"] > 0
            or row["accepted_decisive_basis_count"] > 0
        )
    ]
    fresh_initial_rerun = [
        row for row in ranking if row["typed_retrieval_profile_stale"]
    ]
    return {
        "schema": "freca-v6-batch-analysis-v2-v6.1.1",
        "semantic_replay_enabled": True,
        "semantic_replay_api_calls": 0,
        "coordinate_count": len(rows),
        "coordinate_completeness_pass": not missing and not extra and len(rows) == 25,
        "repair_enabled": False,
        "na_enabled": False,
        "outcome_counts": dict(outcome_counts),
        "label_counts": dict(label_counts),
        "proof_blocker_counts": dict(blocker_counts.most_common()),
        "non_executable_terminal_counts": dict(terminal_counts.most_common()),
        "accepted_decisive_basis_total": sum(
            row["accepted_decisive_basis_count"] for row in rows
        ),
        "counterchecks_complete_total": sum(row["counterchecks_complete"] for row in rows),
        "by_cp": by_cp,
        "ranked_coordinates": ranking,
        "repair_admissible_coordinates": repair_admissible,
        "repairable_unknown_coordinate_count": len(repair_admissible),
        "fresh_initial_rerun_coordinates": fresh_initial_rerun,
        "stale_typed_retrieval_coordinate_count": len(fresh_initial_rerun),
        "semantic_replay_validation_failure_total": sum(
            row["semantic_replay_validation_failures"] for row in rows
        ),
        "decision": (
            "RERUN_INITIAL_WITH_V6_1_TYPED_RETRIEVAL_BEFORE_REPAIR"
            if fresh_initial_rerun
            else (
                "GO_FOR_BOUNDED_REPAIR"
                if repair_admissible
                else "NO_GO_FOR_REPAIR_RESOLVE_TYPED_INPUTS_FIRST"
            )
        ),
        "interpretation": (
            "V6.1 first revalidates persisted model relations through the deterministic "
            "semantic gate. If the old typed-retrieval target differs from the current "
            "RequirementPredicateProfile, the old candidate universe is stale and a fresh "
            "initial retrieval run is required before repair. Counterevidence closure does "
            "not block an already-grounded directional existence proof; contradictions are "
            "preserved as BOTH."
        ),
    }


def markdown(report: dict) -> str:
    lines = [
        "# V6 five-case / five-CP initial batch",
        "",
        f"- Coordinates: {report['coordinate_count']}/25",
        f"- Outcomes: `{report['outcome_counts']}`",
        f"- Labels: `{report['label_counts']}`",
        f"- Accepted decisive bases: {report['accepted_decisive_basis_total']}",
        f"- Complete counterchecks: {report['counterchecks_complete_total']}",
        f"- Stale typed-retrieval coordinates: {report['stale_typed_retrieval_coordinate_count']}",
        f"- Semantic replay validation failures: {report['semantic_replay_validation_failure_total']}",
        f"- Repairable UNKNOWN coordinates: {report['repairable_unknown_coordinate_count']}",
        f"- Decision: **{report['decision']}**",
        "",
        "## By CP",
        "",
        "| CP | truth-bearing mean/max | aligned mean | unassessed mean | decisive bases | stale retrieval |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["by_cp"]:
        lines.append(
            f"| {row['cp_id']} | {row['truth_bearing_mean']:.1f}/{row['truth_bearing_max']} "
            f"| {row['aligned_mean']:.1f} | {row['unassessed_mean']:.1f} "
            f"| {row['accepted_decisive_basis_total']} "
            f"| {row['stale_typed_retrieval_coordinates']} |"
        )
    lines += ["", "## Repairable UNKNOWN coordinates", ""]
    if report["repair_admissible_coordinates"]:
        for row in report["repair_admissible_coordinates"]:
            lines.append(
                f"- `{row['re_id']} / {row['cp_id']}`: truth-bearing="
                f"{row['direct_truth_bearing_source_count']}, unassessed="
                f"{row['unassessed_candidate_count']}, decisive_basis="
                f"{row['accepted_decisive_basis_count']}, open_goals="
                f"{row['open_goal_count']}"
            )
    else:
        lines.append("- none")
    lines += ["", "## Closest coordinates", ""]
    for row in report["ranked_coordinates"][:8]:
        lines.append(
            f"- `{row['re_id']} / {row['cp_id']}`: truth-bearing="
            f"{row['direct_truth_bearing_source_count']}, unassessed="
            f"{row['unassessed_candidate_count']}, decisive_basis="
            f"{row['accepted_decisive_basis_count']}, stale_retrieval="
            f"{row['typed_retrieval_profile_stale']}, terminal="
            f"{row['non_executable_terminal_codes']}"
        )
    lines += ["", "## Blockers", ""]
    for code, count in report["proof_blocker_counts"].items():
        lines.append(f"- `{code}`: {count}/25")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.run_root, args.contracts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({
        "coordinate_count": report["coordinate_count"],
        "outcome_counts": report["outcome_counts"],
        "accepted_decisive_basis_total": report["accepted_decisive_basis_total"],
        "decision": report["decision"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
