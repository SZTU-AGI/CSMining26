#!/usr/bin/env python3
"""FRECA observed-shape multi-atom support helpers v1.

Supports only the contract shapes actually observed in the 32-contract census:
- applicability: CONST
- non_applicability: CONST
- satisfaction: ATOM or nested ALL

No ANY/NOT/IF_THEN, no conditional applicability, no positive-N/A inference.
No final labels and no answer comparator.
"""

from __future__ import annotations

import json
from typing import Any


MULTI_ATOM_EVIDENCE_SYSTEM = r"""
You are the EvidenceRequirement compiler in a closed-source compliance system.

You are working BEFORE any case evidence is accessed. Use only the supplied
official checking-point criterion, frozen Core contract, validated
CandidateLedger, validated RuleSetRelation, and deterministic FACET_SEEDS.

Do NOT create new legal obligations. Do NOT decide compliance.

A frozen contract may contain multiple legal atoms. Each FACET_SEED belongs to
exactly one frozen atom. EvidenceRequirements are evidence/proof facets for that
atom; they are not new contract logic leaves.

Rules:
- Return EXACTLY ONE DECISIVE EvidenceRequirement for every FACET_SEED.
- Do not omit, merge, or split FACET_SEEDS.
- atom_id must equal the seed atom_id.
- basis_candidate_ids must be a non-empty subset of that seed's candidate IDs.
- RULES query_sources may cite only candidate IDs in that seed and must copy an
  exact supplied Rules quote.
- CP query_sources must copy exact official CP text.
- proposition_to_establish must not add an obligation beyond the frozen atom.
- Do not mention cases, filenames, tracks, labels, historical outputs, or
  expected answers.

Return JSON only.
"""



# FRECA CONTRACT-BUNDLE NORMALIZATION V1
def _unwrap_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("Contract/bundle must be an object")

    inner = contract.get("contract")
    if isinstance(inner, dict):
        return inner

    return contract


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



def _group_members(group: dict) -> list[str]:
    for key in (
        "member_candidate_ids",
        "member_ids",
        "candidate_ids",
        "members",
    ):
        value = group.get(key)
        if isinstance(value, list):
            return [str(x) for x in value if str(x)]
    return []


def _build_multi_atom_facet_seeds_before_identical_basis_dedup_v1(
    contract: dict,
    rule_set_relation: dict,
    eligible_ids: set[str],
) -> list[dict]:
    """Derive deterministic evidence facets separately for each frozen atom."""
    atoms = _atom_definitions(contract)
    groups = [
        g for g in (rule_set_relation.get("groups", []) or [])
        if isinstance(g, dict)
    ]

    seeds = []
    seen = set()

    def add_seed(atom_id, seed_id, group_ids, members, relation):
        members = sorted({
            str(m) for m in members if str(m) in eligible_ids
        })
        if not members:
            return
        key = (atom_id, tuple(group_ids), tuple(members), relation)
        if key in seen:
            return
        seen.add(key)
        seeds.append({
            "facet_seed_id": seed_id,
            "atom_id": atom_id,
            "source_group_ids": list(group_ids),
            "basis_candidate_ids": members,
            "seed_relation": relation,
        })

    for atom_id in sorted(atoms):
        atom = atoms[atom_id]
        atom_basis = [
            str(x)
            for x in (atom.get("basis_candidate_ids", []) or [])
            if str(x) in eligible_ids
        ]
        if not atom_basis:
            raise ValueError(
                f"{atom_id}: no contract-eligible PRIMARY_NORM basis candidates"
            )

        atom_basis_set = set(atom_basis)
        before = len(seeds)

        for group in sorted(groups, key=lambda g: str(g.get("group_id") or "")):
            group_id = str(group.get("group_id") or "").strip()
            relation = str(group.get("relation") or "").strip().upper()
            local_members = [
                m for m in _group_members(group) if m in atom_basis_set
            ]
            if not local_members:
                continue

            if relation in {"CONTESTED", "UNRESOLVED"}:
                raise ValueError(
                    f"{atom_id}: unresolved RuleSetRelation group "
                    f"{group_id}: {relation}"
                )

            if relation in {"SPECIALIZES", "ALTERNATIVE"}:
                add_seed(
                    atom_id,
                    f"FS-{atom_id}-{group_id}",
                    [group_id] if group_id else [],
                    local_members,
                    relation,
                )
            elif relation == "CUMULATIVE":
                for index, member in enumerate(sorted(set(local_members)), 1):
                    add_seed(
                        atom_id,
                        f"FS-{atom_id}-{group_id}-{index}",
                        [group_id] if group_id else [],
                        [member],
                        "CUMULATIVE_MEMBER",
                    )
            # SUPPORTS_SAME_CRITERION and CONTEXT_ONLY do not create
            # independent mandatory evidence facets.

        if len(seeds) == before:
            add_seed(
                atom_id,
                f"FS-{atom_id}-FALLBACK",
                [],
                atom_basis,
                "FALLBACK_ATOM_BASIS",
            )

    per_atom = {}
    for seed in seeds:
        per_atom[seed["atom_id"]] = per_atom.get(seed["atom_id"], 0) + 1
    too_many = {k: v for k, v in per_atom.items() if v > 6}
    if too_many:
        raise ValueError(
            "EvidenceRequirement over-atomization per atom: " + repr(too_many)
        )

    return seeds

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
        "INPUT_JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nCompile exactly one DECISIVE EvidenceRequirement per "
          "FACET_SEED. Return JSON only."
    )



