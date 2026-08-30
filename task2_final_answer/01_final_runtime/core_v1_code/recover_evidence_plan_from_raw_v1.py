#!/usr/bin/env python3
"""Recover a FRECA EvidenceRequirement plan from an already-saved raw model JSON.

ZERO API.

Only deterministic / schema fields are normalized from authoritative FACET_SEEDs:
- top-level requirements container (only from an unambiguous one-level wrapper);
- requirement_id (stable ER1..ERn in seed order);
- facet_seed_id (must already identify exactly one expected seed);
- atom_id;
- polarity = SUPPORT;
- decisiveness = DECISIVE;
- source_group_ids = exact seed source groups;
- query_sources[*].source upper-cased.

Substantive language is NOT rewritten:
- proposition_to_establish
- criterion_quote
- basis_candidate_ids
- query_sources quotes/candidate_ids
- reason

The CURRENT live validator remains the final authority.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import evidence_reasoning_v2 as er
import freca_core_v1 as core
import multi_atom_support_v1 as ma


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    return value


def save_json_atomic(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def unwrap_contract(bundle: dict) -> dict:
    inner = bundle.get("contract")
    return inner if isinstance(inner, dict) else bundle


def requirements_list(raw: dict) -> tuple[list[dict], str]:
    direct = raw.get("requirements")
    if isinstance(direct, list) and direct:
        return direct, "requirements"

    candidates = []
    for key, value in raw.items():
        if isinstance(value, dict):
            reqs = value.get("requirements")
            if isinstance(reqs, list) and reqs:
                candidates.append((f"{key}.requirements", reqs))
        elif isinstance(value, list) and value and all(
            isinstance(x, dict) for x in value
        ):
            # Only consider lists whose items look like EvidenceRequirements.
            if all(
                ("facet_seed_id" in x or "requirement_id" in x)
                for x in value
            ):
                candidates.append((key, value))

    if len(candidates) != 1:
        raise ValueError(
            "Could not unambiguously recover requirements list; "
            f"candidates={[x[0] for x in candidates]}"
        )

    return candidates[0][1], candidates[0][0]


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


def normalize_raw(
    *,
    raw: dict,
    contract_bundle: dict,
    ledger: dict,
    relation: dict,
) -> tuple[dict, dict]:
    contract = unwrap_contract(contract_bundle)

    seeds = ma.build_multi_atom_facet_seeds(
        contract,
        relation,
        eligible_ids(ledger),
    )
    seed_order = [s["facet_seed_id"] for s in seeds]
    seed_map = {s["facet_seed_id"]: s for s in seeds}

    requirements, recovered_from = requirements_list(raw)

    rows_by_seed: dict[str, list[dict]] = {}
    extras = []

    for row in requirements:
        if not isinstance(row, dict):
            raise ValueError("EvidenceRequirement item must be an object")

        seed_id = str(row.get("facet_seed_id") or "").strip()
        if seed_id not in seed_map:
            extras.append(seed_id or "<missing>")
            continue
        rows_by_seed.setdefault(seed_id, []).append(row)

    missing = [sid for sid in seed_order if sid not in rows_by_seed]
    duplicate = {
        sid: len(rows)
        for sid, rows in rows_by_seed.items()
        if len(rows) != 1
    }

    if missing or duplicate or extras:
        raise ValueError(
            "Raw output cannot be structurally normalized without inference: "
            f"missing_seeds={missing}, duplicate_seeds={duplicate}, extras={extras}"
        )

    normalized = []
    changes = []

    for index, seed_id in enumerate(seed_order, start=1):
        seed = seed_map[seed_id]
        row = copy.deepcopy(rows_by_seed[seed_id][0])

        def set_fixed(key, value):
            before = row.get(key)
            if before != value:
                changes.append({
                    "facet_seed_id": seed_id,
                    "field": key,
                    "before": before,
                    "after": value,
                })
            row[key] = value

        set_fixed("requirement_id", f"ER{index}")
        set_fixed("facet_seed_id", seed_id)
        set_fixed("atom_id", seed["atom_id"])
        set_fixed("polarity", "SUPPORT")
        set_fixed("decisiveness", "DECISIVE")
        set_fixed(
            "source_group_ids",
            list(seed.get("source_group_ids") or []),
        )

        query_sources = row.get("query_sources")
        if isinstance(query_sources, list):
            fixed_sources = []
            for source in query_sources:
                if not isinstance(source, dict):
                    fixed_sources.append(source)
                    continue
                source = copy.deepcopy(source)
                before = source.get("source")
                if isinstance(before, str):
                    after = before.upper()
                    if before != after:
                        changes.append({
                            "facet_seed_id": seed_id,
                            "field": "query_sources[].source",
                            "before": before,
                            "after": after,
                        })
                    source["source"] = after
                fixed_sources.append(source)
            row["query_sources"] = fixed_sources

        normalized.append(row)

    return {
        "requirements": normalized,
    }, {
        "recovered_from": recovered_from,
        "expected_seed_ids": seed_order,
        "change_count": len(changes),
        "changes": changes,
        "substantive_fields_rewritten": False,
        "api_called": False,
        "answer_comparator_used": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cp", required=True)
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument("--normalized-output", type=Path, required=True)
    p.add_argument(
        "--install-valid-plan",
        action="store_true",
        help="Write contracts_v2/CPx_evidence_requirements.json only if CURRENT validator passes.",
    )
    args = p.parse_args()

    cp = core.get_cp(args.cp)
    cp_id = cp["cp_id"]

    contract_path = args.contract_dir / f"{cp_id}.json"
    ledger_path = args.contract_dir / f"{cp_id}_candidate_ledger.json"
    relation_path = args.contract_dir / f"{cp_id}_rule_set_relation.json"
    plan_path = args.contract_dir / f"{cp_id}_evidence_requirements.json"

    raw = load_json(args.raw)
    contract = load_json(contract_path)
    ledger = load_json(ledger_path)
    relation = load_json(relation_path)

    normalized_raw, audit = normalize_raw(
        raw=raw,
        contract_bundle=contract,
        ledger=ledger,
        relation=relation,
    )

    save_json_atomic(
        {
            "schema": "freca-evidence-plan-structural-normalization-v1",
            "cp_id": cp_id,
            "source_raw": str(args.raw),
            "audit": audit,
            "normalized_raw": normalized_raw,
        },
        args.normalized_output,
    )

    print("=" * 80)
    print("FRECA EVIDENCE PLAN RAW RECOVERY V1")
    print("=" * 80)
    print("CP:", cp_id)
    print("raw:", args.raw)
    print("recovered_from:", audit["recovered_from"])
    print("expected seeds:", ", ".join(audit["expected_seed_ids"]))
    print("deterministic changes:", audit["change_count"])
    for change in audit["changes"]:
        print(
            " ",
            change["facet_seed_id"],
            change["field"],
            repr(change["before"]),
            "->",
            repr(change["after"]),
        )

    try:
        validated = er.validate_evidence_requirements(
            normalized_raw,
            cp,
            contract,
            ledger,
            relation,
        )
    except Exception as exc:
        print()
        print("CURRENT VALIDATOR: FAIL")
        print(type(exc).__name__ + ":", exc)
        print("Normalized artifact:", args.normalized_output)
        print("API called: False")
        raise SystemExit(2)

    print()
    print("CURRENT VALIDATOR: PASS")
    print("requirements:", len(validated.get("requirements") or []))
    print("facet_seeds:", len(validated.get("facet_seeds") or []))

    if args.install_valid_plan:
        save_json_atomic(validated, plan_path)
        # Read-back validation.
        replayed = load_json(plan_path)
        er.validate_evidence_requirements(
            replayed,
            cp,
            contract,
            ledger,
            relation,
        )
        print("Installed valid plan:", plan_path)

    print("Normalized artifact:", args.normalized_output)
    print("API called: False")
    print("Answer comparator used: False")


if __name__ == "__main__":
    main()
