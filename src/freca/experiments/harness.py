"""Self-Improving Audit Harness (outer loop).

This module implements the *outer* loop described in Lil'Log's "Harness
Engineering for Self-Improvement": treat the way the model is organised as the
optimisable object, not just the model's answers. The inner loop (per-CP
retrieve -> judge -> verify -> repair) already exists in
:mod:`freca.experiments.stage_audit` and :mod:`freca.experiments.agent_audit`;
this outer loop sits above it and consists of four layers:

L1  HarnessConfig        - the only object a Proposer may edit. A small set of
                           knobs (method routing, track3 condition, retrieval
                           depth, verifier threshold) that are *not* rules, not
                           prompts, and not the gold labels.
L2  analyze_failures     - cluster wrong CPs into systematic failure modes
                           (N/A misjudge, citation missing, retrieval gap,
                           unresolved conflict, cross-CP inconsistency,
                           verifier no-op).
L3  propose_harness_changes
                         - an Agent reads the failure report + current config
                           and emits a HarnessProposal: a config patch +
                           rationale + targeted failure modes. The patch is
                           validated against a strict whitelist; anything
                           outside :class:`HarnessConfig` is rejected.
L4  run_regression       - re-run the patched config on held-in and held-out
                           Micro-Gold splits, compare to baseline, accept only
                           if held-out accuracy improves AND no Element
                           regresses past a tolerance.

:func:`run_harness_cycle` chains the four layers into one automatic
improvement loop. The first revision runs code-only (no live API) - the
regression layer is exercised via :class:`ReplayJsonClient` in tests; real
acceptance data waits for quota recovery and a populated Micro-Gold.

Read-only boundary (Proposer may NEVER touch):
  - Prompt rule text / CP semantics
  - Hand-written 1/0 decision rules
  - Micro-Gold labels
  - The evaluation program and scoring logic
  - The regulation text
  - Model-call budget ceiling
  - The submission table
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from freca.experiments.models import (
    ExecutionResult,
    ExperimentMethod,
    StageTrace,
    Track3Condition,
)
from freca.experiments.orchestrator import run_experiment
from freca.experiments.planning import build_execution_plan
from freca.experiments.metrics import compute_run_metrics
from freca.experiments.models import RunMetrics, SilverReference, SilverTier
from freca.experiments.silver import build_silver_reference
from freca.llm import JsonChatClient
from freca.models import CheckpointDefinition, StrictModel, Verdict
from freca.state import atomic_write_json


# ─────────────────────────────────────────────────────────────────────────────
# L1 - HarnessConfig (the only object a Proposer may edit)
# ─────────────────────────────────────────────────────────────────────────────


class HarnessConfig(StrictModel):
    """The tunable surface of the harness.

    Every field here is a *non-semantic* knob: which method to route to, how
    aggressively to redact Track 3, how deep the RAG retrieval goes, and where
    the agent-audit verifier fires. None of these fields carry CP-specific
    rules or prompt text - that is the read-only boundary.
    """

    method: ExperimentMethod = ExperimentMethod.CASE_FULL
    track3_condition: Track3Condition = Track3Condition.RAW
    per_scope_limit: int = Field(default=12, ge=1, le=50)
    uncertainty_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


# Whitelist of fields a Proposer patch may touch. Kept explicit (rather than
# "every field on HarnessConfig") so that adding a read-only field to
# HarnessConfig later does not silently widen the editable surface.
PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {"method", "track3_condition", "per_scope_limit", "uncertainty_threshold"}
)


class HarnessConfigPatch(StrictModel):
    """A partial edit to :class:`HarnessConfig`.

    Only keys in :data:`PATCHABLE_FIELDS` are permitted; unknown keys are
    rejected at validation time so a Proposer cannot smuggle in new fields.
    Every field is optional - a patch only needs to touch the knobs it changes.
    """

    method: ExperimentMethod | None = None
    track3_condition: Track3Condition | None = None
    per_scope_limit: int | None = Field(default=None, ge=1, le=50)
    uncertainty_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    def applied_to(self, base: HarnessConfig) -> HarnessConfig:
        """Return a new config with this patch's non-None fields overlaid."""
        updates: dict[str, Any] = {}
        for field_name in PATCHABLE_FIELDS:
            value = getattr(self, field_name)
            if value is not None:
                updates[field_name] = value
        return base.model_copy(update=updates)