def validate_evidence_requirements(
    er_env: dict[str, Any],
    raw: dict,
    cp: dict,
    contract: dict,
    ledger_artifact: dict,
    rule_set_relation: dict,
) -> dict:
    requirements = raw.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("EvidenceRequirement compiler returned no requirements list")

    normalize_ws = er_env["normalize_ws"]
    quote_match_mode = er_env["quote_match_mode"]
    safe_id_re = er_env["_SAFE_ID_RE"]
    candidates, decisions = er_env["_candidate_maps"](ledger_artifact)
    atoms = _atom_definitions(contract)

    eligible_ids = {
        candidate_id
        for candidate_id, decision in decisions.items()
        if (
            decision.get("selected")
            and decision.get("relation") == "PRIMARY_NORM"
            and decision.get("contract_eligible", False)
        )
    }

    seeds = build_multi_atom_facet_seeds(
        contract, rule_set_relation, eligible_ids
    )
    seed_map = {seed["facet_seed_id"]: seed for seed in seeds}
    valid_groups = {
        str(g.get("group_id"))
        for g in (rule_set_relation.get("groups", []) or [])
        if isinstance(g, dict) and g.get("group_id")
    }

    validated = []
    seen_ids = set()
    seen_seeds = set()
    seen_semantics = set()

    for item in requirements:
        if not isinstance(item, dict):
            raise ValueError("EvidenceRequirement item must be an object")

        rid = str(item.get("requirement_id") or "").strip()
        if not rid or not safe_id_re.fullmatch(rid):
            raise ValueError(f"Invalid requirement_id: {rid!r}")
        if rid in seen_ids:
            raise ValueError(f"Duplicate requirement_id: {rid}")
        seen_ids.add(rid)

        seed_id = str(item.get("facet_seed_id") or "").strip()
        if seed_id not in seed_map:
            raise ValueError(f"{rid}: unknown facet_seed_id {seed_id!r}")
        if seed_id in seen_seeds:
            raise ValueError(f"{rid}: duplicate requirement for seed {seed_id}")
        seen_seeds.add(seed_id)
        seed = seed_map[seed_id]

        atom_id = str(item.get("atom_id") or "").strip()
        if atom_id != seed["atom_id"] or atom_id not in atoms:
            raise ValueError(
                f"{rid}: atom_id {atom_id!r} does not match seed atom "
                f"{seed['atom_id']!r}"
            )

        proposition = str(item.get("proposition_to_establish") or "").strip()
        if not proposition:
            raise ValueError(f"{rid}: empty proposition_to_establish")
        semantic_key = (atom_id, normalize_ws(proposition).lower())
        if semantic_key in seen_semantics:
            raise ValueError(f"{rid}: duplicate semantic facet within {atom_id}")
        seen_semantics.add(semantic_key)

        if item.get("polarity") != "SUPPORT":
            raise ValueError(f"{rid}: polarity must be SUPPORT")
        if item.get("decisiveness") != "DECISIVE":
            raise ValueError(
                f"{rid}: each deterministic facet seed must compile to one "
                "DECISIVE requirement"
            )

        criterion_quote = str(item.get("criterion_quote") or "").strip()
        criterion_mode = quote_match_mode(criterion_quote, cp["criterion"])
        if criterion_mode is None:
            raise ValueError(f"{rid}: criterion_quote is not grounded")

        basis_ids = [str(x) for x in (item.get("basis_candidate_ids", []) or [])]
        if not basis_ids:
            raise ValueError(f"{rid}: decisive requirement has no legal basis")
        outside = set(basis_ids) - set(seed["basis_candidate_ids"])
        if outside:
            raise ValueError(
                f"{rid}: basis candidates outside {seed_id}: {sorted(outside)}"
            )

        group_ids = [str(x) for x in (item.get("source_group_ids", []) or [])]
        unknown = set(group_ids) - valid_groups
        if unknown:
            raise ValueError(f"{rid}: unknown RuleSetRelation groups {sorted(unknown)}")
        missing = set(seed["source_group_ids"]) - set(group_ids)
        if missing:
            raise ValueError(f"{rid}: missing seed source groups {sorted(missing)}")

        query_sources = item.get("query_sources", [])
        if not isinstance(query_sources, list) or not query_sources:
            raise ValueError(f"{rid}: query_sources must be non-empty")

        checked_sources = []
        seed_candidates = set(seed["basis_candidate_ids"])
        for source in query_sources:
            if not isinstance(source, dict):
                raise ValueError(f"{rid}: invalid query source")
            source_type = str(source.get("source") or "").upper()
            candidate_id = source.get("candidate_id")
            quote = str(source.get("quote") or "").strip()
            if not quote:
                raise ValueError(f"{rid}: empty query source quote")

            if source_type == "CP":
                if quote_match_mode(quote, cp["criterion"]) is None:
                    raise ValueError(f"{rid}: ungrounded CP query quote")
                candidate_id = None
            elif source_type == "RULES":
                candidate_id = str(candidate_id or "")
                if candidate_id not in seed_candidates:
                    raise ValueError(
                        f"{rid}: Rules query candidate {candidate_id} lies "
                        f"outside {seed_id}"
                    )
                candidate = candidates.get(candidate_id)
                if not candidate:
                    raise ValueError(f"{rid}: unknown Rules candidate {candidate_id}")
                own_text = candidate.get("own_text", candidate.get("text", ""))
                if quote_match_mode(quote, own_text) is None:
                    raise ValueError(
                        f"{rid}: Rules query quote not grounded in {candidate_id}"
                    )
            else:
                raise ValueError(f"{rid}: invalid query source {source_type}")

            checked_sources.append({
                "source": source_type,
                "candidate_id": candidate_id,
                "quote": quote,
            })

        validated.append({
            "requirement_id": rid,
            "facet_seed_id": seed_id,
            "atom_id": atom_id,
            "proposition_to_establish": proposition,
            "polarity": "SUPPORT",
            "decisiveness": "DECISIVE",
            "criterion_quote": criterion_quote,
            "criterion_match_mode": criterion_mode,
            "basis_candidate_ids": basis_ids,
            "source_group_ids": group_ids,
            "seed_relation": seed["seed_relation"],
            "query_sources": checked_sources,
            "reason": str(item.get("reason") or ""),
        })

    missing_seeds = set(seed_map) - seen_seeds
    if missing_seeds:
        raise ValueError(
            "EvidenceRequirement compiler omitted deterministic facet seeds: "
            f"{sorted(missing_seeds)}"
        )
    if len(validated) != len(seeds):
        raise ValueError(
            f"EvidenceRequirement count must equal facet seed count: "
            f"{len(validated)} != {len(seeds)}"
        )

    return {
        "schema": "freca-core-evidence-requirements-v2-multi-atom-v1",
        "cp_id": cp["cp_id"],
        "requirements": validated,
        "facet_seeds": seeds,
        "pilot_only": False,
        "notes": [
            "EvidenceRequirements remain proof facets, not contract leaves.",
            "Each facet is scoped to one frozen contract atom.",
            "Observed production satisfaction operators: ATOM/ALL.",
        ],
        "answer_comparator_used": False,
    }


