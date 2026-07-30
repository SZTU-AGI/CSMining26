"""Tests for the layered silver-standard reference and comparison."""
from __future__ import annotations

import json
from pathlib import Path

from freca.experiments.evaluation import compare_to_silver
from freca.experiments.models import (
    ExecutionResult,
    ExecutionUnit,
    ExperimentMethod,
    ExperimentVerdict,
    SilverEntry,
    SilverReference,
    SilverTier,
)
from freca.experiments.silver import (
    add_weak_consensus,
    build_silver_from_anomaly_report,
    build_silver_reference,
    load_human_labels,
    merge_silver,
)
from freca.models import CheckpointDefinition, Verdict


def _cp(cp_id: str) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element-1",
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
        citation_ids=("a",),
        uncertainty=0.1,
    )


def _result(case_id: int, verdicts: tuple[ExperimentVerdict, ...]) -> ExecutionResult:
    return ExecutionResult(
        unit=ExecutionUnit(
            case_id=case_id,
            method=ExperimentMethod.CASE_FULL,
            checkpoint_ids=tuple(v.cp_id for v in verdicts),
        ),
        valid=True,
        errors=(),
        verdicts=verdicts,
        input_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )


def _anomaly_report(tmp_path: Path, anomaly_ids: list[int]) -> Path:
    cases = {str(i): {"anomaly_flag": True, "anomaly_reason": "x", "anomaly_verdict": "N/A"} for i in anomaly_ids}
    cases["1"] = {"anomaly_flag": False, "anomaly_reason": "", "anomaly_verdict": "pending"}
    report = {
        "summary": {
            "total": 100,
            "anomaly_count": len(anomaly_ids),
            "evaluated_count": 100 - len(anomaly_ids),
            "anomaly_case_ids": anomaly_ids,
        },
        "cases": cases,
    }
    path = tmp_path / "anomaly_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_anomaly_report_builds_all_na_across_all_cps(tmp_path: Path) -> None:
    checkpoints = [_cp(f"CP{i}") for i in range(1, 4)]
    report = _anomaly_report(tmp_path, [24, 100])

    entries = build_silver_from_anomaly_report(
        anomaly_report_path=report, checkpoints=checkpoints
    )

    assert set(entries.keys()) == {"24", "100"}
    for case_id in ("24", "100"):
        assert set(entries[case_id].keys()) == {"CP1", "CP2", "CP3"}
        for entry in entries[case_id].values():
            assert entry.verdict == Verdict.NOT_APPLICABLE
            assert entry.tier == SilverTier.ANOMALY_RULE


def test_human_labels_load_and_validate(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1"), _cp("CP2")]
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"1": {"CP1": "1", "CP2": "0"}}), encoding="utf-8")

    entries = load_human_labels(labels_path=labels, checkpoints=checkpoints)

    assert entries["1"]["CP1"].verdict == Verdict.COMPLIANT
    assert entries["1"]["CP2"].verdict == Verdict.NON_COMPLIANT
    assert entries["1"]["CP1"].tier == SilverTier.HUMAN


def test_human_labels_rejects_unknown_cp(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1")]
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"1": {"CP999": "1"}}), encoding="utf-8")

    try:
        load_human_labels(labels_path=labels, checkpoints=checkpoints)
    except ValueError as exc:
        assert "CP999" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown cp_id")


def test_human_labels_rejects_bad_verdict(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1")]
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"1": {"CP1": "maybe"}}), encoding="utf-8")

    try:
        load_human_labels(labels_path=labels, checkpoints=checkpoints)
    except ValueError as exc:
        assert "maybe" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid verdict")


