"""Tests for the Self-Improving Audit Harness (L1-L4 + cycle)."""
from __future__ import annotations

from pathlib import Path

import pytest

from freca.experiments.harness import (
    HarnessConfig,
    HarnessConfigPatch,
    PATCHABLE_FIELDS,
    config_metrics,
)
from freca.experiments.harness import (
    FailureMode,
    analyze_failures,
)
from freca.experiments.harness import (
    HarnessProposal,
    propose_harness_changes,
)
from freca.experiments.harness import (
    _decide_acceptance,
    run_regression,
)
from freca.experiments.harness import (
    RegressionResult,
    run_harness_cycle,
)
from freca.llm import ReplayJsonClient
from freca.experiments.models import (
    ExecutionResult,
    ExecutionUnit,
    ExperimentMethod,
    ExperimentVerdict,
    SilverEntry,
    SilverReference,
    SilverTier,
    Track3Condition,
)
from freca.models import CheckpointDefinition, Verdict


def _cp(cp_id: str, element_id: int = 1) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=element_id,
        element_title=f"Element-{element_id}",
        section_title="section",
        text=f"checkpoint {cp_id}",
        source_file="cp.xlsx",
        cell="A1",
    )


def _verdict(cp_id: str, verdict: str) -> ExperimentVerdict:
    return ExperimentVerdict(
        cp_id=cp_id,
        verdict=Verdict(verdict),
        reason="r",
        citation_ids=("case:1:track1",),
        uncertainty=0.1,
    )


def _result(case_id: int, values: dict[str, str], element_map: dict[str, int] | None = None) -> ExecutionResult:
    checkpoints = [_cp(cp_id, (element_map or {}).get(cp_id, 1)) for cp_id in values]
    return ExecutionResult(
        unit=ExecutionUnit(case_id=case_id, method=ExperimentMethod.CASE_FULL, checkpoint_ids=tuple(values)),
        valid=True,
        verdicts=tuple(_verdict(cp_id, v) for cp_id, v in values.items()),
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )


# ── L1 ───────────────────────────────────────────────────────────────────────


def test_patch_only_overlays_non_none_fields() -> None:
    base = HarnessConfig()
    patch = HarnessConfigPatch(per_scope_limit=25)
    patched = patch.applied_to(base)
    assert patched.per_scope_limit == 25
    assert patched.method == base.method  # unchanged
    assert patched.track3_condition == base.track3_condition


def test_patch_rejects_unknown_fields() -> None:
    with pytest.raises(Exception):
        HarnessConfigPatch.model_validate({"per_scope_limit": 5, "sneaky_rule": "CP23=0"})


def test_patchable_fields_does_not_include_prompt_text() -> None:
    # The read-only boundary: prompt text / CP rules are never patchable.
    assert "method" in PATCHABLE_FIELDS
    assert "prompt" not in PATCHABLE_FIELDS
    assert "cp_rules" not in PATCHABLE_FIELDS


def test_config_metrics_aggregates_overall_and_per_element() -> None:
    checkpoints = [_cp("CP1", 1), _cp("CP2", 1), _cp("CP3", 2)]
    silver = SilverReference(
        entries={
            "1": {
                "CP1": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
                "CP2": SilverEntry(verdict=Verdict.NON_COMPLIANT, tier=SilverTier.HUMAN),
                "CP3": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
            }
        }
    )
    # CP1 correct (1==1), CP2 wrong (1!=0), CP3 correct (1==1) -> overall 2/3
    result = _result(1, {"CP1": "1", "CP2": "1", "CP3": "1"})
    overall, per_element = config_metrics(results=[result], checkpoints=checkpoints, silver=silver)
    assert overall == pytest.approx(2 / 3)
    assert per_element[1] == pytest.approx(0.5)  # 1 correct of 2
    assert per_element[2] == pytest.approx(1.0)  # 1 correct of 1


def test_config_metrics_ignores_unanchored_cases() -> None:
    checkpoints = [_cp("CP1"), _cp("CP2")]
    silver = SilverReference(entries={"1": {"CP1": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN)}})
    # case 1 has CP1 anchored; case 2 has no silver -> ignored entirely
    results = [
        _result(1, {"CP1": "1", "CP2": "1"}),
        _result(2, {"CP1": "1", "CP2": "1"}),
    ]
    overall, per_element = config_metrics(results=results, checkpoints=checkpoints, silver=silver)
    assert overall == 1.0  # only CP1 on case 1 counts, and it matches


