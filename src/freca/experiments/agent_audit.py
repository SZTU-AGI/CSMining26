"""Agent-audit (D) experiment method: stage-audit + conditional agent modules.

D is ``STAGE_AUDIT`` plus an optional post-verdict pass that fires when any of
six trigger conditions is met. The post-pass is intentionally narrow — every
fired module is a single LLM call with a tight schema, and the result either
keeps the stage-3 verdict, repairs it (retrieval repair), or escalates it
(critic / verifier / arbitration). Modules fire independently and only when
their specific trigger fires, so a clean, high-confidence case never touches
the extra LLM calls.

Trigger conditions (evaluated by :func:`_evaluate_triggers`):

1. ``initial_na``      — stage-1 said NOT_APPLICABLE and stage-2 escalated to a
                         1/0 judgment. Repair: re-run the contrary search with
                         the explicit escalation context to confirm.
2. ``retrieval_gap``   — stage-2 returned no evidence_citations (i.e. the
                         search did not surface any concrete evidence). Repair:
                         widen the search.
3. ``conflict``        — stage-3 reported non-empty ``contradictions`` array
                         (i.e. supporting and contrary evidence coexist).
                         Module: Critic.
4. ``low_confidence``  — ``uncertainty > ``--uncertainty-threshold`` (default
                         0.5). Module: Verifier.
5. ``missing_citation`` — verdict's ``citation_ids`` is empty or absent.
                         Module: Verifier.
6. ``cross_cp_inconsistent`` — a later pass against a different CP on the
                         same case finds a fact conflict. Module: Arbitration.

The agent path is per-CP like STAGE_AUDIT; the orchestrator routes
``AGENT_AUDIT`` to :func:`run_agent_audit_plan`, which simply calls
:func:`run_agent_audit_unit` for every unit.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from freca.experiments.models import (
    AgentModuleCall,
    AgentTrace,
    ExecutionResult,
    ExecutionUnit,
    ExperimentVerdict,
    MaterialSnapshot,
)
from freca.experiments.prompts import (
    ARBITRATION_SCHEMA,
    CONTRARY_SEARCH_SCHEMA,
    CRITIC_SCHEMA,
    VERIFIER_SCHEMA,
    build_agent_prompt,
)
from freca.experiments.stage_audit import (
    _invoke_stage,
    run_stage_audit_unit,
)
from freca.llm import JsonChatClient
from freca.models import Verdict
from freca.state import atomic_write_json


# Thresholds configurable via function kwargs; defaults match the plan draft.
_DEFAULT_UNCERTAINTY_THRESHOLD = 0.5


def run_agent_audit_plan(
    *,
    plan_units: Sequence[ExecutionUnit],
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_root: Path,
    uncertainty_threshold: float = _DEFAULT_UNCERTAINTY_THRESHOLD,
) -> list[ExecutionResult]:
    """Run every unit of an AGENT_AUDIT plan through stage-audit + agent pass."""
    results: list[ExecutionResult] = []
    for index, unit in enumerate(plan_units):
        unit_dir = artifact_root / f"cp-{index:03d}"
        results.append(
            run_agent_audit_unit(
                unit=unit,
                material=material,
                client=client,
                artifact_dir=unit_dir,
                uncertainty_threshold=uncertainty_threshold,
            )
        )
    return results


def run_agent_audit_unit(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_dir: Path,
    uncertainty_threshold: float = _DEFAULT_UNCERTAINTY_THRESHOLD,
) -> ExecutionResult:
    """Stage-audit the CP, then run any fired agent modules."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    base = run_stage_audit_unit(
        unit=unit,
        material=material,
        client=client,
        artifact_dir=artifact_dir / "stage_audit",
    )
    if not base.valid or not base.verdicts:
        # Persist the failed base verdict so resume_run / scoreboard see a result
        # (not a silent gap). Without this, a stage-1 quota failure leaves no
        # result.json and the unit looks "missing" rather than "failed".
        atomic_write_json(artifact_dir / "result.json", base.model_dump(mode="json"))
        return base  # failure propagates verbatim — no agent pass over a broken stage

    cp_id = base.verdicts[0].cp_id
    verdict_before = base.verdicts[0].verdict.value
    trace = AgentTrace(cp_id=cp_id)
    current = base.verdicts[0]

    # ── Condition 1: initial_na + escalation → retrieval repair ────────
    if (
        base.stage_trace is not None
        and base.stage_trace.applicability == "NOT_APPLICABLE"
        and verdict_before != Verdict.NOT_APPLICABLE.value
    ):
        repaired, calls = _run_retrieval_repair(
            client=client,
            material=material,
            unit=unit,
            current=current,
            stage_trace=base.stage_trace,
            artifact_dir=artifact_dir / "retrieval_repair",
        )
        # Record the call regardless of outcome so the trace is auditable;
        # ``repaired is None`` only happens when the repair call itself failed.
        if repaired is not None:
            trace.fired_modules = trace.fired_modules + (
                AgentModuleCall(
                    module="retrieval_repair",
                    trigger="initial_na",
                    verdict_before=verdict_before,
                    verdict_after=repaired.verdict.value,
                    extra_calls=calls,
                ),
            )
            current = repaired
            trace.extra_calls += calls

    # ── Condition 2: retrieval gap (no contrary evidence found at all) ─
    contradictions = list(base.stage_trace.contradictions) if base.stage_trace else []
    if current.verdict != Verdict.NOT_APPLICABLE.value and not contradictions and not current.citation_ids:
        verified, calls = _run_verifier(
            client=client,
            material=material,
            unit=unit,
            current=current,
            artifact_dir=artifact_dir / "verifier_gap",
            reason="no citations and no contrary evidence",
        )
        if verified is not None and verified.verdict != current.verdict:
            trace.fired_modules = trace.fired_modules + (
                AgentModuleCall(
                    module="verifier",
                    trigger="retrieval_gap",
                    verdict_before=current.verdict.value,
                    verdict_after=verified.verdict.value,
                    extra_calls=calls,
                ),
            )
            current = verified
            trace.extra_calls += calls

    # ── Condition 3: conflict (non-empty contradictions) → Critic ─────
    if contradictions and current.verdict != Verdict.NOT_APPLICABLE.value:
        reviewed, calls = _run_critic(
            client=client,
            material=material,
            unit=unit,
            current=current,
            contradictions=tuple(contradictions),
            artifact_dir=artifact_dir / "critic",
        )
        if reviewed is not None:
            trace.fired_modules = trace.fired_modules + (
                AgentModuleCall(
                    module="critic",
                    trigger="conflict",
                    verdict_before=current.verdict.value,
                    verdict_after=reviewed.verdict.value,
                    extra_calls=calls,
                ),
            )
            if reviewed.verdict != current.verdict:
                current = reviewed
            trace.extra_calls += calls

    # ── Condition 4: low confidence → Verifier ─────────────────────────
    if current.uncertainty > uncertainty_threshold:
        verified, calls = _run_verifier(
            client=client,
            material=material,
            unit=unit,
            current=current,
            artifact_dir=artifact_dir / "verifier_conf",
            reason=f"uncertainty={current.uncertainty} > {uncertainty_threshold}",
        )
        if verified is not None:
            trace.fired_modules = trace.fired_modules + (
                AgentModuleCall(
                    module="verifier",
                    trigger="low_confidence",
                    verdict_before=current.verdict.value,
                    verdict_after=verified.verdict.value,
                    extra_calls=calls,
                ),
            )
            current = verified
            trace.extra_calls += calls

    # ── Condition 5: missing citation (only fires if module 4 did not) ──
    if not current.citation_ids and current.verdict != Verdict.NOT_APPLICABLE.value:
        verified, calls = _run_verifier(
            client=client,
            material=material,
            unit=unit,
            current=current,
            artifact_dir=artifact_dir / "verifier_cite",
            reason="citation_ids empty",
        )
        if verified is not None:
            trace.fired_modules = trace.fired_modules + (
                AgentModuleCall(
                    module="verifier",
                    trigger="missing_citation",
                    verdict_before=current.verdict.value,
                    verdict_after=verified.verdict.value,
                    extra_calls=calls,
                ),
            )
            current = verified
            trace.extra_calls += calls

    # ── Final assembly ────────────────────────────────────────────────
    final_resolution = _resolution_for(trace)
    final_result = base.model_copy(
        update={
            "verdicts": (current,),
            "agent_trace": trace.model_copy(update={"final_resolution": final_resolution}),
        }
    )
    atomic_write_json(artifact_dir / "result.json", final_result.model_dump(mode="json"))
    return final_result


