#!/usr/bin/env python3
"""FRECA Core Repair Feedback / EvaluationDiff v1.

This is the first frozen before-vs-after repair evaluator.

It performs:
  1. append-only merge of RepairRound artifacts into a NEW requirement bundle;
  2. recompute RequirementCoverage;
  3. feed explicit temporal/reliability artifacts into ProofStandard;
  4. rerun post-proof Argument evaluation;
  5. rebuild ProcedureObjective and OpenGoal ledger;
  6. compute deterministic Hard Gates + Effect Vector + EvaluationDiff.

It DOES NOT:
  - consume human / historical / consensus / previous-system 1/0/N/A;
  - compute accuracy or weak-reference agreement;
  - produce an overall weighted score;
  - overwrite any upstream artifact;
  - change or emit a final submission label.

Frozen repair-level evaluation policy:
  Hard Gates
    source grounding
    no semantic rewrite of old alignments
    no unsupported propagation
    conflict preservation
    no layer bypass
    no answer comparator
    no repeated executed action signature

  Effect Vector
    VerifiedSignalGain
    ResolvedDecisiveGoalCount
    ProofBlockerDelta
    CoverageDelta
    NewConflictCount
    CandidateDispositionGain
    Cost telemetry (only if actually persisted)
    SubstantiveChange
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


# ============================================================================
# Generic helpers
# ============================================================================


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


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(str(x) for x in parts)
    return (
        prefix
        + "-"
        + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def alignment_id(row: dict) -> str:
    return str(
        row.get("alignment_evidence_id")
        or row.get("alignment_id")
        or row.get("fact_candidate_id")
        or (
            str(row.get("evidence_id", ""))
            + "#"
            + str(
                (row.get("fact_candidate") or {}).get(
                    "fact_candidate_id",
                    "",
                )
            )
        )
    )


def norm_ws(value: Any) -> str:
    return " ".join(str(value or "").split())


# ============================================================================
# Round artifact extraction
# ============================================================================


def round_new_alignments(round_bundle: dict) -> list[dict]:
    rows = []
    seen = set()

    for execution in round_bundle.get("action_executions", []):
        for row in execution.get("new_alignments", []):
            aid = alignment_id(row)
            if not aid or aid in seen:
                continue
            seen.add(aid)
            rows.append(copy.deepcopy(row))

    return rows


def round_temporal_assessments(round_bundle: dict) -> list[dict]:
    rows = []
    for execution in round_bundle.get("action_executions", []):
        rows.extend(
            copy.deepcopy(
                execution.get("temporal_assessments", [])
            )
        )
    return rows


def round_reliability_assessments(round_bundle: dict) -> list[dict]:
    rows = []
    for execution in round_bundle.get("action_executions", []):
        rows.extend(
            copy.deepcopy(
                execution.get(
                    "information_reliability_assessments",
                    [],
                )
            )
        )
    return rows


# ============================================================================
# Append-only merge
# ============================================================================


CORE_ALIGNMENT_FIELDS = (
    "relation",
    "argument_admission_channel",
    "argument_truth_bearing",
    "argument_review_required",
    "accepted_for_argument",
    "accepted_for_proof",
    "identity_use_decision",
    "identity_decisive_proof_eligible",
    "predicate_compatibility",
    "typed_decisive_proof_eligible",
)


def build_alignment_lookup(rows: list[dict]) -> dict[str, dict]:
    lookup = {}
    for row in rows:
        aid = alignment_id(row)
        if aid:
            lookup[aid] = row

        fc = row.get("fact_candidate_id") or (
            row.get("fact_candidate") or {}
        ).get("fact_candidate_id")
        if fc:
            lookup[str(fc)] = row

    return lookup



def round_retrieval_trace_updates(
    round_bundle: dict,
) -> list[dict]:
    """Collect restricted, validated channel-completion trace deltas."""

    rows = []
    seen = set()

    sources = [
        round_bundle.get("retrieval_trace_updates", [])
    ]

    for execution in round_bundle.get("action_executions", []):
        sources.append(
            execution.get("retrieval_trace_updates", [])
        )

    for source in sources:
        for row in source or []:
            update_sha = str(
                row.get("update_sha256")
                or sha256_json(row)
            )

            if update_sha in seen:
                continue

            seen.add(update_sha)
            rows.append(copy.deepcopy(row))

    return rows


def apply_retrieval_trace_updates(
    *,
    traces: list[dict],
    updates: list[dict],
) -> tuple[list[dict], dict]:
    """Apply only an allow-listed channel-completion delta.

    Existing query / lexical / typed semantics are immutable.
    """

    out = copy.deepcopy(traces)

    by_need = {
        str(row.get("need_id")): row
        for row in out
    }

    applied = []
    rejected = []

    for update in updates:
        need_id = str(update.get("need_id", ""))

        if need_id not in by_need:
            rejected.append(
                {
                    "need_id": need_id,
                    "reason": "UNKNOWN_NEED_ID",
                }
            )
            continue

        if (
            update.get("update_type")
            != "EXECUTE_MISSING_CHANNEL"
            or update.get("channel")
            != "STRUCTURE"
        ):
            rejected.append(
                {
                    "need_id": need_id,
                    "reason": "UNSUPPORTED_TRACE_UPDATE_TYPE",
                }
            )
            continue

        trace = by_need[need_id]

        expected_base = update.get(
            "base_trace_sha256"
        )

        if (
            expected_base
            and expected_base
            != sha256_json(trace)
        ):
            rejected.append(
                {
                    "need_id": need_id,
                    "reason": "BASE_TRACE_HASH_MISMATCH",
                }
            )
            continue

        universe = trace.get(
            "candidate_universe"
        )

        if not isinstance(universe, list):
            universe = copy.deepcopy(
                trace.get("candidates", [])
            )

        by_id = {
            str(
                row.get("evidence_id")
                or row.get("id")
            ): row
            for row in universe
            if (
                row.get("evidence_id")
                or row.get("id")
            )
        }

        additions = update.get(
            "candidate_universe_additions",
            [],
        )

        appended = []

        for candidate in additions:
            evidence_id = str(
                candidate.get("evidence_id")
                or candidate.get("id")
                or ""
            )

            if not evidence_id:
                rejected.append(
                    {
                        "need_id": need_id,
                        "reason": "ADDITION_MISSING_EVIDENCE_ID",
                    }
                )
                continue

            if evidence_id in by_id:
                continue

            row = copy.deepcopy(candidate)
            row["evidence_id"] = evidence_id

            universe.append(row)
            by_id[evidence_id] = row
            appended.append(evidence_id)

        trace["candidate_universe"] = universe
        trace["candidate_universe_ids"] = [
            str(
                row.get("evidence_id")
                or row.get("id")
            )
            for row in universe
            if (
                row.get("evidence_id")
                or row.get("id")
            )
        ]
        trace["candidate_universe_count"] = len(
            trace["candidate_universe_ids"]
        )
        trace["candidate_universe_persisted"] = True

        # The scanner actually traversed every current Core evidence record.
        trace["structure_full_scan"] = bool(
            update.get("structure_full_scan")
        )
        trace["structure_scan_complete"] = bool(
            update.get("structure_scan_complete")
        )
        trace["structure_scan"] = copy.deepcopy(
            update.get("structure_scan", {})
        )

        applied.append(
            {
                "need_id": need_id,
                "update_sha256":
                    update.get("update_sha256"),
                "appended_candidate_ids":
                    appended,
                "appended_candidate_count":
                    len(appended),
            }
        )

    return out, {
        "applied_trace_updates":
            applied,
        "rejected_trace_updates":
            rejected,
    }



def merge_round_into_requirement_result(
    requirement_result: dict,
    round_bundle: dict,
) -> tuple[dict, dict]:
    merged = copy.deepcopy(requirement_result)

    # ------------------------------------------------------------------
    # 1. Restricted retrieval-trace updates first.
    # ------------------------------------------------------------------
    trace_updates = round_retrieval_trace_updates(
        round_bundle
    )

    (
        merged_traces,
        trace_update_diag,
    ) = apply_retrieval_trace_updates(
        traces=merged.get(
            "retrieval_traces",
            [],
        ),
        updates=trace_updates,
    )

    merged["retrieval_traces"] = merged_traces

    # ------------------------------------------------------------------
    # 2. Append-only alignment merge.
    # ------------------------------------------------------------------
    old_rows = merged.get("alignments", [])
    old_by_id = {
        alignment_id(row): row
        for row in old_rows
        if alignment_id(row)
    }

    new_rows = round_new_alignments(round_bundle)

    duplicate_new_alignment_ids = []
    appended_alignment_ids = []

    for row in new_rows:
        aid = alignment_id(row)

        if aid in old_by_id:
            duplicate_new_alignment_ids.append(aid)
            continue

        old_rows.append(row)
        old_by_id[aid] = row
        appended_alignment_ids.append(aid)

    merged["alignments"] = old_rows

    lookup = build_alignment_lookup(old_rows)

    # ------------------------------------------------------------------
    # 3. Typed repair assessments.
    # ------------------------------------------------------------------
    attached_temporal = []
    unresolved_temporal_targets = []

    for assessment in round_temporal_assessments(round_bundle):
        target = str(assessment.get("target_artifact_id", ""))
        row = lookup.get(target)

        if row is None:
            unresolved_temporal_targets.append(target)
            continue

        row["temporal_assessment"] = copy.deepcopy(assessment)
        row["temporal_relation"] = assessment.get(
            "temporal_relation",
            "UNKNOWN",
        )
        attached_temporal.append(
            str(assessment.get("assessment_id"))
        )

    attached_reliability = []
    unresolved_reliability_targets = []

    for assessment in round_reliability_assessments(round_bundle):
        target = str(assessment.get("target_artifact_id", ""))
        row = lookup.get(target)

        if row is None:
            unresolved_reliability_targets.append(target)
            continue

        row["information_reliability"] = copy.deepcopy(assessment)
        attached_reliability.append(
            str(assessment.get("assessment_id"))
        )

    merged["schema"] = (
        "freca-core-requirement-reasoning-v2-repair-merged-v1.1"
    )

    merged["repair_merge"] = {
        "parent_requirement_result_sha256":
            sha256_json(requirement_result),

        "repair_round_bundle_id":
            round_bundle.get("round_artifact_bundle_id"),

        "repair_round_sha256":
            round_bundle.get("bundle_sha256"),

        "appended_alignment_ids":
            appended_alignment_ids,

        "attached_temporal_assessment_ids":
            attached_temporal,

        "attached_reliability_assessment_ids":
            attached_reliability,

        "duplicate_new_alignment_ids":
            duplicate_new_alignment_ids,

        "unresolved_temporal_target_ids":
            unresolved_temporal_targets,

        "unresolved_reliability_target_ids":
            unresolved_reliability_targets,

        "retrieval_trace_update_diagnostics":
            trace_update_diag,

        "upstream_artifacts_overwritten":
            False,
    }

    merged["repair_merge"]["merge_sha256"] = sha256_json(
        merged["repair_merge"]
    )

    diagnostics = {
        "appended_alignment_ids":
            appended_alignment_ids,

        "attached_temporal_assessment_ids":
            attached_temporal,

        "attached_reliability_assessment_ids":
            attached_reliability,

        "duplicate_new_alignment_ids":
            duplicate_new_alignment_ids,

        "unresolved_temporal_target_ids":
            unresolved_temporal_targets,

        "unresolved_reliability_target_ids":
            unresolved_reliability_targets,

        "retrieval_trace_update_diagnostics":
            trace_update_diag,
    }

    return merged, diagnostics



def inject_coverage_gate(
    requirement_result: dict,
    coverage: dict,
) -> dict:
    """Install typed coverage result into NEW bundle only.

    ProofStandard v1 currently consumes coverage from requirement_result.proof_gate.
    This adapter bridges the explicit Coverage v1.1 artifact without changing
    ProofStandard semantics.
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