# ── L2 ───────────────────────────────────────────────────────────────────────


def _silver(case_id: int, entries: dict[str, Verdict]) -> SilverReference:
    return SilverReference(
        entries={
            str(case_id): {
                cp_id: SilverEntry(verdict=v, tier=SilverTier.HUMAN) for cp_id, v in entries.items()
            }
        }
    )


def test_analyze_failures_labels_na_misjudge() -> None:
    checkpoints = [_cp("CP1"), _cp("CP2")]
    silver = _silver(1, {"CP1": Verdict.COMPLIANT, "CP2": Verdict.NOT_APPLICABLE})
    # Candidate says N/A on CP1 (silver=1) and 1 on CP2 (silver=N/A) -> both NA_MISJUDGE
    result = _result(1, {"CP1": "N/A", "CP2": "1"})
    report = analyze_failures(results=[result], checkpoints=checkpoints, silver=silver)
    assert report.total_anchored == 2
    assert report.total_wrong == 2
    assert report.mode_counts.get(FailureMode.NA_MISJUDGE) == 2


def test_analyze_failures_labels_conflict_unresolved_when_no_critic() -> None:
    checkpoints = [_cp("CP1")]
    silver = _silver(1, {"CP1": Verdict.NON_COMPLIANT})
    # Candidate says 1 (wrong) with citations but stage_trace has contradictions and no critic fired.
    from freca.experiments.models import StageTrace

    result = ExecutionResult(
        unit=ExecutionUnit(case_id=1, method=ExperimentMethod.AGENT_AUDIT, checkpoint_ids=("CP1",)),
        valid=True,
        verdicts=(ExperimentVerdict(cp_id="CP1", verdict=Verdict.COMPLIANT, reason="r", citation_ids=("case:1:track1",), uncertainty=0.2),),
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
        stage_trace=StageTrace(cp_id="CP1", applicability="APPLICABLE", contradictions=("case:1:track2",)),
        agent_trace=None,  # no critic fired
    )
    report = analyze_failures(results=[result], checkpoints=checkpoints, silver=silver)
    modes = report.instances[0].modes
    assert FailureMode.CONFLICT_UNRESOLVED in modes


def test_analyze_failures_labels_cross_cp_inconsistency() -> None:
    checkpoints = [_cp("CP1", 1), _cp("CP2", 1)]
    silver = _silver(1, {"CP1": Verdict.NON_COMPLIANT, "CP2": Verdict.NON_COMPLIANT})
    # Both CPs cite the same chunk but reach OPPOSITE verdicts (CP1=1, CP2=0).
    # CP1 is wrong (silver=0) and inconsistent with CP2 on the same evidence.
    shared_cite = "case:1:track1"
    result = ExecutionResult(
        unit=ExecutionUnit(case_id=1, method=ExperimentMethod.CASE_FULL, checkpoint_ids=("CP1", "CP2")),
        valid=True,
        verdicts=(
            ExperimentVerdict(cp_id="CP1", verdict=Verdict.COMPLIANT, reason="r", citation_ids=(shared_cite,), uncertainty=0.1),
            ExperimentVerdict(cp_id="CP2", verdict=Verdict.NON_COMPLIANT, reason="r", citation_ids=(shared_cite,), uncertainty=0.1),
        ),
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )
    report = analyze_failures(results=[result], checkpoints=checkpoints, silver=silver)
    cp1_instance = next(i for i in report.instances if i.cp_id == "CP1")
    assert FailureMode.CROSS_CP_INCONSISTENT in cp1_instance.modes


def test_analyze_failures_skips_correct_and_unanchored() -> None:
    checkpoints = [_cp("CP1"), _cp("CP2"), _cp("CP3")]
    silver = _silver(1, {"CP1": Verdict.COMPLIANT})  # only CP1 anchored
    result = _result(1, {"CP1": "1", "CP2": "0", "CP3": "N/A"})
    report = analyze_failures(results=[result], checkpoints=checkpoints, silver=silver)
    assert report.total_anchored == 1
    assert report.total_wrong == 0
    assert report.instances == ()


