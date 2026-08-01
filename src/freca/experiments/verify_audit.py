"""Verify-audit experiment method: one-shot base + unconditional per-CP verify.

VERIFY_AUDIT is the "always review" counterpart to AGENT_AUDIT's conditional
review. AGENT_AUDIT fires its verifier only on low-confidence / missing-
citation / conflict CPs; VERIFY_AUDIT re-checks **every** checkpoint's verdict
with a verifier pass, regardless of confidence — including N/A verdicts.

Paired with AGENT_AUDIT, this answers whether the verify step should be gated
or unconditional, and at what token cost.

Flow (per case, one plan unit holding all CPs):

  1. One-shot base call — all CPs judged in a single call, reusing the
     CASE_FULL ``SYSTEM_PROMPT`` / ``VERDICT_SCHEMA`` runner
     (:func:`freca.experiments.runner.run_execution`).
  2. For every CP unconditionally — a verifier second-look
     (:func:`freca.experiments.agent_audit._run_verifier`) that may PASS (keep
     the verdict), FAIL (correct it), or be UNCERTAIN (keep it).

Each CP is emitted as its own :class:`ExecutionResult` carrying one verdict +
one :class:`AgentTrace` (the single verifier call, ``trigger="always"``). This
keeps the per-CP trace shape identical to AGENT_AUDIT, so the harness failure-
attribution (:func:`freca.experiments.harness.analyze_failures`) and the metric
pipeline work on VERIFY_AUDIT results **unchanged**. A verifier that looked and
changed nothing is recorded with ``verdict_before == verdict_after``, which the
harness naturally attributes to ``VERIFIER_NOOP`` — the meaningful "we double-
checked and still got it wrong" failure mode for an unconditional verify.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from freca.experiments.agent_audit import _run_verifier
from freca.experiments.models import (
    AgentModuleCall,
    AgentTrace,
    ExecutionResult,
    ExecutionUnit,
    MaterialSnapshot,
)
from freca.experiments.runner import run_execution
from freca.llm import JsonChatClient
from freca.state import atomic_write_json


def run_verify_audit_plan(
    *,
    plan_units: Sequence[ExecutionUnit],
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_root: Path,
) -> list[ExecutionResult]:
    """Run every unit of a VERIFY_AUDIT plan through base + unconditional verify.

    A VERIFY_AUDIT plan holds a single all-CPs unit (the one-shot base); this
    runs that base once and then verifies every CP, emitting one per-CP result.
    """
    results: list[ExecutionResult] = []
    for index, unit in enumerate(plan_units):
        unit_dir = artifact_root / f"case-{unit.case_id:03d}-unit-{index:03d}"
        results.extend(
            run_verify_audit_unit(
                unit=unit,
                material=material,
                client=client,
                artifact_dir=unit_dir,
            )
        )
    return results


def run_verify_audit_unit(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_dir: Path,
) -> list[ExecutionResult]:
    """One-shot base judgment over all CPs, then an unconditional per-CP verify.

    Returns one :class:`ExecutionResult` per CP (each with 1 verdict + 1
    :class:`AgentTrace`), so the result shape matches the per-CP methods and
    downstream metrics / harness work unchanged. If the base call fails, a
    single failure result is returned so the error propagates verbatim.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Step 1 — one-shot base judgment (all CPs in one call, like CASE_FULL).
    base = run_execution(
        unit=unit,
        material=material,
        client=client,
        artifact_dir=artifact_dir / "base",
    )
    if not base.valid or not base.verdicts:
        atomic_write_json(artifact_dir / "result.json", base.model_dump(mode="json"))
        return [base]

    # Step 2 — unconditional verifier pass on every CP.
    per_cp_results: list[ExecutionResult] = []
    for cp_index, base_verdict in enumerate(base.verdicts):
        # build_agent_prompt assumes one CP per unit, so build a per-CP view.
        cp_unit = ExecutionUnit(
            case_id=unit.case_id,
            method=unit.method,
            checkpoint_ids=(base_verdict.cp_id,),
        )
        verified, calls = _run_verifier(
            client=client,
            material=material,
            unit=cp_unit,
            current=base_verdict,
            artifact_dir=artifact_dir / f"verify-cp-{cp_index:03d}",
            reason="unconditional verify (always review)",
        )

        # Record the call whenever it ran; flip the verdict only on a real
        # change (FAIL with a different verdict). PASS / UNCERTAIN / call-error
        # all keep the base verdict but still log the verifier call.
        verdict_after = verified.verdict.value if verified is not None else base_verdict.verdict.value
        fired = AgentModuleCall(
            module="verifier",
            trigger="always",
            verdict_before=base_verdict.verdict.value,
            verdict_after=verdict_after,
            extra_calls=calls,
        )
        final_verdict = (
            verified
            if verified is not None and verified.verdict != base_verdict.verdict
            else base_verdict
        )
        trace = AgentTrace(
            cp_id=base_verdict.cp_id,
            fired_modules=(fired,),
            extra_calls=calls,
            final_resolution="VERIFIED",
        )
        per_cp_results.append(
            ExecutionResult(
                unit=cp_unit,
                valid=True,
                verdicts=(final_verdict,),
                input_sha256=material.input_sha256,
                prompt_sha256=base.prompt_sha256,
                agent_trace=trace,
            )
        )
        # Persist the per-CP final result so downstream readers (scoreboard /
        # agreement / harness) can reload it from disk - the in-memory return is
        # lost once the run exits. Mirrors how stage_audit / agent_audit write
        # one result.json per CP.
        atomic_write_json(
            artifact_dir / f"verify-cp-{cp_index:03d}" / "result.json",
            per_cp_results[-1].model_dump(mode="json"),
        )

    atomic_write_json(
        artifact_dir / "summary.json",
        {
            "method": unit.method.value,
            "case_id": unit.case_id,
            "cps_verified": len(per_cp_results),
            "flipped": sum(
                1
                for r in per_cp_results
                if r.agent_trace is not None
                and r.agent_trace.fired_modules
                and r.agent_trace.fired_modules[0].verdict_before
                != r.agent_trace.fired_modules[0].verdict_after
            ),
        },
    )
    return per_cp_results


__all__ = [
    "run_verify_audit_plan",
    "run_verify_audit_unit",
]