# ============================================================================
# Rerun affected Layer 7 components
# ============================================================================


def rerun_layer7(
    *,
    merged_requirement_result: dict,
    contract_bundle: dict,
) -> dict:
    import coverage_v1
    import proof_standard_v1
    import procedure_objective_v1
    import open_goal_v1

    coverage = coverage_v1.evaluate_coverage_bundle(
        merged_requirement_result
    )

    requirement_for_proof = inject_coverage_gate(
        merged_requirement_result,
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

    return {
        "requirement_result": requirement_for_proof,
        "coverage": coverage,
        "proof_standard": proof,
        "procedure_plan": procedure,
        "open_goals": open_goals,
    }


# ============================================================================
# Comparison helpers
# ============================================================================


def proof_reports(proof: dict) -> dict[str, dict]:
    return {
        str(row["requirement_id"]): row
        for row in proof.get("requirement_reports", [])
    }


def directional_blockers(proof: dict) -> dict[str, set[str]]:
    out = {}

    for rid, row in proof_reports(proof).items():
        for direction, field in (
            ("SUPPORT", "support_proof"),
            ("ATTACK", "attack_proof"),
        ):
            report = row.get(field) or {}
            out[
                f"{rid}.{direction.lower()}"
            ] = set(
                str(x)
                for x in report.get("failure_codes", [])
            )

    return out


def statement_states(proof: dict) -> dict[str, str]:
    return {
        str(row.get("statement_id")):
            str(row.get("accepted_state", "UNKNOWN"))
        for row in proof.get("requirement_reports", [])
        if row.get("statement_id")
    }


def raw_statement_states(proof: dict) -> dict[str, str]:
    return {
        str(row.get("statement_id")):
            str(row.get("raw_state", "UNKNOWN"))
        for row in proof.get("requirement_reports", [])
        if row.get("statement_id")
    }


def argument_standings(proof: dict) -> dict[str, str]:
    post = proof.get("post_proof_argument") or {}
    accepted = post.get("accepted_argument_evaluation") or {}

    return {
        str(row.get("argument_id")):
            str(row.get("standing", "UNDECIDED"))
        for row in accepted.get("argument_instances", [])
        if row.get("argument_id")
    }


def internal_outcomes(proof: dict) -> list[str]:
    values = []

    for value in (
        proof.get("internal_outcome"),
        (proof.get("post_proof_argument") or {}).get(
            "internal_outcome"
        ),
    ):
        if value and value not in values:
            values.append(str(value))

    return values or ["UNKNOWN"]


def coverage_by_need(coverage: dict) -> dict[str, dict]:
    return {
        str(row["need_id"]): row
        for row in coverage.get("need_reports", [])
    }


def semantic_goal_key(goal: dict) -> tuple[str, str, str, str]:
    ext = goal.get("core_extension") or {}

    return (
        str(goal.get("goal_type", "")),
        str(goal.get("target_statement_id", "")),
        str(ext.get("direction", "")),
        str(ext.get("goal_origin", "")),
    )


def goal_semantic_map(ledger: dict) -> dict[tuple, dict]:
    out = {}

    for goal in ledger.get("goals", []):
        key = semantic_goal_key(goal)

        # If duplicate semantic keys ever occur, keep all IDs in diagnostics
        # rather than silently treating one as resolved.
        if key not in out:
            out[key] = goal

    return out


def changed_map(
    before: dict[str, str],
    after: dict[str, str],
) -> dict[str, list[str]]:
    out = {}

    for key in sorted(set(before) | set(after)):
        b = before.get(key)
        a = after.get(key)

        if b != a:
            out[key] = [b, a]

    return out


def collect_new_conflicts(
    proof_before: dict,
    proof_after: dict,
) -> list[str]:
    before = proof_reports(proof_before)
    after = proof_reports(proof_after)

    conflicts = []

    for rid, row_after in after.items():
        row_before = before.get(rid, {})

        before_conflict = (
            row_before.get("raw_state") == "BOTH"
            or row_before.get("contradiction_state") == "PRESERVED"
        )

        after_conflict = (
            row_after.get("raw_state") == "BOTH"
            or row_after.get("contradiction_state") == "PRESERVED"
        )

        if after_conflict and not before_conflict:
            conflicts.append(
                str(row_after.get("statement_id") or f"stmt-{rid.lower()}")
            )

    before_args = argument_standings(proof_before)
    after_args = argument_standings(proof_after)

    for aid, standing in after_args.items():
        if (
            standing == "CONFLICTED"
            and before_args.get(aid) != "CONFLICTED"
        ):
            conflicts.append(aid)

    return sorted(set(conflicts))


# ============================================================================
# Hard Gates
# ============================================================================


def source_grounding_violations(new_alignments: list[dict]) -> list[str]:
    violations = []

    for row in new_alignments:
        relation = str(row.get("relation", ""))
        channel = str(
            row.get("argument_admission_channel", "")
        )

        # Only semantic / argument-visible alignments can influence reasoning.
        if (
            relation not in {"SUPPORT", "ATTACK"}
            and channel not in {"DIRECT", "CONDITIONAL"}
        ):
            continue

        aid = alignment_id(row)
        fact = row.get("fact_candidate") or {}
        fact_quote = norm_ws(fact.get("quote"))
        exact_quote = norm_ws(row.get("exact_quote"))

        if not row.get("evidence_id"):
            violations.append(
                f"{aid}:SOURCE_LOCATOR_MISSING"
            )

        if not row.get("fact_candidate_id") and not fact.get(
            "fact_candidate_id"
        ):
            violations.append(
                f"{aid}:FACT_CANDIDATE_ID_MISSING"
            )

        if not fact_quote:
            violations.append(
                f"{aid}:FACT_CANDIDATE_QUOTE_MISSING"
            )

        if not exact_quote:
            violations.append(
                f"{aid}:EXACT_QUOTE_MISSING"
            )
        elif fact_quote and exact_quote not in fact_quote:
            violations.append(
                f"{aid}:EXACT_QUOTE_NOT_IN_FACT_CANDIDATE"
            )

    return violations


def semantic_rewrite_violations(
    before_rr: dict,
    after_rr: dict,
) -> list[str]:
    before = {
        alignment_id(row): row
        for row in before_rr.get("alignments", [])
        if alignment_id(row)
    }

    after = {
        alignment_id(row): row
        for row in after_rr.get("alignments", [])
        if alignment_id(row)
    }

    violations = []

    for aid, old in before.items():
        new = after.get(aid)

        if new is None:
            violations.append(
                f"{aid}:OLD_ALIGNMENT_REMOVED"
            )
            continue

        for field in CORE_ALIGNMENT_FIELDS:
            if old.get(field) != new.get(field):
                violations.append(
                    f"{aid}:{field}:CHANGED"
                )

        old_fact = old.get("fact_candidate") or {}
        new_fact = new.get("fact_candidate") or {}

        if norm_ws(old_fact.get("quote")) != norm_ws(
            new_fact.get("quote")
        ):
            violations.append(
                f"{aid}:FACT_QUOTE_CHANGED"
            )

    return violations


def conflict_suppression_violations(
    proof_before: dict,
    proof_after: dict,
) -> list[str]:
    before = proof_reports(proof_before)
    after = proof_reports(proof_after)

    violations = []

    for rid, old in before.items():
        new = after.get(rid, {})

        old_conflict = (
            old.get("raw_state") == "BOTH"
            or old.get("contradiction_state") == "PRESERVED"
        )

        new_conflict = (
            new.get("raw_state") == "BOTH"
            or new.get("contradiction_state") == "PRESERVED"
        )

        if old_conflict and not new_conflict:
            violations.append(
                f"{rid}:PREEXISTING_CONFLICT_SUPPRESSED"
            )

    return violations


def affected_requirement_ids(
    *,
    round_bundle: dict,
    requirement_result_before: dict,
) -> set[str]:
    affected = set()

    old_lookup = build_alignment_lookup(
        requirement_result_before.get("alignments", [])
    )

    for row in round_new_alignments(round_bundle):
        rid = row.get("requirement_id")
        if rid:
            affected.add(str(rid))

    for assessment in (
        round_temporal_assessments(round_bundle)
        + round_reliability_assessments(round_bundle)
    ):
        target = str(assessment.get("target_artifact_id", ""))
        old = old_lookup.get(target)
        if old and old.get("requirement_id"):
            affected.add(str(old["requirement_id"]))

    return affected


def unsupported_propagation_violations(
    *,
    proof_before: dict,
    proof_after: dict,
    affected_rids: set[str],
) -> list[str]:
    before = proof_reports(proof_before)
    after = proof_reports(proof_after)

    violations = []

    for rid in sorted(set(before) | set(after)):
        old_state = (before.get(rid) or {}).get("accepted_state")
        new_state = (after.get(rid) or {}).get("accepted_state")

        if (
            old_state != new_state
            and rid not in affected_rids
        ):
            violations.append(
                f"{rid}:STATEMENT_CHANGED_WITHOUT_AFFECTED_ARTIFACT_PATH"
            )

    return violations


def layer_bypass_violations(
    *,
    round_bundle: dict,
    proof_after: dict,
) -> list[str]:
    violations = []

    if round_bundle.get("proof_state_modified") is True:
        violations.append(
            "ROUND_BUNDLE_DIRECTLY_MODIFIED_PROOF"
        )

    if round_bundle.get("final_label") is not None:
        violations.append(
            "ROUND_BUNDLE_EMITTED_FINAL_LABEL"
        )

    for execution in round_bundle.get("action_executions", []):
        action_id = execution.get("action_id")

        if execution.get("proof_state_modified") is True:
            violations.append(
                f"{action_id}:ACTION_DIRECTLY_MODIFIED_PROOF"
            )

        if execution.get("final_label") is not None:
            violations.append(
                f"{action_id}:ACTION_EMITTED_FINAL_LABEL"
            )

    if proof_after.get("submission_label") is not None:
        violations.append(
            "PROOF_STANDARD_EMITTED_SUBMISSION_LABEL"
        )

    post = proof_after.get("post_proof_argument") or {}

    if post.get("submission_label") is not None:
        violations.append(
            "ARGUMENT_EMITTED_SUBMISSION_LABEL"
        )

    return violations


def repeated_action_signature_violations(
    round_bundle: dict,
) -> list[str]:
    signatures = {}
    action_ids = set()
    violations = []

    for execution in round_bundle.get("action_executions", []):
        action_id = str(execution.get("action_id", ""))

        if action_id in action_ids:
            violations.append(
                f"{action_id}:DUPLICATE_EXECUTION_ACTION_ID"
            )

        action_ids.add(action_id)

        signature = execution.get("action_signature")

        if not signature:
            # Preserve older execution artifact compatibility, but a missing
            # signature is separately visible and cannot prove duplication.
            continue

        signature = str(signature)

        if signature in signatures:
            violations.append(
                (
                    f"{action_id}:REPEATED_ACTION_SIGNATURE_WITH_"
                    f"{signatures[signature]}"
                )
            )
        else:
            signatures[signature] = action_id

    return violations


def evaluate_hard_gates(
    *,
    requirement_result_before: dict,
    requirement_result_after: dict,
    proof_before: dict,
    proof_after: dict,
    round_bundle: dict,
) -> dict:
    new_alignments = round_new_alignments(round_bundle)

    affected = affected_requirement_ids(
        round_bundle=round_bundle,
        requirement_result_before=requirement_result_before,
    )

    gate_violations = {
        "source_grounding":
            source_grounding_violations(new_alignments),

        "illegal_semantic_rewrite":
            semantic_rewrite_violations(
                requirement_result_before,
                requirement_result_after,
            ),

        "unsupported_propagation":
            unsupported_propagation_violations(
                proof_before=proof_before,
                proof_after=proof_after,
                affected_rids=affected,
            ),

        "conflict_suppression":
            conflict_suppression_violations(
                proof_before,
                proof_after,
            ),

        "layer_bypass":
            layer_bypass_violations(
                round_bundle=round_bundle,
                proof_after=proof_after,
            ),

        # This evaluator exposes no comparator input argument.  Keep this
        # explicit so production manifests can assert the isolation property.
        "answer_comparator_usage":
            [],

        "repeated_action_signature":
            repeated_action_signature_violations(
                round_bundle
            ),
    }

    gates = {
        name: {
            "pass": len(violations) == 0,
            "violation_count": len(violations),
            "violations": violations,
        }
        for name, violations in gate_violations.items()
    }

    return {
        "all_hard_gates_pass":
            all(row["pass"] for row in gates.values()),
        "gates":
            gates,
        "affected_requirement_ids":
            sorted(affected),
        "answer_comparator_inputs":
            [],
    }


# ============================================================================
# Effect Vector
# ============================================================================



def verified_signal_gain(round_bundle: dict) -> dict:
    truth_bearing = []
    goal_aligned_truth_bearing = []
    off_goal_truth_bearing = []
    conditional_semantic = []
    resolved_temporal = []
    decisive_reliability = []

    for execution in round_bundle.get("action_executions", []):
        need_id = str(
            execution.get("need_id")
            or execution.get("query_plan_id")
            or ""
        )

        expected_direction = execution.get(
            "goal_direction"
        )

        if not expected_direction:
            if need_id.endswith(".attack"):
                expected_direction = "ATTACK"
            elif need_id.endswith(".support"):
                expected_direction = "SUPPORT"

        for row in execution.get("new_alignments", []):
            aid = alignment_id(row)
            relation = row.get("relation")
            channel = row.get("argument_admission_channel")

            if relation not in {"SUPPORT", "ATTACK"}:
                continue

            if (
                channel == "DIRECT"
                and row.get("argument_truth_bearing") is True
            ):
                truth_bearing.append(aid)

                if (
                    expected_direction
                    and relation == expected_direction
                ):
                    goal_aligned_truth_bearing.append(aid)
                else:
                    off_goal_truth_bearing.append(aid)

            elif channel == "CONDITIONAL":
                conditional_semantic.append(aid)

        for row in execution.get("temporal_assessments", []):
            if (
                row.get("status") == "RESOLVED"
                and row.get("temporal_relation")
                in {"IN_SCOPE", "OUT_OF_SCOPE", "OVERLAPS"}
            ):
                assessment_id = str(
                    row.get("assessment_id")
                )

                resolved_temporal.append(
                    assessment_id
                )

                # A resolved typed assessment directly addresses its own
                # directional OpenGoal; count it as goal aligned.
                goal_aligned_truth_bearing.append(
                    assessment_id
                )

        for row in execution.get(
            "information_reliability_assessments",
            [],
        ):
            if str(row.get("status", "")).upper() in {
                "PASS",
                "FAIL",
            }:
                assessment_id = str(
                    row.get("assessment_id")
                )

                decisive_reliability.append(
                    assessment_id
                )

                goal_aligned_truth_bearing.append(
                    assessment_id
                )

    truth_bearing = sorted(set(truth_bearing))
    goal_aligned_truth_bearing = sorted(
        set(goal_aligned_truth_bearing)
    )
    off_goal_truth_bearing = sorted(
        set(off_goal_truth_bearing)
    )
    conditional_semantic = sorted(set(conditional_semantic))
    resolved_temporal = sorted(set(resolved_temporal))
    decisive_reliability = sorted(
        set(decisive_reliability)
    )

    verified_count = (
        len(truth_bearing)
        + len(resolved_temporal)
        + len(decisive_reliability)
    )

    return {
        "truth_bearing_alignment_ids":
            truth_bearing,

        "truth_bearing_alignment_count":
            len(truth_bearing),

        "goal_aligned_verified_signal_ids":
            goal_aligned_truth_bearing,

        "goal_aligned_verified_signal_count":
            len(goal_aligned_truth_bearing),

        "off_goal_verified_signal_ids":
            off_goal_truth_bearing,

        "off_goal_verified_signal_count":
            len(off_goal_truth_bearing),

        "conditional_semantic_alignment_ids":
            conditional_semantic,

        "conditional_semantic_alignment_count":
            len(conditional_semantic),

        "resolved_temporal_assessment_ids":
            resolved_temporal,

        "resolved_temporal_assessment_count":
            len(resolved_temporal),

        "decisive_reliability_assessment_ids":
            decisive_reliability,

        "decisive_reliability_assessment_count":
            len(decisive_reliability),

        "verified_signal_gain_count":
            verified_count,

        "validated_non_truth_bearing_signal_count":
            len(conditional_semantic),
    }



def proof_blocker_delta(
    before: dict,
    after: dict,
) -> dict:
    b = directional_blockers(before)
    a = directional_blockers(after)

    rows = {}
    total_before = 0
    total_after = 0

    for key in sorted(set(b) | set(a)):
        before_codes = b.get(key, set())
        after_codes = a.get(key, set())

        total_before += len(before_codes)
        total_after += len(after_codes)

        rows[key] = {
            "before":
                sorted(before_codes),
            "after":
                sorted(after_codes),
            "removed":
                sorted(before_codes - after_codes),
            "added":
                sorted(after_codes - before_codes),
            "net_count_delta":
                len(before_codes) - len(after_codes),
        }

    return {
        "by_direction":
            rows,
        "before_total":
            total_before,
        "after_total":
            total_after,
        # Positive means fewer blockers after repair.
        "net_blocker_reduction":
            total_before - total_after,
    }


def coverage_delta(
    before: dict,
    after: dict,
) -> dict:
    b = coverage_by_need(before)
    a = coverage_by_need(after)

    rows = {}
    disposition_gain = 0
    changed_ids = []

    for need_id in sorted(set(b) | set(a)):
        br = b.get(need_id, {})
        ar = a.get(need_id, {})

        b_un = len(br.get("unassessed_candidate_ids", []))
        a_un = len(ar.get("unassessed_candidate_ids", []))

        gain = b_un - a_un
        disposition_gain += max(0, gain)

        changed = any(
            [
                br.get("status") != ar.get("status"),
                br.get("required_level") != ar.get("required_level"),
                br.get("achieved_level") != ar.get("achieved_level"),
                br.get("proof_coverage_pass")
                    != ar.get("proof_coverage_pass"),
                b_un != a_un,
                br.get("candidate_count")
                    != ar.get("candidate_count"),
            ]
        )

        if changed:
            changed_ids.append(
                str(
                    ar.get("coverage_id")
                    or br.get("coverage_id")
                    or need_id
                )
            )

        rows[need_id] = {
            "before_status":
                br.get("status"),
            "after_status":
                ar.get("status"),
            "before_required_level":
                br.get("required_level"),
            "after_required_level":
                ar.get("required_level"),
            "before_achieved_level":
                br.get("achieved_level"),
            "after_achieved_level":
                ar.get("achieved_level"),
            "before_unassessed":
                b_un,
            "after_unassessed":
                a_un,
            "candidate_disposition_gain":
                gain,
            "before_proof_coverage_pass":
                br.get("proof_coverage_pass"),
            "after_proof_coverage_pass":
                ar.get("proof_coverage_pass"),
        }

    return {
        "by_need":
            rows,
        "candidate_disposition_gain":
            disposition_gain,
        "changed_coverage_ids":
            sorted(set(changed_ids)),
        "before_proof_coverage_complete":
            bool(
                before.get(
                    "proof_coverage_complete",
                    before.get("coverage_complete", False),
                )
            ),
        "after_proof_coverage_complete":
            bool(
                after.get(
                    "proof_coverage_complete",
                    after.get("coverage_complete", False),
                )
            ),
    }


def goal_delta(
    before: dict,
    after: dict,
) -> dict:
    b = goal_semantic_map(before)
    a = goal_semantic_map(after)

    resolved_keys = sorted(set(b) - set(a))
    new_keys = sorted(set(a) - set(b))
    persistent_keys = sorted(set(a) & set(b))

    resolved_ids = [
        str(b[key]["goal_id"])
        for key in resolved_keys
    ]

    new_ids = [
        str(a[key]["goal_id"])
        for key in new_keys
    ]

    rekeyed = []

    for key in persistent_keys:
        before_id = str(b[key].get("goal_id"))
        after_id = str(a[key].get("goal_id"))

        if before_id != after_id:
            rekeyed.append(
                {
                    "semantic_goal_key": list(key),
                    "before_goal_id": before_id,
                    "after_goal_id": after_id,
                    "reason":
                        (
                            "ID changed while semantic goal persisted; "
                            "not counted as resolved/new."
                        ),
                }
            )

    resolved_decisive = [
        str(b[key]["goal_id"])
        for key in resolved_keys
        if b[key].get("estimated_verdict_impact") == "DECISIVE"
    ]

    return {
        "resolved_goal_ids":
            sorted(resolved_ids),
        "new_goal_ids":
            sorted(new_ids),
        "resolved_decisive_goal_ids":
            sorted(resolved_decisive),
        "resolved_decisive_goal_count":
            len(resolved_decisive),
        "persistent_semantic_goal_count":
            len(persistent_keys),
        "goal_id_rekeys":
            rekeyed,
    }


def cost_vector(round_bundle: dict) -> dict:
    executions = round_bundle.get("action_executions", [])

    return {
        "executed_action_count":
            len(executions),

        "alignment_target_parent_count":
            sum(
                len(execution.get("target_artifact_ids", []))
                for execution in executions
                if execution.get("action_type")
                == "ALIGN_NEXT_CANDIDATE_BATCH"
            ),

        "model_calls":
            None,
        "prompt_tokens":
            None,
        "completion_tokens":
            None,
        "total_tokens":
            None,
        "wall_time_ms":
            None,

        "telemetry_status":
            "NOT_PERSISTED_BY_REPAIR_EXECUTOR_V1",

        "interpretation":
            (
                "Cost efficiency cannot yet be ranked quantitatively from "
                "the artifact alone. Console logs are not treated as a "
                "production metric source."
            ),
    }


# ============================================================================
# EvaluationDiff
# ============================================================================


def component_bundle_id(
    *,
    requirement_result: dict,
    coverage: dict,
    proof: dict,
    open_goals: dict,
) -> str:
    return stable_id(
        "evalbundle",
        sha256_json(requirement_result),
        sha256_json(coverage),
        sha256_json(proof),
        sha256_json(open_goals),
    )


def build_evaluation_diff(
    *,
    before_rr: dict,
    after_rr: dict,
    coverage_before: dict,
    coverage_after: dict,
    proof_before: dict,
    proof_after: dict,
    open_goals_before: dict,
    open_goals_after: dict,
    round_bundle: dict,
    hard_gates: dict,
) -> dict:
    before_bundle_id = component_bundle_id(
        requirement_result=before_rr,
        coverage=coverage_before,
        proof=proof_before,
        open_goals=open_goals_before,
    )

    after_bundle_id = component_bundle_id(
        requirement_result=after_rr,
        coverage=coverage_after,
        proof=proof_after,
        open_goals=open_goals_after,
    )

    new_alignments = round_new_alignments(round_bundle)

    before_fact_ids = {
        str(
            row.get("fact_candidate_id")
            or (row.get("fact_candidate") or {}).get(
                "fact_candidate_id"
            )
        )
        for row in before_rr.get("alignments", [])
        if (
            row.get("fact_candidate_id")
            or (row.get("fact_candidate") or {}).get(
                "fact_candidate_id"
            )
        )
    }

    after_fact_ids = {
        str(
            row.get("fact_candidate_id")
            or (row.get("fact_candidate") or {}).get(
                "fact_candidate_id"
            )
        )
        for row in after_rr.get("alignments", [])
        if (
            row.get("fact_candidate_id")
            or (row.get("fact_candidate") or {}).get(
                "fact_candidate_id"
            )
        )
    }

    statement_change = changed_map(
        statement_states(proof_before),
        statement_states(proof_after),
    )

    raw_statement_change = changed_map(
        raw_statement_states(proof_before),
        raw_statement_states(proof_after),
    )

    argument_change = changed_map(
        argument_standings(proof_before),
        argument_standings(proof_after),
    )

    coverage_change = coverage_delta(
        coverage_before,
        coverage_after,
    )

    goals = goal_delta(
        open_goals_before,
        open_goals_after,
    )

    blockers = proof_blocker_delta(
        proof_before,
        proof_after,
    )

    signals = verified_signal_gain(
        round_bundle
    )

    new_conflicts = collect_new_conflicts(
        proof_before,
        proof_after,
    )

    cost = cost_vector(round_bundle)

    changed_use_decision_ids = []

    # Current round does not execute a UseDecision-changing action.
    # Keep the field explicit for frozen D8.11 compatibility.

    substantive_change = bool(
        hard_gates["all_hard_gates_pass"]
        and any(
            [
                signals["verified_signal_gain_count"] > 0,
                coverage_change["candidate_disposition_gain"] > 0,
                goals["resolved_decisive_goal_count"] > 0,
                bool(statement_change),
                bool(raw_statement_change),
                bool(argument_change),
                bool(coverage_change["changed_coverage_ids"]),
                bool(new_conflicts),
                bool(changed_use_decision_ids),
            ]
        )
    )

    new_evidence_ids = []
    # No raw evidence item was added by this RepairPlan.  New alignments are
    # not relabeled as new Evidence IDs.

    diff = {
        "schema":
            "freca-core-evaluation-diff-v1.1",

        "diff_id":
            stable_id(
                "eval-diff",
                before_bundle_id,
                after_bundle_id,
            ),

        "before_bundle_id":
            before_bundle_id,
        "after_bundle_id":
            after_bundle_id,

        "new_evidence_ids":
            new_evidence_ids,

        "new_fact_candidate_ids":
            sorted(after_fact_ids - before_fact_ids),

        "changed_use_decision_ids":
            changed_use_decision_ids,

        "new_alignment_ids":
            sorted(
                set(
                    alignment_id(row)
                    for row in new_alignments
                    if alignment_id(row)
                )
            ),

        "changed_coverage_ids":
            coverage_change["changed_coverage_ids"],

        "changed_statement_states":
            statement_change,

        "changed_raw_statement_states":
            raw_statement_change,

        "changed_argument_standings":
            argument_change,

        "before_internal_outcomes":
            internal_outcomes(proof_before),

        "after_internal_outcomes":
            internal_outcomes(proof_after),

        "resolved_goal_ids":
            goals["resolved_goal_ids"],

        "new_goal_ids":
            goals["new_goal_ids"],

        "new_conflict_ids":
            new_conflicts,

        "substantive_change":
            substantive_change,

        # Frozen repair-level evaluation additions.
        "hard_gates":
            hard_gates,

        "effect_vector": {
            "verified_signal_gain":
                signals,

            "resolved_decisive_goal_count":
                goals["resolved_decisive_goal_count"],

            "resolved_decisive_goal_ids":
                goals["resolved_decisive_goal_ids"],

            "proof_blocker_delta":
                blockers,

            "coverage_delta":
                coverage_change,

            "new_conflict_count":
                len(new_conflicts),

            "candidate_disposition_gain":
                coverage_change["candidate_disposition_gain"],

            "cost":
                cost,
        },

        "goal_semantic_delta":
            goals,

        "comparison_policy": {
            "hard_gates_first":
                True,
            "overall_weighted_score":
                None,
            "answer_comparator_used":
                False,
            "human_or_historical_labels_used":
                False,
            "weak_reference_agreement_used":
                False,
            "goal_aligned_signal_split_frozen":
                True,
            "conflict_discovery_is_not_penalized":
                True,
            "rejected_candidate_is_not_automatically_failure":
                True,
        },
    }

    diff["structured_semantic_judgment"] = (
        build_structured_semantic_judgment(diff)
    )

    diff["stop_gate_diagnostic"] = (
        build_stop_gate_diagnostic(diff)
    )

    diff["diff_sha256"] = sha256_json(diff)

    return diff


def build_structured_semantic_judgment(diff: dict) -> dict:
    effect = diff["effect_vector"]
    signals = effect["verified_signal_gain"]
    blockers = effect["proof_blocker_delta"]
    coverage = effect["coverage_delta"]

    if signals["verified_signal_gain_count"] > 0:
        new_info = (
            f"{signals['verified_signal_gain_count']} new truth-bearing or "
            "decisively resolved validated signal(s) were obtained; "
            f"{signals['truth_bearing_alignment_count']} are truth-bearing "
            "semantic alignments."
        )
    else:
        new_info = (
            "No new truth-bearing or decisively resolved validated signal "
            "was obtained."
        )

    if coverage["candidate_disposition_gain"] > 0:
        new_info += (
            f" In addition, {coverage['candidate_disposition_gain']} "
            "previously unassessed candidate disposition(s) were closed."
        )

    removed = sum(
        len(row["removed"])
        for row in blockers["by_direction"].values()
    )

    added = sum(
        len(row["added"])
        for row in blockers["by_direction"].values()
    )

    blocker_answer = (
        f"Proof blocker codes removed={removed}, added={added}, "
        f"net blocker reduction={blockers['net_blocker_reduction']}. "
        "A blocker-name substitution alone is not treated as goal resolution."
    )

    uncertainty = (
        f"Resolved decisive goals="
        f"{effect['resolved_decisive_goal_count']}; "
        f"new conflicts={effect['new_conflict_count']}; "
        f"substantive_change={diff['substantive_change']}. "
        "Conflict discovery is preserved rather than treated as regression."
    )

    cost = effect["cost"]

    if cost["telemetry_status"] != "PERSISTED":
        worth = (
            "Cost-efficiency cannot yet be concluded because exact model-call, "
            "token, and wall-time telemetry was not persisted in the repair "
            "artifact. The evaluator refuses to infer cost from console text."
        )
    else:
        worth = (
            "Cost telemetry is available and may be compared against verified "
            "signal and decisive-goal resolution."
        )

    return {
        "what_new_externally_grounded_information_was_obtained":
            new_info,

        "which_blockers_changed_and_why":
            blocker_answer,

        "did_uncertainty_change_for_a_valid_reason":
            uncertainty,

        "was_information_gain_worth_action_cost":
            worth,
    }


def build_stop_gate_diagnostic(diff: dict) -> dict:
    reasons = []

    if (
        not diff["new_evidence_ids"]
        and not diff["new_fact_candidate_ids"]
        and not diff["new_alignment_ids"]
    ):
        reasons.append("NO_NEW_ARTIFACT")

    if (
        not diff["new_evidence_ids"]
        and not diff["new_alignment_ids"]
    ):
        reasons.append("NO_NEW_EVIDENCE_OR_ALIGNMENT")

    if (
        not diff["resolved_goal_ids"]
        and not diff["new_goal_ids"]
        and not diff["changed_statement_states"]
    ):
        reasons.append("NO_GOAL_STATE_CHANGE")

    if diff["before_bundle_id"] == diff["after_bundle_id"]:
        reasons.append("REPEATED_EVALUATION_HASH")

    if diff["new_conflict_ids"]:
        reasons.append("NEW_BLOCKING_CONFLICT")

    return {
        "candidate_stop_reasons":
            reasons,

        "force_defer_recommended":
            bool(reasons),

        "note":
            (
                "This is a diagnostic only. Routing/DEFER is performed only "
                "after the frozen repair state machine consumes the complete "
                "EvaluationDiff and RepairHistory."
            ),
    }


# ============================================================================
# Self-tests
# ============================================================================


def run_self_tests() -> None:
    # Goal rekey must not fake resolution.
    before_goals = {
        "goals": [
            {
                "goal_id": "g-old",
                "goal_type": "RESOLVE_TIME",
                "target_statement_id": "stmt-er2",
                "estimated_verdict_impact": "DECISIVE",
                "core_extension": {
                    "direction": "ATTACK",
                    "goal_origin": "PROOF_STANDARD_TEMPORAL_GATE",
                },
            }
        ]
    }

    after_goals = {
        "goals": [
            {
                "goal_id": "g-new",
                "goal_type": "RESOLVE_TIME",
                "target_statement_id": "stmt-er2",
                "estimated_verdict_impact": "DECISIVE",
                "core_extension": {
                    "direction": "ATTACK",
                    "goal_origin": "PROOF_STANDARD_TEMPORAL_GATE",
                },
            }
        ]
    }

    gd = goal_delta(before_goals, after_goals)

    assert gd["resolved_goal_ids"] == []
    assert gd["new_goal_ids"] == []
    assert len(gd["goal_id_rekeys"]) == 1

    # Conflict suppression must be caught.
    proof_b = {
        "requirement_reports": [
            {
                "requirement_id": "ER2",
                "raw_state": "BOTH",
                "contradiction_state": "PRESERVED",
            }
        ]
    }

    proof_a = {
        "requirement_reports": [
            {
                "requirement_id": "ER2",
                "raw_state": "TRUE",
                "contradiction_state": "NONE",
            }
        ]
    }

    assert conflict_suppression_violations(
        proof_b,
        proof_a,
    )

    # Rejected candidates are disposition progress, not verified truth signals.
    round_bundle = {
        "action_executions": [
            {
                "action_id": "a1",
                "action_signature": "sha256:a1",
                "new_alignments": [
                    {
                        "alignment_evidence_id": "x#1",
                        "evidence_id": "x",
                        "fact_candidate_id": "fc1",
                        "relation": "IRRELEVANT",
                        "argument_admission_channel": "REJECTED",
                        "argument_truth_bearing": False,
                    },
                    {
                        "alignment_evidence_id": "x#2",
                        "evidence_id": "x2",
                        "fact_candidate_id": "fc2",
                        "relation": "ATTACK",
                        "argument_admission_channel": "DIRECT",
                        "argument_truth_bearing": True,
                        "exact_quote": "adverse",
                        "fact_candidate": {
                            "fact_candidate_id": "fc2",
                            "quote": "explicit adverse finding",
                        },
                    },
                ],
            }
        ]
    }

    signals = verified_signal_gain(round_bundle)
    assert signals["verified_signal_gain_count"] == 1
    assert signals["truth_bearing_alignment_count"] == 1


    # Goal-aligned split: ATTACK need + SUPPORT truth signal is off-goal.
    off_goal_bundle = {
        "action_executions": [
            {
                "need_id": "ER1.attack",
                "new_alignments": [
                    {
                        "alignment_evidence_id": "off#1",
                        "relation": "SUPPORT",
                        "argument_admission_channel": "DIRECT",
                        "argument_truth_bearing": True,
                    }
                ],
            }
        ]
    }

    split = verified_signal_gain(
        off_goal_bundle
    )

    assert split[
        "verified_signal_gain_count"
    ] == 1

    assert split[
        "goal_aligned_verified_signal_count"
    ] == 0

    assert split[
        "off_goal_verified_signal_count"
    ] == 1

    # Restricted STRUCTURE trace update may append candidates but must not
    # mutate the frozen query/typed/lexical fields.
    trace = {
        "need_id": "ER1.attack",
        "query": "frozen query",
        "coverage_requirement": "CANDIDATE_DISCOVERY",
        "typed_fact_scan": {"mode": "FULL_CASE_SCAN"},
        "raw_lexical_scan": {"mode": "TOP_K_PER_VARIANT"},
        "candidate_universe": [
            {
                "evidence_id": "old",
                "text": "old",
            }
        ],
    }

    update = {
        "need_id": "ER1.attack",
        "channel": "STRUCTURE",
        "update_type": "EXECUTE_MISSING_CHANNEL",
        "base_trace_sha256": sha256_json(trace),
        "candidate_universe_additions": [
            {
                "evidence_id": "new",
                "text": "new",
            }
        ],
        "structure_full_scan": True,
        "structure_scan_complete": True,
        "structure_scan": {
            "mode": "FULL_CASE_STRUCTURE_RECORD_SCAN",
            "scan_chunk_count": 2,
            "full_scan": True,
        },
    }

    merged_traces, diag = apply_retrieval_trace_updates(
        traces=[trace],
        updates=[update],
    )

    assert merged_traces[0]["query"] == "frozen query"
    assert merged_traces[0]["candidate_universe_count"] == 2
    assert merged_traces[0]["structure_full_scan"] is True
    assert len(diag["applied_trace_updates"]) == 1

    print("repair_feedback_v1_1 self-tests: PASS")
    print("  semantic OpenGoal rekey != fake goal resolution")
    print("  pre-existing conflict suppression is a hard-gate violation")
    print("  rejected alignment != truth-bearing verified signal")
    print("  restricted STRUCTURE trace updates are mergeable")
    print("  goal-aligned vs off-goal verified signals are separated")
    print("  no weighted overall score / no answer comparator")


# ============================================================================
# CLI
# ============================================================================


def output_path(prefix: Path, suffix: str) -> Path:
    return prefix.parent / f"{prefix.name}_{suffix}.json"


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )
    parser.add_argument(
        "--coverage-before",
        type=Path,
    )
    parser.add_argument(
        "--proof-before",
        type=Path,
    )
    parser.add_argument(
        "--open-goals-before",
        type=Path,
    )
    parser.add_argument(
        "--repair-round",
        type=Path,
    )
    parser.add_argument(
        "--contract",
        type=Path,
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if all(
            value is None
            for value in (
                args.requirement_result,
                args.coverage_before,
                args.proof_before,
                args.open_goals_before,
                args.repair_round,
                args.contract,
            )
        ):
            return

    required = {
        "--requirement-result":
            args.requirement_result,
        "--coverage-before":
            args.coverage_before,
        "--proof-before":
            args.proof_before,
        "--open-goals-before":
            args.open_goals_before,
        "--repair-round":
            args.repair_round,
        "--contract":
            args.contract,
    }

    missing = [
        name
        for name, value in required.items()
        if value is None
    ]

    if missing:
        parser.error(
            "missing required arguments: "
            + ", ".join(missing)
        )

    rr_before = load_json(args.requirement_result)
    coverage_before = load_json(args.coverage_before)
    proof_before = load_json(args.proof_before)
    goals_before = load_json(args.open_goals_before)
    round_bundle = load_json(args.repair_round)
    contract = load_json(args.contract)

    if not round_bundle.get("round_execution_complete", False):
        raise SystemExit(
            "Repair round is not complete; EvaluationDiff requires the "
            "complete frozen RepairPlan round."
        )

    merged_rr, merge_diag = merge_round_into_requirement_result(
        rr_before,
        round_bundle,
    )

    rerun = rerun_layer7(
        merged_requirement_result=merged_rr,
        contract_bundle=contract,
    )

    rr_after = rerun["requirement_result"]
    coverage_after = rerun["coverage"]
    proof_after = rerun["proof_standard"]
    procedure_after = rerun["procedure_plan"]
    goals_after = rerun["open_goals"]

    hard_gates = evaluate_hard_gates(
        requirement_result_before=rr_before,
        requirement_result_after=rr_after,
        proof_before=proof_before,
        proof_after=proof_after,
        round_bundle=round_bundle,
    )

    diff = build_evaluation_diff(
        before_rr=rr_before,
        after_rr=rr_after,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        proof_before=proof_before,
        proof_after=proof_after,
        open_goals_before=goals_before,
        open_goals_after=goals_after,
        round_bundle=round_bundle,
        hard_gates=hard_gates,
    )

    prefix = (
        args.output_prefix
        or args.repair_round.with_name(
            args.repair_round.stem
            + "_feedback_v1"
        )
    )

    paths = {
        "requirement_after":
            output_path(prefix, "requirement_after"),

        "coverage_after":
            output_path(prefix, "coverage_after"),

        "proof_after":
            output_path(prefix, "proof_after"),

        "procedure_after":
            output_path(prefix, "procedure_after"),

        "open_goals_after":
            output_path(prefix, "open_goals_after"),

        "evaluation_diff":
            output_path(prefix, "evaluation_diff"),
    }

    save_json(rr_after, paths["requirement_after"])
    save_json(coverage_after, paths["coverage_after"])
    save_json(proof_after, paths["proof_after"])
    save_json(procedure_after, paths["procedure_after"])
    save_json(goals_after, paths["open_goals_after"])
    save_json(diff, paths["evaluation_diff"])

    print("=" * 72)
    print("FRECA REPAIR FEEDBACK / EVALUATION DIFF V1")
    print("=" * 72)

    print()
    print("Hard Gates:")
    print(
        "  ALL PASS:",
        hard_gates["all_hard_gates_pass"],
    )

    for name, gate in hard_gates["gates"].items():
        print(
            f"  {name}:",
            "PASS" if gate["pass"] else "FAIL",
            f"violations={gate['violation_count']}",
        )

        for violation in gate["violations"][:5]:
            print("    -", violation)

    effect = diff["effect_vector"]

    print()
    print("Effect Vector:")
    print(
        "  VerifiedSignalGain:",
        effect["verified_signal_gain"][
            "verified_signal_gain_count"
        ],
    )
    print(
        "    truth-bearing alignments:",
        effect["verified_signal_gain"][
            "truth_bearing_alignment_count"
        ],
    )
    print(
        "    goal-aligned verified:",
        effect["verified_signal_gain"][
            "goal_aligned_verified_signal_count"
        ],
    )
    print(
        "    off-goal verified:",
        effect["verified_signal_gain"][
            "off_goal_verified_signal_count"
        ],
    )
    print(
        "  ResolvedDecisiveGoalCount:",
        effect["resolved_decisive_goal_count"],
    )
    print(
        "  ProofBlockerDelta:",
        effect["proof_blocker_delta"][
            "net_blocker_reduction"
        ],
    )
    print(
        "  CandidateDispositionGain:",
        effect["candidate_disposition_gain"],
    )
    print(
        "  NewConflictCount:",
        effect["new_conflict_count"],
    )
    print(
        "  Cost telemetry:",
        effect["cost"]["telemetry_status"],
    )

    print()
    print(
        "Statement changes:",
        diff["changed_statement_states"],
    )
    print(
        "Raw statement changes:",
        diff["changed_raw_statement_states"],
    )
    print(
        "Argument standing changes:",
        diff["changed_argument_standings"],
    )
    print(
        "Resolved goals:",
        diff["resolved_goal_ids"],
    )
    print(
        "New goals:",
        diff["new_goal_ids"],
    )

    if diff["goal_semantic_delta"]["goal_id_rekeys"]:
        print(
            "Goal ID rekeys ignored as semantic resolution:",
            len(
                diff["goal_semantic_delta"][
                    "goal_id_rekeys"
                ]
            ),
        )

    print()
    print(
        "Substantive change:",
        diff["substantive_change"],
    )
    print(
        "Candidate stop reasons:",
        diff["stop_gate_diagnostic"][
            "candidate_stop_reasons"
        ],
    )

    print()
    print("Structured semantic judgment:")

    judgment = diff["structured_semantic_judgment"]

    print(
        "  New information:",
        judgment[
            "what_new_externally_grounded_information_was_obtained"
        ],
    )
    print(
        "  Blockers:",
        judgment[
            "which_blockers_changed_and_why"
        ],
    )
    print(
        "  Uncertainty:",
        judgment[
            "did_uncertainty_change_for_a_valid_reason"
        ],
    )
    print(
        "  Cost:",
        judgment[
            "was_information_gain_worth_action_cost"
        ],
    )

    print()
    print("Saved:")
    for name, path in paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