def test_human_labels_accepts_rich_template_form_and_skips_empty(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1"), _cp("CP2"), _cp("CP3")]
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "_instructions": "fill me",
                "1": {
                    "CP1": {"verdict": "1", "cp_text": "...", "section": "1.1"},
                    "CP2": {"verdict": "", "cp_text": "..."},
                    "CP3": {"verdict": "N/A"},
                },
            }
        ),
        encoding="utf-8",
    )

    entries = load_human_labels(labels_path=labels, checkpoints=checkpoints)

    assert set(entries["1"].keys()) == {"CP1", "CP3"}  # CP2 empty skipped
    assert entries["1"]["CP1"].verdict == Verdict.COMPLIANT
    assert entries["1"]["CP3"].verdict == Verdict.NOT_APPLICABLE


def test_merge_silver_human_overrides_anomaly(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1")]
    anomaly = _anomaly_report(tmp_path, [100])
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"100": {"CP1": "1"}}), encoding="utf-8")

    reference = build_silver_reference(
        anomaly_report_path=anomaly,
        human_labels_path=labels,
        checkpoints=checkpoints,
    )

    entry = reference.cp_verdict(100, "CP1")
    assert entry is not None
    assert entry.tier == SilverTier.HUMAN
    assert entry.verdict == Verdict.COMPLIANT


def test_compare_to_silver_only_counts_anchored_tiers(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1"), _cp("CP2")]
    # CP1 anchored (human), CP2 weak consensus (should be ignored)
    reference = SilverReference(
        entries={
            "1": {
                "CP1": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
                "CP2": SilverEntry(verdict=Verdict.NON_COMPLIANT, tier=SilverTier.WEAK_CONSENSUS),
            }
        }
    )
    candidate = _result(1, (_verdict("CP1", "1"), _verdict("CP2", "1")))

    comparison = compare_to_silver(candidate=candidate, reference=reference)

    assert comparison.shared_checkpoints == ("CP1",)  # CP2 excluded
    assert comparison.matched_checkpoints == ("CP1",)
    assert comparison.silver_agreement == 1.0


def test_compare_to_silver_reports_partial_agreement(tmp_path: Path) -> None:
    reference = SilverReference(
        entries={
            "1": {
                "CP1": SilverEntry(verdict=Verdict.COMPLIANT, tier=SilverTier.HUMAN),
                "CP2": SilverEntry(verdict=Verdict.NON_COMPLIANT, tier=SilverTier.HUMAN),
            }
        }
    )
    candidate = _result(1, (_verdict("CP1", "1"), _verdict("CP2", "1")))

    comparison = compare_to_silver(candidate=candidate, reference=reference)

    assert comparison.shared_checkpoints == ("CP1", "CP2")
    assert comparison.matched_checkpoints == ("CP1",)
    assert comparison.silver_agreement == 0.5


def test_add_weak_consensus_does_not_overwrite_anchored(tmp_path: Path) -> None:
    checkpoints = [_cp("CP1"), _cp("CP2")]
    anomaly = _anomaly_report(tmp_path, [100])
    reference = build_silver_reference(
        anomaly_report_path=anomaly, human_labels_path=None, checkpoints=checkpoints
    )

    # case_full says CP1 passes, CP2 passes for the anomaly case
    case_full_result = _result(100, (_verdict("CP1", "1"), _verdict("CP2", "1")))
    augmented = add_weak_consensus(reference, case_full_results=[case_full_result])

    # Anomaly anchor (N/A) must survive; weak consensus only fills gaps.
    assert augmented.cp_verdict(100, "CP1").tier == SilverTier.ANOMALY_RULE
    assert augmented.cp_verdict(100, "CP1").verdict == Verdict.NOT_APPLICABLE


def test_add_weak_consensus_fills_uncovered_cps() -> None:
    reference = SilverReference(entries={})
    case_full_result = _result(1, (_verdict("CP1", "1"), _verdict("CP2", "0")))

    augmented = add_weak_consensus(reference, case_full_results=[case_full_result])

    assert augmented.cp_verdict(1, "CP1").tier == SilverTier.WEAK_CONSENSUS
    assert augmented.cp_verdict(1, "CP1").verdict == Verdict.COMPLIANT
    assert augmented.cp_verdict(1, "CP2").verdict == Verdict.NON_COMPLIANT