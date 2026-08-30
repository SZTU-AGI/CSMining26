#!/usr/bin/env python3
"""Inventory current FRECA V2 contract logic shapes.

Zero API. No evidence. No answers.

The goal is to determine implementation coverage before any 4100 run.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap_contract(bundle: dict) -> dict:
    value = bundle.get("contract")
    return value if isinstance(value, dict) else bundle


def expr_shape(expr) -> str:
    if not isinstance(expr, dict):
        return "MISSING"

    op = str(expr.get("op") or "UNKNOWN").upper()

    if op == "CONST":
        return f"CONST:{expr.get('value')}"

    if op == "ATOM":
        return "ATOM"

    children = expr.get("children")

    if isinstance(children, list):
        child_shapes = sorted(
            expr_shape(child)
            for child in children
        )
        return (
            f"{op}("
            + ",".join(child_shapes)
            + ")"
        )

    child = expr.get("child")
    if isinstance(child, dict):
        return f"{op}({expr_shape(child)})"

    return op


def collect_ops(expr, out=None):
    if out is None:
        out = []

    if not isinstance(expr, dict):
        out.append("MISSING")
        return out

    op = str(expr.get("op") or "UNKNOWN").upper()
    out.append(op)

    for child in expr.get("children") or []:
        collect_ops(child, out)

    if isinstance(expr.get("child"), dict):
        collect_ops(expr["child"], out)

    return out


def numeric_cp_id(path: Path) -> tuple[int, str]:
    m = re.search(r"CP(\d+)", path.stem, re.I)
    return (
        int(m.group(1)) if m else 10**9,
        path.name,
    )


def classify_capability(contract: dict) -> tuple[str, list[str]]:
    blockers = []

    app = contract.get("applicability")
    na = contract.get("non_applicability")
    sat = contract.get("satisfaction")

    if not (
        isinstance(app, dict)
        and str(app.get("op")).upper() == "CONST"
    ):
        blockers.append(
            "NON_CONST_APPLICABILITY"
        )

    if not (
        isinstance(na, dict)
        and str(na.get("op")).upper() == "CONST"
    ):
        blockers.append(
            "NON_CONST_NON_APPLICABILITY"
        )

    sat_ops = set(collect_ops(sat))

    # Current argument_core_v1 is a narrow evidence-facet benchmark substrate.
    # ATOM-only satisfaction is directly represented. Any composite contract
    # must be handled by a proper AST/graph production evaluator rather than
    # silently flattened.
    if sat_ops - {"ATOM"}:
        blockers.append(
            "COMPOSITE_SATISFACTION_REQUIRES_AST_OR_GRAPH_EVALUATOR"
        )

    atom_count = len(contract.get("atoms") or [])
    if atom_count != 1:
        blockers.append(
            "MULTI_ATOM_CONTRACT_REQUIRES_GENERAL_GRAPH_PROJECTION"
        )

    if blockers:
        return "NEEDS_PRODUCTION_ADAPTER_WORK", blockers

    return "CURRENT_CORE_DIRECTLY_REPRESENTABLE", []


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    args = parser.parse_args()

    files = sorted(
        args.contract_dir.glob("CP*.json"),
        key=numeric_cp_id,
    )

    # Exclude side artifacts.
    files = [
        p
        for p in files
        if not any(
            suffix in p.stem
            for suffix in (
                "_candidate_ledger",
                "_rule_set_relation",
                "_evidence_requirements",
            )
        )
    ]

    rows = []

    for path in files:
        bundle = load_json(path)
        contract = unwrap_contract(bundle)

        status, blockers = classify_capability(
            contract
        )

        rows.append({
            "cp_id":
                contract.get("cp_id")
                or bundle.get("cp", {}).get("cp_id")
                or path.stem,
            "path":
                str(path),
            "atom_count":
                len(contract.get("atoms") or []),
            "applicability_shape":
                expr_shape(
                    contract.get("applicability")
                ),
            "satisfaction_shape":
                expr_shape(
                    contract.get("satisfaction")
                ),
            "non_applicability_shape":
                expr_shape(
                    contract.get("non_applicability")
                ),
            "operators":
                sorted(set(
                    collect_ops(
                        contract.get("applicability")
                    )
                    + collect_ops(
                        contract.get("satisfaction")
                    )
                    + collect_ops(
                        contract.get("non_applicability")
                    )
                )),
            "current_core_status":
                status,
            "blockers":
                blockers,
        })

    status_counts = collections.Counter(
        row["current_core_status"]
        for row in rows
    )

    operator_counts = collections.Counter()

    for row in rows:
        for op in row["operators"]:
            operator_counts[op] += 1

    result = {
        "schema":
            "freca-core-contract-shape-inventory-v1",
        "contract_dir":
            str(args.contract_dir),
        "contract_count":
            len(rows),
        "expected_contract_count":
            41,
        "all_41_present":
            len(rows) == 41,
        "status_counts":
            dict(status_counts),
        "operator_cp_counts":
            dict(sorted(operator_counts.items())),
        "contracts":
            rows,
        "answer_comparator_used":
            False,
    }

    print("=" * 78)
    print("FRECA CONTRACT SHAPE INVENTORY V1")
    print("=" * 78)
    print("Contracts found:", len(rows), "/ 41")
    print()
    print("Capability:")
    for key, value in sorted(
        status_counts.items()
    ):
        print(f"  {key}: {value}")

    print()
    print("Operators:")
    for key, value in sorted(
        operator_counts.items()
    ):
        print(f"  {key}: {value} CP(s)")

    print()
    print("Per CP:")

    for row in rows:
        print(
            f"  {row['cp_id']}: "
            f"atoms={row['atom_count']} | "
            f"app={row['applicability_shape']} | "
            f"sat={row['satisfaction_shape']} | "
            f"na={row['non_applicability_shape']} | "
            f"{row['current_core_status']}"
        )

        if row["blockers"]:
            print(
                "    blockers:",
                ", ".join(row["blockers"]),
            )

    if not rows:
        print(
            "  No CP contract files found. "
            "Compile/inventory normative assets before production."
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
        print()
        print("Saved:", args.output)


if __name__ == "__main__":
    main()
