#!/usr/bin/env python3
"""Bootstrap a FRECA CP12 frozen Layer-7 root from an existing requirement result.

This exists only to bring replication pilots to the SAME pre-repair artifact
level as Pilot C.

Pipeline:
    requirement_reasoning_v2
      -> coverage_v1
      -> inject coverage gate into an in-memory copy
      -> proof_standard_v1
      -> post-proof argument
      -> procedure_objective_v1
      -> open_goal_v1

No DeepSeek/API calls.
No repair action.
No final label.
No answer comparator.
Original requirement_result is never overwritten.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json(value).encode("utf-8")
        ).hexdigest()
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def inject_coverage_gate(
    requirement_result: dict,
    coverage: dict,
) -> dict:
    """Bridge Coverage v1.1 into the existing ProofStandard v1 input schema.

    This matches the bridge used by repair_feedback_v1.x.
    """

    patched = copy.deepcopy(requirement_result)

    gate = patched.setdefault("proof_gate", {})

    proof_coverage_complete = bool(
        coverage.get(
            "proof_coverage_complete",
            coverage.get("coverage_complete", False),
        )
    )

    gate["coverage_complete"] = proof_coverage_complete
    gate["coverage_source_schema"] = coverage.get("schema")
    gate["coverage_source_sha256"] = coverage.get("bundle_sha256")

    summary_by_rid = {
        str(row["requirement_id"]): row
        for row in coverage.get("requirement_summaries", [])
    }

    for report in gate.get("requirement_reports", []):
        rid = str(report.get("requirement_id", ""))
        summary = summary_by_rid.get(rid, {})

        report["coverage_pass"] = bool(
            summary.get("proof_coverage_pass", False)
        )

        report["coverage_status_v1_1"] = summary.get(
            "coverage_status"
        )

    return patched


def bootstrap_root(
    *,
    requirement_result: dict,
    contract_bundle: dict,
) -> dict:
    import coverage_v1
    import proof_standard_v1
    import procedure_objective_v1
    import open_goal_v1

    coverage = coverage_v1.evaluate_coverage_bundle(
        requirement_result
    )

    requirement_for_proof = inject_coverage_gate(
        requirement_result,
        coverage,
    )

    proof = proof_standard_v1.evaluate_proof_standard_bundle(
        requirement_for_proof
    )

    proof["post_proof_argument"] = (
        proof_standard_v1.run_post_proof_argument(
            requirement_result=requirement_for_proof,
            contract_bundle=contract_bundle,
            proof_bundle=proof,
        )
    )

    procedure = procedure_objective_v1.build_plan(
        requirement_for_proof,
        coverage,
    )

    open_goals = open_goal_v1.build_open_goal_ledger(
        requirement_result=requirement_for_proof,
        coverage=coverage,
        procedure_plan=procedure,
        proof_standard=proof,
        contract_bundle=contract_bundle,
    )

    manifest = {
        "schema":
            "freca-core-frozen-layer7-root-manifest-v1",

        "case_id":
            requirement_result.get("case_id"),

        "cp_id":
            (
                requirement_result.get("cp_id")
                or (
                    requirement_result.get(
                        "evidence_requirement_plan",
                        {},
                    ).get("cp_id")
                )
            ),

        "source_requirement_result_sha256":
            sha256_json(requirement_result),

        "contract_sha256":
            sha256_json(contract_bundle),

        "coverage_sha256":
            coverage.get("bundle_sha256")
            or sha256_json(coverage),

        "proof_sha256":
            proof.get("bundle_sha256")
            or sha256_json(proof),

        "procedure_sha256":
            procedure.get("bundle_sha256")
            or sha256_json(procedure),

        "open_goals_sha256":
            (
                open_goals.get("semantic_sha256")
                or open_goals.get("bundle_sha256")
                or sha256_json(open_goals)
            ),

        "proof_state_modified":
            False,

        "repair_action_executed":
            False,

        "final_label":
            None,

        "answer_comparator_used":
            False,

        "human_or_historical_labels_used":
            False,
    }

    manifest["manifest_sha256"] = sha256_json(manifest)

    return {
        "requirement_for_proof":
            requirement_for_proof,

        "coverage":
            coverage,

        "proof":
            proof,

        "procedure":
            procedure,

        "open_goals":
            open_goals,

        "manifest":
            manifest,
    }


def run_self_tests() -> None:
    # This test checks only the coverage bridge semantics and artifact policy.
    rr = {
        "proof_gate": {
            "coverage_complete": False,
            "requirement_reports": [
                {
                    "requirement_id": "ER1",
                    "coverage_pass": False,
                }
            ],
        }
    }

    cov = {
        "schema": "coverage-fixture",
        "bundle_sha256": "sha256:cov",
        "proof_coverage_complete": True,
        "requirement_summaries": [
            {
                "requirement_id": "ER1",
                "proof_coverage_pass": True,
                "coverage_status": "COMPLETE",
            }
        ],
    }

    patched = inject_coverage_gate(rr, cov)

    assert rr["proof_gate"]["coverage_complete"] is False
    assert patched["proof_gate"]["coverage_complete"] is True
    assert (
        patched["proof_gate"]["requirement_reports"][0]["coverage_pass"]
        is True
    )
    assert (
        patched["proof_gate"]["coverage_source_sha256"]
        == "sha256:cov"
    )

    print("bootstrap_frozen_root_v1 self-tests: PASS")
    print("  original requirement result not mutated")
    print("  Coverage v1.1 bridge matches repair-feedback path")
    print("  no API / repair / answer comparator behavior")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--contract",
        type=Path,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_v2"),
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if args.requirement_result is None:
            return

    if args.requirement_result is None:
        parser.error("--requirement-result is required")

    if args.contract is None:
        parser.error("--contract is required")

    rr = load_json(args.requirement_result)
    contract = load_json(args.contract)

    result = bootstrap_root(
        requirement_result=rr,
        contract_bundle=contract,
    )

    case_id = str(
        rr.get("case_id")
        or args.requirement_result.stem.split("_CP", 1)[0]
    )

    cp_id = str(
        rr.get("cp_id")
        or rr.get("evidence_requirement_plan", {}).get("cp_id")
        or "CP12"
    )

    prefix = f"{case_id}_{cp_id}"

    paths = {
        # Diagnostic only. Preflight does NOT consume this file.
        "requirement_for_proof":
            args.output_dir
            / f"{prefix}_layer7_requirement_for_proof_v1.json",

        # Exact filenames expected by replication_preflight_v1.
        "coverage":
            args.output_dir
            / f"{prefix}_coverage_v1_1.json",

        "proof":
            args.output_dir
            / f"{prefix}_proof_standard_v1.json",

        "procedure":
            args.output_dir
            / f"{prefix}_procedure_objective_v1.json",

        "open_goals":
            args.output_dir
            / f"{prefix}_open_goals_v1.json",

        "manifest":
            args.output_dir
            / f"{prefix}_frozen_root_manifest_v1.json",
    }

    for key, path in paths.items():
        save_json(result[key], path)

    coverage = result["coverage"]
    proof = result["proof"]
    procedure = result["procedure"]
    goals = result["open_goals"]

    print("=" * 76)
    print("FRECA FROZEN LAYER-7 ROOT BOOTSTRAP V1")
    print("=" * 76)
    print()
    print("Case:", case_id)
    print("CP:", cp_id)
    print(
        "Coverage discovery/proof:",
        coverage.get("discovery_complete"),
        "/",
        coverage.get("proof_coverage_complete"),
    )
    print(
        "Coverage next artifact:",
        coverage.get("next_required_artifact"),
    )
    print(
        "Proof internal outcome:",
        proof.get("internal_outcome"),
    )
    print(
        "Procedure objectives:",
        len(procedure.get("audit_procedure_objectives", [])),
    )
    print(
        "Open goals:",
        len(goals.get("goals", [])),
    )
    print(
        "Fully resolved:",
        goals.get("fully_resolved"),
    )
    print("Repair executed: False")
    print("Final label: None")
    print()
    print("Saved:")
    for key, path in paths.items():
        print(f"  {key}: {path}")


if __name__ == "__main__":
    main()
