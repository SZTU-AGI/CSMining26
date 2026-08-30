#!/usr/bin/env python3
"""Temporal and reliability applicability plus directional Proof V2 adapter."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import proof_standard_v1_1 as proof_v1
import evidence_nature_v1 as evidence_nature


TEMPORAL_STATES = {
    "TEMPORAL_REQUIRED",
    "TEMPORAL_NOT_REQUIRED",
    "TEMPORAL_UNRESOLVED",
}

TRUTH_BEARING_RECORD_NATURES = {
    "PHYSICAL_DESIGN_FEATURE",
    "OBSERVATION_RECORD",
    "ACTIVITY_RECORD",
    "CURRENT_CONDITION",
    "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",
    "REVIEW_FINDING",
    "ADVERSE_OPERATIONAL_FINDING",
    "EXPLICIT_CONTROL_ABSENCE",
    "REGISTRATION_STATUS_RECORD",
    "REGISTERED_OPERATION_SCOPE",
    "REGISTRATION_SCOPE_DEFECT",
    "EQUIPMENT_CONDITION",
    "RISK_CONTROL_OUTCOME",
}

NON_ACTUAL_RECORD_NATURES = {
    "PLAN_STATEMENT",
    "PROCEDURE_STATEMENT",
}


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


def stable_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256(
        "\n".join(str(x) for x in parts).encode("utf-8")
    ).hexdigest()[:20]


def _contract_atom(contract_bundle: dict, atom_id: str | None) -> dict:
    contract = contract_bundle.get("contract", contract_bundle)
    matches = [
        row
        for row in contract.get("atoms", []) or []
        if str(row.get("atom_id")) == str(atom_id)
    ]
    return matches[0] if len(matches) == 1 else {}


def classify_temporal_requirement(
    requirement: dict,
    contract_bundle: dict,
) -> dict:
    """Classify whether a temporal gate is applicable.

    Explicit contract/requirement annotations remain authoritative.  V6.1 adds
    a deterministic fallback from the typed requirement predicate so an initial
    run does not become terminal merely because the contract omitted a redundant
    ``temporal_required`` flag.
    """
    atom = _contract_atom(contract_bundle, requirement.get("atom_id"))
    candidates = [
        ("EvidenceRequirement.temporal_requirement", requirement.get("temporal_requirement")),
        ("EvidenceRequirement.temporal_required", requirement.get("temporal_required")),
        ("ContractAtom.temporal_requirement", atom.get("temporal_requirement")),
        ("ContractAtom.temporal_required", atom.get("temporal_required")),
    ]
    state = "TEMPORAL_UNRESOLVED"
    basis = []
    for source, value in candidates:
        if value is None:
            continue
        normalized = str(value).strip().upper()
        if value is True or normalized in {"TRUE", "REQUIRED", "TEMPORAL_REQUIRED"}:
            candidate_state = "TEMPORAL_REQUIRED"
        elif value is False or normalized in {"FALSE", "NOT_REQUIRED", "TEMPORAL_NOT_REQUIRED"}:
            candidate_state = "TEMPORAL_NOT_REQUIRED"
        else:
            candidate_state = "TEMPORAL_UNRESOLVED"
        basis.append({"source": source, "value": value, "state": candidate_state})

    grounded_states = {row["state"] for row in basis if row["state"] != "TEMPORAL_UNRESOLVED"}
    if len(grounded_states) == 1:
        state = next(iter(grounded_states))
    elif len(grounded_states) > 1:
        state = "TEMPORAL_UNRESOLVED"

    # Typed deterministic fallback only when explicit annotations do not decide.
    profile = evidence_nature.infer_requirement_predicate_profile(requirement)
    fallback_used = False
    if state == "TEMPORAL_UNRESOLVED" and not grounded_states:
        temporality = profile.get("target_temporality")
        targets = set(profile.get("target_kinds", []) or [])
        if temporality in {
            evidence_nature.TARGET_TIME_ONGOING,
            evidence_nature.TARGET_TIME_EVENT,
            evidence_nature.TARGET_TIME_POINT,
        }:
            state = "TEMPORAL_REQUIRED"
            fallback_used = True
            basis.append({
                "source": "RequirementPredicateProfile.target_temporality",
                "value": temporality,
                "state": state,
            })
        elif evidence_nature.TARGET_DESIGN_CONSTRUCTION in targets:
            state = "TEMPORAL_NOT_REQUIRED"
            fallback_used = True
            basis.append({
                "source": "RequirementPredicateProfile.target_kinds",
                "value": evidence_nature.TARGET_DESIGN_CONSTRUCTION,
                "state": state,
            })

    result = {
        "schema": "freca-temporal-requirement-classification-v2",
        "classification_id": stable_id(
            "temporal-class",
            str(requirement.get("requirement_id")),
            canonical_json(basis),
        ),
        "requirement_id": str(requirement.get("requirement_id")),
        "atom_id": requirement.get("atom_id"),
        "state": state,
        "basis": basis,
        "typed_predicate_profile": profile,
        "typed_fallback_used": fallback_used,
        "reason_codes": (
            []
            if state != "TEMPORAL_UNRESOLVED"
            else [
                "NO_TYPED_TEMPORAL_APPLICABILITY_BASIS"
                if not basis
                else "TEMPORAL_APPLICABILITY_BASIS_CONFLICT_OR_UNKNOWN"
            ]
        ),
    }
    result["classification_sha256"] = sha256_json(result)
    return result


def _nature(row: dict) -> tuple[set[str], dict]:
    fact = row.get("fact_candidate") or {}
    nature = row.get("evidence_nature") or fact.get("evidence_nature") or {}
    values = set(map(str, nature.get("evidence_natures", []) or []))
    assertion = nature.get("assertion_mode") or {}
    return values, assertion


def build_information_reliability_assessment(
    *,
    row: dict,
    requirement: dict,
    requirement_result: dict,
) -> dict:
    fact = row.get("fact_candidate") or {}
    nature_values, assertion = _nature(row)
    quote = str(row.get("exact_quote") or fact.get("quote") or "").strip()
    quote_mode = str(row.get("quote_match_mode") or "").upper()
    grounding = fact.get("grounding_valid") is True
    identity_decision = str(row.get("identity_use_decision") or "")
    identity_fit = bool(
        row.get("identity_decisive_proof_eligible") is True
        and identity_decision == "ADMIT_DIRECT"
    )
    direct = row.get("argument_admission_channel") == "DIRECT"
    provenance_pass = bool(
        quote
        and grounding
        and quote_mode
        in {"EXACT", "EXACT_RAW", "EXACT_NORMALIZED", "SPAN_VALIDATED"}
        and fact.get("source_id")
    )

    relation = str(row.get("relation") or "")
    requirement_id = str(requirement.get("requirement_id"))
    opposite = "ATTACK" if relation == "SUPPORT" else "SUPPORT"
    conflicting_ids = sorted(
        str(other.get("alignment_evidence_id") or other.get("evidence_id"))
        for other in requirement_result.get("alignments", [])
        if str(other.get("requirement_id")) == requirement_id
        and other.get("relation") == opposite
        and other.get("argument_admission_channel") == "DIRECT"
    )

    actual_signal = bool(assertion.get("actual_signal_present"))
    modality = str(assertion.get("modality") or fact.get("modality") or "UNKNOWN")
    speech_act = str(
        assertion.get("speech_act") or fact.get("speech_act") or "UNKNOWN"
    )
    non_actual = bool(
        nature_values & NON_ACTUAL_RECORD_NATURES
        or modality in {"PLANNED", "REQUIRED", "CONDITIONAL"}
        or speech_act == "PROCEDURE"
    )
    truth_bearing_nature = bool(nature_values & TRUTH_BEARING_RECORD_NATURES)

    explicit_requirement = str(
        requirement.get("information_reliability_requirement")
        or requirement.get("reliability_requirement")
        or ""
    ).upper()
    if explicit_requirement in {"NOT_REQUIRED", "RELIABILITY_NOT_REQUIRED"}:
        status = "NOT_REQUIRED"
        permitted_scope = "CONTRACT_GROUNDED_NOT_REQUIRED"
        reasons = []
    elif provenance_pass and identity_fit and direct and truth_bearing_nature and not non_actual:
        status = "PASS"
        permitted_scope = "OBSERVABLE_FACT_AS_TYPED_BY_EVIDENCE_NATURE"
        reasons = []
    else:
        status = "UNRESOLVED"
        permitted_scope = "STATEMENT_OR_SOURCE_EXISTENCE_ONLY"
        reasons = []
        if not provenance_pass:
            reasons.append("SOURCE_PROVENANCE_OR_QUOTE_GROUNDING_INCOMPLETE")
        if not identity_fit:
            reasons.append("CASE_IDENTITY_FIT_INCOMPLETE")
        if not direct:
            reasons.append("DIRECTNESS_INCOMPLETE")
        if non_actual:
            reasons.append("PLAN_OR_PROCEDURE_NOT_ACTUAL_PERFORMANCE")
        if not truth_bearing_nature:
            reasons.append("RECORD_NATURE_NOT_TRUTH_BEARING_FOR_OBSERVABLE_FACT")

    assessment = {
        "schema": "freca-information-reliability-assessment-v2",
        "assessment_id": stable_id(
            "reliability-v2",
            str(row.get("alignment_evidence_id") or row.get("evidence_id")),
            requirement_id,
        ),
        "target_artifact_id": str(
            row.get("alignment_evidence_id")
            or row.get("fact_candidate_id")
            or row.get("evidence_id")
        ),
        "requirement_id": requirement_id,
        "source_authenticity_provenance": {
            "status": "PASS" if provenance_pass else "UNRESOLVED",
            "source_id": fact.get("source_id"),
            "exact_quote_present": bool(quote),
            "quote_match_mode": quote_mode,
            "grounding_valid": grounding,
        },
        "case_identity_fit": {
            "status": "PASS" if identity_fit else "UNRESOLVED",
            "identity_use_decision": identity_decision,
            "relation_to_case": row.get("identity_relation_to_case"),
        },
        "record_nature": {
            "values": sorted(nature_values),
            "actual_signal_present": actual_signal,
            "modality": modality,
            "speech_act": speech_act,
            "truth_bearing_for_observable_fact": truth_bearing_nature and not non_actual,
        },
        "directness": "DIRECT" if direct else "NOT_DIRECT",
        "content_conflict": {
            "status": "PRESENT" if conflicting_ids else "NONE_FOUND",
            "conflicting_alignment_ids": conflicting_ids,
            "conflict_erased": False,
        },
        "independence_corroboration": {
            "status": "CORRELATED_OR_UNKNOWN",
            "independence_inferred_from_filename_count": False,
        },
        "permitted_inference_scope": permitted_scope,
        "status": status,
        "reason_codes": sorted(set(reasons)),
    }
    assessment["assessment_sha256"] = sha256_json(assessment)
    return assessment


def infer_temporal_relation(
    *,
    row: dict,
    requirement: dict,
    classification: dict,
) -> tuple[str, list[str]]:
    """Infer row-level temporal fit from already typed, grounded semantics.

    This is intentionally conservative: it never turns a plan/procedure into an
    actual event and it only returns IN_SCOPE for a DIRECT typed basis whose
    assertion semantics match the requirement temporality.
    """
    state = classification.get("state")
    if state == "TEMPORAL_NOT_REQUIRED":
        return "NOT_REQUIRED", []
    if state != "TEMPORAL_REQUIRED":
        return "UNRESOLVED", ["TEMPORAL_REQUIREMENT_UNRESOLVED"]

    if str(row.get("predicate_compatibility") or "") != "DIRECT":
        return "UNRESOLVED", ["TEMPORAL_ONLY_EVALUATED_FOR_DIRECT_TYPED_BASIS"]

    profile = classification.get("typed_predicate_profile") or evidence_nature.infer_requirement_predicate_profile(requirement)
    temporality = profile.get("target_temporality")
    natures, assertion = _nature(row)
    modality = str(assertion.get("modality") or (row.get("fact_candidate") or {}).get("modality") or "UNKNOWN")
    actual = bool(assertion.get("actual_signal_present"))

    if temporality == evidence_nature.TARGET_TIME_EVENT:
        if modality == "ACTUAL" and natures & {
            "ACTIVITY_RECORD", "OBSERVATION_RECORD", "REVIEW_FINDING"
        }:
            return "IN_SCOPE", []
        return "UNRESOLVED", ["EVENT_TIME_NOT_GROUNDED_BY_ACTUAL_EVENT_EVIDENCE"]

    if temporality == evidence_nature.TARGET_TIME_ONGOING:
        # Current-state language can ground an ongoing proposition without an
        # artificial exact-date requirement. Point/event records remain
        # corroborative at the compatibility layer unless they explicitly state
        # the current/most-recent condition.
        if actual and natures & {
            "CURRENT_CONDITION",
            "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",
            "ADVERSE_OPERATIONAL_FINDING",
            "EXPLICIT_CONTROL_ABSENCE",
            "EQUIPMENT_CONDITION",
            "RISK_CONTROL_OUTCOME",
            "REGISTRATION_SCOPE_DEFECT",
        }:
            return "IN_SCOPE", []
        if natures & {"REGISTERED_OPERATION_SCOPE", "REGISTRATION_STATUS_RECORD"}:
            return "IN_SCOPE", []
        return "UNRESOLVED", ["ONGOING_STATE_TEMPORAL_FIT_NOT_ESTABLISHED"]

    return "UNRESOLVED", ["TEMPORAL_RELATION_TARGET_KIND_UNRESOLVED"]


def apply_gate_assessments(
    *,
    requirement_result: dict,
    contract_bundle: dict,
) -> tuple[dict, dict]:
    patched = copy.deepcopy(requirement_result)
    requirements = {
        str(row["requirement_id"]): row
        for row in patched["evidence_requirement_plan"].get("requirements", [])
    }
    classifications = {
        rid: classify_temporal_requirement(req, contract_bundle)
        for rid, req in requirements.items()
    }
    reliability_ids = []
    for row in patched.get("alignments", []):
        rid = str(row.get("requirement_id"))
        requirement = requirements.get(rid, {})
        temporal = classifications.get(rid)
        if temporal:
            row["temporal_requirement_classification"] = copy.deepcopy(temporal)
            if not row.get("temporal_assessment") and not row.get("temporal_relation"):
                relation, relation_reasons = infer_temporal_relation(
                    row=row,
                    requirement=requirement,
                    classification=temporal,
                )
                row["temporal_relation"] = relation
                row["temporal_assessment"] = {
                    "schema": "freca-deterministic-temporal-assessment-v6.1",
                    "relation": relation,
                    "reason_codes": relation_reasons,
                    "derived_from_typed_artifacts_only": True,
                }

        existing_assessment = row.get("information_reliability") or {}
        if existing_assessment.get("schema") == "freca-information-reliability-assessment-v2":
            assessment = copy.deepcopy(existing_assessment)
        else:
            assessment = build_information_reliability_assessment(
                row=row,
                requirement=requirement,
                requirement_result=patched,
            )
        row["information_reliability"] = assessment
        reliability_ids.append(assessment["assessment_id"])

    audit = {
        "schema": "freca-proof-gate-applicability-audit-v2",
        "temporal_classifications": list(classifications.values()),
        "information_reliability_assessment_ids": sorted(reliability_ids),
        "missing_information_became_pass": False,
        "typed_temporal_fallback_enabled": True,
        "not_required_collapsed_to_pass": False,
    }
    audit["audit_sha256"] = sha256_json(audit)
    patched["proof_gate_applicability_v2"] = audit
    return patched, audit


def validate_gate_applicability(
    *,
    requirement_result: dict,
    contract_bundle: dict,
) -> tuple[bool, list[str]]:
    requirements = {
        str(row["requirement_id"]): row
        for row in requirement_result["evidence_requirement_plan"].get(
            "requirements", []
        )
    }
    expected = {
        rid: classify_temporal_requirement(requirement, contract_bundle)
        for rid, requirement in requirements.items()
    }
    reasons = []
    for row in requirement_result.get("alignments", []):
        rid = str(row.get("requirement_id"))
        classification = row.get("temporal_requirement_classification") or {}
        state = classification.get("state")
        if state not in TEMPORAL_STATES:
            reasons.append(f"INVALID_TEMPORAL_CLASSIFICATION_STATE:{rid}:{state}")
            continue
        if canonical_json(classification) != canonical_json(expected.get(rid, {})):
            reasons.append(f"TEMPORAL_CLASSIFICATION_RECOMPUTATION_MISMATCH:{rid}")
        if state == "TEMPORAL_NOT_REQUIRED" and str(
            row.get("temporal_relation")
        ).upper() != "NOT_REQUIRED":
            reasons.append(f"NOT_REQUIRED_COLLAPSED_OR_REWRITTEN:{rid}")
    return not reasons, sorted(set(reasons))


def execute_information_reliability_action(
    *,
    action: dict,
    requirement_result: dict,
) -> dict:
    requirements = {
        str(row["requirement_id"]): row
        for row in requirement_result["evidence_requirement_plan"].get(
            "requirements", []
        )
    }
    lookup = {}
    for row in requirement_result.get("alignments", []):
        for key in (
            row.get("alignment_evidence_id"),
            row.get("fact_candidate_id"),
            row.get("evidence_id"),
        ):
            if key:
                lookup[str(key)] = row
    assessments = []
    unresolved_targets = []
    for target in action.get("target_artifact_ids", []) or []:
        row = lookup.get(str(target))
        if row is None:
            unresolved_targets.append(str(target))
            continue
        rid = str(row.get("requirement_id"))
        assessment = build_information_reliability_assessment(
            row=row,
            requirement=requirements.get(rid, {}),
            requirement_result=requirement_result,
        )
        assessment["action_id"] = action["action_id"]
        unsigned = dict(assessment)
        unsigned.pop("assessment_sha256", None)
        assessment["assessment_sha256"] = sha256_json(unsigned)
        assessments.append(assessment)

    decisive = sum(row["status"] in {"PASS", "NOT_REQUIRED"} for row in assessments)
    failed = sum(row["status"] == "FAIL" for row in assessments)
    unresolved = len(assessments) - decisive - failed + len(unresolved_targets)
    execution = {
        "schema": "freca-core-repair-action-execution-v2",
        "execution_id": stable_id("repair-exec-v2", action["action_id"]),
        "action_id": action["action_id"],
        "goal_id": action["goal_id"],
        "action_type": "ASSESS_INFORMATION_RELIABILITY",
        "target_artifact_ids": list(action.get("target_artifact_ids", []) or []),
        "information_reliability_assessments": assessments,
        "unresolved_target_artifact_ids": unresolved_targets,
        "passed_or_not_required_count": decisive,
        "failed_count": failed,
        "unresolved_count": unresolved,
        "signal_status": (
            "NEW_VALIDATED_SIGNAL" if decisive or failed else "NO_NEW_VALIDATED_SIGNAL"
        ),
        "action_execution_status": "EXECUTED",
        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,
    }
    execution["execution_sha256"] = sha256_json(execution)
    return execution


def _temporal_gate(rows: list[dict]) -> tuple[bool, str, list[str]]:
    if not rows:
        return False, "MISSING", ["NO_TEMPORAL_BASIS_ROWS"]
    statuses = []
    codes = []
    for row in rows:
        classification = row.get("temporal_requirement_classification") or {}
        state = classification.get("state", "TEMPORAL_UNRESOLVED")
        if state == "TEMPORAL_NOT_REQUIRED":
            statuses.append("NOT_REQUIRED")
            continue
        if state == "TEMPORAL_UNRESOLVED":
            statuses.append("UNRESOLVED")
            codes.append("TEMPORAL_REQUIREMENT_UNRESOLVED")
            continue
        relation = str(row.get("temporal_relation") or "UNRESOLVED").upper()
        if relation in {"PASS", "MATCH", "IN_SCOPE", "IN_PERIOD"}:
            statuses.append("PASS")
        elif relation in {"FAIL", "OUT_OF_SCOPE", "OUT_OF_PERIOD", "MISMATCH"}:
            statuses.append("FAIL")
            codes.append("TEMPORAL_SCOPE_EXPLICITLY_FAILED")
        else:
            statuses.append("UNRESOLVED")
            codes.append("TEMPORAL_SCOPE_UNRESOLVED")
    if "PASS" in statuses:
        return True, "PASS", []
    if "NOT_REQUIRED" in statuses:
        return True, "NOT_REQUIRED", []
    if "UNRESOLVED" in statuses:
        return False, "UNRESOLVED", sorted(set(codes))
    return False, "FAIL", sorted(set(codes))


def _reliability_gate(rows: list[dict]) -> tuple[bool, str, list[str]]:
    if not rows:
        return False, "MISSING", ["NO_RELIABILITY_BASIS_ROWS"]
    statuses = []
    for row in rows:
        assessment = row.get("information_reliability") or {}
        statuses.append(str(assessment.get("status") or "UNRESOLVED").upper())
    if "PASS" in statuses:
        return True, "PASS", []
    if "NOT_REQUIRED" in statuses:
        return True, "NOT_REQUIRED", []
    if "FAIL" in statuses:
        return False, "FAIL", ["INFORMATION_RELIABILITY_FAILED"]
    return False, "UNRESOLVED", ["INFORMATION_RELIABILITY_UNRESOLVED"]


def _evaluate_direction(
    *,
    requirement: dict,
    rows: list[dict],
    direction: str,
    coverage_pass: bool,
) -> dict:
    rid = str(requirement["requirement_id"])
    if direction == "SUPPORT":
        basis = proof_v1._direct_rows(rows, "SUPPORT")
        standard = proof_v1.PROOF_STANDARDS["AUDIT_SUFFICIENT"]
    else:
        basis = [
            row
            for row in proof_v1._direct_rows(rows, "ATTACK")
            if proof_v1._is_explicit_adverse_fact(row)
        ]
        standard = proof_v1.PROOF_STANDARDS["EXPLICIT_VIOLATION"]

    all_support = proof_v1._direct_rows(rows, "SUPPORT")
    all_attack = proof_v1._direct_rows(rows, "ATTACK")
    contradiction = bool(all_support and all_attack)
    relevance_pass = bool(basis)
    identity_pass = bool(basis) and all(
        row.get("argument_admission_channel") == "DIRECT"
        and row.get("identity_decisive_proof_eligible") is True
        for row in basis
    )
    temporal_pass, temporal_status, temporal_codes = _temporal_gate(basis)
    reliability_pass, reliability_status, reliability_codes = _reliability_gate(basis)
    failures = []
    if not relevance_pass:
        failures.append(
            "NO_DIRECT_SUPPORT_BASIS"
            if direction == "SUPPORT"
            else "NO_EXPLICIT_VIOLATION_BASIS"
        )
    if basis and not identity_pass:
        failures.append("IDENTITY_GATE_FAILED")
    failures.extend(temporal_codes)
    failures.extend(reliability_codes)
    if not coverage_pass:
        failures.append("COVERAGE_INCOMPLETE")

    accepted = all(
        [
            relevance_pass,
            identity_pass,
            temporal_pass,
            reliability_pass,
            coverage_pass,
        ]
    )
    return {
        "report_id": f"proof-{rid.lower()}-{direction.lower()}-v2",
        "statement_id": f"stmt-{rid.lower()}",
        "requirement_id": rid,
        "direction": direction,
        "proof_standard_id": standard["proof_standard_id"],
        "standard_kind": standard["standard_kind"],
        "relevance_pass": relevance_pass,
        "identity_pass": identity_pass,
        "temporal_pass": temporal_pass,
        "temporal_status": temporal_status,
        "reliability_pass": reliability_pass,
        "reliability_status": reliability_status,
        "coverage_pass": coverage_pass,
        "contradiction_state": "PRESERVED" if contradiction else "NONE",
        "contradiction_policy": standard["contradiction_policy"],
        "source_independence_state": proof_v1.source_independence_state(
            basis, standard_kind=standard["standard_kind"]
        ),
        "accepted_direction": accepted,
        "failure_codes": sorted(set(failures)),
        "basis_artifact_ids": [proof_v1.alignment_id(row) for row in basis],
        "basis_evidence_ids": [str(row.get("evidence_id")) for row in basis],
        "gate_inputs": {
            "basis_count": len(basis),
            "coverage_required": True,
            "typed_temporal_applicability_used": True,
            "typed_reliability_components_used": True,
        },
    }


def evaluate_proof_standard_bundle(
    requirement_result: dict,
    coverage: dict,
) -> dict:
    plan = requirement_result["evidence_requirement_plan"]
    alignments = requirement_result.get("alignments", [])
    directional = coverage.get("directional_proof_coverage", {})
    reports = []
    for requirement in plan.get("requirements", []):
        rid = str(requirement["requirement_id"])
        rows = [
            row for row in alignments if str(row.get("requirement_id")) == rid
        ]
        support = _evaluate_direction(
            requirement=requirement,
            rows=rows,
            direction="SUPPORT",
            coverage_pass=bool(directional.get(rid, {}).get("SUPPORT", False)),
        )
        attack = _evaluate_direction(
            requirement=requirement,
            rows=rows,
            direction="ATTACK",
            coverage_pass=bool(directional.get(rid, {}).get("ATTACK", False)),
        )
        accepted_state = proof_v1.state_from_pair(
            support["accepted_direction"], attack["accepted_direction"]
        )
        failure_codes = []
        for proof in (support, attack):
            for code in proof.get("failure_codes", []):
                if code not in failure_codes:
                    failure_codes.append(code)
        reports.append(
            {
                "requirement_id": rid,
                "statement_id": f"stmt-{rid.lower()}",
                "atom_id": requirement.get("atom_id"),
                "decisiveness": requirement.get("decisiveness"),
                "raw_state": proof_v1.state_from_pair(
                    bool(proof_v1._direct_rows(rows, "SUPPORT")),
                    bool(proof_v1._direct_rows(rows, "ATTACK")),
                ),
                "accepted_state": accepted_state,
                "contradiction_state": (
                    "PRESERVED"
                    if proof_v1._direct_rows(rows, "SUPPORT")
                    and proof_v1._direct_rows(rows, "ATTACK")
                    else "NONE"
                ),
                "support_proof": support,
                "attack_proof": attack,
                "failure_codes": failure_codes,
                "proof_standard_status": (
                    "PASSED" if accepted_state != "UNKNOWN" else "UNRESOLVED"
                ),
                "coverage_policy_version": "PURPOSE_SPECIFIC_COVERAGE_V2_2",
            }
        )

    bundle = {
        "schema": "freca-core-proof-standard-v2",
        "proof_policy_version": "DIRECTIONAL_PROOF_GATE_V2_2",
        "cp_id": plan.get("cp_id"),
        "proof_standard_templates": proof_v1.PROOF_STANDARDS,
        "coverage_complete": coverage.get("proof_coverage_complete", False),
        "directional_coverage": directional,
        "requirement_reports": reports,
        "evaluation_locked": False,
        "internal_outcome": "UNKNOWN",
        "submission_label": None,
        "invariants": {
            "missing_temporal_not_pass": True,
            "missing_reliability_not_pass": True,
            "not_required_is_typed_not_fabricated_pass": True,
            "conflicting_support_attack_preserved": True,
        },
    }
    bundle["bundle_sha256"] = sha256_json(bundle)
    return bundle