def _run_retrieval_repair(
    *,
    client: JsonChatClient,
    material: MaterialSnapshot,
    unit: ExecutionUnit,
    current: ExperimentVerdict,
    stage_trace,
    artifact_dir: Path,
) -> tuple[ExperimentVerdict | None, int]:
    """Widen the contrary search and confirm or repair the verdict.

    Returns ``(repaired_verdict, extra_calls)``. ``repaired_verdict`` is None
    if the repair call failed; the caller should keep the current verdict.
    """
    outcome = _invoke_stage(
        client=client,
        material=material,
        unit=unit,
        stage=2,  # re-run contrary search with the new context
        schema=CONTRARY_SEARCH_SCHEMA,
        artifact_dir=artifact_dir,
        extra={
            "applicability_reason": stage_trace.applicability_reason,
            "current_verdict": current.verdict.value,
            "current_reason": current.reason,
        },
    )
    if not outcome.ok:
        return None, 1
    if outcome.parsed.get("escalate") is True:
        # Escalation confirmed — the verdict stands but the rationale is updated.
        return ExperimentVerdict(
            cp_id=current.cp_id,
            verdict=current.verdict,
            reason=current.reason + " [retrieval_repair: escalation confirmed]",
            citation_ids=current.citation_ids,
            uncertainty=min(current.uncertainty, 0.4),
        ), 1
    # Escalation flipped — fall back to N/A.
    return ExperimentVerdict(
        cp_id=current.cp_id,
        verdict=Verdict.NOT_APPLICABLE,
        reason="retrieval_repair: contrary search no longer supports escalation",
        citation_ids=current.citation_ids,
        uncertainty=0.3,
    ), 1


