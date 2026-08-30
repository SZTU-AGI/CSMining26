#!/usr/bin/env python3
"""FRECA Core typed ProofStandard substrate v1.

This module implements the smallest D7.14-compatible proof-gate layer for the
current Core.  It deliberately does not implement full Coverage, temporal
resolution, or InformationReliability.  Missing upstream artifacts cause the
corresponding gates to fail/resolve to UNKNOWN rather than being guessed.

Key separation:
    raw evidence state
        != proof accepted_state
        != final task outcome

For each EvidenceRequirement:
    SUPPORT direction -> AUDIT_SUFFICIENT
    ATTACK direction  -> EXPLICIT_VIOLATION

The two directions are evaluated independently and recombined as a
FourValuedState.  If both directions pass, BOTH is preserved.

No final 1/0/N/A label is produced.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


# ============================================================================
# Basic helpers
# ============================================================================


STATE_PAIRS = {
    "UNKNOWN": (False, False),
    "TRUE": (True, False),
    "FALSE": (False, True),
    "BOTH": (True, True),
}


def state_from_pair(
    support: bool,
    attack: bool,
) -> str:
    if support and attack:
        return "BOTH"
    if support:
        return "TRUE"
    if attack:
        return "FALSE"
    return "UNKNOWN"


def canonical_json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(
    value: Any,
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json(value).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def load_json(
    path: Path,
) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def save_json(
    value: Any,
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def alignment_id(
    row: dict,
) -> str:
    return str(
        row.get(
            "alignment_evidence_id"
        )
        or row.get(
            "fact_candidate_id"
        )
        or row.get(
            "evidence_id"
        )
        or "unknown-alignment"
    )


# ============================================================================
# Frozen minimal ProofStandard templates
# ============================================================================


PROOF_STANDARDS = {
    "AUDIT_SUFFICIENT": {
        "proof_standard_id":
            "proof-audit-sufficient-v1",
        "standard_kind":
            "AUDIT_SUFFICIENT",
        "require_relevance":
            True,
        "require_identity_match":
            True,
        "require_temporal_match":
            True,
        "require_reliability_gate":
            True,
        "require_coverage_gate":
            True,
        "contradiction_policy":
            "PRESERVE_BOTH",
    },
    "EXPLICIT_VIOLATION": {
        "proof_standard_id":
            "proof-explicit-violation-v1",
        "standard_kind":
            "EXPLICIT_VIOLATION",
        "require_relevance":
            True,
        "require_identity_match":
            True,
        "require_temporal_match":
            True,
        "require_reliability_gate":
            True,
        # Conservative pilot policy:
        # require targeted bidirectional coverage before locking an adverse
        # direction, so exceptions/rebuttals are not silently skipped.
        "require_coverage_gate":
            True,
        "contradiction_policy":
            "PRESERVE_BOTH",
    },
}


# ============================================================================
# Directional evidence selection
# ============================================================================


def _direct_rows(
    rows: list[dict],
    relation: str,
) -> list[dict]:
    return [
        row
        for row in rows
        if (
            row.get(
                "relation"
            )
            == relation
            and row.get(
                "argument_admission_channel"
            )
            == "DIRECT"
            and row.get(
                "argument_truth_bearing"
            )
            is True
        )
    ]


def _is_explicit_adverse_fact(
    row: dict,
) -> bool:
    """Conservative explicit-adverse candidate test.

    This does NOT prove violation.  It only determines whether an ATTACK row
    is eligible to enter the EXPLICIT_VIOLATION proof standard.

    Required:
      - DIRECT typed compatibility;
      - grounded/admitted ATTACK alignment;
      - actual adverse FactCandidate;
      - validated grounding.
    """

    if (
        row.get(
            "relation"
        )
        != "ATTACK"
    ):
        return False

    if (
        row.get(
            "argument_admission_channel"
        )
        != "DIRECT"
    ):
        return False

    if (
        row.get(
            "argument_truth_bearing"
        )
        is not True
    ):
        return False

    if (
        row.get(
            "predicate_compatibility"
        )
        != "DIRECT"
    ):
        return False

    fact = (
        row.get(
            "fact_candidate"
        )
        or {}
    )

    if (
        fact.get(
            "polarity"
        )
        != "ADVERSE"
    ):
        return False

    if (
        fact.get(
            "modality"
        )
        != "ACTUAL"
    ):
        return False

    if (
        fact.get(
            "grounding_valid"
        )
        is not True
    ):
        return False

    if (
        fact.get(
            "status"
        )
        != "SPAN_VALIDATED"
    ):
        return False

    return True


# ============================================================================
# Gate extraction: only explicit upstream artifacts may pass
# ============================================================================


_TEMPORAL_PASS_VALUES = {
    "PASS",
    "MATCH",
    "IN_SCOPE",
    "IN_PERIOD",
    "NOT_REQUIRED",
}

_TEMPORAL_FAIL_VALUES = {
    "FAIL",
    "OUT_OF_SCOPE",
    "OUT_OF_PERIOD",
    "MISMATCH",
}

_TEMPORAL_UNRESOLVED_VALUES = {
    "UNKNOWN",
    "UNRESOLVED",
    "NOT_ASSESSED",
    "NOT_TESTED",
    "AMBIGUOUS",
    "OVERLAPS",
    "CONDITIONAL",
}


def _row_temporal_status(
    row: dict,
) -> tuple[str, list[str]]:
    """Read explicit temporal artifacts without converting uncertainty to failure.

    Frozen production semantics:
      PASS-like        -> PASS
      OUT_OF_SCOPE     -> FAIL
      UNKNOWN/OVERLAPS -> UNRESOLVED
      conflicting explicit temporal artifacts -> UNRESOLVED

    No temporal state is inferred from evidence prose.
    """

    candidates = [
        row.get("temporal_match"),
        row.get("temporal_relation"),
    ]

    fact = row.get("fact_candidate") or {}

    quality = (
        row.get("evidence_quality")
        or fact.get("evidence_quality")
        or {}
    )

    candidates.append(
        quality.get("temporal_fit")
    )

    explicit = [
        str(value).strip().upper()
        for value in candidates
        if value is not None
        and str(value).strip()
    ]

    if not explicit:
        return (
            "UNRESOLVED",
            ["TEMPORAL_ASSESSMENT_MISSING"],
        )

    classes = set()

    for value in explicit:
        if value in _TEMPORAL_PASS_VALUES:
            classes.add("PASS")
        elif value in _TEMPORAL_FAIL_VALUES:
            classes.add("FAIL")
        elif value in _TEMPORAL_UNRESOLVED_VALUES:
            classes.add("UNRESOLVED")
        else:
            classes.add("UNRESOLVED")

    if classes == {"PASS"}:
        return ("PASS", [])

    if classes == {"FAIL"}:
        return (
            "FAIL",
            ["TEMPORAL_SCOPE_EXPLICITLY_FAILED"],
        )

    if "PASS" in classes and "FAIL" in classes:
        return (
            "UNRESOLVED",
            ["TEMPORAL_ASSESSMENT_CONFLICT"],
        )

    if "UNRESOLVED" in classes:
        return (
            "UNRESOLVED",
            ["TEMPORAL_SCOPE_UNRESOLVED"],
        )

    return (
        "UNRESOLVED",
        ["TEMPORAL_SCOPE_UNRESOLVED"],
    )


def _row_reliability_status(
    row: dict,
) -> tuple[str, list[str]]:
    """Read an explicit InformationReliability/EvidenceQuality artifact.

    Grounding/identity alone are intentionally NOT promoted into reliability.
    """

    fact = (
        row.get(
            "fact_candidate"
        )
        or {}
    )

    reliability = (
        row.get(
            "information_reliability"
        )
        or fact.get(
            "information_reliability"
        )
        or {}
    )

    quality = (
        row.get(
            "evidence_quality"
        )
        or fact.get(
            "evidence_quality"
        )
        or {}
    )

    if reliability:
        status = str(
            reliability.get(
                "status",
                reliability.get(
                    "reliability_status",
                    "",
                ),
            )
        ).upper()

        if status in {
            "PASS",
            "RELIABLE",
            "SUFFICIENT",
        }:
            return (
                "PASS",
                [],
            )

        if status in {
            "FAIL",
            "UNRELIABLE",
            "EXCLUDED",
        }:
            return (
                "FAIL",
                [
                    "INFORMATION_RELIABILITY_FAILED"
                ],
            )

        return (
            "UNRESOLVED",
            [
                "INFORMATION_RELIABILITY_UNRESOLVED"
            ],
        )

    if quality:
        source_reliability = str(
            quality.get(
                "source_reliability",
                "",
            )
        ).upper()

        directness = str(
            quality.get(
                "directness",
                "",
            )
        ).upper()

        record_nature = str(
            quality.get(
                "record_nature",
                "",
            )
        ).upper()

        if (
            source_reliability
            in {
                "HIGH",
                "MEDIUM",
            }
            and directness
            == "DIRECT"
            and record_nature
            not in {
                "",
                "UNKNOWN",
                "TEMPLATE",
            }
        ):
            return (
                "PASS",
                [],
            )

        if (
            source_reliability
            in {
                "LOW",
            }
            or directness
            in {
                "INDIRECT",
                "HEARSAY",
            }
            or record_nature
            == "TEMPLATE"
        ):
            return (
                "FAIL",
                [
                    "EVIDENCE_QUALITY_RELIABILITY_FAILED"
                ],
            )

        return (
            "UNRESOLVED",
            [
                "EVIDENCE_QUALITY_RELIABILITY_UNRESOLVED"
            ],
        )

    return (
        "UNRESOLVED",
        [
            "INFORMATION_RELIABILITY_MISSING"
        ],
    )


def _aggregate_required_gate(
    rows: list[dict],
    extractor,
    *,
    missing_code: str,
) -> tuple[bool, str, list[str]]:
    if not rows:
        return (
            False,
            "FAIL",
            [
                missing_code
            ],
        )

    statuses: list[
        str
    ] = []

    codes: list[
        str
    ] = []

    for row in rows:
        status, row_codes = (
            extractor(
                row
            )
        )

        statuses.append(
            status
        )

        for code in row_codes:
            if (
                code
                not in codes
            ):
                codes.append(
                    code
                )

    # A directional proof may rely on any sufficiently qualified basis item.
    # Therefore one PASS is enough for this gate.  This is not a document-count
    # threshold and does not assert source independence.
    if (
        "PASS"
        in statuses
    ):
        return (
            True,
            "PASS",
            [],
        )

    if (
        "UNRESOLVED"
        in statuses
    ):
        return (
            False,
            "UNRESOLVED",
            codes,
        )

    return (
        False,
        "FAIL",
        codes,
    )


# ============================================================================
# Source-independence diagnostic
# ============================================================================


def _source_id(
    row: dict,
) -> str:
    fact = (
        row.get(
            "fact_candidate"
        )
        or {}
    )

    source = (
        fact.get(
            "source_id"
        )
        or row.get(
            "source_id"
        )
        or str(
            row.get(
                "evidence_id",
                "",
            )
        ).split(
            ":",
            1,
        )[0]
    )

    return str(
        source
    )


def source_independence_state(
    rows: list[dict],
    *,
    standard_kind: str,
) -> str:
    if (
        standard_kind
        == "EXPLICIT_VIOLATION"
    ):
        return (
            "NOT_REQUIRED"
        )

    if not rows:
        return (
            "INSUFFICIENT"
        )

    # Without LineageCluster/PopulationFrame we deliberately do not infer
    # independence merely from different filenames.
    return (
        "CORRELATED_OR_UNKNOWN"
    )


# ============================================================================
# Directional proof gate
# ============================================================================


def evaluate_directional_proof(
    *,
    requirement_id: str,
    statement_id: str,
    rows: list[dict],
    direction: str,
    proof_standard: dict,
    coverage_pass: bool,
    contradiction_present: bool,
) -> dict:
    if (
        direction
        == "SUPPORT"
    ):
        basis_rows = (
            _direct_rows(
                rows,
                "SUPPORT",
            )
        )

        explicit_candidate_rows = (
            basis_rows
        )

    elif (
        direction
        == "ATTACK"
    ):
        attack_rows = (
            _direct_rows(
                rows,
                "ATTACK",
            )
        )

        explicit_candidate_rows = [
            row
            for row
            in attack_rows
            if (
                _is_explicit_adverse_fact(
                    row
                )
            )
        ]

        basis_rows = (
            explicit_candidate_rows
        )

    else:
        raise ValueError(
            f"Unknown direction: {direction}"
        )

    failure_codes: list[
        str
    ] = []

    relevance_pass = bool(
        basis_rows
    )

    if not relevance_pass:
        failure_codes.append(
            (
                "NO_DIRECT_SUPPORT_BASIS"
                if direction
                == "SUPPORT"
                else "NO_EXPLICIT_VIOLATION_BASIS"
            )
        )

    identity_pass = bool(
        basis_rows
    ) and all(
        (
            row.get(
                "argument_admission_channel"
            )
            == "DIRECT"
            and row.get(
                "identity_decisive_proof_eligible"
            )
            is True
        )
        for row
        in basis_rows
    )

    if (
        basis_rows
        and not identity_pass
    ):
        failure_codes.append(
            "IDENTITY_GATE_FAILED"
        )

    (
        temporal_pass,
        temporal_status,
        temporal_codes,
    ) = _aggregate_required_gate(
        basis_rows,
        _row_temporal_status,
        missing_code=
            "NO_TEMPORAL_BASIS_ROWS",
    )

    (
        reliability_pass,
        reliability_status,
        reliability_codes,
    ) = _aggregate_required_gate(
        basis_rows,
        _row_reliability_status,
        missing_code=
            "NO_RELIABILITY_BASIS_ROWS",
    )

    for code in (
        temporal_codes
        + reliability_codes
    ):
        if (
            code
            not in failure_codes
        ):
            failure_codes.append(
                code
            )

    if (
        proof_standard[
            "require_coverage_gate"
        ]
        and not coverage_pass
    ):
        failure_codes.append(
            "COVERAGE_INCOMPLETE"
        )

    if contradiction_present:
        contradiction_state = (
            "PRESERVED"
        )
    else:
        contradiction_state = (
            "NONE"
        )

    required_checks = []

    if proof_standard[
        "require_relevance"
    ]:
        required_checks.append(
            relevance_pass
        )

    if proof_standard[
        "require_identity_match"
    ]:
        required_checks.append(
            identity_pass
        )

    if proof_standard[
        "require_temporal_match"
    ]:
        required_checks.append(
            temporal_pass
        )

    if proof_standard[
        "require_reliability_gate"
    ]:
        required_checks.append(
            reliability_pass
        )

    if proof_standard[
        "require_coverage_gate"
    ]:
        required_checks.append(
            coverage_pass
        )

    contradiction_policy = (
        proof_standard[
            "contradiction_policy"
        ]
    )

    if (
        contradiction_present
        and contradiction_policy
        == "BLOCK"
    ):
        required_checks.append(
            False
        )
        failure_codes.append(
            "CONTRADICTION_BLOCKING"
        )

    elif (
        contradiction_present
        and contradiction_policy
        == "ALLOW_IF_DEFEATED"
    ):
        required_checks.append(
            False
        )
        failure_codes.append(
            "CONTRADICTION_NOT_DEFEATED"
        )

    # PRESERVE_BOTH does not erase either directional proof.
    accepted_direction = (
        bool(
            required_checks
        )
        and all(
            required_checks
        )
    )

    return {
        "report_id":
            (
                f"proof-{requirement_id.lower()}-"
                f"{direction.lower()}-v1"
            ),
        "statement_id":
            statement_id,
        "requirement_id":
            requirement_id,
        "direction":
            direction,
        "proof_standard_id":
            proof_standard[
                "proof_standard_id"
            ],
        "standard_kind":
            proof_standard[
                "standard_kind"
            ],

        "relevance_pass":
            relevance_pass,
        "identity_pass":
            identity_pass,
        "temporal_pass":
            temporal_pass,
        "temporal_status":
            temporal_status,
        "reliability_pass":
            reliability_pass,
        "reliability_status":
            reliability_status,
        "coverage_pass":
            coverage_pass,

        "contradiction_state":
            contradiction_state,
        "contradiction_policy":
            contradiction_policy,

        "source_independence_state":
            source_independence_state(
                basis_rows,
                standard_kind=
                    proof_standard[
                        "standard_kind"
                    ],
            ),

        "accepted_direction":
            accepted_direction,
        "failure_codes":
            failure_codes,

        "basis_artifact_ids": [
            alignment_id(
                row
            )
            for row
            in basis_rows
        ],
        "basis_evidence_ids": [
            str(
                row.get(
                    "evidence_id"
                )
            )
            for row
            in basis_rows
        ],

        "explicit_violation_candidate_ids": [
            alignment_id(
                row
            )
            for row
            in (
                explicit_candidate_rows
                if direction
                == "ATTACK"
                else []
            )
        ],

        "gate_inputs": {
            "basis_count":
                len(
                    basis_rows
                ),
            "coverage_required":
                proof_standard[
                    "require_coverage_gate"
                ],
            "lineage_independence_assessed":
                False,
        },
    }


# ============================================================================
# Requirement-level ProofStandard evaluation
# ============================================================================


def evaluate_requirement_proof(
    *,
    requirement: dict,
    rows: list[dict],
    coverage_pass: bool,
) -> dict:
    rid = str(
        requirement[
            "requirement_id"
        ]
    )

    statement_id = (
        f"stmt-{rid.lower()}"
    )

    direct_support = (
        _direct_rows(
            rows,
            "SUPPORT",
        )
    )

    direct_attack = (
        _direct_rows(
            rows,
            "ATTACK",
        )
    )

    contradiction_present = bool(
        direct_support
        and direct_attack
    )

    support_report = (
        evaluate_directional_proof(
            requirement_id=
                rid,
            statement_id=
                statement_id,
            rows=
                rows,
            direction=
                "SUPPORT",
            proof_standard=
                PROOF_STANDARDS[
                    "AUDIT_SUFFICIENT"
                ],
            coverage_pass=
                coverage_pass,
            contradiction_present=
                contradiction_present,
        )
    )

    attack_report = (
        evaluate_directional_proof(
            requirement_id=
                rid,
            statement_id=
                statement_id,
            rows=
                rows,
            direction=
                "ATTACK",
            proof_standard=
                PROOF_STANDARDS[
                    "EXPLICIT_VIOLATION"
                ],
            coverage_pass=
                coverage_pass,
            contradiction_present=
                contradiction_present,
        )
    )

    accepted_state = (
        state_from_pair(
            support_report[
                "accepted_direction"
            ],
            attack_report[
                "accepted_direction"
            ],
        )
    )

    raw_state = (
        state_from_pair(
            bool(
                direct_support
            ),
            bool(
                direct_attack
            ),
        )
    )

    failure_codes = []

    for report in (
        support_report,
        attack_report,
    ):
        for code in report[
            "failure_codes"
        ]:
            if (
                code
                not in failure_codes
            ):
                failure_codes.append(
                    code
                )

    return {
        "requirement_id":
            rid,
        "statement_id":
            statement_id,
        "atom_id":
            requirement.get(
                "atom_id"
            ),
        "decisiveness":
            requirement.get(
                "decisiveness"
            ),

        "raw_state":
            raw_state,
        "accepted_state":
            accepted_state,

        "contradiction_state":
            (
                "PRESERVED"
                if contradiction_present
                else "NONE"
            ),

        "support_proof":
            support_report,
        "attack_proof":
            attack_report,

        "failure_codes":
            failure_codes,

        "proof_standard_status":
            (
                "PASSED"
                if accepted_state
                != "UNKNOWN"
                else "UNRESOLVED"
            ),
    }


def evaluate_proof_standard_bundle(
    requirement_result: dict,
) -> dict:
    plan = (
        requirement_result[
            "evidence_requirement_plan"
        ]
    )

    alignments = (
        requirement_result.get(
            "alignments",
            []
        )
    )

    previous_proof = (
        requirement_result.get(
            "proof_gate",
            {}
        )
    )

    coverage_complete = bool(
        previous_proof.get(
            "coverage_complete",
            False,
        )
    )

    previous_reports = {
        str(
            item[
                "requirement_id"
            ]
        ): item
        for item
        in previous_proof.get(
            "requirement_reports",
            [],
        )
    }

    reports = []

    for requirement in plan[
        "requirements"
    ]:
        rid = str(
            requirement[
                "requirement_id"
            ]
        )

        rows = [
            row
            for row
            in alignments
            if str(
                row.get(
                    "requirement_id"
                )
            )
            == rid
        ]

        previous = (
            previous_reports.get(
                rid,
                {},
            )
        )

        coverage_pass = bool(
            coverage_complete
            and previous.get(
                "coverage_pass",
                False,
            )
        )

        reports.append(
            evaluate_requirement_proof(
                requirement=
                    requirement,
                rows=
                    rows,
                coverage_pass=
                    coverage_pass,
            )
        )

    bundle = {
        "schema":
            "freca-core-proof-standard-v1",
        "cp_id":
            plan.get(
                "cp_id"
            ),
        "proof_standard_templates":
            PROOF_STANDARDS,
        "coverage_complete":
            coverage_complete,
        "requirement_reports":
            reports,

        "evaluation_locked":
            False,
        "internal_outcome":
            "UNKNOWN",
        "submission_label":
            None,

        "notes": [
            (
                "Missing temporal, InformationReliability, or Coverage "
                "artifacts fail their typed gates; no inference is used "
                "to fill those gaps."
            ),
            (
                "SUPPORT and ATTACK are evaluated independently; "
                "PRESERVE_BOTH never majority-votes contradictory evidence."
            ),
            (
                "This substrate does not apply BurdenRule and cannot "
                "produce final 1/0/N/A."
            ),
        ],
    }

    bundle[
        "bundle_sha256"
    ] = sha256_json(
        bundle
    )

    return bundle


# ============================================================================
# Optional post-proof Argument evaluation
# ============================================================================


def build_post_proof_requirement_result(
    *,
    requirement_result: dict,
    proof_bundle: dict,
) -> dict:
    """Return an in-memory copy with ProofStandard accepted states installed."""

    patched = copy.deepcopy(
        requirement_result
    )

    by_rid = {
        str(
            item[
                "requirement_id"
            ]
        ): item
        for item
        in proof_bundle[
            "requirement_reports"
        ]
    }

    gate = (
        patched.setdefault(
            "proof_gate",
            {},
        )
    )

    for report in gate.get(
        "requirement_reports",
        [],
    ):
        rid = str(
            report[
                "requirement_id"
            ]
        )

        proof_report = (
            by_rid[
                rid
            ]
        )

        report[
            "accepted_state"
        ] = proof_report[
            "accepted_state"
        ]

        report[
            "proof_standard_status"
        ] = proof_report[
            "proof_standard_status"
        ]

        report[
            "proof_standard_v1"
        ] = proof_report

    return patched


def run_post_proof_argument(
    *,
    requirement_result: dict,
    contract_bundle: dict,
    proof_bundle: dict,
) -> dict | None:
    try:
        import argument_core_v1
    except Exception as exc:
        return {
            "status":
                "NOT_RUN",
            "reason":
                (
                    "argument_core_v1 import failed: "
                    + str(exc)
                ),
        }

    patched = (
        build_post_proof_requirement_result(
            requirement_result=
                requirement_result,
            proof_bundle=
                proof_bundle,
        )
    )

    result = (
        argument_core_v1.run_argument_substrate(
            requirement_result=
                patched,
            contract_bundle=
                contract_bundle,
        )
    )

    return {
        "status":
            "RUN",
        "accepted_argument_evaluation":
            result[
                "accepted_argument_evaluation"
            ],
        "raw_shadow_evaluation":
            result[
                "raw_shadow_evaluation"
            ],
        "internal_outcome":
            "UNKNOWN",
        "submission_label":
            None,
    }


# ============================================================================
# Label-free self tests
# ============================================================================


def _synthetic_row(
    *,
    relation: str,
    temporal: str | None = "IN_SCOPE",
    reliable: bool | None = True,
    explicit_adverse: bool = False,
    conditional: bool = False,
) -> dict:
    fact = {
        "fact_candidate_id":
            (
                "fc-attack"
                if relation
                == "ATTACK"
                else "fc-support"
            ),
        "source_id":
            "source-A",
        "polarity":
            (
                "ADVERSE"
                if explicit_adverse
                else "POSITIVE"
            ),
        "modality":
            "ACTUAL",
        "grounding_valid":
            True,
        "status":
            "SPAN_VALIDATED",
    }

    row = {
        "requirement_id":
            "ERX",
        "relation":
            relation,
        "argument_admission_channel":
            (
                "CONDITIONAL"
                if conditional
                else "DIRECT"
            ),
        "argument_truth_bearing":
            not conditional,
        "identity_decisive_proof_eligible":
            not conditional,
        "predicate_compatibility":
            "DIRECT",
        "fact_candidate":
            fact,
        "alignment_evidence_id":
            (
                "align-attack"
                if relation
                == "ATTACK"
                else "align-support"
            ),
        "evidence_id":
            "source-A:P1",
    }

    if temporal is not None:
        row[
            "temporal_match"
        ] = temporal

    if reliable is not None:
        row[
            "information_reliability"
        ] = {
            "status":
                (
                    "PASS"
                    if reliable
                    else "FAIL"
                )
        }

    return row


def _run_self_tests_v1() -> None:
    requirement = {
        "requirement_id":
            "ERX",
        "atom_id":
            "A1",
        "decisiveness":
            "DECISIVE",
    }

    # Support only, all gates pass.
    report = (
        evaluate_requirement_proof(
            requirement=
                requirement,
            rows=[
                _synthetic_row(
                    relation=
                        "SUPPORT"
                )
            ],
            coverage_pass=
                True,
        )
    )

    assert (
        report[
            "accepted_state"
        ]
        == "TRUE"
    )

    # Explicit adverse only, all gates pass.
    report = (
        evaluate_requirement_proof(
            requirement=
                requirement,
            rows=[
                _synthetic_row(
                    relation=
                        "ATTACK",
                    explicit_adverse=
                        True,
                )
            ],
            coverage_pass=
                True,
        )
    )

    assert (
        report[
            "accepted_state"
        ]
        == "FALSE"
    )

    # Both directions pass -> preserve BOTH.
    report = (
        evaluate_requirement_proof(
            requirement=
                requirement,
            rows=[
                _synthetic_row(
                    relation=
                        "SUPPORT"
                ),
                _synthetic_row(
                    relation=
                        "ATTACK",
                    explicit_adverse=
                        True,
                ),
            ],
            coverage_pass=
                True,
        )
    )

    assert (
        report[
            "accepted_state"
        ]
        == "BOTH"
    )

    # Coverage incomplete -> no proof promotion.
    report = (
        evaluate_requirement_proof(
            requirement=
                requirement,
            rows=[
                _synthetic_row(
                    relation=
                        "SUPPORT"
                )
            ],
            coverage_pass=
                False,
        )
    )

    assert (
        report[
            "accepted_state"
        ]
        == "UNKNOWN"
    )

    # Reliability missing -> UNKNOWN.
    report = (
        evaluate_requirement_proof(
            requirement=
                requirement,
            rows=[
                _synthetic_row(
                    relation=
                        "SUPPORT",
                    reliable=
                        None,
                )
            ],
            coverage_pass=
                True,
        )
    )

    assert (
        report[
            "accepted_state"
        ]
        == "UNKNOWN"
    )

    # Conditional identity cannot seed proof.
    report = (
        evaluate_requirement_proof(
            requirement=
                requirement,
            rows=[
                _synthetic_row(
                    relation=
                        "SUPPORT",
                    conditional=
                        True,
                )
            ],
            coverage_pass=
                True,
        )
    )

    assert (
        report[
            "accepted_state"
        ]
        == "UNKNOWN"
    )

    print(
        "proof_standard_v1 self-tests: PASS"
    )
    print(
        "  qualified SUPPORT        -> TRUE"
    )
    print(
        "  qualified ATTACK         -> FALSE"
    )
    print(
        "  qualified SUPPORT+ATTACK -> BOTH"
    )
    print(
        "  coverage incomplete      -> UNKNOWN"
    )
    print(
        "  reliability missing      -> UNKNOWN"
    )
    print(
        "  conditional only         -> UNKNOWN"
    )


# ============================================================================
# CLI
# ============================================================================



def run_self_tests() -> None:
    _run_self_tests_v1()

    # Production-freeze temporal contract.
    assert _row_temporal_status(
        {"temporal_relation": "IN_SCOPE"}
    )[0] == "PASS"

    assert _row_temporal_status(
        {"temporal_relation": "OUT_OF_SCOPE"}
    )[0] == "FAIL"

    assert _row_temporal_status(
        {"temporal_relation": "UNKNOWN"}
    )[0] == "UNRESOLVED"

    assert _row_temporal_status(
        {"temporal_relation": "OVERLAPS"}
    )[0] == "UNRESOLVED"

    assert _row_temporal_status(
        {
            "temporal_relation": "IN_SCOPE",
            "evidence_quality": {
                "temporal_fit": "OUT_OF_SCOPE",
            },
        }
    )[0] == "UNRESOLVED"

    print("proof_standard_v1_1 production-freeze temporal tests: PASS")
    print("  UNKNOWN != FAIL")
    print("  OVERLAPS != automatic PASS")
    print("  explicit PASS/FAIL conflict -> UNRESOLVED")


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
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if (
            args.requirement_result
            is None
            and args.contract
            is None
        ):
            return

    if (
        args.requirement_result
        is None
    ):
        parser.error(
            "--requirement-result is required "
            "unless only --self-test is used"
        )

    requirement_result = load_json(
        args.requirement_result
    )

    proof_bundle = (
        evaluate_proof_standard_bundle(
            requirement_result
        )
    )

    contract_bundle = None

    if (
        args.contract
        is not None
    ):
        contract_bundle = load_json(
            args.contract
        )

        proof_bundle[
            "post_proof_argument"
        ] = (
            run_post_proof_argument(
                requirement_result=
                    requirement_result,
                contract_bundle=
                    contract_bundle,
                proof_bundle=
                    proof_bundle,
            )
        )

    output = (
        args.output
        or args.requirement_result.with_name(
            args.requirement_result.stem
            + "_proof_standard_v1.json"
        )
    )

    save_json(
        proof_bundle,
        output,
    )

    print(
        "=" * 72
    )
    print(
        "FRECA TYPED PROOF STANDARD V1"
    )
    print(
        "=" * 72
    )

    for report in proof_bundle[
        "requirement_reports"
    ]:
        print()
        print(
            report[
                "requirement_id"
            ],
            "raw=",
            report[
                "raw_state"
            ],
            "accepted=",
            report[
                "accepted_state"
            ],
        )

        support = report[
            "support_proof"
        ]

        attack = report[
            "attack_proof"
        ]

        print(
            "  SUPPORT/AUDIT_SUFFICIENT:",
            (
                "PASS"
                if support[
                    "accepted_direction"
                ]
                else "NO"
            ),
            "failures=",
            support[
                "failure_codes"
            ],
        )

        print(
            "  ATTACK/EXPLICIT_VIOLATION:",
            (
                "PASS"
                if attack[
                    "accepted_direction"
                ]
                else "NO"
            ),
            "failures=",
            attack[
                "failure_codes"
            ],
        )

        print(
            "  contradiction:",
            report[
                "contradiction_state"
            ],
        )

    if (
        "post_proof_argument"
        in proof_bundle
    ):
        post = proof_bundle[
            "post_proof_argument"
        ]

        print()
        print(
            "Post-proof argument status:",
            post.get(
                "status"
            ),
        )

        if (
            post.get(
                "status"
            )
            == "RUN"
        ):
            print(
                "Raw argument shadow :",
                post[
                    "raw_shadow_evaluation"
                ][
                    "state"
                ],
                "(DIAGNOSTIC ONLY)",
            )

            print(
                "Accepted argument    :",
                post[
                    "accepted_argument_evaluation"
                ][
                    "state"
                ],
            )

    print()
    print(
        "Final outcome        : UNKNOWN"
    )
    print(
        "Saved               :",
        output,
    )


if __name__ == "__main__":
    main()