def run_with_config(
    *,
    config: HarnessConfig,
    case_ids: Sequence[int],
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    client: JsonChatClient,
    artifact_root: Path,
) -> list[ExecutionResult]:
    """Run one HarnessConfig across every case id, returning flat results.

    Thin wrapper over :func:`run_experiment` that materialises a per-case plan
    from ``config.method`` and forwards the config's knobs. Each case lands
    under ``artifact_root/<method>/case-NNN/track3-<cond>/`` exactly as the
    baseline runner expects, so downstream metrics / silver comparison do not
    need to know the harness exists.
    """
    all_results: list[ExecutionResult] = []
    for case_id in case_ids:
        plan = build_execution_plan(config.method, case_id=case_id, checkpoints=checkpoints)
        results = run_experiment(
            plan=plan,
            checkpoints=checkpoints,
            parsed_dir=parsed_dir,
            track3_condition=config.track3_condition,
            client=client,
            artifact_root=artifact_root,
            per_scope_limit=config.per_scope_limit,
            uncertainty_threshold=config.uncertainty_threshold,
        )
        all_results.extend(results)
    return all_results


def config_metrics(
    *,
    results: Sequence[ExecutionResult],
    checkpoints: Sequence[CheckpointDefinition],
    silver: SilverReference | None,
) -> tuple[float, dict[int, float]]:
    """Aggregate a config's run into (overall_accuracy, per_element_accuracy).

    Used by the regression layer's acceptance gate. Cases without silver
    anchors contribute zero anchored CPs and are naturally ignored.
    """
    per_element_correct: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    total_correct = 0
    total_anchored = 0
    cp_index = {cp.cp_id: cp for cp in checkpoints}
    for result in results:
        metrics = compute_run_metrics(result=result, checkpoints=checkpoints, silver=silver)
        total_correct += metrics.anchored_correct
        total_anchored += metrics.anchored_total
        for em in metrics.per_element:
            per_element_correct[em.element_id].append(em.accuracy)
    overall = (total_correct / total_anchored) if total_anchored else 0.0
    per_element = {
        eid: (sum(vals) / len(vals)) if vals else 0.0 for eid, vals in per_element_correct.items()
    }
    return overall, per_element


# ─────────────────────────────────────────────────────────────────────────────
# L2 - Failure attribution
# ─────────────────────────────────────────────────────────────────────────────


from freca.experiments.models import AgentTrace  # noqa: E402 - intentional late import


class FailureMode:
    """Systematic failure categories the Proposer tries to fix.

    A single wrong CP may match several modes; the analyzer records every match
    so the Proposer sees the full picture, then ranks modes by frequency to
    decide which knob to move first.
    """

    NA_MISJUDGE = "na_misjudge"  # candidate N/A vs silver non-N/A, or vice versa
    CITATION_MISSING = "citation_missing"  # non-N/A verdict with no citations
    RETRIEVAL_GAP = "retrieval_gap"  # non-N/A, no citations, stage-2 found nothing
    CONFLICT_UNRESOLVED = "conflict_unresolved"  # contradictions present, no critic fired
    VERIFIER_NOOP = "verifier_noop"  # verifier fired but verdict unchanged
    CROSS_CP_INCONSISTENT = "cross_cp_inconsistent"  # same case, same cited chunk, opposite verdicts