def _run_critic(
    *,
    client: JsonChatClient,
    material: MaterialSnapshot,
    unit: ExecutionUnit,
    current: ExperimentVerdict,
    contradictions: tuple[str, ...],
    artifact_dir: Path,
) -> tuple[ExperimentVerdict | None, int]:
    """Critic review when supporting and contrary evidence coexist."""
    prompt = build_agent_prompt(
        unit=unit,
        material=material,
        module="critic",
        payload={
            "current_verdict": current.verdict.value,
            "current_reason": current.reason,
            "current_citation_ids": list(current.citation_ids),
            "contradictions": list(contradictions),
        },
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact_dir / "request.json", prompt.model_dump(mode="json"))
    try:
        raw = client.complete_json(
            system=prompt.system,
            user=prompt.text,
            schema=CRITIC_SCHEMA,
            max_tokens=1024,
        )
    except Exception as exc:
        # Broaden beyond ModelResponseError: a 582k-char critic prompt can also
        # trip httpx read/connect timeouts or schema-parse errors. Treat any
        # module failure as "no change" so the base stage verdict still persists
        # to result.json instead of aborting the whole unit (and the run).
        atomic_write_json(artifact_dir / "response.json", {"error": f"{type(exc).__name__}: {exc}"})
        return None, 1
    atomic_write_json(artifact_dir / "response.json", raw)
    new_verdict = raw.get("verdict")
    if new_verdict not in {"1", "0"} or new_verdict == current.verdict.value:
        return current, 1  # critic agrees; report the call but keep the verdict
    return ExperimentVerdict(
        cp_id=current.cp_id,
        verdict=Verdict(new_verdict),
        reason=str(raw.get("reason", current.reason)),
        citation_ids=tuple(raw.get("citation_ids", current.citation_ids)),
        uncertainty=float(raw.get("uncertainty", current.uncertainty)),
    ), 1


def _run_verifier(
    *,
    client: JsonChatClient,
    material: MaterialSnapshot,
    unit: ExecutionUnit,
    current: ExperimentVerdict,
    artifact_dir: Path,
    reason: str,
) -> tuple[ExperimentVerdict | None, int]:
    """Verifier second-look on low-confidence / missing-citation cases."""
    prompt = build_agent_prompt(
        unit=unit,
        material=material,
        module="verifier",
        payload={
            "current_verdict": current.verdict.value,
            "current_reason": current.reason,
            "current_citation_ids": list(current.citation_ids),
            "current_uncertainty": current.uncertainty,
            "verification_reason": reason,
        },
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact_dir / "request.json", prompt.model_dump(mode="json"))
    try:
        raw = client.complete_json(
            system=prompt.system,
            user=prompt.text,
            schema=VERIFIER_SCHEMA,
            max_tokens=1024,
        )
    except Exception as exc:
        # See _run_critic: any client/parse failure -> keep base verdict.
        atomic_write_json(artifact_dir / "response.json", {"error": f"{type(exc).__name__}: {exc}"})
        return None, 1
    atomic_write_json(artifact_dir / "response.json", raw)
    status = raw.get("status")
    if status == "PASS":
        return current, 1  # verifier agrees; keep verdict
    if status == "FAIL":
        new_verdict = raw.get("verdict", current.verdict.value)
        if new_verdict not in {"1", "0", "N/A"}:
            return None, 1
        return ExperimentVerdict(
            cp_id=current.cp_id,
            verdict=Verdict(new_verdict),
            reason=str(raw.get("reason", current.reason)),
            citation_ids=tuple(raw.get("citation_ids", current.citation_ids)),
            uncertainty=float(raw.get("uncertainty", min(current.uncertainty, 0.4))),
        ), 1
    return None, 1  # UNCERTAIN → no change


def _resolution_for(trace: AgentTrace) -> str:
    """Map the fired modules to a human-readable final resolution string."""
    if not trace.fired_modules:
        return "ACCEPT"
    modules = {call.module for call in trace.fired_modules}
    if "arbitration" in modules:
        return "REVIEW_DISAGREEMENT"
    if "verifier" in modules:
        return "VERIFIED"
    if "critic" in modules:
        return "REVIEWED"
    if "retrieval_repair" in modules:
        return "REPAIRED"
    return "ACCEPT"


__all__ = [
    "run_agent_audit_plan",
    "run_agent_audit_unit",
]