# ── L3 ───────────────────────────────────────────────────────────────────────


def _failure_report(wrong: int = 3) -> "FailureReport":
    from freca.experiments.harness import FailureReport, FailureInstance

    return FailureReport(
        total_anchored=10,
        total_wrong=wrong,
        mode_counts={FailureMode.RETRIEVAL_GAP: wrong},
        instances=(
            FailureInstance(
                case_id=1, cp_id="CP1", element_id=1, candidate_verdict="1", silver_verdict="0", modes=(FailureMode.RETRIEVAL_GAP,)
            ),
        ),
    )


def test_proposal_to_patch_rejects_non_whitelisted_field() -> None:
    # A proposal that tries to edit prompt_text (a read-only boundary) must be
    # rejected at to_patch time, even if the Agent emitted it.
    hack = HarnessProposal(field="prompt_text", value="CP23 with <2yr records = 0", rationale="hack the gold")
    with pytest.raises(ValueError):
        hack.to_patch()


def test_proposer_emits_valid_patch_for_retrieval_gap() -> None:
    client = ReplayJsonClient(
        responses=[
            {
                "field": "per_scope_limit",
                "value": 25,
                "rationale": "widen retrieval depth to address retrieval_gap",
                "targeted_failure_modes": [FailureMode.RETRIEVAL_GAP],
            }
        ]
    )
    proposal = propose_harness_changes(
        failure_report=_failure_report(),
        current_config=HarnessConfig(),
        client=client,
    )
    assert proposal.field == "per_scope_limit"
    assert proposal.value == 25
    assert not proposal.is_noop
    patch = proposal.to_patch()
    assert patch.per_scope_limit == 25


def test_proposer_downgrades_out_of_range_value_to_noop() -> None:
    # per_scope_limit max is 50; 999 must be rejected into a no-op, not crash.
    client = ReplayJsonClient(
        responses=[{"field": "per_scope_limit", "value": 999, "rationale": "too big", "targeted_failure_modes": []}]
    )
    proposal = propose_harness_changes(
        failure_report=_failure_report(),
        current_config=HarnessConfig(),
        client=client,
    )
    assert proposal.is_noop
    assert "rejected" in proposal.rationale or "no change" in proposal.rationale


def test_proposer_downgrades_unknown_field_to_noop() -> None:
    # The Agent tries to sneak in a CP-specific rule field; must become no-op.
    client = ReplayJsonClient(
        responses=[{"field": "cp23_rule", "value": "records<2yr=0", "rationale": "sneaky", "targeted_failure_modes": []}]
    )
    proposal = propose_harness_changes(
        failure_report=_failure_report(),
        current_config=HarnessConfig(),
        client=client,
    )
    assert proposal.is_noop
    assert "outside the editable surface" in proposal.rationale


def test_proposer_noop_when_field_is_null() -> None:
    client = ReplayJsonClient(
        responses=[{"field": None, "value": None, "rationale": "no change warranted", "targeted_failure_modes": []}]
    )
    proposal = propose_harness_changes(
        failure_report=_failure_report(wrong=0),
        current_config=HarnessConfig(),
        client=client,
    )
    assert proposal.is_noop


# ── L4 ───────────────────────────────────────────────────────────────────────


def test_acceptance_gate_accepts_pure_improvement() -> None:
    accepted, reason = _decide_acceptance(
        baseline_held_out_accuracy=0.70,
        patched_held_out_accuracy=0.80,
        regressed_elements=[],
        element_regression_tolerance=0.05,
    )
    assert accepted is True
    assert "accepted" in reason


def test_acceptance_gate_rejects_when_held_out_does_not_improve() -> None:
    accepted, reason = _decide_acceptance(
        baseline_held_out_accuracy=0.80,
        patched_held_out_accuracy=0.80,
        regressed_elements=[],
        element_regression_tolerance=0.05,
    )
    assert accepted is False
    assert "did not improve" in reason


