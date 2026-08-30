#!/usr/bin/env python3
"""Discover CP12 pilot artifacts and orchestrate D/T/M experiment units.

Default pilots:
  S023 = RE-NSW-2020-0144
  S038 = RE-NSW-2021-0177
  S065 = RE-NSW-2021-0222

This suite does not merge arm outputs. Each arm/case receives an isolated
directory.

Use --discover-only while contract census is running. It performs zero API.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


CASES = [
    "RE-NSW-2020-0144",
    "RE-NSW-2021-0177",
    "RE-NSW-2021-0222",
]

ARTIFACT_PATTERNS = {
    "requirement_result": [
        "{case}_CP12_requirement_reasoning_v2.json",
    ],
    "coverage": [
        "{case}_CP12_coverage_v1_1.json",
    ],
    "proof": [
        "{case}_CP12_proof_standard_v1.json",
        "{case}_CP12_proof_standard_v1_1.json",
        "{case}_CP12_proof_v1.json",
    ],
    "open_goals": [
        "{case}_CP12_open_goals_v1.json",
        "{case}_CP12_open_goal_ledger_v1.json",
    ],
}


def find_unique(
    *,
    search_roots: list[Path],
    patterns: list[str],
    case: str,
) -> Path | None:
    matches: list[Path] = []

    for root in search_roots:
        if not root.exists():
            continue

        for pattern in patterns:
            name = pattern.format(case=case)
            matches.extend(root.rglob(name))

    # Prefer shortest resolved path; deduplicate.
    unique = {}
    for path in matches:
        unique[str(path.resolve())] = path

    paths = list(unique.values())
    paths.sort(
        key=lambda p: (
            len(p.parts),
            str(p),
        )
    )

    return paths[0] if paths else None


def discover_case(
    case: str,
    search_roots: list[Path],
) -> dict:
    row = {
        "case": case,
        "artifacts": {},
        "missing": [],
    }

    for key, patterns in ARTIFACT_PATTERNS.items():
        path = find_unique(
            search_roots=search_roots,
            patterns=patterns,
            case=case,
        )

        if path is None:
            row["missing"].append(key)
            row["artifacts"][key] = None
        else:
            row["artifacts"][key] = str(path)

    return row


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="Search root; may be repeated.",
    )

    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("contracts_v2/CP12.json"),
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            "results_v2/tree_search_experiment_v1"
        ),
    )

    parser.add_argument(
        "--discover-only",
        action="store_true",
    )

    parser.add_argument(
        "--arm",
        choices=["D", "T", "M"],
        action="append",
    )

    args = parser.parse_args()

    roots = args.root or [
        Path("results_v2"),
        Path("results_v2/replication_roots"),
    ]

    arms = args.arm or ["D", "T", "M"]

    discovery = [
        discover_case(case, roots)
        for case in CASES
    ]

    print("=" * 78)
    print("FRECA CP12 TREE-SEARCH SUITE DISCOVERY V1")
    print("=" * 78)

    for row in discovery:
        print()
        print(row["case"])
        for key, value in row["artifacts"].items():
            print(f"  {key:20} {value}")
        if row["missing"]:
            print(
                "  MISSING:",
                ", ".join(row["missing"]),
            )

    args.output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    discovery_path = (
        args.output_root
        / "artifact_discovery.json"
    )

    discovery_path.write_text(
        json.dumps(
            discovery,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("Saved discovery:", discovery_path)

    if args.discover_only:
        return

    if not args.contract.exists():
        raise SystemExit(
            f"Missing contract: {args.contract}"
        )

    incomplete = [
        row
        for row in discovery
        if row["missing"]
    ]

    if incomplete:
        raise SystemExit(
            "Artifact discovery incomplete. "
            "Do not run experiment until all required roots are explicit."
        )

    runner = Path(
        "tree_search_cp12_runner_v1.py"
    )

    if not runner.exists():
        raise SystemExit(
            f"Missing runner: {runner}"
        )

    results = []

    for row in discovery:
        case = row["case"]
        a = row["artifacts"]

        for arm in arms:
            out_dir = (
                args.output_root
                / case
                / arm
            )

            cmd = [
                sys.executable,
                str(runner),
                "--arm", arm,
                "--requirement-result",
                a["requirement_result"],
                "--coverage",
                a["coverage"],
                "--proof",
                a["proof"],
                "--open-goals",
                a["open_goals"],
                "--contract",
                str(args.contract),
                "--output-dir",
                str(out_dir),
            ]

            print()
            print(
                f"RUN {case} arm={arm}"
            )
            print(" ".join(cmd))

            proc = subprocess.run(cmd)

            results.append({
                "case": case,
                "arm": arm,
                "returncode": proc.returncode,
                "output_dir": str(out_dir),
            })

    summary_path = (
        args.output_root
        / "suite_execution_summary.json"
    )

    summary_path.write_text(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("Saved:", summary_path)

    failed = [
        row
        for row in results
        if row["returncode"] != 0
    ]

    if failed:
        raise SystemExit(
            f"{len(failed)} experiment unit(s) failed"
        )


if __name__ == "__main__":
    main()
