#!/usr/bin/env python3
"""FRECA observed-shape multi-atom Argument projection helpers v1.

Generalizes the existing one-atom Argument substrate to the observed
satisfaction operators ATOM and nested ALL.

EvidenceRequirements remain evidence facets of their frozen legal atom.
The legal satisfaction AST is evaluated only after each atom's argument
standing has been projected from its own requirements.

No case evidence is consumed at template compile time.
No final label or answer comparator is used.
"""

from __future__ import annotations

from typing import Any

import multi_atom_support_v1 as multi


def _ordered_atom_refs(expr: dict | None) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def walk(node):
        if not isinstance(node, dict):
            raise ValueError("Contract satisfaction expression is missing")

        op = str(node.get("op") or "").upper()

        if op == "ATOM":
            atom_id = str(node.get("atom_id") or "").strip()
            if not atom_id:
                raise ValueError("ATOM satisfaction node missing atom_id")
            if atom_id not in seen:
                seen.add(atom_id)
                refs.append(atom_id)
            return

        if op == "ALL":
            children = node.get("children")
            if not isinstance(children, list) or not children:
                raise ValueError("ALL satisfaction node requires children")
            for child in children:
                walk(child)
            return

        raise ValueError(
            f"Observed Argument v1 supports only ATOM/ALL satisfaction; got {op}"
        )

    walk(expr)
    return refs


