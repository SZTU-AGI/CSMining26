#!/usr/bin/env python3
"""FRECA P4b real-evidence production-path smoke v1.

Pipeline:
  freca_core_v2.py evaluate
    -> real case evidence parse/retrieval/alignment (API may be used)
    -> verify embedded EvidenceRequirement plan == current frozen plan
    -> Coverage v1.1 semantics via coverage_v1.evaluate_coverage_bundle
    -> inject explicit coverage gate into an in-memory copy
    -> ProofStandard v1.1
    -> post-proof Argument
    -> Core six-state outcome adapter
    -> FOLD-POLICY-v3

Safeguards:
- refuses to overwrite an existing requirement_result unless explicitly allowed;
- no answer comparator;
- no case-specific semantic rules;
- no contract/plan mutation;
- downstream replay after evaluate uses no API.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import coverage_v1
import proof_standard_v1_1 as proofmod
import core_outcome_adapter_v1 as adapter
import fold_policy_v3_core as foldmod
import evidence_reasoning_v2 as er
import freca_core_v1 as core


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be object")
    return value


def save_json_atomic(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def inject_coverage(rr: dict, coverage: dict) -> dict:
    patched = copy.deepcopy(rr)
    gate = patched.setdefault("proof_gate", {})

    proof_complete = bool(
        coverage.get(
            "proof_coverage_complete",
            coverage.get("coverage_complete", False),
        )
    )

    gate["coverage_complete"] = proof_complete
    gate["coverage_source_schema"] = coverage.get("schema")
    gate["coverage_source_sha256"] = coverage.get("bundle_sha256")

    summary = {
        str(row["requirement_id"]): row
        for row in coverage.get("requirement_summaries", [])
    }

    reports = gate.setdefault("requirement_reports", [])

    if not reports:
        reports.extend(
            {
                "requirement_id": str(row["requirement_id"]),
                "coverage_pass": False,
            }
            for row in rr["evidence_requirement_plan"].get(
                "requirements", []
            )
        )

    for row in reports:
        rid = str(row["requirement_id"])
        s = summary.get(rid, {})
        row["coverage_pass"] = bool(
            s.get("proof_coverage_pass", False)
        )
        row["coverage_status_v1_1"] = s.get(
            "coverage_status"
        )

    return patched


def validate_current_plan(cp_id: str, contract_dir: Path) -> dict:
    cp = core.get_cp(cp_id)
    contract = load_json(contract_dir / f"{cp_id}.json")
    ledger = load_json(
        contract_dir / f"{cp_id}_candidate_ledger.json"
    )
    relation = load_json(
        contract_dir / f"{cp_id}_rule_set_relation.json"
    )
    plan = load_json(
        contract_dir / f"{cp_id}_evidence_requirements.json"
    )

    validated = er.validate_evidence_requirements(
        plan,
        cp,
        contract,
        ledger,
        relation,
    )

    if canonical_json(validated) != canonical_json(plan):
        # Not necessarily an error semantically, but the canonical file should
        # already be current-validator normalized at this stage.
        raise RuntimeError(
            f"{cp_id}: current plan validates to different canonical bytes"
        )

    return plan


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case", required=True)
    p.add_argument("--cp", required=True)
    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results_v2"),
    )
    p.add_argument(
        "--smoke-dir",
        type=Path,
        default=Path("results_v2/p4b_real_evidence_smoke_v1"),
    )
    p.add_argument(
        "--allow-existing-requirement-result",
        action="store_true",
    )
    args = p.parse_args()

    case_id = str(args.case)
    cp_id = str(args.cp)

    runner = Path("freca_core_v2.py")
    if not runner.exists():
        raise SystemExit("Missing freca_core_v2.py")

    contract_path = args.contract_dir / f"{cp_id}.json"
    plan_path = (
        args.contract_dir / f"{cp_id}_evidence_requirements.json"
    )
    if not contract_path.exists():
        raise SystemExit(f"Missing contract: {contract_path}")
    if not plan_path.exists():
        raise SystemExit(f"Missing plan: {plan_path}")

    current_plan = validate_current_plan(
        cp_id,
        args.contract_dir,
    )

    rr_path = (
        args.results_dir
        / f"{case_id}_{cp_id}_requirement_reasoning_v2.json"
    )

    if rr_path.exists() and not args.allow_existing_requirement_result:
        raise SystemExit(
            "Refusing to overwrite existing requirement result: "
            f"{rr_path}\n"
            "Choose a fresh case×CP pair, or explicitly pass "
            "--allow-existing-requirement-result."
        )

    run_dir = args.smoke_dir / f"{case_id}__{cp_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    evaluate_log = run_dir / "evaluate.log"

    cmd = [
        sys.executable,
        str(runner),
        "evaluate",
        "--cp",
        cp_id,
        "--case",
        case_id,
    ]

    print("=" * 88)
    print("FRECA P4B REAL-EVIDENCE SMOKE V1")
    print("=" * 88)
    print("case:", case_id)
    print("cp:", cp_id)
    print("current plan:", plan_path)
    print("requirement result target:", rr_path)
    print("evaluate command:", " ".join(cmd))
    print()

    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    evaluate_log.write_text(
        proc.stdout,
        encoding="utf-8",
    )

    # Echo full child output so the terminal remains inspectable.
    print(proc.stdout, end="")

    if proc.returncode != 0:
        raise SystemExit(
            f"evaluate failed with exit code {proc.returncode}; "
            f"log: {evaluate_log}"
        )

    if not rr_path.exists():
        raise SystemExit(
            "evaluate returned success but expected requirement result "
            f"does not exist: {rr_path}"
        )

    rr = load_json(rr_path)

    if str(rr.get("case_id")) != case_id:
        raise RuntimeError(
            f"case_id mismatch: {rr.get('case_id')} != {case_id}"
        )
    if str(rr.get("cp_id")) != cp_id:
        raise RuntimeError(
            f"cp_id mismatch: {rr.get('cp_id')} != {cp_id}"
        )

    embedded_plan = rr.get("evidence_requirement_plan")
    if not isinstance(embedded_plan, dict):
        raise RuntimeError(
            "requirement result has no evidence_requirement_plan"
        )

    current_plan_sha = sha256_json(current_plan)
    embedded_plan_sha = sha256_json(embedded_plan)

    if embedded_plan_sha != current_plan_sha:
        raise RuntimeError(
            "Evidence evaluation did not consume the current frozen plan: "
            f"embedded={embedded_plan_sha}, current={current_plan_sha}"
        )

    contract = load_json(contract_path)

    coverage = coverage_v1.evaluate_coverage_bundle(rr)
    coverage_path = run_dir / "coverage_v1_1.json"
    save_json_atomic(coverage, coverage_path)

    rr_for_proof = inject_coverage(rr, coverage)
    rr_for_proof_path = run_dir / "requirement_for_proof.json"
    save_json_atomic(rr_for_proof, rr_for_proof_path)

    proof = proofmod.evaluate_proof_standard_bundle(
        rr_for_proof
    )
    proof["coverage_source_sha256"] = coverage.get(
        "bundle_sha256"
    )
    proof["post_proof_argument"] = (
        proofmod.run_post_proof_argument(
            requirement_result=rr_for_proof,
            contract_bundle=contract,
            proof_bundle=proof,
        )
    )

    proof_path = run_dir / "proof_standard_v1_1.json"
    save_json_atomic(proof, proof_path)

    outcome = adapter.build_argument_evaluation_bundle(
        requirement_result=rr_for_proof,
        contract_bundle=contract,
        proof_bundle=proof,
    )
    outcome_path = run_dir / "core_outcome_adapter_v1.json"
    save_json_atomic(outcome, outcome_path)

    if not outcome.get("evaluations"):
        raise RuntimeError("Core outcome adapter produced no evaluations")

    ev = outcome["evaluations"][0]

    branch = {
        "valid": True,
        "internal_outcome": ev["internal_outcome"],
        "fold_gate_report": ev["fold_gate_report"],
    }
    fold = foldmod.fold_envelope([branch])

    if str(fold.get("label")) not in {"1", "0", "N/A"}:
        raise RuntimeError(
            f"Invalid fold label: {fold.get('label')!r}"
        )

    smoke = {
        "schema": "freca-p4b-real-evidence-smoke-v1",
        "case_id": case_id,
        "cp_id": cp_id,
        "evaluate_returncode": proc.returncode,
        "evaluate_log": str(evaluate_log),
        "requirement_result_path": str(rr_path),
        "current_plan_sha256": current_plan_sha,
        "embedded_plan_sha256": embedded_plan_sha,
        "current_plan_exactly_consumed": (
            current_plan_sha == embedded_plan_sha
        ),
        "coverage_path": str(coverage_path),
        "coverage_complete": coverage.get(
            "proof_coverage_complete",
            coverage.get("coverage_complete"),
        ),
        "proof_path": str(proof_path),
        "accepted_argument_state": (
            (
                proof.get("post_proof_argument") or {}
            ).get("accepted_argument_evaluation") or {}
        ).get("state"),
        "outcome_path": str(outcome_path),
        "applicability_state": ev.get("applicability_state"),
        "satisfaction_state": ev.get("satisfaction_state"),
        "violation_state": ev.get("violation_state"),
        "internal_outcome": ev.get("internal_outcome"),
        "fold_decision": fold,
        "evidence_alignment_api_may_be_called": True,
        "post_evaluate_api_called": False,
        "answer_comparator_used": False,
        "contract_mutated": False,
        "plan_mutated": False,
        "smoke_pass": True,
    }

    smoke_path = run_dir / "smoke_result.json"
    save_json_atomic(smoke, smoke_path)

    print()
    print("=" * 88)
    print("P4B POST-EVALUATE REPLAY")
    print("=" * 88)
    print("Current plan exact:", smoke["current_plan_exactly_consumed"])
    print("Coverage complete:", smoke["coverage_complete"])
    print("Accepted Argument:", smoke["accepted_argument_state"])
    print("Applicability:", smoke["applicability_state"])
    print("Satisfaction:", smoke["satisfaction_state"])
    print("Violation:", smoke["violation_state"])
    print("InternalOutcome:", smoke["internal_outcome"])
    print("Fold label:", fold["label"])
    print("Finality:", fold["finality"])
    print("Benchmark fallback:", fold.get("benchmark_fallback"))
    print("Post-evaluate API called: False")
    print("Answer comparator used: False")
    print("SMOKE PASS: True")
    print("Saved:", smoke_path)


if __name__ == "__main__":
    main()