def test_acceptance_gate_rejects_when_element_regresses_despite_overall_gain() -> None:
    # Overall lifts 0.70 -> 0.75, but Element 3 drops past tolerance.
    accepted, reason = _decide_acceptance(
        baseline_held_out_accuracy=0.70,
        patched_held_out_accuracy=0.75,
        regressed_elements=["3"],
        element_regression_tolerance=0.05,
    )
    assert accepted is False
    assert "Element" in reason and "regressed" in reason


def test_run_regression_noop_proposal_short_circuits() -> None:
    # A no-op proposal must not trigger any model calls.
    client = ReplayJsonClient(responses=[])
    proposal = HarnessProposal(field=None, value=None, rationale="no change")
    result = run_regression(
        proposal=proposal,
        baseline_config=HarnessConfig(),
        held_in_case_ids=(1,),
        held_out_case_ids=(2,),
        checkpoints=[_cp("CP1")],
        parsed_dir=Path("/nonexistent"),
        client=client,
        silver=_silver(1, {"CP1": Verdict.COMPLIANT}),
        artifact_root=Path("/tmp/nonexistent"),
    )
    assert result.accepted is False
    assert result.reason == "no-op proposal; nothing to regress"
    assert len(client.requests) == 0


def test_run_regression_rejects_non_whitelisted_proposal() -> None:
    # A proposal whose field is outside the whitelist is rejected before any run.
    client = ReplayJsonClient(responses=[])
    proposal = HarnessProposal(field="prompt_text", value="CP23=0", rationale="hack")
    result = run_regression(
        proposal=proposal,
        baseline_config=HarnessConfig(),
        held_in_case_ids=(1,),
        held_out_case_ids=(2,),
        checkpoints=[_cp("CP1")],
        parsed_dir=Path("/nonexistent"),
        client=client,
        silver=_silver(1, {"CP1": Verdict.COMPLIANT}),
        artifact_root=Path("/tmp/nonexistent"),
    )
    assert result.accepted is False
    assert "rejected at patch validation" in result.reason
    assert len(client.requests) == 0


# ── Closed loop ──────────────────────────────────────────────────────────────


def test_harness_cycle_accepts_improving_patch(monkeypatch, tmp_path: Path) -> None:
    """End-to-end: a patch that lifts held-out accuracy is folded into the config."""
    import freca.experiments.harness as harness_mod

    # Stub run_with_config to return canned results driven by the config's
    # per_scope_limit. baseline (12) -> wrong; patched (25) -> correct.
    def fake_run_with_config(*, config, case_ids, checkpoints, parsed_dir, client, artifact_root):
        # Hold-out accuracy is gated on per_scope_limit: 12 = wrong, 25 = right.
        results = []
        for case_id in case_ids:
            verdict_val = "1" if config.per_scope_limit >= 25 else "0"
            results.append(_result(case_id, {"CP1": verdict_val}))
        return results

    # Stub run_regression so we control acceptance without 4x run_with_config.
    call_count = {"n": 0}

    def fake_run_regression(*, proposal, baseline_config, **_):
        call_count["n"] += 1
        # First (and only) proposal: per_scope_limit=25 -> accepted.
        return RegressionResult(
            proposal=proposal,
            accepted=True,
            reason="accepted: held-out 0.0 -> 1.0",
            baseline_held_in_accuracy=0.0,
            baseline_held_out_accuracy=0.0,
            patched_held_in_accuracy=1.0,
            patched_held_out_accuracy=1.0,
            element_deltas={"1": 1.0},
        )

    monkeypatch.setattr(harness_mod, "run_with_config", fake_run_with_config)
    monkeypatch.setattr(harness_mod, "run_regression", fake_run_regression)

    proposer_client = ReplayJsonClient(
        responses=[{"field": "per_scope_limit", "value": 25, "rationale": "widen retrieval", "targeted_failure_modes": ["retrieval_gap"]}]
    )

    result = run_harness_cycle(
        baseline_config=HarnessConfig(),
        held_in_case_ids=(1,),
        held_out_case_ids=(2,),
        checkpoints=[_cp("CP1")],
        parsed_dir=tmp_path,
        client=proposer_client,
        silver=_silver(1, {"CP1": Verdict.COMPLIANT}),
        artifact_root=tmp_path / "cycle",
        max_iterations=1,
    )
    assert result.accepted_count == 1
    assert result.final_config.per_scope_limit == 25
    assert result.iterations[0].accepted is True