def pair_to_four(support: bool, attack: bool) -> str:
    if support and attack:
        return "BOTH"
    if support:
        return "TRUE"
    if attack:
        return "FALSE"
    return "UNKNOWN"


def _reports_by_atom(requirement_result: dict, proof_bundle: dict):
    meta = {
        str(r.get("requirement_id")): r
        for r in (
            requirement_result.get("evidence_requirement_plan", {})
            .get("requirements", []) or []
        )
        if isinstance(r, dict)
        and r.get("requirement_id")
        and str(r.get("decisiveness", "")).upper() == "DECISIVE"
    }
    proof = {
        str(r.get("requirement_id")): r
        for r in (proof_bundle.get("requirement_reports", []) or [])
        if isinstance(r, dict) and r.get("requirement_id")
    }

    by_atom = {}
    reasons = []
    for rid, row in sorted(meta.items()):
        atom_id = str(row.get("atom_id") or "").strip()
        if not atom_id:
            reasons.append(f"DECISIVE_REQUIREMENT_MISSING_ATOM_ID:{rid}")
            continue
        if rid not in proof:
            reasons.append(f"PROOF_BUNDLE_MISSING_DECISIVE_REQUIREMENT:{rid}")
            continue
        by_atom.setdefault(atom_id, []).append(proof[rid])
    return by_atom, reasons