class FailureInstance(StrictModel):
    """One wrong CP annotated with the failure modes it exhibits."""

    case_id: int
    cp_id: str
    element_id: int
    candidate_verdict: str
    silver_verdict: str
    modes: tuple[str, ...]
    detail: str = ""


class FailureReport(StrictModel):
    """Aggregated failure attribution across one config's run.

    ``mode_counts`` ranks failure modes by frequency so the Proposer can pick
    the highest-leverage knob. ``instances`` keeps per-CP detail for the
    Proposer's prompt.
    """

    total_anchored: int
    total_wrong: int
    mode_counts: dict[str, int]
    instances: tuple[FailureInstance, ...]


def analyze_failures(
    *,
    results: Sequence[ExecutionResult],
    checkpoints: Sequence[CheckpointDefinition],
    silver: SilverReference,
) -> FailureReport:
    """Attribute every wrong CP to one or more :class:`FailureMode`.

    Only anchored CPs (silver tier ANOMALY_RULE or HUMAN) are scored; wrong
    verdicts on unanchored CPs cannot be labelled because there is no ground
    truth to compare against.
    """
    cp_index = {cp.cp_id: cp for cp in checkpoints}
    instances: list[FailureInstance] = []
    mode_counts: dict[str, int] = {}
    total_anchored = 0
    total_wrong = 0

    # Pre-index citations per case for cross-CP inconsistency detection.
    case_citations: dict[int, dict[str, list[tuple[str, str]]]] = {}
    for result in results:
        per_chunk: dict[str, list[tuple[str, str]]] = {}
        for verdict in result.verdicts:
            for cite in verdict.citation_ids:
                per_chunk.setdefault(cite, []).append((verdict.cp_id, verdict.verdict.value))
        case_citations[result.unit.case_id] = per_chunk

    for result in results:
        case_id = result.unit.case_id
        case_entries = silver.entries.get(str(case_id), {})
        candidate_by_cp = {v.cp_id: v for v in result.verdicts}
        for cp_id, entry in case_entries.items():
            if entry.tier not in (SilverTier.ANOMALY_RULE, SilverTier.HUMAN):
                continue
            if cp_id not in cp_index:
                continue
            total_anchored += 1
            candidate = candidate_by_cp.get(cp_id)
            if candidate is None or candidate.verdict == entry.verdict:
                continue  # correct (or missing) - not a failure
            total_wrong += 1
            modes: list[str] = []
            detail_parts: list[str] = []
            cand_v = candidate.verdict.value
            sil_v = entry.verdict.value

            # NA misjudge
            if (cand_v == Verdict.NOT_APPLICABLE.value) != (sil_v == Verdict.NOT_APPLICABLE.value):
                modes.append(FailureMode.NA_MISJUDGE)
                detail_parts.append(f"candidate={cand_v} silver={sil_v}")

            # Citation missing / retrieval gap
            if cand_v != Verdict.NOT_APPLICABLE.value and not candidate.citation_ids:
                modes.append(FailureMode.CITATION_MISSING)
                if result.stage_trace is not None and not result.stage_trace.contradictions:
                    modes.append(FailureMode.RETRIEVAL_GAP)
                    detail_parts.append("no citations and stage found no contrary evidence")

            # Conflict unresolved
            if result.stage_trace is not None and result.stage_trace.contradictions:
                fired_modules = (
                    {m.module for m in result.agent_trace.fired_modules}
                    if result.agent_trace is not None
                    else set()
                )
                if "critic" not in fired_modules:
                    modes.append(FailureMode.CONFLICT_UNRESOLVED)
                    detail_parts.append(f"contradictions={list(result.stage_trace.contradictions)[:3]}")

            # Verifier no-op
            if result.agent_trace is not None:
                for call in result.agent_trace.fired_modules:
                    if call.module == "verifier" and call.verdict_before == call.verdict_after:
                        modes.append(FailureMode.VERIFIER_NOOP)
                        detail_parts.append("verifier fired but verdict unchanged")
                        break

            # Cross-CP inconsistency: same cited chunk, opposite verdicts in same case
            for cite in candidate.citation_ids:
                siblings = case_citations.get(case_id, {}).get(cite, [])
                opposite = [
                    (other_cp, other_v)
                    for other_cp, other_v in siblings
                    if other_cp != cp_id and other_v != cand_v and other_v != Verdict.NOT_APPLICABLE.value
                ]
                if opposite:
                    modes.append(FailureMode.CROSS_CP_INCONSISTENT)
                    detail_parts.append(f"chunk {cite} cited with opposite verdict by {opposite[:2]}")
                    break

            if not modes:
                modes.append("other")
            for m in modes:
                mode_counts[m] = mode_counts.get(m, 0) + 1
            instances.append(
                FailureInstance(
                    case_id=case_id,
                    cp_id=cp_id,
                    element_id=cp_index[cp_id].element_id,
                    candidate_verdict=cand_v,
                    silver_verdict=sil_v,
                    modes=tuple(modes),
                    detail="; ".join(detail_parts),
                )
            )

    return FailureReport(
        total_anchored=total_anchored,
        total_wrong=total_wrong,
        mode_counts=dict(sorted(mode_counts.items(), key=lambda kv: kv[1], reverse=True)),
        instances=tuple(instances),
    )


