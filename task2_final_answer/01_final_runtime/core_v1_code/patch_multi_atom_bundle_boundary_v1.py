#!/usr/bin/env python3
from __future__ import annotations

import ast
import shutil
from pathlib import Path

TARGET = Path("multi_atom_support_v1.py")
MARKER = "FRECA CONTRACT-BUNDLE NORMALIZATION V1"


def find_top_level_function(source: str, name: str):
    tree = ast.parse(source)
    matches = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one top-level {name}; found {len(matches)}")
    return matches[0]


def insert_before_function(source: str, name: str, block: str) -> str:
    node = find_top_level_function(source, name)
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.lineno - 1] = [block.rstrip() + "\n\n"]
    return "".join(lines)


def replace_function(source: str, name: str, replacement: str) -> str:
    node = find_top_level_function(source, name)
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n\n"]
    return "".join(lines)


UNWRAP_BLOCK = """
# FRECA CONTRACT-BUNDLE NORMALIZATION V1
def _unwrap_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("Contract/bundle must be an object")

    inner = contract.get("contract")
    if isinstance(inner, dict):
        return inner

    return contract
"""


ATOM_FUNCTION = """
def _atom_definitions(contract: dict) -> dict[str, dict]:
    contract = _unwrap_contract(contract)

    atoms = {}
    for item in contract.get("atoms", []) or []:
        if not isinstance(item, dict):
            continue
        atom_id = str(item.get("atom_id") or "").strip()
        if atom_id:
            atoms[atom_id] = item

    if not atoms:
        raise ValueError("Frozen contract contains no atom definitions")

    return atoms
"""


PROMPT_FUNCTION = """
def make_evidence_requirement_prompt(
    er_env: dict[str, Any],
    cp: dict,
    contract: dict,
    ledger_artifact: dict,
    rule_set_relation: dict,
) -> str:
    contract = _unwrap_contract(contract)

    candidate_maps = er_env["_candidate_maps"]
    candidates, decisions = candidate_maps(ledger_artifact)

    eligible = []
    for candidate_id, decision in decisions.items():
        if not (
            decision.get("selected")
            and decision.get("relation") == "PRIMARY_NORM"
            and decision.get("contract_eligible", False)
        ):
            continue
        candidate = candidates.get(candidate_id)
        if not candidate:
            continue
        eligible.append({
            "candidate_id": candidate_id,
            "citation": candidate.get("citation", ""),
            "unit_type": candidate.get("unit_type", ""),
            "own_text": candidate.get("own_text", candidate.get("text", "")),
            "legal_basis_relation": decision.get("legal_basis_relation"),
            "legal_basis_cp_quote": decision.get("legal_basis_cp_quote", ""),
            "legal_basis_policy_quote": decision.get("legal_basis_policy_quote", ""),
        })

    eligible.sort(key=lambda x: x["candidate_id"])
    eligible_ids = {x["candidate_id"] for x in eligible}

    seeds = build_multi_atom_facet_seeds(
        contract,
        rule_set_relation,
        eligible_ids,
    )

    payload = {
        "official_cp": {
            "cp_id": cp["cp_id"],
            "subelement": cp.get("subelement", ""),
            "criterion": cp["criterion"],
        },
        "frozen_contract": {
            "atoms": contract.get("atoms", []),
            "satisfaction": contract.get("satisfaction"),
            "logic_basis": contract.get("logic_basis", []),
        },
        "eligible_primary_norms": eligible,
        "validated_rule_set_relation": rule_set_relation,
        "facet_seeds": seeds,
        "answer_comparator_used": False,
    }

    return (
        "INPUT_JSON:\\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\\n\\nCompile exactly one DECISIVE EvidenceRequirement per "
          "FACET_SEED. Return JSON only."
    )
"""


def patch_source(source: str) -> str:
    if MARKER in source:
        return source

    for token in (
        "def _atom_definitions(",
        "def make_evidence_requirement_prompt(",
        "def validate_evidence_requirements(",
        "def build_multi_atom_facet_seeds(",
    ):
        if token not in source:
            raise RuntimeError(f"Unexpected helper; missing {token}")

    patched = insert_before_function(
        source,
        "_atom_definitions",
        UNWRAP_BLOCK,
    )
    patched = replace_function(
        patched,
        "_atom_definitions",
        ATOM_FUNCTION,
    )
    patched = replace_function(
        patched,
        "make_evidence_requirement_prompt",
        PROMPT_FUNCTION,
    )

    ast.parse(patched)
    return patched


def self_test(patched: str) -> None:
    ns = {"__name__": "_bundle_fix_test"}
    exec(compile(patched, "<patched_helper>", "exec"), ns)

    inner = {
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
        "logic_basis": [],
    }
    bundle = {"schema": "fixture", "contract": inner}
    relation = {"groups": []}
    eligible = {"r1", "r2"}

    seeds_inner = ns["build_multi_atom_facet_seeds"](
        inner, relation, eligible
    )
    seeds_bundle = ns["build_multi_atom_facet_seeds"](
        bundle, relation, eligible
    )
    assert seeds_inner == seeds_bundle

    fake_env = {
        "_candidate_maps": lambda _ledger: (
            {
                "r1": {"candidate_id": "r1", "own_text": "rule one"},
                "r2": {"candidate_id": "r2", "own_text": "rule two"},
            },
            {
                "r1": {
                    "selected": True,
                    "relation": "PRIMARY_NORM",
                    "contract_eligible": True,
                },
                "r2": {
                    "selected": True,
                    "relation": "PRIMARY_NORM",
                    "contract_eligible": True,
                },
            },
        )
    }
    cp = {"cp_id": "CPX", "criterion": "fixture criterion"}

    prompt_inner = ns["make_evidence_requirement_prompt"](
        fake_env, cp, inner, {}, relation
    )
    prompt_bundle = ns["make_evidence_requirement_prompt"](
        fake_env, cp, bundle, {}, relation
    )
    assert prompt_inner == prompt_bundle

    assert ns["_atom_definitions"](inner) == ns["_atom_definitions"](bundle)

    print("bundle-boundary self-tests: PASS")
    print("  raw contract == canonical bundle atom definitions")
    print("  raw contract == canonical bundle facet seeds")
    print("  raw contract == canonical bundle prompt")
    print("  zero API / semantics unchanged")


def main() -> None:
    if not TARGET.exists():
        raise SystemExit(
            "Missing multi_atom_support_v1.py; "
            "run from /home/MeggieYu/freca/core_v1"
        )

    source = TARGET.read_text(encoding="utf-8")
    patched = patch_source(source)

    self_test(patched)

    if patched == source:
        print("Already installed:", MARKER)
        return

    backup = TARGET.with_name(
        TARGET.name + ".before_contract_bundle_fix_v1"
    )
    if not backup.exists():
        shutil.copy2(TARGET, backup)

    tmp = TARGET.with_suffix(TARGET.suffix + ".bundle_fix_v1.tmp")
    tmp.write_text(patched, encoding="utf-8")
    ast.parse(tmp.read_text(encoding="utf-8"))
    tmp.replace(TARGET)

    print("Installed:", MARKER)
    print("target:", TARGET)
    print("backup:", backup)


if __name__ == "__main__":
    main()
