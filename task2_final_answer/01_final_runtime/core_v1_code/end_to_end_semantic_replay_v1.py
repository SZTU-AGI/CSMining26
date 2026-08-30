#!/usr/bin/env python3
"""No-API CP12 end-to-end production semantic replay v1.

Frozen requirement_result
  -> inject existing coverage gate
  -> ProofStandard v1.1
  -> accepted Argument
  -> six-state Core outcome adapter
  -> FOLD-POLICY-v3

No retrieval/alignment/API call is performed.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import proof_standard_v1_1 as proofmod
import core_outcome_adapter_v1 as adapter
import fold_policy_v3_core as foldmod


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    gate["coverage_source_sha256"] = (
        coverage.get("bundle_sha256")
    )

    summary = {
        str(row["requirement_id"]): row
        for row in coverage.get("requirement_summaries", [])
    }

    reports = gate.setdefault("requirement_reports", [])

    if not reports:
        # Create minimal bridge rows if the old gate lacks them.
        reports.extend(
            {
                "requirement_id": str(row["requirement_id"]),
                "coverage_pass": False,
            }
            for row in rr[
                "evidence_requirement_plan"
            ].get("requirements", [])
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


def replay(
    *,
    requirement_result: dict,
    coverage: dict,
    contract: dict,
) -> dict:
    rr = inject_coverage(requirement_result, coverage)

    proof = proofmod.evaluate_proof_standard_bundle(rr)

    proof["coverage_source_sha256"] = (
        coverage.get("bundle_sha256")
    )

    proof["post_proof_argument"] = (
        proofmod.run_post_proof_argument(
            requirement_result=rr,
            contract_bundle=contract,
            proof_bundle=proof,
        )
    )

    outcome = adapter.build_argument_evaluation_bundle(
        requirement_result=rr,
        contract_bundle=contract,
        proof_bundle=proof,
    )

    ev = outcome["evaluations"][0]

    branch = {
        "valid": True,
        "internal_outcome": ev["internal_outcome"],
        "fold_gate_report": ev["fold_gate_report"],
    }

    fold = foldmod.fold_envelope([branch])

    return {
        "schema":
            "freca-core-end-to-end-semantic-replay-v1",
        "case_id":
            requirement_result.get("case_id"),
        "cp_id":
            requirement_result.get("cp_id"),
        "proof":
            proof,
        "argument_evaluation_bundle":
            outcome,
        "fold_decision":
            fold,
        "api_called":
            False,
        "answer_comparator_used":
            False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirement-result", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = replay(
        requirement_result=load_json(args.requirement_result),
        coverage=load_json(args.coverage),
        contract=load_json(args.contract),
    )

    save_json(result, args.output)

    ev = result["argument_evaluation_bundle"]["evaluations"][0]
    fd = result["fold_decision"]

    print("=" * 72)
    print("FRECA END-TO-END SEMANTIC REPLAY V1")
    print("=" * 72)
    print("Case:", result["case_id"])
    print("CP:", result["cp_id"])
    print("Applicability:", ev["applicability_state"])
    print("Satisfaction:", ev["satisfaction_state"])
    print("Violation:", ev["violation_state"])
    print("InternalOutcome:", ev["internal_outcome"])
    print("Fold label:", fd["label"])
    print("Finality:", fd["finality"])
    print("Benchmark fallback:", fd.get("benchmark_fallback"))
    print("API called: False")
    print("Saved:", args.output)


if __name__ == "__main__":
    main()