def atom_states(contract: dict, requirement_result: dict, proof_bundle: dict):
    atoms = _atom_definitions(contract)
    by_atom, reasons = _reports_by_atom(requirement_result, proof_bundle)
    states = {}

    for atom_id in sorted(atoms):
        rows = by_atom.get(atom_id, [])
        if not rows:
            code = f"NO_DECISIVE_EVIDENCE_REQUIREMENT_FOR_ATOM:{atom_id}"
            states[atom_id] = {
                "support": False,
                "attack": False,
                "state": "UNKNOWN",
                "decisive_requirement_ids": [],
                "reason_codes": [code],
            }
            reasons.append(code)
            continue

        support = all(
            (row.get("support_proof") or {}).get("accepted_direction") is True
            for row in rows
        )
        attack = any(
            (row.get("attack_proof") or {}).get("accepted_direction") is True
            for row in rows
        )
        states[atom_id] = {
            "support": bool(support),
            "attack": bool(attack),
            "state": pair_to_four(bool(support), bool(attack)),
            "decisive_requirement_ids": sorted(
                str(row.get("requirement_id")) for row in rows
            ),
            "reason_codes": [],
        }

    return states, reasons


def eval_expr(expr: dict | None, states: dict[str, dict]):
    if not isinstance(expr, dict):
        return False, False, ["CONTRACT_EXPRESSION_MISSING"]
    op = str(expr.get("op") or "").upper()

    if op == "CONST":
        value = expr.get("value")
        if value is True:
            return True, False, []
        if value is False:
            return False, True, []
        return False, False, ["CONST_ROOT_VALUE_INVALID"]

    if op == "ATOM":
        atom_id = str(expr.get("atom_id") or "").strip()
        state = states.get(atom_id)
        if state is None:
            return False, False, [f"UNKNOWN_CONTRACT_ATOM:{atom_id}"]
        return (
            bool(state["support"]),
            bool(state["attack"]),
            list(state.get("reason_codes") or []),
        )

    if op == "ALL":
        children = expr.get("children")
        if not isinstance(children, list) or not children:
            return False, False, ["ALL_REQUIRES_NONEMPTY_CHILDREN"]
        results = [eval_expr(child, states) for child in children]
        support = all(row[0] for row in results)
        attack = any(row[1] for row in results)
        reasons = []
        for _, _, child_reasons in results:
            reasons.extend(child_reasons)
        return support, attack, reasons

    return False, False, [f"UNSUPPORTED_SATISFACTION_OPERATOR:{op or 'UNKNOWN'}"]


