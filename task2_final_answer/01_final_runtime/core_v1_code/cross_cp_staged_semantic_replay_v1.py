#!/usr/bin/env python3
"""FRECA cross-CP staged semantic replay v1.

Zero-API / no-case replay over the REAL frozen artifacts for CP1..CP41:
- contracts_v2/CPx.json
- contracts_v2/CPx_evidence_requirements.json
- current EvidenceRequirement validator
- current Argument template/evaluator
- current Core outcome adapter
- current FOLD-POLICY-v3

For every CP, exercise four synthetic ProofStandard-accepted state patterns:
1. all decisive ERs TRUE       -> Argument TRUE -> PROVEN_COMPLIANT -> label 1
2. first decisive ER FALSE     -> Argument FALSE -> PROVEN_NON_COMPLIANT -> label 0
3. first decisive ER UNKNOWN   -> Argument UNKNOWN -> UNKNOWN -> fallback label 0
4. first decisive ER BOTH      -> Argument BOTH -> CONFLICTING -> fallback label 0

These fixtures contain no case facts and no benchmark answers. They test wiring and
four-valued propagation only.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import argument_core_v1 as argument
import core_outcome_adapter_v1 as adapter
import evidence_reasoning_v2 as er
import fold_policy_v3_core as fold


CP_IDS = [f"CP{i}" for i in range(1, 42)]


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    return value


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unwrap_contract(bundle: dict) -> dict:
    inner = bundle.get("contract")
    return inner if isinstance(inner, dict) else bundle


def atom_refs(expr: dict | None) -> list[str]:
    refs = []

    def walk(node):
        if not isinstance(node, dict):
            raise ValueError("Invalid satisfaction expression")
        op = str(node.get("op") or "").upper()
        if op == "ATOM":
            atom_id = str(node.get("atom_id") or "").strip()
            if not atom_id:
                raise ValueError("ATOM missing atom_id")
            refs.append(atom_id)
            return
        if op == "ALL":
            children = node.get("children")
            if not isinstance(children, list) or not children:
                raise ValueError("ALL requires non-empty children")
            for child in children:
                walk(child)
            return
        raise ValueError(f"Unsupported satisfaction operator in replay: {op!r}")

    walk(expr)
    return refs


def state_pair(name: str) -> tuple[bool, bool]:
    name = name.upper()
    if name == "TRUE":
        return True, False
    if name == "FALSE":
        return False, True
    if name == "BOTH":
        return True, True
    if name == "UNKNOWN":
        return False, False
    raise ValueError(name)


def proof_bundle(plan: dict, states: dict[str, str], arg_eval: dict) -> dict:
    reports = []
    for row in plan.get("requirements", []):
        rid = str(row["requirement_id"])
        state = states[rid]
        support, attack = state_pair(state)
        reports.append({
            "requirement_id": rid,
            "statement_id": f"stmt-{rid.lower()}",
            "accepted_state": state,
            "contradiction_state": "PRESERVED" if state == "BOTH" else "NONE",
            "support_proof": {
                "report_id": f"{rid}-support-synthetic",
                "accepted_direction": support,
            },
            "attack_proof": {
                "report_id": f"{rid}-attack-synthetic",
                "accepted_direction": attack,
            },
        })

    return {
        "bundle_sha256": "sha256:synthetic-cross-cp-replay",
        "requirement_reports": reports,
        "post_proof_argument": {
            "status": "RUN",
            "accepted_argument_evaluation": {
                "state": arg_eval["state"],
                "standing_pro_argument_ids": list(
                    arg_eval.get("standing_pro_argument_ids") or []
                ),
                "standing_con_argument_ids": list(
                    arg_eval.get("standing_con_argument_ids") or []
                ),
                "conflicted_argument_ids": list(
                    arg_eval.get("conflicted_argument_ids") or []
                ),
                "undecided_argument_ids": list(
                    arg_eval.get("undecided_argument_ids") or []
                ),
            },
        },
    }


def make_requirement_result(cp_id: str, plan: dict) -> dict:
    return {
        "case_id": "synthetic-no-case",
        "cp_id": cp_id,
        "evidence_requirement_plan": plan,
        "answer_comparator_used": False,
    }


FIXTURES = {
    "ALL_TRUE": {
        "override": None,
        "argument": "TRUE",
        "outcome": "PROVEN_COMPLIANT",
        "label": "1",
        "benchmark_fallback": False,
        "finality": "EVIDENCE_DEMONSTRATED",
    },
    "ONE_FALSE": {
        "override": "FALSE",
        "argument": "FALSE",
        "outcome": "PROVEN_NON_COMPLIANT",
        "label": "0",
        "benchmark_fallback": False,
        "finality": "EVIDENCE_REBUTTED",
    },
    "ONE_UNKNOWN": {
        "override": "UNKNOWN",
        "argument": "UNKNOWN",
        "outcome": "UNKNOWN",
        "label": "0",
        "benchmark_fallback": True,
        "finality": "UNKNOWN_BENCHMARK_FALLBACK",
    },
    "ONE_BOTH": {
        "override": "BOTH",
        "argument": "BOTH",
        "outcome": "CONFLICTING",
        "label": "0",
        "benchmark_fallback": True,
        "finality": "INTERPRETATION_CONFLICT_FALLBACK",
    },
}


def audit_cp(contract_dir: Path, cp_id: str) -> dict:
    bundle = load_json(contract_dir / f"{cp_id}.json")
    contract = unwrap_contract(bundle)
    ledger = load_json(contract_dir / f"{cp_id}_candidate_ledger.json")
    relation = load_json(contract_dir / f"{cp_id}_rule_set_relation.json")
    plan = load_json(contract_dir / f"{cp_id}_evidence_requirements.json")

    cp = er.core.get_cp(cp_id)
    er.validate_evidence_requirements(plan, cp, bundle, ledger, relation)

    contract_atoms = [
        str(row.get("atom_id"))
        for row in (contract.get("atoms") or [])
        if isinstance(row, dict) and row.get("atom_id")
    ]
    refs = atom_refs(contract.get("satisfaction"))
    if set(refs) != set(contract_atoms):
        raise AssertionError(
            f"{cp_id}: satisfaction refs {sorted(set(refs))} != atoms {sorted(set(contract_atoms))}"
        )

    requirements = plan.get("requirements") or []
    if not requirements:
        raise AssertionError(f"{cp_id}: empty EvidenceRequirement plan")

    decisive = [
        row for row in requirements
        if str(row.get("decisiveness") or "").upper() == "DECISIVE"
    ]
    if len(decisive) != len(requirements):
        raise AssertionError(f"{cp_id}: non-decisive requirements present in frozen plan")

    by_atom = {atom_id: [] for atom_id in contract_atoms}
    for row in requirements:
        atom_id = str(row.get("atom_id") or "")
        if atom_id not in by_atom:
            raise AssertionError(f"{cp_id}: plan requirement targets unknown atom {atom_id}")
        by_atom[atom_id].append(str(row["requirement_id"]))

    uncovered = [a for a, rids in by_atom.items() if not rids]
    if uncovered:
        raise AssertionError(f"{cp_id}: uncovered atoms {uncovered}")

    template = argument.compile_minimal_argument_template(
        contract_bundle=bundle,
        evidence_requirement_plan=plan,
    )

    first_rid = str(requirements[0]["requirement_id"])
    fixture_rows = []

    for fixture_name, expected in FIXTURES.items():
        states = {
            str(row["requirement_id"]): "TRUE"
            for row in requirements
        }
        if expected["override"] is not None:
            states[first_rid] = expected["override"]

        arg_states = {
            rid: argument.as_state(state)
            for rid, state in states.items()
        }
        arg_eval = argument.evaluate_benchmark_statement(
            template=template,
            requirement_states=arg_states,
        )

        if arg_eval["state"] != expected["argument"]:
            raise AssertionError(
                f"{cp_id}/{fixture_name}: argument={arg_eval['state']} expected={expected['argument']}"
            )

        rr = make_requirement_result(cp_id, plan)
        proof = proof_bundle(plan, states, arg_eval)

        outcome_bundle = adapter.build_argument_evaluation_bundle(
            requirement_result=rr,
            contract_bundle=bundle,
            proof_bundle=proof,
        )
        evaluation = outcome_bundle["evaluations"][0]

        if evaluation["internal_outcome"] != expected["outcome"]:
            raise AssertionError(
                f"{cp_id}/{fixture_name}: outcome={evaluation['internal_outcome']} expected={expected['outcome']}"
            )
        if evaluation.get("submission_label") is not None:
            raise AssertionError(
                f"{cp_id}/{fixture_name}: adapter emitted submission label"
            )

        folded = fold.fold_branch(evaluation)

        if folded["label"] != expected["label"]:
            raise AssertionError(
                f"{cp_id}/{fixture_name}: label={folded['label']} expected={expected['label']}"
            )
        if bool(folded["benchmark_fallback"]) != expected["benchmark_fallback"]:
            raise AssertionError(
                f"{cp_id}/{fixture_name}: fallback={folded['benchmark_fallback']} expected={expected['benchmark_fallback']}"
            )
        if folded["finality"] != expected["finality"]:
            raise AssertionError(
                f"{cp_id}/{fixture_name}: finality={folded['finality']} expected={expected['finality']}"
            )

        fixture_rows.append({
            "fixture": fixture_name,
            "overridden_requirement_id": (
                first_rid if expected["override"] is not None else None
            ),
            "argument_state": arg_eval["state"],
            "internal_outcome": evaluation["internal_outcome"],
            "fold_label": folded["label"],
            "fold_finality": folded["finality"],
            "benchmark_fallback": folded["benchmark_fallback"],
        })

    return {
        "cp_id": cp_id,
        "atom_count": len(contract_atoms),
        "requirement_count": len(requirements),
        "requirements_by_atom": by_atom,
        "satisfaction_atom_refs": refs,
        "fixtures": fixture_rows,
        "pass": True,
    }


def runtime_fingerprints(contract_dir: Path) -> dict:
    module_paths = {
        "evidence_reasoning_v2": Path(er.__file__).resolve(),
        "argument_core_v1": Path(argument.__file__).resolve(),
        "core_outcome_adapter_v1": Path(adapter.__file__).resolve(),
        "fold_policy_v3_core": Path(fold.__file__).resolve(),
    }

    try:
        import multi_atom_support_v1 as ma
        module_paths["multi_atom_support_v1"] = Path(ma.__file__).resolve()
    except Exception:
        pass

    try:
        import multi_atom_argument_support_v1 as mas
        module_paths["multi_atom_argument_support_v1"] = Path(mas.__file__).resolve()
    except Exception:
        pass

    return {
        "modules": {
            name: {
                "path": str(path),
                "sha256": sha256_file(path),
            }
            for name, path in module_paths.items()
        },
        "contracts_sha256": {
            cp_id: sha256_file(contract_dir / f"{cp_id}.json")
            for cp_id in CP_IDS
        },
        "plans_sha256": {
            cp_id: sha256_file(contract_dir / f"{cp_id}_evidence_requirements.json")
            for cp_id in CP_IDS
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("results_v2/cross_cp_staged_semantic_replay_v1.json"),
    )
    args = p.parse_args()

    rows = []
    failures = []

    print("=" * 88)
    print("FRECA CROSS-CP STAGED SEMANTIC REPLAY V1")
    print("=" * 88)

    for cp_id in CP_IDS:
        try:
            row = audit_cp(args.contract_dir, cp_id)
            rows.append(row)
            print(
                f"{cp_id}: PASS | atoms={row['atom_count']} | ERs={row['requirement_count']} "
                "| TRUE→1 FALSE→0 UNKNOWN→0* BOTH→0*"
            )
        except Exception as exc:
            failures.append({
                "cp_id": cp_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            print(f"{cp_id}: FAIL | {type(exc).__name__}: {exc}")

    report = {
        "schema": "freca-cross-cp-staged-semantic-replay-v1",
        "scope": "REAL_41_CONTRACTS_AND_REAL_41_EVIDENCE_REQUIREMENT_PLANS_SYNTHETIC_STATE_REPLAY",
        "expected_cp_count": 41,
        "passed_cp_count": len(rows),
        "failed_cp_count": len(failures),
        "all_pass": len(rows) == 41 and not failures,
        "fixture_expectations": FIXTURES,
        "rows": rows,
        "failures": failures,
        "runtime_fingerprints": runtime_fingerprints(args.contract_dir),
        "api_called": False,
        "case_evidence_used": False,
        "answer_comparator_used": False,
        "notes": [
            "This is a wiring/four-valued semantic replay, not a factual case audit.",
            "It consumes the real frozen contract and EvidenceRequirement plan for every CP.",
            "Only FOLD-POLICY-v3 emits 1/0 labels in this replay.",
            "UNKNOWN and CONFLICTING are explicit benchmark fallbacks, not substantive findings.",
        ],
    }

    save_json(report, args.output)

    print()
    print("Passed CPs:", len(rows), "/ 41")
    print("ALL PASS:", report["all_pass"])
    print("API called: False")
    print("Case evidence used: False")
    print("Answer comparator used: False")
    print("Saved:", args.output)

    if not report["all_pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
