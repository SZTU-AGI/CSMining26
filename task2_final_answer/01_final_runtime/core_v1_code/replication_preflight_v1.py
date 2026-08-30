#!/usr/bin/env python3
"""FRECA cross-pilot A/B replication preflight v1.

Zero API calls. No answer comparator.

Checks whether a case is eligible for the already-frozen paired experiment:
  Arm A = TOP_K_NEXT_BATCH_EXPANSION
  Arm B = CHANNEL_COMPLETION_ONLY
  Need  = ER1.attack (default)
  Budget = 24 parents (default)

It verifies:
  - required before-artifacts exist;
  - frozen RetrievalNeed exists;
  - coverage report exists;
  - unassessed candidate count is non-zero;
  - the persisted candidate universe is available;
  - current Core parser can reload the same case package;
  - STRUCTURE full parser-record scan would expose records outside the frozen
    candidate universe;
  - if an OpenGoal ledger is present, it reports whether a matching FIND_ATTACK
    semantic goal exists (diagnostic, not a gold signal).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_one(rows: list[dict], key: str, value: str) -> dict | None:
    hits = [
        row
        for row in rows
        if str(row.get(key)) == value
    ]
    if len(hits) == 1:
        return hits[0]
    return None


def evaluate_case_preflight(
    *,
    case_id: str,
    cp_id: str,
    need_id: str,
    root_dir: Path,
    parent_budget: int,
) -> dict:
    prefix = f"{case_id}_{cp_id}"

    paths = {
        "requirement_result":
            root_dir / f"{prefix}_requirement_reasoning_v2.json",
        "coverage":
            root_dir / f"{prefix}_coverage_v1_1.json",
        "proof":
            root_dir / f"{prefix}_proof_standard_v1.json",
        "open_goals":
            root_dir / f"{prefix}_open_goals_v1.json",
    }

    missing = [
        name
        for name, path in paths.items()
        if not path.exists()
    ]

    row = {
        "case_id": case_id,
        "cp_id": cp_id,
        "need_id": need_id,
        "parent_budget": parent_budget,
        "paths": {
            name: str(path)
            for name, path in paths.items()
        },
        "missing_artifacts": missing,
        "eligible": False,
        "reasons": [],
    }

    if missing:
        row["reasons"].append(
            "MISSING_BEFORE_ARTIFACTS"
        )
        return row

    rr = load_json(paths["requirement_result"])
    cov = load_json(paths["coverage"])
    goals = load_json(paths["open_goals"])

    trace = find_one(
        rr.get("retrieval_traces", []),
        "need_id",
        need_id,
    )

    if trace is None:
        row["reasons"].append(
            "RETRIEVAL_NEED_TRACE_MISSING_OR_DUPLICATE"
        )
        return row

    coverage = find_one(
        cov.get("need_reports", []),
        "need_id",
        need_id,
    )

    if coverage is None:
        row["reasons"].append(
            "COVERAGE_NEED_MISSING_OR_DUPLICATE"
        )
        return row

    universe = (
        trace.get("candidate_universe")
        or trace.get("candidates")
        or []
    )

    universe_ids = {
        str(
            candidate.get("evidence_id")
            or candidate.get("id")
        )
        for candidate in universe
        if (
            candidate.get("evidence_id")
            or candidate.get("id")
        )
    }

    unassessed = [
        str(x)
        for x in coverage.get(
            "unassessed_candidate_ids",
            coverage.get(
                "universe_unassessed_candidate_ids",
                [],
            ),
        )
    ]

    row["candidate_universe_count"] = len(universe_ids)
    row["unassessed_candidate_count"] = len(unassessed)
    row["arm_a_can_fill_budget"] = (
        len(unassessed) >= parent_budget
    )

    import freca_core_v1 as core

    case_dir = core.find_case_dir(case_id)
    chunks = core.load_case_evidence(case_dir)

    parser_ids = []

    for chunk in chunks:
        evidence_id = str(
            chunk.get("id")
            or chunk.get("evidence_id")
            or chunk.get("chunk_id")
            or ""
        )
        if evidence_id:
            parser_ids.append(evidence_id)

    new_structure_ids = [
        evidence_id
        for evidence_id in parser_ids
        if evidence_id not in universe_ids
    ]

    row["parser_record_count"] = len(parser_ids)
    row["structure_new_outside_universe_count"] = len(
        new_structure_ids
    )
    row["arm_b_can_fill_budget"] = (
        len(new_structure_ids) >= parent_budget
    )

    expected_direction = (
        "ATTACK"
        if need_id.endswith(".attack")
        else (
            "SUPPORT"
            if need_id.endswith(".support")
            else ""
        )
    )

    expected_goal_type = (
        "FIND_ATTACK"
        if expected_direction == "ATTACK"
        else "FIND_SUPPORT"
    )

    matching_goals = []

    for goal in goals.get("goals", []):
        ext = goal.get("core_extension") or {}
        if (
            str(goal.get("goal_type")) == expected_goal_type
            and str(ext.get("direction", "")) == expected_direction
            and (
                str(goal.get("target_statement_id", "")).lower()
                .endswith(
                    need_id.split(".", 1)[0].lower()
                )
                or str(goal.get("target_statement_id", ""))
                == f"stmt-{need_id.split('.', 1)[0].lower()}"
            )
        ):
            matching_goals.append(
                str(goal.get("goal_id"))
            )

    row["matching_open_goal_ids"] = matching_goals
    row["matching_open_goal_present"] = bool(matching_goals)

    if not unassessed:
        row["reasons"].append(
            "NO_UNASSESSED_CANDIDATES_FOR_ARM_A"
        )

    if not new_structure_ids:
        row["reasons"].append(
            "NO_NEW_STRUCTURE_RECORDS_FOR_ARM_B"
        )

    if not row["arm_a_can_fill_budget"]:
        row["reasons"].append(
            "ARM_A_CANNOT_FILL_EQUAL_PARENT_BUDGET"
        )

    if not row["arm_b_can_fill_budget"]:
        row["reasons"].append(
            "ARM_B_CANNOT_FILL_EQUAL_PARENT_BUDGET"
        )

    if not matching_goals:
        row["reasons"].append(
            "MATCHING_OPEN_GOAL_NOT_FOUND_DIAGNOSTIC"
        )

    # Strict experimental eligibility requires both arms to fill the same
    # semantic parent budget. OpenGoal presence is reported separately because
    # older ledgers may encode IDs differently; do not manufacture a failure.
    row["eligible"] = bool(
        len(unassessed) >= parent_budget
        and len(new_structure_ids) >= parent_budget
    )

    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        action="append",
        required=True,
    )
    parser.add_argument(
        "--cp",
        default="CP12",
    )
    parser.add_argument(
        "--need-id",
        default="ER1.attack",
    )
    parser.add_argument(
        "--root-dir",
        type=Path,
        default=Path("results_v2"),
    )
    parser.add_argument(
        "--parent-budget",
        type=int,
        default=24,
    )
    parser.add_argument(
        "--output",
        type=Path,
    )

    args = parser.parse_args()

    rows = [
        evaluate_case_preflight(
            case_id=case_id,
            cp_id=args.cp,
            need_id=args.need_id,
            root_dir=args.root_dir,
            parent_budget=args.parent_budget,
        )
        for case_id in args.case
    ]

    result = {
        "schema":
            "freca-core-cross-pilot-replication-preflight-v1",
        "cp_id":
            args.cp,
        "need_id":
            args.need_id,
        "parent_budget":
            args.parent_budget,
        "answer_comparator_used":
            False,
        "cases":
            rows,
        "all_cases_eligible":
            all(row["eligible"] for row in rows),
    }

    print("=" * 78)
    print("FRECA CROSS-PILOT A/B REPLICATION PREFLIGHT V1")
    print("=" * 78)

    for row in rows:
        print()
        print(row["case_id"])
        print("  eligible:", row["eligible"])

        if row["missing_artifacts"]:
            print(
                "  missing:",
                row["missing_artifacts"],
            )
            continue

        print(
            "  universe / unassessed:",
            row["candidate_universe_count"],
            "/",
            row["unassessed_candidate_count"],
        )
        print(
            "  parser records / new STRUCTURE:",
            row["parser_record_count"],
            "/",
            row["structure_new_outside_universe_count"],
        )
        print(
            "  equal-budget A/B:",
            row["arm_a_can_fill_budget"],
            "/",
            row["arm_b_can_fill_budget"],
        )
        print(
            "  matching OpenGoal:",
            row["matching_open_goal_present"],
            row["matching_open_goal_ids"],
        )
        print(
            "  reasons:",
            row["reasons"],
        )

    print()
    print(
        "ALL CASES ELIGIBLE:",
        result["all_cases_eligible"],
    )

    if args.output:
        args.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.output.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Saved:", args.output)


if __name__ == "__main__":
    main()
