#!/usr/bin/env python3
"""Zero-API audit for every observed canonical multi-atom ALL contract.

Requires install_multi_atom_all_v1.py to have been installed.

For each real canonical contract:
- validates applicability/non-applicability remain CONST;
- validates satisfaction uses only observed ATOM/ALL;
- validates every referenced atom exists;
- synthetic all-support proof -> PROVEN_COMPLIANT;
- synthetic one-atom attack -> PROVEN_NON_COMPLIANT;
- synthetic support+attack on one atom -> CONFLICTING;
- Argument projection itself preserves a BOTH facet as BOTH.

No case evidence, API, answer comparator, or final 1/0/N/A is used.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import core_outcome_adapter_v1 as adapter
import argument_core_v1 as argument


ALLOWED_SAT_OPS = {"ATOM", "ALL"}


def is_multi_atom_all_contract(contract: dict) -> bool:
    sat = contract.get("satisfaction")
    atoms = [row for row in contract.get("atoms", []) if isinstance(row, dict) and row.get("atom_id")]
    return len(atoms) > 1 and "ALL" in set(collect_ops(sat))


def discover_cp_ids(contract_dir: Path) -> list[str]:
    rows = []
    for path in sorted(contract_dir.glob("CP*.json"), key=lambda p: int(p.stem[2:]) if p.stem[2:].isdigit() else 10**9):
        bundle = load_json(path)
        contract = unwrap_contract(bundle)
        if is_multi_atom_all_contract(contract):
            rows.append(path.stem)
    return rows


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap_contract(bundle: dict) -> dict:
    value = bundle.get("contract")
    return value if isinstance(value, dict) else bundle


def collect_ops(expr: dict, out=None):
    out = [] if out is None else out
    if not isinstance(expr, dict):
        out.append("MISSING")
        return out

    op = str(expr.get("op") or "").upper()
    out.append(op)

    for child in expr.get("children") or []:
        collect_ops(child, out)

    return out


def collect_atom_refs(expr: dict, out=None):
    out = [] if out is None else out
    if not isinstance(expr, dict):
        return out

    op = str(expr.get("op") or "").upper()

    if op == "ATOM":
        atom_id = str(expr.get("atom_id") or "")
        if atom_id:
            out.append(atom_id)

    for child in expr.get("children") or []:
        collect_atom_refs(child, out)

    return out


def build_fixture(
    cp_id: str,
    contract: dict,
    *,
    attacked_atom: str | None = None,
    both_atom: str | None = None,
):
    atoms = [
        str(row["atom_id"])
        for row in contract.get("atoms", [])
    ]

    requirements = []
    reports = []

    for index, atom_id in enumerate(atoms, 1):
        rid = f"ER{index}"
        support = True
        attack = False

        if attacked_atom == atom_id:
            support = False
            attack = True

        if both_atom == atom_id:
            support = True
            attack = True

        requirements.append({
            "requirement_id": rid,
            "atom_id": atom_id,
            "decisiveness": "DECISIVE",
        })

        reports.append({
            "requirement_id": rid,
            "statement_id": f"stmt-{rid.lower()}",
            "accepted_state": (
                "BOTH"
                if support and attack
                else "TRUE"
                if support
                else "FALSE"
                if attack
                else "UNKNOWN"
            ),
            "contradiction_state": (
                "PRESERVED"
                if support and attack
                else "NONE"
            ),
            "support_proof": {
                "report_id": f"{rid}-support",
                "accepted_direction": support,
            },
            "attack_proof": {
                "report_id": f"{rid}-attack",
                "accepted_direction": attack,
            },
        })

    rr = {
        "case_id": "synthetic-no-case",
        "cp_id": cp_id,
        "evidence_requirement_plan": {
            "requirements": requirements,
        },
    }

    proof = {
        "bundle_sha256": "sha256:synthetic",
        "requirement_reports": reports,
        "post_proof_argument": {
            "status": "RUN",
            "accepted_argument_evaluation": {
                "state": "UNKNOWN",
                "standing_pro_argument_ids": [],
                "standing_con_argument_ids": [],
                "conflicted_argument_ids": [],
                "undecided_argument_ids": [],
            },
        },
    }

    return rr, proof


def audit_one(path: Path) -> dict:
    bundle = load_json(path)
    contract = unwrap_contract(bundle)

    cp_id = path.stem

    app = contract.get("applicability")
    na = contract.get("non_applicability")
    sat = contract.get("satisfaction")

    if not (
        isinstance(app, dict)
        and str(app.get("op")).upper() == "CONST"
    ):
        raise AssertionError(
            f"{cp_id}: applicability not CONST"
        )

    if not (
        isinstance(na, dict)
        and str(na.get("op")).upper() == "CONST"
    ):
        raise AssertionError(
            f"{cp_id}: non_applicability not CONST"
        )

    ops = set(collect_ops(sat))
    unsupported = ops - ALLOWED_SAT_OPS
    if unsupported:
        raise AssertionError(
            f"{cp_id}: unsupported satisfaction ops "
            f"{sorted(unsupported)}"
        )

    atoms = {
        str(row["atom_id"])
        for row in contract.get("atoms", [])
        if isinstance(row, dict) and row.get("atom_id")
    }

    refs = set(collect_atom_refs(sat))

    if refs != atoms:
        raise AssertionError(
            f"{cp_id}: satisfaction refs {sorted(refs)} "
            f"!= atom definitions {sorted(atoms)}"
        )

    rr, proof = build_fixture(
        cp_id,
        contract,
    )

    # Exercise the actual patched Argument projection on the real contract AST.
    template = argument.compile_minimal_argument_template(
        contract_bundle=bundle,
        evidence_requirement_plan=rr["evidence_requirement_plan"],
    )
    all_true_states = {
        row["requirement_id"]: argument.as_state("TRUE")
        for row in rr["evidence_requirement_plan"]["requirements"]
    }
    arg_all_true = argument.evaluate_benchmark_statement(
        template=template,
        requirement_states=all_true_states,
    )
    if arg_all_true["state"] != "TRUE":
        raise AssertionError(
            f"{cp_id}: Argument all-support -> {arg_all_true['state']}"
        )

    first_rid = rr["evidence_requirement_plan"]["requirements"][0]["requirement_id"]
    one_false_states = dict(all_true_states)
    one_false_states[first_rid] = argument.as_state("FALSE")
    arg_one_false = argument.evaluate_benchmark_statement(
        template=template,
        requirement_states=one_false_states,
    )
    if arg_one_false["state"] != "FALSE":
        raise AssertionError(
            f"{cp_id}: Argument one-negative -> {arg_one_false['state']}"
        )

    one_unknown_states = dict(all_true_states)
    one_unknown_states[first_rid] = argument.as_state("UNKNOWN")
    arg_one_unknown = argument.evaluate_benchmark_statement(
        template=template,
        requirement_states=one_unknown_states,
    )
    if arg_one_unknown["state"] != "UNKNOWN":
        raise AssertionError(
            f"{cp_id}: Argument one-unknown -> {arg_one_unknown['state']}"
        )

    one_both_states = dict(all_true_states)
    one_both_states[first_rid] = argument.as_state("BOTH")
    arg_one_both = argument.evaluate_benchmark_statement(
        template=template,
        requirement_states=one_both_states,
    )
    if arg_one_both["state"] != "BOTH":
        raise AssertionError(
            f"{cp_id}: Argument one-BOTH -> {arg_one_both['state']}"
        )

    compliant = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=bundle,
        proof_bundle=proof,
    )

    compliant_state = compliant[
        "common_internal_outcome"
    ]

    if compliant_state != "PROVEN_COMPLIANT":
        raise AssertionError(
            f"{cp_id}: all-support fixture -> "
            f"{compliant_state}"
        )

    first_atom = sorted(atoms)[0]

    rr, proof = build_fixture(
        cp_id,
        contract,
        attacked_atom=first_atom,
    )

    adverse = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=bundle,
        proof_bundle=proof,
    )

    adverse_state = adverse[
        "common_internal_outcome"
    ]

    if adverse_state != "PROVEN_NON_COMPLIANT":
        raise AssertionError(
            f"{cp_id}: one-attack fixture -> "
            f"{adverse_state}"
        )

    rr, proof = build_fixture(
        cp_id,
        contract,
        both_atom=first_atom,
    )

    conflict = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=bundle,
        proof_bundle=proof,
    )

    conflict_state = conflict[
        "common_internal_outcome"
    ]

    if conflict_state != "CONFLICTING":
        raise AssertionError(
            f"{cp_id}: BOTH fixture -> "
            f"{conflict_state}"
        )

    return {
        "cp_id": cp_id,
        "atom_count": len(atoms),
        "satisfaction_ops": sorted(ops),
        "all_support": compliant_state,
        "one_attack": adverse_state,
        "one_both": conflict_state,
        "argument_all_support": arg_all_true["state"],
        "argument_one_negative": arg_one_false["state"],
        "argument_one_unknown": arg_one_unknown["state"],
        "argument_one_both": arg_one_both["state"],
        "answer_comparator_used": False,
        "api_called": False,
    }


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
        default=Path(
            "results_v2/"
            "multi_atom_contract_audit_v1_2.json"
        ),
    )
    args = parser.parse_args()

    rows = []
    cp_ids = discover_cp_ids(args.contract_dir)
    if not cp_ids:
        raise SystemExit("No canonical multi-atom ALL contracts discovered")

    print("Discovered multi-atom ALL CPs:", ", ".join(cp_ids))

    for cp_id in cp_ids:
        path = args.contract_dir / f"{cp_id}.json"

        if not path.exists():
            raise SystemExit(
                f"Missing canonical contract: {path}"
            )

        row = audit_one(path)
        rows.append(row)

        print(
            cp_id,
            "PASS",
            "| atoms=", row["atom_count"],
            "| all-support=", row["all_support"],
            "| attack=", row["one_attack"],
            "| both=", row["one_both"],
        )

    result = {
        "schema":
            "freca-multi-atom-contract-audit-v1-2",
        "all_pass": True,
        "contracts": rows,
        "api_called": False,
        "answer_comparator_used": False,
    }

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
    print("ALL PASS: True")
    print("Saved:", args.output)


if __name__ == "__main__":
    main()
