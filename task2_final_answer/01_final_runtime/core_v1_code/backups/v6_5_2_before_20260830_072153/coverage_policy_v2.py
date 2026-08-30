#!/usr/bin/env python3
"""Purpose-specific RequirementCoverage policy for Production V2."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import coverage_v1
import proof_standard_v1_1 as proof_v1
from procedure_executor_v2 import (
    COVERAGE_PURPOSES,
    validate_procedure_artifact,
)


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


def default_purpose(direction: str) -> str:
    if direction == "SUPPORT":
        return "POSITIVE_EXISTENCE_PROOF"
    if direction == "ATTACK":
        return "EXPLICIT_ADVERSE_PROOF"
    raise ValueError(f"Unsupported retrieval direction: {direction}")


def _direct_rows(requirement_result: dict, requirement_id: str, direction: str) -> list[dict]:
    relation = "SUPPORT" if direction == "SUPPORT" else "ATTACK"
    rows = []
    for row in requirement_result.get("alignments", []):
        if str(row.get("requirement_id")) != requirement_id:
            continue
        if row.get("relation") != relation:
            continue
        if row.get("argument_admission_channel") != "DIRECT":
            continue
        if row.get("identity_decisive_proof_eligible") is not True:
            continue
        if direction == "ATTACK" and not proof_v1._is_explicit_adverse_fact(row):
            continue
        rows.append(row)
    return rows


def _procedure_index(requirement_result: dict) -> dict[str, dict]:
    out = {}
    for artifact in requirement_result.get(
        "targeted_coverage_procedure_artifacts", []
    ) or []:
        need_id = str(artifact.get("need_id") or "")
        valid, reasons = validate_procedure_artifact(artifact)
        row = dict(artifact)
        row["validation_pass"] = valid
        row["validation_reason_codes"] = reasons
        if need_id:
            out[need_id] = row
    return out


def _burden_rule_allows_absence(requirement: dict, contract_bundle: dict) -> bool:
    explicit = requirement.get("absence_inference_permitted")
    if explicit is not None:
        return explicit is True
    contract = contract_bundle.get("contract", contract_bundle)
    rules = contract.get("burden_rules", []) or []
    return any(
        row.get("effect") in {"NOT_DEMONSTRATED", "REQUIRE_POSITIVE_SUPPORT"}
        and row.get("absence_inference_permitted") is True
        for row in rules
        if isinstance(row, dict)
    )


def evaluate_coverage_bundle(
    requirement_result: dict,
    *,
    contract_bundle: dict,
    purpose_overrides: dict[str, str] | None = None,
) -> dict:
    purpose_overrides = purpose_overrides or {}
    base = coverage_v1.evaluate_coverage_bundle(requirement_result)
    procedures = _procedure_index(requirement_result)
    requirements = {
        str(row["requirement_id"]): row
        for row in requirement_result["evidence_requirement_plan"].get(
            "requirements", []
        )
    }
    traces = {
        str(row["need_id"]): row
        for row in requirement_result.get("retrieval_traces", [])
    }
    base_reports = {
        str(row["need_id"]): row for row in base.get("need_reports", [])
    }

    reports = []
    directional_pass: dict[str, dict[str, bool]] = {}
    for need_id, trace in sorted(traces.items()):
        requirement_id = str(trace["requirement_id"])
        direction = str(trace["direction"])
        purpose = purpose_overrides.get(need_id, default_purpose(direction))
        if purpose not in COVERAGE_PURPOSES:
            raise ValueError(f"Unsupported coverage purpose for {need_id}: {purpose}")

        opposite_direction = "ATTACK" if direction == "SUPPORT" else "SUPPORT"
        opposite_need_id = next(
            (
                other_id
                for other_id, other in traces.items()
                if str(other.get("requirement_id")) == requirement_id
                and str(other.get("direction")) == opposite_direction
            ),
            None,
        )
        own_procedure = procedures.get(need_id)
        countercheck = procedures.get(str(opposite_need_id)) if opposite_need_id else None
        own_complete = bool(
            own_procedure
            and own_procedure.get("validation_pass")
            and own_procedure.get("completion_status") == "COMPLETE"
        )
        countercheck_complete = bool(
            countercheck
            and countercheck.get("validation_pass")
            and countercheck.get("completion_status") == "COMPLETE"
        )
        basis_rows = _direct_rows(requirement_result, requirement_id, direction)
        positive_basis = bool(basis_rows)
        own_discovery_complete = bool(
            base_reports.get(need_id, {}).get("procedure_complete", False)
        )
        countercheck_discovery_complete = bool(
            base_reports.get(str(opposite_need_id), {}).get("procedure_complete", False)
            if opposite_need_id
            else False
        )
        burden_allows_absence = _burden_rule_allows_absence(
            requirements.get(requirement_id, {}), contract_bundle
        )

        if purpose == "EXPLICIT_ADVERSE_PROOF":
            # V6.2: one grounded explicit adverse fact is itself an existential
            # counterexample.  Requiring completion of the whole ATTACK search
            # before admitting that counterexample is logically backwards and
            # makes explicit violations unreachable in an open candidate pool.
            # Opposite SUPPORT remains independently discoverable and may later
            # yield BOTH; no contradiction is erased here.
            proof_pass = bool(positive_basis)
            if proof_pass and countercheck_complete:
                status = "EXPLICIT_ADVERSE_BASIS_COUNTERCHECK_CLOSED"
            elif proof_pass:
                status = "EXPLICIT_ADVERSE_BASIS_COUNTERCHECK_OPEN"
            else:
                status = "NO_EXPLICIT_ADVERSE_BASIS"

        elif purpose == "POSITIVE_EXISTENCE_PROOF":
            # Positive support keeps the V6.1 discovery-completion requirement.
            # A generic supporting instance is not automatically sufficient for
            # universal/ongoing obligations.  Aggregate/all-state facts may still
            # satisfy the registered directional procedure upstream.
            proof_pass = bool(positive_basis and own_discovery_complete)
            if proof_pass and countercheck_complete:
                status = "EXPLICIT_BASIS_DISCOVERY_COMPLETE_COUNTERCHECK_CLOSED"
            elif proof_pass:
                status = "EXPLICIT_BASIS_DISCOVERY_COMPLETE_COUNTERCHECK_OPEN"
            elif positive_basis:
                status = "EXPLICIT_BASIS_DISCOVERY_INCOMPLETE"
            else:
                status = "NO_EXPLICIT_BASIS"
        elif purpose == "ABSENCE_BASED_INFERENCE":
            proof_pass = bool(own_complete and burden_allows_absence)
            status = (
                "ABSENCE_INFERENCE_PROCEDURE_AND_BURDEN_COMPLETE"
                if proof_pass
                else "ABSENCE_INFERENCE_NOT_PERMITTED"
            )
        elif purpose in {
            "CONTRADICTION_COUNTERCHECK",
            "NON_APPLICABILITY_COUNTERCHECK",
        }:
            proof_pass = own_complete
            status = "COUNTERCHECK_COMPLETE" if proof_pass else "COUNTERCHECK_INCOMPLETE"
        else:  # pragma: no cover - guarded above
            raise AssertionError(purpose)

        limiting = []
        if not positive_basis and purpose in {
            "POSITIVE_EXISTENCE_PROOF",
            "EXPLICIT_ADVERSE_PROOF",
        }:
            limiting.append("NO_EXPLICIT_DIRECTIONAL_BASIS")
        if positive_basis and not own_discovery_complete and purpose == "POSITIVE_EXISTENCE_PROOF":
            limiting.append("DIRECTIONAL_DISCOVERY_PROCEDURE_INCOMPLETE")
        if positive_basis and not own_discovery_complete and purpose == "EXPLICIT_ADVERSE_PROOF":
            # Diagnostic only; an explicit counterexample is already sufficient
            # for the adverse direction.
            limiting.append("ADVERSE_DISCOVERY_OPEN_NONBLOCKING")
        if positive_basis and not countercheck_complete and purpose in {
            "POSITIVE_EXISTENCE_PROOF",
            "EXPLICIT_ADVERSE_PROOF",
        }:
            # Diagnostic/open-goal only: this does not block a positive
            # existence proof under PRESERVE_BOTH semantics.
            limiting.append("COUNTEREVIDENCE_CLOSURE_OPEN_NONBLOCKING")
        if purpose == "ABSENCE_BASED_INFERENCE" and not burden_allows_absence:
            limiting.append("CONTRACT_BURDEN_RULE_DOES_NOT_PERMIT_ABSENCE_INFERENCE")
        if own_procedure and not own_procedure.get("validation_pass"):
            limiting.extend(own_procedure.get("validation_reason_codes", []))

        base_report = dict(base_reports.get(need_id, {}))
        base_report.update(
            {
                "coverage_purpose": purpose,
                "purpose_source": (
                    "EXPLICIT_OVERRIDE" if need_id in purpose_overrides else "DIRECTIONAL_DEFAULT"
                ),
                "explicit_basis_present": positive_basis,
                "directional_discovery_complete": own_discovery_complete,
                "countercheck_discovery_complete": countercheck_discovery_complete,
                "countercheck_closure_required_for_directional_existence": False,
                "basis_artifact_ids": sorted(
                    str(row.get("alignment_evidence_id") or row.get("evidence_id"))
                    for row in basis_rows
                ),
                "targeted_procedure_artifact_id": (
                    own_procedure.get("procedure_artifact_id") if own_procedure else None
                ),
                "targeted_procedure_complete": own_complete,
                "countercheck_need_id": opposite_need_id,
                "countercheck_procedure_artifact_id": (
                    countercheck.get("procedure_artifact_id") if countercheck else None
                ),
                "countercheck_complete": countercheck_complete,
                "burden_rule_allows_absence_inference": burden_allows_absence,
                "proof_coverage_pass": proof_pass,
                "coverage_pass": proof_pass,
                "status": status,
                "limiting_factors": sorted(
                    set(base_report.get("limiting_factors", []) + limiting)
                ),
            }
        )
        reports.append(base_report)
        directional_pass.setdefault(requirement_id, {})[direction] = proof_pass

    summaries = []
    for requirement_id, requirement in sorted(requirements.items()):
        req_reports = [
            row for row in reports if row.get("requirement_id") == requirement_id
        ]
        by_direction = directional_pass.get(requirement_id, {})
        summaries.append(
            {
                "requirement_id": requirement_id,
                "decisiveness": requirement.get("decisiveness"),
                "directional_proof_coverage": {
                    "SUPPORT": bool(by_direction.get("SUPPORT", False)),
                    "ATTACK": bool(by_direction.get("ATTACK", False)),
                },
                "proof_coverage_pass": bool(
                    by_direction.get("SUPPORT") or by_direction.get("ATTACK")
                ),
                "coverage_status": (
                    "PURPOSE_SPECIFIC_PROOF_COVERAGE_REACHABLE"
                    if by_direction.get("SUPPORT") or by_direction.get("ATTACK")
                    else "PURPOSE_SPECIFIC_PROOF_COVERAGE_PENDING"
                ),
                "coverage_purposes": sorted(
                    {str(row.get("coverage_purpose")) for row in req_reports}
                ),
                "limiting_factors": sorted(
                    {
                        factor
                        for row in req_reports
                        for factor in row.get("limiting_factors", [])
                    }
                ),
            }
        )

    proof_coverage_complete = bool(
        summaries and all(row["proof_coverage_pass"] for row in summaries)
    )
    bundle = {
        "schema": "freca-core-requirement-coverage-v2",
        "coverage_policy_version": "PURPOSE_SPECIFIC_COVERAGE_V2_3",
        "cp_id": requirement_result.get("cp_id"),
        "case_id": requirement_result.get("case_id"),
        "need_reports": reports,
        "requirement_summaries": summaries,
        "directional_proof_coverage": directional_pass,
        "discovery_complete": base.get("discovery_complete", False),
        "proof_coverage_complete": proof_coverage_complete,
        "coverage_complete": proof_coverage_complete,
        "coverage_pass": proof_coverage_complete,
        "adverse_inference_allowed": any(
            row.get("coverage_purpose") == "ABSENCE_BASED_INFERENCE"
            and row.get("proof_coverage_pass")
            for row in reports
        ),
        "burden_rule_applied": False,
        "invariants": {
            "top_k_never_means_exhaustive": True,
            "absence_never_establishes_violation_without_burden_rule": True,
            "missing_never_establishes_non_applicability": True,
            "positive_and_adverse_existential_proofs_preserve_both": True,
            "countercheck_closure_not_required_for_grounded_direction": True,
            "absence_inference_still_requires_explicit_closure_and_burden_rule": True,
        },
    }
    bundle["bundle_sha256"] = sha256_json(bundle)
    return bundle


def validate_coverage_bundle(
    requirement_result: dict,
    *,
    contract_bundle: dict,
    bundle: dict,
    purpose_overrides: dict[str, str] | None = None,
) -> tuple[bool, list[str]]:
    expected = evaluate_coverage_bundle(
        requirement_result,
        contract_bundle=contract_bundle,
        purpose_overrides=purpose_overrides,
    )
    reasons = []
    if bundle.get("bundle_sha256") != sha256_json(
        {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    ):
        reasons.append("COVERAGE_BUNDLE_HASH_MISMATCH")
    if canonical_json(bundle) != canonical_json(expected):
        reasons.append("COVERAGE_BUNDLE_RECOMPUTATION_MISMATCH")
    return not reasons, reasons