# ─────────────────────────────────────────────────────────────────────────────
# L3 - Harness Proposer
# ─────────────────────────────────────────────────────────────────────────────


import json  # noqa: E402 - intentional late import

from freca.llm import ModelResponseError  # noqa: E402


# Maps each failure mode to the config knobs that plausibly address it. The
# Proposer prompt shows this map so the Agent knows which knobs are relevant
# for which failure - but the Agent still picks the value; the map is guidance,
# not an automatic rule.
_FAILURE_KNOB_HINTS: dict[str, list[str]] = {
    FailureMode.NA_MISJUDGE: ["method", "uncertainty_threshold"],
    FailureMode.CITATION_MISSING: ["per_scope_limit", "method"],
    FailureMode.RETRIEVAL_GAP: ["per_scope_limit", "method"],
    FailureMode.CONFLICT_UNRESOLVED: ["method", "uncertainty_threshold"],
    FailureMode.VERIFIER_NOOP: ["uncertainty_threshold", "method"],
    FailureMode.CROSS_CP_INCONSISTENT: ["method"],
}


_PROPOSER_SYSTEM = """You are a harness proposer for an agricultural compliance audit system.
You analyse systematic failure modes from a previous run and propose a SMALL,
reversible change to the harness configuration - never to the prompts, the CP
rules, the regulation text, the Micro-Gold labels, or the scoring logic.

You may ONLY change these knobs (the editable surface):
  - method: one of case_full | element_full | checkpoint_full | automatic_retrieval | stage_audit | agent_audit
  - track3_condition: raw | masked
  - per_scope_limit: integer 1..50 (RAG retrieval depth; only affects automatic_retrieval)
  - uncertainty_threshold: float 0.0..1.0 (agent_audit verifier trigger)

HARD CONSTRAINTS:
- Do NOT propose changes to anything outside the four knobs above.
- Do NOT invent CP-specific rules (e.g. "CP23 with <2yr records = 0"). That is
  reward hacking and is forbidden by the contest rules.
- Propose at most one knob change per cycle. Pick the highest-leverage one.
- If no change is warranted, return an empty patch.

Reply with a single JSON object of this exact shape, nothing else:
{"field": "<one of the four knobs>", "value": <new value>, "rationale": "<one sentence>", "targeted_failure_modes": ["<mode>", ...]}

If no change is warranted, reply: {"field": null, "value": null, "rationale": "no change", "targeted_failure_modes": []}
"""