def test_harness_cycle_stops_on_noop_proposal(monkeypatch, tmp_path: Path) -> None:
    import freca.experiments.harness as harness_mod

    def fake_run_with_config(*, config, case_ids, **_):
        # Always returns a wrong result so failures exist and the Proposer is called.
        return [_result(cid, {"CP1": "0"}) for cid in case_ids]

    monkeypatch.setattr(harness_mod, "run_with_config", fake_run_with_config)
    # Proposer returns a no-op on the first call.
    proposer_client = ReplayJsonClient(
        responses=[{"field": None, "value": None, "rationale": "nothing to try", "targeted_failure_modes": []}]
    )

    result = run_harness_cycle(
        baseline_config=HarnessConfig(),
        held_in_case_ids=(1,),
        held_out_case_ids=(2,),
        checkpoints=[_cp("CP1")],
        parsed_dir=tmp_path,
        client=proposer_client,
        silver=_silver(1, {"CP1": Verdict.COMPLIANT}),
        artifact_root=tmp_path / "cycle",
        max_iterations=3,
    )
    assert result.accepted_count == 0
    assert "no-op" in result.stopped_reason
    assert result.final_config == result.baseline_config


def test_harness_cycle_stops_after_two_consecutive_rejections(monkeypatch, tmp_path: Path) -> None:
    import freca.experiments.harness as harness_mod

    def fake_run_with_config(*, config, case_ids, **_):
        return [_result(cid, {"CP1": "0"}) for cid in case_ids]

    def fake_run_regression(*, proposal, baseline_config, **_):
        return RegressionResult(
            proposal=proposal,
            accepted=False,
            reason="held-out accuracy did not improve (0.0 -> 0.0)",
            baseline_held_in_accuracy=0.0,
            baseline_held_out_accuracy=0.0,
            patched_held_in_accuracy=0.0,
            patched_held_out_accuracy=0.0,
        )

    monkeypatch.setattr(harness_mod, "run_with_config", fake_run_with_config)
    monkeypatch.setattr(harness_mod, "run_regression", fake_run_regression)

    proposer_client = ReplayJsonClient(
        responses=[
            {"field": "per_scope_limit", "value": 20, "rationale": "try wider", "targeted_failure_modes": []},
            {"field": "uncertainty_threshold", "value": 0.3, "rationale": "try lower threshold", "targeted_failure_modes": []},
        ]
    )

    result = run_harness_cycle(
        baseline_config=HarnessConfig(),
        held_in_case_ids=(1,),
        held_out_case_ids=(2,),
        checkpoints=[_cp("CP1")],
        parsed_dir=tmp_path,
        client=proposer_client,
        silver=_silver(1, {"CP1": Verdict.COMPLIANT}),
        artifact_root=tmp_path / "cycle",
        max_iterations=5,
    )
    assert result.accepted_count == 0
    assert "two consecutive rejections" in result.stopped_reason
    assert len(result.iterations) == 2


def test_harness_cycle_stops_when_no_failures_left(monkeypatch, tmp_path: Path) -> None:
    import freca.experiments.harness as harness_mod

    def fake_run_with_config(*, config, case_ids, **_):
        # All correct -> no failures -> loop stops before proposing.
        return [_result(cid, {"CP1": "1"}) for cid in case_ids]

    monkeypatch.setattr(harness_mod, "run_with_config", fake_run_with_config)
    proposer_client = ReplayJsonClient(responses=[])  # never called

    result = run_harness_cycle(
        baseline_config=HarnessConfig(),
        held_in_case_ids=(1,),
        held_out_case_ids=(2,),
        checkpoints=[_cp("CP1")],
        parsed_dir=tmp_path,
        client=proposer_client,
        silver=_silver(1, {"CP1": Verdict.COMPLIANT}),
        artifact_root=tmp_path / "cycle",
        max_iterations=3,
    )
    assert result.accepted_count == 0
    assert "no failures" in result.stopped_reason
    assert len(proposer_client.requests) == 0
