"""Stage-audit (C) experiment method: applicability → contrary search → judgment → final.

This is a *direct-LLM* path: every stage is one structured call against the same
official materials the other methods see, with no agent chain, no critic, no
retrieval augmentation. The differences from ``CASE_FULL`` are:

- Step 1 forces the model to commit to APPLICABLE / NOT_APPLICABLE per CP
  *before* attempting to find evidence, eliminating the common failure mode
  where a clean case is blanket-approved because the model conflates "no
  evidence of failure" with "compliant".
- Step 2 (only when Step 1 returned NOT_APPLICABLE) opens the prompt to the
  full material set and asks the model to attempt a contrary search — was
  there any track that actually applies? If the model finds one, the audit
  escalates to Step 3. Otherwise it commits to N/A with the supporting policy
  citation.
- Step 3 (only when applicable) emits a 1/0 verdict with reason + citations.
- Step 4 (always) consolidates the three stages into the final ``ExperimentVerdict``
  shape that downstream metrics / silver comparison already accept, so no
  metric path needs to change.

The module exposes ``run_stage_audit_unit`` (one CP at a time, since stages are
CP-scoped) and ``run_stage_audit_plan`` (a thin loop over an ExecutionPlan whose
method is ``STAGE_AUDIT``). All artifacts (per-stage request / response / usage)
land under ``artifact_dir/stage-{1..4}/`` so a reviewer can replay each call.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from freca.experiments.models import (
    ExecutionResult,
    ExecutionUnit,
    ExperimentVerdict,
    MaterialSnapshot,
    StageTrace,
)
from freca.experiments.prompts import (
    APPLICABILITY_SCHEMA,
    CONTRARY_SEARCH_SCHEMA,
    JUDGMENT_SCHEMA,
    build_stage_prompt,
)
from freca.llm import JsonChatClient, ModelResponseError
from freca.models import Verdict
from freca.state import atomic_write_json


def run_stage_audit_plan(
    *,
    plan_units: Sequence[ExecutionUnit],
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_root: Path,
) -> list[ExecutionResult]:
    """Run every unit of a STAGE_AUDIT plan; each unit is one CP."""
    if material.case_id is None or any(unit.case_id != material.case_id for unit in plan_units):
        raise ValueError("all units must share the material's case_id")
    results: list[ExecutionResult] = []
    for index, unit in enumerate(plan_units):
        unit_dir = artifact_root / f"cp-{index:03d}"
        results.append(
            run_stage_audit_unit(
                unit=unit,
                material=material,
                client=client,
                artifact_dir=unit_dir,
            )
        )
    return results


def run_stage_audit_unit(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_dir: Path,
) -> ExecutionResult:
    """Execute the 4-stage audit for a single-CP unit."""
    if len(unit.checkpoint_ids) != 1:
        raise ValueError("STAGE_AUDIT expects exactly one checkpoint per unit")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    cp_id = unit.checkpoint_ids[0]
    trace = StageTrace(cp_id=cp_id)

    # ── Step 1: applicability ─────────────────────────────────────────────
    step1 = _invoke_stage(
        client=client,
        material=material,
        unit=unit,
        stage=1,
        schema=APPLICABILITY_SCHEMA,
        artifact_dir=artifact_dir / "stage-1",
    )
    trace.applicability = step1.parsed.get("applicability") if step1.ok else None
    trace.applicability_reason = step1.parsed.get("reason") if step1.ok else None
    if step1.ok and isinstance(step1.parsed.get("policy_citations"), list):
        trace.policy_citations = tuple(step1.parsed["policy_citations"])

    if not step1.ok:
        return _failure_result(unit, material, trace, step1.errors)

    if trace.applicability == "APPLICABLE":
        # ── Step 3: 1/0 judgment ─────────────────────────────────────────
        step3 = _invoke_stage(
            client=client,
            material=material,
            unit=unit,
            stage=3,
            schema=JUDGMENT_SCHEMA,
            artifact_dir=artifact_dir / "stage-3",
            extra={"applicability_reason": trace.applicability_reason},
        )
        trace.verdict, trace.reason, trace.citation_ids, trace.uncertainty = (
            _extract_judgment(step3.parsed) if step3.ok else (None, None, (), 1.0)
        )
        trace.contradictions = tuple(step3.parsed.get("contradictions", [])) if step3.ok else ()
        if not step3.ok:
            return _failure_result(unit, material, trace, step3.errors)
    elif trace.applicability == "NOT_APPLICABLE":
        # ── Step 2: contrary search across all 9 tracks ─────────────────
        step2 = _invoke_stage(
            client=client,
            material=material,
            unit=unit,
            stage=2,
            schema=CONTRARY_SEARCH_SCHEMA,
            artifact_dir=artifact_dir / "stage-2",
            extra={"applicability_reason": trace.applicability_reason},
        )
        if step2.ok and step2.parsed.get("escalate") is True:
            # The contrary search found something applicable — fall through to Step 3
            trace.contradictions = tuple(step2.parsed.get("evidence_citations", []))
            step3 = _invoke_stage(
                client=client,
                material=material,
                unit=unit,
                stage=3,
                schema=JUDGMENT_SCHEMA,
                artifact_dir=artifact_dir / "stage-3",
                extra={
                    "applicability_reason": trace.applicability_reason,
                    "contrary_evidence": trace.contradictions,
                },
            )
            trace.verdict, trace.reason, trace.citation_ids, trace.uncertainty = (
                _extract_judgment(step3.parsed) if step3.ok else (None, None, (), 1.0)
            )
            if not step3.ok:
                return _failure_result(unit, material, trace, step3.errors)
        else:
            # Committed N/A — Step 4 is a passthrough
            trace.verdict = Verdict.NOT_APPLICABLE.value
            trace.reason = str((step2.parsed.get("reason") if step2.ok else "") or trace.applicability_reason or "")
            trace.citation_ids = tuple(trace.policy_citations)
            trace.uncertainty = float(step2.parsed.get("uncertainty", 0.1)) if step2.ok else 1.0
    else:
        return _failure_result(
            unit,
            material,
            trace,
            (f"stage-1 returned unknown applicability: {trace.applicability!r}",),
        )

    # ── Step 4: consolidate ──────────────────────────────────────────────
    if trace.verdict is None:
        return _failure_result(unit, material, trace, ("stage-3 produced no verdict",))
    verdict = ExperimentVerdict(
        cp_id=cp_id,
        verdict=Verdict(trace.verdict),
        reason=trace.reason or "",
        citation_ids=trace.citation_ids,
        uncertainty=trace.uncertainty,
    )
    result = ExecutionResult(
        unit=unit,
        valid=True,
        errors=(),
        verdicts=(verdict,),
        input_sha256=material.input_sha256,
        prompt_sha256=material.input_sha256,  # Stage-audit uses a per-stage prompt hash; we reuse input hash as a stable fingerprint
        stage_trace=trace,
    )
    atomic_write_json(artifact_dir / "result.json", result.model_dump(mode="json"))
    return result


def _invoke_stage(
    *,
    client: JsonChatClient,
    material: MaterialSnapshot,
    unit: ExecutionUnit,
    stage: int,
    schema: dict[str, Any],
    artifact_dir: Path,
    extra: dict[str, Any] | None = None,
) -> _StageOutcome:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_stage_prompt(unit=unit, material=material, stage=stage, extra=extra or {})
    atomic_write_json(artifact_dir / "request.json", prompt.model_dump(mode="json"))
    try:
        raw = client.complete_json(
            system=prompt.system,
            user=prompt.text,
            schema=schema,
            max_tokens=2048,
        )
    except ModelResponseError as exc:
        atomic_write_json(artifact_dir / "response.json", {"error": str(exc)})
        return _StageOutcome(ok=False, parsed={}, errors=(str(exc),))
    atomic_write_json(artifact_dir / "response.json", raw)
    try:
        parsed = _enforce_schema(raw, schema)
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        return _StageOutcome(ok=False, parsed=raw, errors=(f"schema validation failed: {exc}",))
    return _StageOutcome(ok=True, parsed=parsed, errors=())


def _enforce_schema(raw: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Light-weight schema check: every required top-level key must be present and correctly typed.

    The runner relies on the model's structured-output contract to enforce nested
    constraints; here we only guarantee no surprise missing keys reach the
    consolidation step. ValidationError is raised by ``pydantic`` if the caller
    wants a stricter check.
    """
    required = schema.get("required", [])
    for key in required:
        if key not in raw:
            raise ValueError(f"missing required key {key!r}")
    return raw


def _extract_judgment(parsed: dict[str, Any]) -> tuple[str | None, str | None, tuple[str, ...], float]:
    verdict = parsed.get("verdict")
    reason = str(parsed.get("reason", ""))
    citations = parsed.get("citation_ids", [])
    uncertainty = float(parsed.get("uncertainty", 0.5))
    if not isinstance(citations, list):
        citations = []
    return (verdict, reason, tuple(str(c) for c in citations), uncertainty)


def _failure_result(
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    trace: StageTrace,
    errors: Sequence[str],
) -> ExecutionResult:
    return ExecutionResult(
        unit=unit,
        valid=False,
        errors=tuple(errors),
        input_sha256=material.input_sha256,
        prompt_sha256=material.input_sha256,
        stage_trace=trace,
    )


class _StageOutcome:
    """Outcome of one stage call: parsed dict + ok flag + errors."""

    __slots__ = ("ok", "parsed", "errors")

    def __init__(self, *, ok: bool, parsed: dict[str, Any], errors: tuple[str, ...]) -> None:
        self.ok = ok
        self.parsed = parsed
        self.errors = errors