def compile_argument_template(
    arg_env: dict[str, Any],
    *,
    contract_bundle: dict,
    evidence_requirement_plan: dict,
) -> dict:
    StatementTemplate = arg_env["StatementTemplate"]
    ArgumentPremiseTemplate = arg_env["ArgumentPremiseTemplate"]
    ArgumentTemplate = arg_env["ArgumentTemplate"]
    LogicOperator = arg_env["LogicOperator"]
    PremiseRole = arg_env["PremiseRole"]
    RequiredPolarity = arg_env["RequiredPolarity"]
    ArgumentDirection = arg_env["ArgumentDirection"]
    model_dump = arg_env["model_dump"]
    sha256_json = arg_env["sha256_json"]

    contract = contract_bundle.get("contract") or contract_bundle
    cp_id = str(
        contract.get(
            "cp_id",
            evidence_requirement_plan.get("cp_id", ""),
        )
    )
    if not cp_id:
        raise ValueError("Missing cp_id")

    satisfaction = contract.get("satisfaction")
    atom_refs = _ordered_atom_refs(satisfaction)

    atoms = {
        str(item["atom_id"]): item
        for item in (contract.get("atoms", []) or [])
        if isinstance(item, dict) and item.get("atom_id")
    }

    missing_atoms = [a for a in atom_refs if a not in atoms]
    if missing_atoms:
        raise ValueError(
            "Satisfaction references missing contract atoms: "
            + ", ".join(missing_atoms)
        )

    requirements = list(
        evidence_requirement_plan.get("requirements", []) or []
    )
    if not requirements:
        raise ValueError("EvidenceRequirement plan is empty")

    by_atom: dict[str, list[dict]] = {a: [] for a in atom_refs}
    outside = []

    for requirement in requirements:
        atom_id = str(requirement.get("atom_id") or "")
        if atom_id not in by_atom:
            outside.append(
                str(requirement.get("requirement_id") or "")
            )
            continue
        by_atom[atom_id].append(requirement)

    if outside:
        raise ValueError(
            "EvidenceRequirements target atoms outside satisfaction AST: "
            + ", ".join(outside)
        )

    empty_atoms = [
        atom_id for atom_id, rows in by_atom.items()
        if not rows
    ]
    if empty_atoms:
        raise ValueError(
            "Satisfaction atoms have no EvidenceRequirements: "
            + ", ".join(empty_atoms)
        )

    requirement_statements = []
    requirement_statement_ids: dict[str, str] = {}

    for requirement in requirements:
        rid = str(requirement["requirement_id"])
        statement_id = f"stmt-{rid.lower()}"
        if rid in requirement_statement_ids:
            raise ValueError(f"Duplicate requirement_id: {rid}")
        requirement_statement_ids[rid] = statement_id

        requirement_statements.append(
            StatementTemplate(
                statement_id=statement_id,
                logic_node_id=f"logic-{rid.lower()}",
                proposition_id=f"prop-{rid.lower()}",
                semantic_role="OBSERVABLE_CASE_FACT",
                logic_operator=LogicOperator.ATOM,
                direct_alignment_policy="ALLOW_OBSERVABLE_ONLY",
                proof_standard_id="proof-audit-sufficient",
                burden_rule_id="burden-no-adverse-from-absence",
                source_span_ids=[],
            )
        )

    atom_benchmark_statements = []
    atom_benchmark_statement_ids: dict[str, str] = {}
    arguments = []

    for atom_id in atom_refs:
        rows = by_atom[atom_id]
        benchmark_statement_id = f"stmt-{atom_id.lower()}-benchmark"
        atom_benchmark_statement_ids[atom_id] = benchmark_statement_id

        atom_benchmark_statements.append(
            StatementTemplate(
                statement_id=benchmark_statement_id,
                logic_node_id=f"logic-{atom_id.lower()}-benchmark",
                proposition_id=f"prop-{atom_id.lower()}-benchmark",
                semantic_role="LEGAL_SATISFACTION_ATOM",
                logic_operator=(
                    LogicOperator.ALL_OF
                    if len(rows) > 1
                    else LogicOperator.ATOM
                ),
                direct_alignment_policy="FORBID",
                proof_standard_id="proof-structural-deterministic",
                burden_rule_id="burden-no-adverse-from-absence",
                source_span_ids=[],
            )
        )

        positive_premises = [
            ArgumentPremiseTemplate(
                statement_id=requirement_statement_ids[
                    str(row["requirement_id"])
                ],
                premise_role=PremiseRole.ORDINARY,
                required_polarity=RequiredPolarity.POSITIVE,
            )
            for row in rows
        ]

        arguments.append(
            ArgumentTemplate(
                argument_id=f"arg-{atom_id.lower()}-benchmark-pro",
                scheme="BENCHMARK_OPERATIONALIZATION",
                premises=positive_premises,
                conclusion_statement_id=benchmark_statement_id,
                direction=ArgumentDirection.PRO,
                source_norm_event_ids=[],
                source_span_ids=[],
            )
        )

        for row in rows:
            rid = str(row["requirement_id"])
            arguments.append(
                ArgumentTemplate(
                    argument_id=f"arg-{atom_id.lower()}-{rid.lower()}-con",
                    scheme="RULE_VIOLATION",
                    premises=[
                        ArgumentPremiseTemplate(
                            statement_id=requirement_statement_ids[rid],
                            premise_role=PremiseRole.ORDINARY,
                            required_polarity=RequiredPolarity.NEGATIVE,
                        )
                    ],
                    conclusion_statement_id=benchmark_statement_id,
                    direction=ArgumentDirection.CON,
                    source_norm_event_ids=[],
                    source_span_ids=[],
                )
            )

    if len(atom_refs) == 1:
        root_statement_id = atom_benchmark_statement_ids[atom_refs[0]]
        root_statement = None
    else:
        root_statement_id = f"stmt-{cp_id.lower()}-satisfaction-root"
        root_statement = StatementTemplate(
            statement_id=root_statement_id,
            logic_node_id=f"logic-{cp_id.lower()}-satisfaction-root",
            proposition_id=f"prop-{cp_id.lower()}-satisfaction-root",
            semantic_role="LEGAL_SATISFACTION_ROOT",
            logic_operator=LogicOperator.ALL_OF,
            direct_alignment_policy="FORBID",
            proof_standard_id="proof-structural-deterministic",
            burden_rule_id="burden-no-adverse-from-absence",
            source_span_ids=[],
        )

    statement_nodes = (
        requirement_statements
        + atom_benchmark_statements
        + ([root_statement] if root_statement is not None else [])
    )

    payload = {
        "schema": "freca-core-argument-template-v2-multi-atom-all",
        "cp_id": cp_id,
        "atom_id": atom_refs[0] if len(atom_refs) == 1 else None,
        "atom_ids": atom_refs,
        "atom_proposition": (
            atoms[atom_refs[0]].get("proposition")
            if len(atom_refs) == 1
            else None
        ),
        "atom_propositions": {
            atom_id: atoms[atom_id].get("proposition")
            for atom_id in atom_refs
        },
        "requirement_statement_ids": requirement_statement_ids,
        "requirements_by_atom": {
            atom_id: [
                str(row["requirement_id"])
                for row in by_atom[atom_id]
            ]
            for atom_id in atom_refs
        },
        "atom_benchmark_statement_ids":
            atom_benchmark_statement_ids,
        "benchmark_statement_id": root_statement_id,
        "satisfaction_ast": satisfaction,
        "statement_nodes": [
            model_dump(item)
            for item in statement_nodes
        ],
        "argument_nodes": [
            model_dump(item)
            for item in arguments
        ],
        "compile_constraints": {
            "evidence_blind": True,
            "historical_labels_consumed": False,
            "case_evidence_consumed": False,
            "answer_comparator_used": False,
            "supported_satisfaction_ops": ["ATOM", "ALL"],
            "direct_alignment_target_roles":
                ["OBSERVABLE_CASE_FACT"],
            "benchmark_direct_alignment_forbidden": True,
        },
    }

    payload["template_sha256"] = sha256_json(payload)
    return payload


