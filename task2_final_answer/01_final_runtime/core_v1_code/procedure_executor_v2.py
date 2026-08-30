#!/usr/bin/env python3
"""Deterministic TARGETED_COMPLETE procedure executor for Production V2.

The executor describes and validates completed work; it never upgrades
coverage by assigning a boolean alone.  Its universe is explicitly limited to
the persisted candidate universe for one RetrievalNeed in the current task
package and is never described as exhaustive real-world coverage.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import coverage_v1


COVERAGE_PURPOSES = {
    "POSITIVE_EXISTENCE_PROOF",
    "EXPLICIT_ADVERSE_PROOF",
    "ABSENCE_BASED_INFERENCE",
    "CONTRADICTION_COUNTERCHECK",
    "NON_APPLICABILITY_COUNTERCHECK",
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


def _trace(requirement_result: dict, need_id: str) -> dict:
    matches = [
        row
        for row in requirement_result.get("retrieval_traces", [])
        if str(row.get("need_id")) == need_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one retrieval trace for {need_id}; found {len(matches)}"
        )
    return matches[0]


def _objective(procedure_plan: dict, need_id: str) -> dict:
    matches = [
        row
        for row in procedure_plan.get("audit_procedure_objectives", [])
        if need_id
        == str((row.get("core_extension") or {}).get("need_id") or "")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one ProcedureObjective for {need_id}; found {len(matches)}"
        )
    return matches[0]


def _channel_reports(trace: dict) -> list[dict]:
    by_name = {
        "RAW_LEXICAL": coverage_v1.lexical_channel(trace),
        "TYPED_FACT": coverage_v1.typed_channel(trace),
        "STRUCTURE": coverage_v1.structure_channel(trace),
    }
    expected = list(trace.get("expected_channels", []) or by_name)
    reports = []
    for name in expected:
        row = by_name.get(name)
        reports.append(
            {
                "channel": name,
                "present": row is not None,
                "executed": bool(row and row.get("executed")),
                "complete_for_candidate_discovery": bool(
                    row and row.get("complete_for_candidate_discovery")
                ),
                "source_report": row,
            }
        )
    return reports


def _exclusion_reasons(trace: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in trace.get("candidate_universe", []) or []:
        candidate_id = str(row.get("evidence_id") or "")
        decision = str(row.get("identity_use_decision") or "")
        reasons = list(row.get("identity_reason_codes", []) or [])
        if decision and decision not in {"ADMIT_DIRECT", "ADMIT_CONDITIONAL"}:
            out[candidate_id] = sorted(set([decision, *map(str, reasons)]))
    return dict(sorted(out.items()))


def execute_targeted_complete(
    *,
    requirement_result: dict,
    procedure_plan: dict,
    need_id: str,
    coverage_purpose: str,
    action_id: str,
    goal_id: str,
) -> dict:
    if coverage_purpose not in COVERAGE_PURPOSES:
        raise ValueError(f"Unsupported coverage purpose: {coverage_purpose}")

    trace = _trace(requirement_result, need_id)
    objective = _objective(procedure_plan, need_id)
    alignments = requirement_result.get("alignments", [])
    disposition = coverage_v1.candidate_disposition(
        trace=trace,
        alignments=alignments,
    )
    channel_reports = _channel_reports(trace)

    universe = trace.get("candidate_universe")
    universe_available = bool(
        trace.get("candidate_universe_persisted")
        and isinstance(universe, list)
    )
    universe_ids = sorted(
        str(row.get("evidence_id"))
        for row in (universe or [])
        if row.get("evidence_id")
    )
    required_channels = [row["channel"] for row in channel_reports]
    executed_channels = [
        row["channel"] for row in channel_reports if row["executed"]
    ]
    failed_channels = [
        row["channel"]
        for row in channel_reports
        if not row["executed"]
        or not row["complete_for_candidate_discovery"]
    ]
    parse_gaps = sorted(map(str, trace.get("parse_gap_ids", []) or []))
    readability_gaps = sorted(
        map(str, trace.get("readability_gap_ids", []) or [])
    )
    missing_tracks = sorted(
        map(str, trace.get("missing_required_track_types", []) or [])
    )
    unassessed = list(
        disposition.get("universe_unassessed_candidate_ids", []) or []
    )

    complete = bool(
        universe_available
        and required_channels
        and not failed_channels
        and not parse_gaps
        and not readability_gaps
        and not missing_tracks
        and not unassessed
    )

    reason_codes = []
    if not universe_available:
        reason_codes.append("CANDIDATE_UNIVERSE_UNAVAILABLE")
    if not required_channels:
        reason_codes.append("NO_REGISTERED_RETRIEVAL_CHANNELS")
    if failed_channels:
        reason_codes.append("REQUIRED_RETRIEVAL_CHANNEL_INCOMPLETE")
    if parse_gaps:
        reason_codes.append("MATERIAL_PARSE_GAP")
    if readability_gaps:
        reason_codes.append("MATERIAL_READABILITY_GAP")
    if missing_tracks:
        reason_codes.append("REQUIRED_TRACK_UNAVAILABLE")
    if unassessed:
        reason_codes.append("CANDIDATE_DISPOSITION_INCOMPLETE")

    population_frame_id = objective.get("population_frame_id")
    basis_ids = [
        str(objective.get("objective_id")),
        str(population_frame_id),
        str(trace.get("need_id")),
    ]
    basis_ids = [value for value in basis_ids if value and value != "None"]

    artifact = {
        "schema": "freca-targeted-coverage-procedure-v2",
        "procedure_executor_version": "TARGETED_COMPLETE_EXECUTOR_V2_1",
        "procedure_artifact_id": stable_id(
            "targeted-procedure",
            action_id,
            need_id,
            coverage_purpose,
            sha256_json(universe_ids),
        ),
        "action_id": action_id,
        "goal_id": goal_id,
        "action_type": "COMPLETE_TARGETED_COVERAGE",
        "coverage_purpose": coverage_purpose,
        "requirement_id": str(trace.get("requirement_id")),
        "proposition_id": str(
            objective.get("proposition_id")
            or f"prop-{str(trace.get('requirement_id')).lower()}"
        ),
        "need_id": need_id,
        "direction": str(trace.get("direction")),
        "population_frame_id": population_frame_id,
        "candidate_universe_identity": {
            "scope": "PERSISTED_GENERATED_CANDIDATES_FOR_CURRENT_TASK_PACKAGE_NEED",
            "real_world_exhaustiveness_claimed": False,
            "candidate_universe_count": len(universe_ids),
            "candidate_universe_sha256": sha256_json(universe_ids),
        },
        "required_retrieval_channels": required_channels,
        "executed_retrieval_channels": executed_channels,
        "channel_reports": channel_reports,
        "candidate_disposition_counts": {
            "universe": disposition.get("candidate_universe_count", 0),
            "assessed": disposition.get("universe_assessed_count", 0),
            "unassessed": len(unassessed),
            "excluded": len(
                disposition.get("universe_excluded_candidate_ids", []) or []
            ),
            "conditional": len(
                disposition.get("universe_conditional_candidate_ids", []) or []
            ),
        },
        "unassessed_candidate_ids": unassessed,
        "deterministic_exclusions_and_reasons": _exclusion_reasons(trace),
        "model_assessed_candidate_count": disposition.get(
            "universe_assessed_count", 0
        ),
        "model_assessed_candidate_ids": sorted(
            set(universe_ids) - set(unassessed)
        ),
        "parse_readability_gaps": sorted(set(parse_gaps + readability_gaps)),
        "identity_time_exclusions": sorted(
            set(disposition.get("universe_excluded_candidate_ids", []) or [])
        ),
        "counterevidence_scan_result": {
            "direction_scanned": str(trace.get("direction")),
            "registered_channels_executed": not failed_channels,
            "candidate_disposition_complete": not unassessed,
            "explicit_support_alignment_ids": sorted(
                str(row.get("alignment_evidence_id") or row.get("evidence_id"))
                for row in alignments
                if need_id in (row.get("retrieval_need_ids", []) or [])
                and row.get("relation") == "SUPPORT"
            ),
            "explicit_attack_alignment_ids": sorted(
                str(row.get("alignment_evidence_id") or row.get("evidence_id"))
                for row in alignments
                if need_id in (row.get("retrieval_need_ids", []) or [])
                and row.get("relation") == "ATTACK"
            ),
        },
        "completion_status": "COMPLETE" if complete else "INCOMPLETE",
        "completion_basis_ids": basis_ids if complete else [],
        "reason_codes": sorted(set(reason_codes)),
        "proof_state_modified": False,
        "final_label": None,
    }
    artifact["procedure_artifact_sha256"] = sha256_json(artifact)

    return {
        "schema": "freca-core-repair-action-execution-v2",
        "execution_id": stable_id("repair-exec-v2", action_id),
        "action_id": action_id,
        "goal_id": goal_id,
        "action_type": "COMPLETE_TARGETED_COVERAGE",
        "targeted_coverage_procedure_artifacts": [artifact],
        "signal_status": (
            "NEW_VALIDATED_SIGNAL" if complete else "NO_NEW_VALIDATED_SIGNAL"
        ),
        "action_execution_status": "EXECUTED",
        "upstream_artifacts_mutated": False,
        "proof_state_modified": False,
        "final_label": None,
    }


def validate_procedure_artifact(artifact: dict) -> tuple[bool, list[str]]:
    reasons = []
    required_fields = {
        "coverage_purpose",
        "requirement_id",
        "proposition_id",
        "candidate_universe_identity",
        "required_retrieval_channels",
        "executed_retrieval_channels",
        "candidate_disposition_counts",
        "deterministic_exclusions_and_reasons",
        "model_assessed_candidate_count",
        "parse_readability_gaps",
        "identity_time_exclusions",
        "counterevidence_scan_result",
        "completion_status",
        "completion_basis_ids",
        "procedure_artifact_sha256",
    }
    for field in sorted(required_fields - set(artifact)):
        reasons.append(f"MISSING_REQUIRED_FIELD:{field}")
    if artifact.get("schema") != "freca-targeted-coverage-procedure-v2":
        reasons.append("INVALID_SCHEMA")
    expected_hash = artifact.get("procedure_artifact_sha256")
    unsigned = dict(artifact)
    unsigned.pop("procedure_artifact_sha256", None)
    if expected_hash != sha256_json(unsigned):
        reasons.append("PROCEDURE_ARTIFACT_HASH_MISMATCH")
    if artifact.get("completion_status") == "COMPLETE":
        if not artifact.get("completion_basis_ids"):
            reasons.append("COMPLETE_WITHOUT_BASIS_IDS")
        identity = artifact.get("candidate_universe_identity") or {}
        if not identity.get("candidate_universe_sha256"):
            reasons.append("COMPLETE_WITHOUT_UNIVERSE_HASH")
        if identity.get("real_world_exhaustiveness_claimed") is True:
            reasons.append("FORBIDDEN_REAL_WORLD_EXHAUSTIVENESS_CLAIM")
        if artifact.get("parse_readability_gaps"):
            reasons.append("COMPLETE_WITH_MATERIAL_PARSE_GAP")
        if (artifact.get("candidate_disposition_counts") or {}).get(
            "unassessed", 0
        ):
            reasons.append("COMPLETE_WITH_UNASSESSED_CANDIDATES")
        required = set(artifact.get("required_retrieval_channels", []) or [])
        executed = set(artifact.get("executed_retrieval_channels", []) or [])
        if not required or not required.issubset(executed):
            reasons.append("COMPLETE_WITH_MISSING_CHANNEL")
        reports = artifact.get("channel_reports", []) or []
        report_by_channel = {
            str(row.get("channel")): row for row in reports if isinstance(row, dict)
        }
        if any(
            not report_by_channel.get(channel, {}).get(
                "complete_for_candidate_discovery", False
            )
            for channel in required
        ):
            reasons.append("COMPLETE_WITH_INCOMPLETE_CHANNEL_REPORT")
        countercheck = artifact.get("counterevidence_scan_result")
        if not isinstance(countercheck, dict):
            reasons.append("COMPLETE_WITHOUT_COUNTEREVIDENCE_SCAN")
        else:
            if countercheck.get("registered_channels_executed") is not True:
                reasons.append("COMPLETE_WITH_INCOMPLETE_COUNTEREVIDENCE_CHANNELS")
            if countercheck.get("candidate_disposition_complete") is not True:
                reasons.append("COMPLETE_WITH_INCOMPLETE_COUNTEREVIDENCE_DISPOSITION")
    return not reasons, reasons