class HarnessProposal(StrictModel):
    """One proposed config edit + its justification.

    ``field`` must be a member of :data:`PATCHABLE_FIELDS`; this is enforced at
    validation so a Proposer that tries to edit a non-whitelisted field is
    rejected before the patch is ever applied.
    """

    field: str | None = None
    value: Any = None
    rationale: str = Field(min_length=1)
    targeted_failure_modes: tuple[str, ...] = Field(default_factory=tuple)

    def to_patch(self) -> HarnessConfigPatch:
        """Convert the proposal into a validated :class:`HarnessConfigPatch`.

        Raises :class:`ValueError` if ``field`` is not patchable or ``value``
        is the wrong type for that field - the caller treats this as a
        rejected proposal.
        """
        if self.field is None:
            return HarnessConfigPatch()
        if self.field not in PATCHABLE_FIELDS:
            raise ValueError(f"proposed field {self.field!r} is not in the editable surface")
        if self.value is None:
            return HarnessConfigPatch()
        # Build the patch via model_validate so Pydantic checks the value type
        # and range for the chosen field, and rejects any extra keys.
        return HarnessConfigPatch.model_validate({self.field: self.value})

    @property
    def is_noop(self) -> bool:
        return self.field is None or self.value is None


def propose_harness_changes(
    *,
    failure_report: FailureReport,
    current_config: HarnessConfig,
    client: JsonChatClient,
) -> HarnessProposal:
    """Ask the Proposer Agent for one config edit grounded in the failure report.

    The Agent sees: the failure-mode frequency table, a sample of wrong CP
    instances with their modes, the current config, and the editable-knob
    hints per failure mode. Its JSON reply is validated into a
    :class:`HarnessProposal`; a structurally invalid reply (wrong field, bad
    value, or non-JSON) becomes a no-op proposal so the cycle can continue
    without crashing.
    """
    user_payload = {
        "current_config": current_config.model_dump(mode="json"),
        "editable_knobs": sorted(PATCHABLE_FIELDS),
        "failure_mode_counts": failure_report.mode_counts,
        "total_anchored": failure_report.total_anchored,
        "total_wrong": failure_report.total_wrong,
        "wrong_cp_samples": [
            inst.model_dump(mode="json") for inst in failure_report.instances[:12]
        ],
        "knob_hints_per_failure_mode": _FAILURE_KNOB_HINTS,
    }
    proposal_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "value", "rationale", "targeted_failure_modes"],
        "properties": {
            "field": {"type": ["string", "null"]},
            "value": {},
            "rationale": {"type": "string"},
            "targeted_failure_modes": {"type": "array", "items": {"type": "string"}},
        },
    }
    try:
        raw = client.complete_json(
            system=_PROPOSER_SYSTEM,
            user=json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
            schema=proposal_schema,
            max_tokens=512,
        )
    except ModelResponseError:
        return HarnessProposal(rationale="proposer call failed; no change proposed")
    return _coerce_proposal(raw)


def _coerce_proposal(raw: Any) -> HarnessProposal:
    """Validate the Agent's JSON into a :class:`HarnessProposal`.

    Any structural problem (unknown field, value out of range, field not in
    the whitelist) downgrades to a no-op proposal rather than raising, so a
    buggy Proposer reply never crashes the improvement cycle.
    """
    if not isinstance(raw, dict):
        return HarnessProposal(rationale="proposer reply was not a JSON object; no change")
    field_name = raw.get("field")
    value = raw.get("value")
    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return HarnessProposal(rationale="proposer reply missing rationale; no change")
    if field_name is None or value is None:
        return HarnessProposal(
            rationale=rationale,
            targeted_failure_modes=tuple(raw.get("targeted_failure_modes", []) or []),
        )
    if field_name not in PATCHABLE_FIELDS:
        return HarnessProposal(
            rationale=f"proposed field {field_name!r} is outside the editable surface; rejected",
        )
    # Validate the value against the patch model to enforce type + range.
    try:
        HarnessConfigPatch.model_validate({field_name: value})
    except ValidationError:
        return HarnessProposal(
            rationale=f"proposed value {value!r} for {field_name!r} failed validation; rejected",
        )
    modes = raw.get("targeted_failure_modes", []) or []
    return HarnessProposal(
        field=field_name,
        value=value,
        rationale=rationale,
        targeted_failure_modes=tuple(modes) if isinstance(modes, list) else (),
    )