def evaluate_argument_template(
    arg_env: dict[str, Any],
    *,
    template: dict,
    requirement_states: dict,
) -> dict:
    ArgumentTemplate = arg_env["ArgumentTemplate"]
    FourValuedState = arg_env["FourValuedState"]
    evaluate_argument_standing = arg_env[
        "evaluate_argument_standing"
    ]
    state_from_pair = arg_env["state_from_pair"]

    statement_states = {
        f"stmt-{rid.lower()}": state
        for rid, state in requirement_states.items()
    }

    arguments = [
        ArgumentTemplate.model_validate(item)
        for item in template["argument_nodes"]
    ]

    standings = []

    for argument in arguments:
        standing, reason_codes = evaluate_argument_standing(
            argument,
            statement_states,
        )

        standings.append({
            "argument_id": argument.argument_id,
            "scheme": argument.scheme,
            "direction": argument.direction.value,
            "conclusion_statement_id":
                argument.conclusion_statement_id,
            "standing": standing,
            "standing_reason_codes": reason_codes,
            "premises": [
                {
                    "statement_id": premise.statement_id,
                    "required_polarity":
                        premise.required_polarity.value,
                    "premise_role": premise.premise_role.value,
                    "observed_state": statement_states.get(
                        premise.statement_id,
                        FourValuedState.UNKNOWN,
                    ).value,
                }
                for premise in argument.premises
            ],
        })

    atom_ids = list(template.get("atom_ids") or [])
    atom_statement_ids = dict(
        template.get("atom_benchmark_statement_ids") or {}
    )

    # Backward compatibility for an old single-atom template.
    if not atom_ids and template.get("atom_id"):
        atom_ids = [str(template["atom_id"])]
        atom_statement_ids = {
            atom_ids[0]: str(template["benchmark_statement_id"])
        }

    atom_states = {}
    atom_evaluations = {}

    for atom_id in atom_ids:
        stmt_id = atom_statement_ids[atom_id]

        pro_in = [
            row for row in standings
            if (
                row["conclusion_statement_id"] == stmt_id
                and row["direction"] == "PRO"
                and row["standing"] == "IN"
            )
        ]
        con_in = [
            row for row in standings
            if (
                row["conclusion_statement_id"] == stmt_id
                and row["direction"] == "CON"
                and row["standing"] == "IN"
            )
        ]
        conflicted = [
            row for row in standings
            if (
                row["conclusion_statement_id"] == stmt_id
                and row["standing"] == "CONFLICTED"
            )
        ]
        undecided = [
            row for row in standings
            if (
                row["conclusion_statement_id"] == stmt_id
                and row["standing"] == "UNDECIDED"
            )
        ]

        # Four-valued projection is computed from the decisive facet states,
        # not merely from IN argument IDs.  D7.13 correctly marks an
        # argument whose premise is BOTH as CONFLICTED; if we used only
        # pro_in/con_in here, that would collapse a genuine BOTH facet to
        # UNKNOWN.  For an atom operationalised by evidence facets, the
        # paraconsistent conjunction is:
        #   support(atom) = ALL facets have support
        #   attack(atom)  = ANY facet has attack
        # This preserves BOTH and matches the recursive ALL semantics used
        # by the outcome adapter.
        atom_requirement_ids = list(
            (template.get("requirements_by_atom") or {}).get(atom_id, [])
        )
        if not atom_requirement_ids:
            raise ValueError(
                f"Atom {atom_id} has no requirement projection in template"
            )

        facet_states = [
            requirement_states.get(rid, FourValuedState.UNKNOWN)
            for rid in atom_requirement_ids
        ]
        support = all(bool(state.pair[0]) for state in facet_states)
        attack = any(bool(state.pair[1]) for state in facet_states)
        atom_state = state_from_pair(support, attack)

        atom_states[atom_id] = {
            "support": bool(support),
            "attack": bool(attack),
            "state": atom_state.value,
            "requirement_ids": atom_requirement_ids,
        }

        atom_evaluations[atom_id] = {
            "benchmark_statement_id": stmt_id,
            "state": atom_state.value,
            "standing_pro_argument_ids": [
                row["argument_id"]
                for row in pro_in
            ],
            "standing_con_argument_ids": [
                row["argument_id"]
                for row in con_in
            ],
            "conflicted_argument_ids": [
                row["argument_id"]
                for row in conflicted
            ],
            "undecided_argument_ids": [
                row["argument_id"]
                for row in undecided
            ],
        }

    satisfaction_ast = (
        template.get("satisfaction_ast")
        or {
            "op": "ATOM",
            "atom_id": template.get("atom_id"),
        }
    )

    root_support, root_attack, root_reasons = multi.eval_expr(
        satisfaction_ast,
        atom_states,
    )
    root_state = state_from_pair(
        root_support,
        root_attack,
    )

    root_pro_ids = []
    root_con_ids = []
    conflicted_ids = []
    undecided_ids = []

    for atom_id in atom_ids:
        ev = atom_evaluations[atom_id]

        if root_support:
            root_pro_ids.extend(
                ev["standing_pro_argument_ids"]
            )

        if ev["standing_con_argument_ids"]:
            root_con_ids.extend(
                ev["standing_con_argument_ids"]
            )

        conflicted_ids.extend(
            ev["conflicted_argument_ids"]
        )
        undecided_ids.extend(
            ev["undecided_argument_ids"]
        )

    return {
        "benchmark_statement_id":
            template["benchmark_statement_id"],
        "state": root_state.value,
        "satisfaction_ast_state": root_state.value,
        "satisfaction_ast_reason_codes":
            sorted(set(root_reasons)),
        "atom_evaluations": atom_evaluations,
        "standing_pro_argument_ids":
            sorted(set(root_pro_ids)),
        "standing_con_argument_ids":
            sorted(set(root_con_ids)),
        "conflicted_argument_ids":
            sorted(set(conflicted_ids)),
        "undecided_argument_ids":
            sorted(set(undecided_ids)),
        "argument_instances": standings,
    }


def self_test() -> None:
    refs = _ordered_atom_refs({
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
    })
    assert refs == ["A1", "A2", "A3"]
    print("multi_atom_argument_support_v1 self-tests: PASS")
    # Regression target: evaluate_argument_template must preserve a BOTH
    # requirement through atom projection.  The full integration assertion is
    # exercised by multi_atom_contract_audit_v1_1.py against argument_core_v1.
    print("  ordered nested-ALL atom projection")
    print("  four-valued facet conjunction (ALL-support / ANY-attack)")
    print("  no case evidence / no final label / no comparator")


if __name__ == "__main__":
    self_test()