def derive_root_states(
    adapter_env: dict[str, Any],
    *,
    contract_bundle: dict,
    proof_bundle: dict,
    requirement_result: dict,
) -> dict:
    contract = adapter_env["unwrap_contract"](contract_bundle)
    applicability, app_codes = adapter_env["const_root_state"](
        contract.get("applicability")
    )
    non_applicability, na_codes = adapter_env["const_root_state"](
        contract.get("non_applicability")
    )

    arg = adapter_env["accepted_argument_summary"](proof_bundle)

    # Backward-compatible diagnostic path for the adapter's pre-existing
    # synthetic fixtures, which intentionally omitted contract atoms/AST.
    # Real canonical production contracts always carry atoms + satisfaction.
    if not contract.get("atoms") or not isinstance(contract.get("satisfaction"), dict):
        support = bool(arg["standing_pro_argument_ids"])
        attack = bool(arg["standing_con_argument_ids"])
        states = {}
        atom_codes = []
        expr_codes = ["LEGACY_FIXTURE_WITHOUT_CONTRACT_AST"]
    else:
        states, atom_codes = atom_states(
            contract, requirement_result, proof_bundle
        )
        support, attack, expr_codes = eval_expr(
            contract.get("satisfaction"), states
        )

    unresolved = app_codes + na_codes + atom_codes + expr_codes + arg["reason_codes"]
    if arg["conflicted_argument_ids"]:
        unresolved.append("ACCEPTED_ARGUMENT_CONFLICTED_PREMISE")
    if arg["undecided_argument_ids"]:
        unresolved.append("ACCEPTED_ARGUMENT_UNDECIDED")

    return {
        "applicability_state": applicability,
        "non_applicability_state": non_applicability,
        "satisfaction_state": "TRUE" if support else "UNKNOWN",
        "violation_state": "TRUE" if attack else "UNKNOWN",
        "satisfaction_four_valued_state": pair_to_four(support, attack),
        "atom_four_valued_states": states,
        "satisfaction_operator_support": {
            "observed_supported_ops": ["CONST", "ATOM", "ALL"],
            "unsupported_ops_are_unknown": True,
        },
        "accepted_argument_state": arg["state"],
        "standing_pro_argument_ids": arg["standing_pro_argument_ids"],
        "standing_con_argument_ids": arg["standing_con_argument_ids"],
        "conflicted_argument_ids": arg["conflicted_argument_ids"],
        "undecided_argument_ids": arg["undecided_argument_ids"],
        "unresolved_reason_codes": sorted(set(unresolved)),
    }


def self_test() -> None:
    contract = {
        "atoms": [
            {"atom_id": "A1", "basis_candidate_ids": ["r1"]},
            {"atom_id": "A2", "basis_candidate_ids": ["r2"]},
            {"atom_id": "A3", "basis_candidate_ids": ["r3"]},
        ],
        "satisfaction": {
            "op": "ALL",
            "children": [
                {"op": "ATOM", "atom_id": "A1"},
                {
                    "op": "ALL",
                    "children": [
                        {"op": "ATOM", "atom_id": "A2"},
                        {"op": "ATOM", "atom_id": "A3"},
                    ],
                },
            ],
        },
    }
    seeds = build_multi_atom_facet_seeds(contract, {"groups": []}, {"r1", "r2", "r3"})
    assert [s["atom_id"] for s in seeds] == ["A1", "A2", "A3"]

    states = {
        "A1": {"support": True, "attack": False},
        "A2": {"support": True, "attack": False},
        "A3": {"support": True, "attack": False},
    }
    assert eval_expr(contract["satisfaction"], states)[:2] == (True, False)
    states["A3"] = {"support": False, "attack": False}
    assert eval_expr(contract["satisfaction"], states)[:2] == (False, False)
    states["A3"] = {"support": False, "attack": True}
    assert eval_expr(contract["satisfaction"], states)[:2] == (False, True)
    states["A3"] = {"support": True, "attack": True}
    assert eval_expr(contract["satisfaction"], states)[:2] == (True, True)

    print("multi_atom_support_v1 self-tests: PASS")
    print("  per-atom facet seeds")
    print("  nested ALL support/attack semantics")
    print("  BOTH preserved")
    print("  no final label / no answer comparator")


if __name__ == "__main__":
    self_test()
