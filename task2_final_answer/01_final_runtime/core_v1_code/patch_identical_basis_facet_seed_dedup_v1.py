#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import evidence_reasoning_v2 as er
import multi_atom_support_v1 as ma

TARGET = Path("multi_atom_support_v1.py")
CONTRACT_DIR = Path("contracts_v2")
MARKER = "FRECA IDENTICAL-BASIS FACET-SEED DEDUP V1"
OLD_NAME = "_build_multi_atom_facet_seeds_before_identical_basis_dedup_v1"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be object")
    return value


def unwrap_contract(bundle: dict) -> dict:
    inner = bundle.get("contract")
    return inner if isinstance(inner, dict) else bundle


def eligible_ids(ledger: dict) -> set[str]:
    _candidates, decisions = er._candidate_maps(ledger)
    return {
        str(candidate_id)
        for candidate_id, decision in decisions.items()
        if (
            decision.get("selected")
            and decision.get("relation") == "PRIMARY_NORM"
            and decision.get("contract_eligible", False)
        )
    }


def duplicate_classes_for_cp(cp_id: str) -> list[dict]:
    contract = unwrap_contract(load_json(CONTRACT_DIR / f"{cp_id}.json"))
    ledger = load_json(CONTRACT_DIR / f"{cp_id}_candidate_ledger.json")
    relation = load_json(CONTRACT_DIR / f"{cp_id}_rule_set_relation.json")

    seeds = ma.build_multi_atom_facet_seeds(
        contract,
        relation,
        eligible_ids(ledger),
    )

    groups = {}
    for seed in seeds:
        key = (
            str(seed.get("atom_id") or ""),
            tuple(sorted({
                str(x)
                for x in (seed.get("basis_candidate_ids") or [])
            })),
        )
        groups.setdefault(key, []).append(seed)

    out = []
    for (atom_id, basis), rows in groups.items():
        if len(rows) <= 1:
            continue
        out.append({
            "cp_id": cp_id,
            "atom_id": atom_id,
            "basis_candidate_ids": list(basis),
            "seed_ids": [str(x.get("facet_seed_id") or "") for x in rows],
            "seed_relations": [str(x.get("seed_relation") or "") for x in rows],
            "source_group_ids": [list(x.get("source_group_ids") or []) for x in rows],
        })
    return out


def preflight() -> list[dict]:
    rows = []
    for i in range(1, 42):
        rows.extend(duplicate_classes_for_cp(f"CP{i}"))

    print("=" * 88)
    print("IDENTICAL-BASIS FACET-SEED PREFLIGHT")
    print("=" * 88)

    if not rows:
        print("No duplicate same-atom/same-basis seed classes found.")
        return []

    for row in rows:
        print()
        print(
            row["cp_id"],
            row["atom_id"],
            "basis=",
            row["basis_candidate_ids"],
        )
        print("  seeds:", row["seed_ids"])
        print("  relations:", row["seed_relations"])
        print("  groups:", row["source_group_ids"])

    affected = sorted({row["cp_id"] for row in rows})
    unsafe = [
        cp_id
        for cp_id in affected
        if (CONTRACT_DIR / f"{cp_id}_evidence_requirements.json").exists()
    ]

    if unsafe:
        raise RuntimeError(
            "Refusing patch: duplicate seed classes affect CPs that already "
            "have materialized plans: " + ", ".join(unsafe)
        )

    print()
    print(
        "All affected CPs currently have no materialized plan. "
        "Patch may proceed without invalidating accepted plans."
    )
    return rows


def find_top_level_function(source: str, name: str):
    tree = ast.parse(source)
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}; found {len(matches)}")
    return matches[0]


WRAPPER = """
# FRECA IDENTICAL-BASIS FACET-SEED DEDUP V1
def build_multi_atom_facet_seeds(
    contract: dict,
    rule_set_relation: dict,
    eligible_ids: set[str],
) -> list[dict]:
    raw_seeds = _build_multi_atom_facet_seeds_before_identical_basis_dedup_v1(
        contract,
        rule_set_relation,
        eligible_ids,
    )

    deduped = []
    seen = set()

    for seed in raw_seeds:
        key = (
            str(seed.get("atom_id") or ""),
            tuple(
                sorted({
                    str(x)
                    for x in (
                        seed.get("basis_candidate_ids")
                        or []
                    )
                })
            ),
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(seed)

    return deduped
"""


