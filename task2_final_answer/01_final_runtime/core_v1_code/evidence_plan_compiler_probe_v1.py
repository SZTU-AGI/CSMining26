#!/usr/bin/env python3
"""Zero-API probe of the live FRECA EvidenceRequirement compiler.

Inspects the currently imported evidence_reasoning_v2 on Server25 and exercises
only deterministic prompt/seed construction for representative CPs.
No DeepSeek/model call is made and no artifact is written under contracts_v2.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import evidence_reasoning_v2 as er
import freca_core_v1 as core


CONTRACT_DIR = Path("contracts_v2")
CP_IDS = ["CP1", "CP12", "CP6", "CP27"]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap_contract(x: dict) -> dict:
    return x.get("contract") if isinstance(x.get("contract"), dict) else x


def eligible_ids(ledger: dict) -> set[str]:
    if hasattr(er, "_candidate_maps"):
        _, decisions = er._candidate_maps(ledger)
    else:
        decisions = {}
        for row in ledger.get("decisions", []):
            if isinstance(row, dict) and row.get("candidate_id"):
                decisions[str(row["candidate_id"])] = row

    return {
        str(cid)
        for cid, d in decisions.items()
        if isinstance(d, dict)
        and d.get("selected")
        and d.get("relation") == "PRIMARY_NORM"
        and d.get("contract_eligible", False)
    }


print("=" * 88)
print("FRECA LIVE EVIDENCE-PLAN COMPILER PROBE V1 — ZERO API")
print("=" * 88)
print("evidence_reasoning_v2:", Path(er.__file__).resolve())
print("freca_core_v1:", Path(core.__file__).resolve())

for name in (
    "normalize_contract_atoms",
    "build_evidence_facet_seeds",
    "make_evidence_requirement_prompt",
    "validate_evidence_requirements",
    "compile_evidence_requirements",
):
    obj = getattr(er, name, None)
    print(f"\n{name}:")
    if obj is None:
        print("  MISSING")
        continue
    print("  signature:", inspect.signature(obj))
    try:
        src = inspect.getsource(obj)
        print("  source markers:")
        for marker in (
            "exactly one",
            "facet_seed",
            "FACET_SEED",
            "normalize_contract_atoms",
            "build_evidence_facet_seeds",
        ):
            if marker in src:
                print("   -", marker)
    except Exception as exc:
        print("  source unavailable:", exc)

for cp_id in CP_IDS:
    print("\n" + "#" * 88)
    print(cp_id)
    print("#" * 88)

    cp = core.get_cp(cp_id)
    contract_bundle = load(CONTRACT_DIR / f"{cp_id}.json")
    contract = unwrap_contract(contract_bundle)
    ledger = load(CONTRACT_DIR / f"{cp_id}_candidate_ledger.json")
    relation = load(CONTRACT_DIR / f"{cp_id}_rule_set_relation.json")

    atoms = [
        str(row.get("atom_id"))
        for row in (contract.get("atoms") or [])
        if isinstance(row, dict) and row.get("atom_id")
    ]
    elig = eligible_ids(ledger)

    print("atoms:", atoms)
    print("satisfaction:", json.dumps(contract.get("satisfaction"), ensure_ascii=False))
    print("eligible PRIMARY_NORM ids:", len(elig))
    print("relation groups:", [
        g.get("group_id")
        for g in relation.get("groups", [])
        if isinstance(g, dict)
    ])

    seed_builder = getattr(er, "build_evidence_facet_seeds", None)
    if seed_builder is not None:
        print("facet seeds by atom:")
        for atom_id in atoms:
            try:
                sig = inspect.signature(seed_builder)
                if "atom_id" in sig.parameters:
                    seeds = seed_builder(relation, elig, atom_id=atom_id)
                else:
                    seeds = seed_builder(relation, elig)
                print(
                    " ", atom_id, "=>",
                    [
                        {
                            "facet_seed_id": s.get("facet_seed_id"),
                            "atom_id": s.get("atom_id"),
                            "source_group_ids": s.get("source_group_ids"),
                            "basis_candidate_ids": s.get("basis_candidate_ids"),
                            "seed_relation": s.get("seed_relation"),
                        }
                        for s in seeds
                    ],
                )
            except Exception as exc:
                print(" ", atom_id, "=> ERROR:", type(exc).__name__, str(exc))

    print("prompt construction:")
    try:
        prompt = er.make_evidence_requirement_prompt(
            cp, contract, ledger, relation
        )
        print("  PASS")
        print("  chars:", len(prompt))
        if "FACET_SEED" in prompt or "facet_seeds" in prompt:
            print("  facet seeds included: True")
        else:
            print("  facet seeds included: False")
    except Exception as exc:
        print("  FAIL:", type(exc).__name__, str(exc))

print("\nAPI called: False")
print("Artifacts mutated: False")