# ─────────────────────────────────────────────────────────────────────────────
# L4 - Regression test layer
# ─────────────────────────────────────────────────────────────────────────────


class RegressionResult(StrictModel):
    """Outcome of one proposed patch's held-in / held-out evaluation.

    Acceptance requires BOTH:
      1. held-out overall_accuracy strictly improves over baseline, AND
      2. no Element's accuracy regresses by more than ``element_regression_tolerance``.

    A patch that lifts the overall number by over-fitting one Element while
    breaking another is rejected - this is the guard against benchmark
    overfitting and reward hacking at the regression gate.
    """

    proposal: HarnessProposal
    accepted: bool
    reason: str
    baseline_held_in_accuracy: float
    baseline_held_out_accuracy: float
    patched_held_in_accuracy: float
    patched_held_out_accuracy: float
    element_deltas: dict[str, float] = Field(default_factory=dict)  # eid -> (patched - baseline) on held_out


_DEFAULT_ELEMENT_TOLERANCE = 0.05  # an Element may drop up to 5pp before the patch is rejected


def run_regression(
    *,
    proposal: HarnessProposal,
    baseline_config: HarnessConfig,
    held_in_case_ids: Sequence[int],
    held_out_case_ids: Sequence[int],
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    client: JsonChatClient,
    silver: SilverReference,
    artifact_root: Path,
    element_regression_tolerance: float = _DEFAULT_ELEMENT_TOLERANCE,
) -> RegressionResult:
    """Evaluate one proposal against the baseline on both splits.

    Runs the patched config on held-in and held-out, compares to the baseline
    config's held-out accuracy + per-Element accuracy, and applies the
    acceptance gate. Persisted artifacts land under ``artifact_root/regression/``.
    """
    if proposal.is_noop:
        return RegressionResult(
            proposal=proposal,
            accepted=False,
            reason="no-op proposal; nothing to regress",
            baseline_held_in_accuracy=0.0,
            baseline_held_out_accuracy=0.0,
            patched_held_in_accuracy=0.0,
            patched_held_out_accuracy=0.0,
        )

    try:
        patch = proposal.to_patch()
    except ValueError as exc:
        return RegressionResult(
            proposal=proposal,
            accepted=False,
            reason=f"proposal rejected at patch validation: {exc}",
            baseline_held_in_accuracy=0.0,
            baseline_held_out_accuracy=0.0,
            patched_held_in_accuracy=0.0,
            patched_held_out_accuracy=0.0,
        )

    patched_config = patch.applied_to(baseline_config)

    baseline_held_in = run_with_config(
        config=baseline_config,
        case_ids=held_in_case_ids,
        checkpoints=checkpoints,
        parsed_dir=parsed_dir,
        client=client,
        artifact_root=artifact_root / "regression" / "baseline_held_in",
    )
    baseline_held_out = run_with_config(
        config=baseline_config,
        case_ids=held_out_case_ids,
        checkpoints=checkpoints,
        parsed_dir=parsed_dir,
        client=client,
        artifact_root=artifact_root / "regression" / "baseline_held_out",
    )
    patched_held_in = run_with_config(
        config=patched_config,
        case_ids=held_in_case_ids,
        checkpoints=checkpoints,
        parsed_dir=parsed_dir,
        client=client,
        artifact_root=artifact_root / "regression" / "patched_held_in",
    )
    patched_held_out = run_with_config(
        config=patched_config,
        case_ids=held_out_case_ids,
        checkpoints=checkpoints,
        parsed_dir=parsed_dir,
        client=client,
        artifact_root=artifact_root / "regression" / "patched_held_out",
    )

    b_in_overall, _ = config_metrics(results=baseline_held_in, checkpoints=checkpoints, silver=silver)
    b_out_overall, b_out_per_elem = config_metrics(results=baseline_held_out, checkpoints=checkpoints, silver=silver)
    p_in_overall, _ = config_metrics(results=patched_held_in, checkpoints=checkpoints, silver=silver)
    p_out_overall, p_out_per_elem = config_metrics(results=patched_held_out, checkpoints=checkpoints, silver=silver)

    element_deltas: dict[str, float] = {}
    regressed_elements: list[str] = []
    for eid in sorted(set(b_out_per_elem) | set(p_out_per_elem)):
        delta = p_out_per_elem.get(eid, 0.0) - b_out_per_elem.get(eid, 0.0)
        element_deltas[str(eid)] = round(delta, 4)
        if delta < -element_regression_tolerance:
            regressed_elements.append(str(eid))

    accepted, reason = _decide_acceptance(
        baseline_held_out_accuracy=b_out_overall,
        patched_held_out_accuracy=p_out_overall,
        regressed_elements=regressed_elements,
        element_regression_tolerance=element_regression_tolerance,
    )

    return RegressionResult(
        proposal=proposal,
        accepted=accepted,
        reason=reason,
        baseline_held_in_accuracy=b_in_overall,
        baseline_held_out_accuracy=b_out_overall,
        patched_held_in_accuracy=p_in_overall,
        patched_held_out_accuracy=p_out_overall,
        element_deltas=element_deltas,
    )