def patch_source(source: str) -> str:
    if MARKER in source:
        return source

    if OLD_NAME in source:
        raise RuntimeError(
            "Renamed pre-dedup function already exists without marker."
        )

    node = find_top_level_function(
        source,
        "build_multi_atom_facet_seeds",
    )

    lines = source.splitlines(keepends=True)
    idx = node.lineno - 1

    old = "def build_multi_atom_facet_seeds("
    if old not in lines[idx]:
        raise RuntimeError(
            "Unexpected build_multi_atom_facet_seeds formatting: "
            + lines[idx].rstrip()
        )

    lines[idx] = lines[idx].replace(
        old,
        f"def {OLD_NAME}(",
        1,
    )

    insert_at = node.end_lineno
    lines[insert_at:insert_at] = [
        "\n" + WRAPPER.strip() + "\n\n"
    ]

    patched = "".join(lines)
    ast.parse(patched)

    if MARKER not in patched:
        raise RuntimeError("Patch marker missing")

    return patched


def self_test(patched: str) -> None:
    ns = {"__name__": "_seed_dedup_test"}
    exec(
        compile(
            patched,
            "<patched_multi_atom_support_v1>",
            "exec",
        ),
        ns,
    )

    builder = ns["build_multi_atom_facet_seeds"]

    contract = {
        "atoms": [
            {"atom_id": "A1", "basis_candidate_ids": ["r1"]},
            {"atom_id": "A2", "basis_candidate_ids": ["r2"]},
        ],
        "satisfaction": {
            "op": "ALL",
            "children": [
                {"op": "ATOM", "atom_id": "A1"},
                {"op": "ATOM", "atom_id": "A2"},
            ],
        },
    }

    relation = {
        "groups": [
            {
                "group_id": "G1",
                "relation": "SPECIALIZES",
                "member_candidate_ids": ["r1"],
            },
            {
                "group_id": "G2",
                "relation": "CUMULATIVE",
                "member_candidate_ids": ["r1", "r2"],
            },
        ]
    }

    seeds = builder(
        contract,
        relation,
        {"r1", "r2"},
    )

    keys = [
        (
            str(seed.get("atom_id") or ""),
            tuple(sorted(
                str(x)
                for x in (seed.get("basis_candidate_ids") or [])
            )),
        )
        for seed in seeds
    ]

    assert len(keys) == len(set(keys))
    assert ("A1", ("r1",)) in keys
    assert ("A2", ("r2",)) in keys

    print("identical-basis facet-seed self-test: PASS")
    print("  same atom + same exact basis deduplicated")
    print("  distinct atom/basis seeds preserved")
    print("  stable-first")
    print("  no case evidence / no answer comparator")


def main() -> None:
    if not TARGET.exists():
        raise SystemExit(
            "Missing multi_atom_support_v1.py; run from core_v1."
        )

    duplicates = preflight()

    if not duplicates:
        print("Nothing to patch.")
        return

    source = TARGET.read_text(encoding="utf-8")
    patched = patch_source(source)

    self_test(patched)

    if patched == source:
        print("Already installed:", MARKER)
        return

    backup = TARGET.with_name(
        TARGET.name + ".before_identical_basis_seed_dedup_v1"
    )
    if not backup.exists():
        shutil.copy2(TARGET, backup)

    tmp = TARGET.with_suffix(
        TARGET.suffix + ".seed_dedup_v1.tmp"
    )
    tmp.write_text(patched, encoding="utf-8")
    ast.parse(tmp.read_text(encoding="utf-8"))
    tmp.replace(TARGET)

    print()
    print("Installed:", MARKER)
    print("target:", TARGET)
    print("backup:", backup)


if __name__ == "__main__":
    main()