def _decide_acceptance(
    *,
    baseline_held_out_accuracy: float,
    patched_held_out_accuracy: float,
    regressed_elements: list[str],
    element_regression_tolerance: float,
) -> tuple[bool, str]:
    """Pure acceptance-gate logic, separated so it can be unit-tested in isolation.

    Accept iff held-out accuracy strictly improves AND no Element regresses
    beyond ``element_regression_tolerance``.
    """
    improved = patched_held_out_accuracy > baseline_held_out_accuracy
    if not improved:
        return False, (
            f"held-out accuracy did not improve "
            f"({baseline_held_out_accuracy:.3f} -> {patched_held_out_accuracy:.3f})"
        )
    if regressed_elements:
        return False, (
            f"held-out improved but Element(s) {regressed_elements} regressed beyond "
            f"tolerance {element_regression_tolerance}"
        )
    return True, (
        f"accepted: held-out {baseline_held_out_accuracy:.3f} -> {patched_held_out_accuracy:.3f}, "
        f"no Element regressed beyond {element_regression_tolerance}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Closed loop - run_harness_cycle
# ─────────────────────────────────────────────────────────────────────────────


class HarnessCycleIteration(StrictModel):
    """One iteration of the improvement loop: proposal + regression outcome."""

    iteration: int
    config_before: HarnessConfig
    proposal: HarnessProposal
    regression: RegressionResult | None = None
    config_after: HarnessConfig
    accepted: bool


class HarnessCycleResult(StrictModel):
    """Full trace of a harness improvement run.

    ``iterations`` is the per-round audit trail; ``final_config`` is the last
    accepted config (or the baseline if nothing was accepted); ``accepted_count``
    lets the caller see at a glance whether the loop improved anything.
    """

    baseline_config: HarnessConfig
    final_config: HarnessConfig
    iterations: tuple[HarnessCycleIteration, ...]
    accepted_count: int
    stopped_reason: str


def run_harness_cycle(
    *,
    baseline_config: HarnessConfig,
    held_in_case_ids: Sequence[int],
    held_out_case_ids: Sequence[int],
    checkpoints: Sequence[CheckpointDefinition],
    parsed_dir: Path,
    client: JsonChatClient,
    silver: SilverReference,
    artifact_root: Path,
    max_iterations: int = 3,
    element_regression_tolerance: float = _DEFAULT_ELEMENT_TOLERANCE,
) -> HarnessCycleResult:
    """Run the full self-improvement loop for up to ``max_iterations`` rounds.

    Each iteration:
      1. Run the current config on held-in (the failure-analysis split).
      2. Attribute failures with :func:`analyze_failures`.
      3. Propose one config edit with :func:`propose_harness_changes`.
      4. Regression-test it on held-in + held-out with :func:`run_regression`.
      5. Accept -> fold the patch into the running config; Reject -> keep it.

    The loop stops early when:
      - the Proposer returns a no-op (nothing left to try), or
      - ``max_iterations`` is reached, or
      - a proposal is rejected two consecutive times (diminishing returns).

    All artifacts persist under ``artifact_root/cycle-<N>/`` so every round is
    replayable.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if not held_in_case_ids or not held_out_case_ids:
        raise ValueError("held-in and held-out case sets must both be non-empty")

    current_config = baseline_config
    iterations: list[HarnessCycleIteration] = []
    accepted_count = 0
    consecutive_rejections = 0
    stopped_reason = "max_iterations reached"

    for index in range(max_iterations):
        cycle_dir = artifact_root / f"cycle-{index:03d}"

        # 1. Run current config on held-in for failure analysis.
        held_in_results = run_with_config(
            config=current_config,
            case_ids=held_in_case_ids,
            checkpoints=checkpoints,
            parsed_dir=parsed_dir,
            client=client,
            artifact_root=cycle_dir / "held_in",
        )

        # 2. Attribute failures.
        failure_report = analyze_failures(
            results=held_in_results,
            checkpoints=checkpoints,
            silver=silver,
        )
        atomic_write_json(cycle_dir / "failure_report.json", failure_report.model_dump(mode="json"))

        if failure_report.total_wrong == 0:
            stopped_reason = "no failures left to fix on held-in"
            break

        # 3. Propose.
        proposal = propose_harness_changes(
            failure_report=failure_report,
            current_config=current_config,
            client=client,
        )
        atomic_write_json(cycle_dir / "proposal.json", proposal.model_dump(mode="json"))

        if proposal.is_noop:
            iterations.append(
                HarnessCycleIteration(
                    iteration=index,
                    config_before=current_config,
                    proposal=proposal,
                    regression=None,
                    config_after=current_config,
                    accepted=False,
                )
            )
            stopped_reason = "proposer returned a no-op"
            break

        # 4. Regression-test.
        regression = run_regression(
            proposal=proposal,
            baseline_config=current_config,
            held_in_case_ids=held_in_case_ids,
            held_out_case_ids=held_out_case_ids,
            checkpoints=checkpoints,
            parsed_dir=parsed_dir,
            client=client,
            silver=silver,
            artifact_root=cycle_dir / "regression",
            element_regression_tolerance=element_regression_tolerance,
        )
        atomic_write_json(cycle_dir / "regression.json", regression.model_dump(mode="json"))

        # 5. Accept or reject.
        if regression.accepted:
            current_config = proposal.to_patch().applied_to(current_config)
            accepted_count += 1
            consecutive_rejections = 0
        else:
            consecutive_rejections += 1

        iterations.append(
            HarnessCycleIteration(
                iteration=index,
                config_before=current_config if not regression.accepted else baseline_config,
                proposal=proposal,
                regression=regression,
                config_after=current_config,
                accepted=regression.accepted,
            )
        )

        if consecutive_rejections >= 2:
            stopped_reason = "two consecutive rejections; stopping to avoid diminishing returns"
            break

    return HarnessCycleResult(
        baseline_config=baseline_config,
        final_config=current_config,
        iterations=tuple(iterations),
        accepted_count=accepted_count,
        stopped_reason=stopped_reason,
    )
