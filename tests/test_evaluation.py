from pathlib import Path

from freca.evaluation import (
    GoldLabel,
    compare_reports,
    evaluate_run,
    load_gold_labels,
)
from freca.models import Applicability, AuditDecision, Verdict
from freca.state import atomic_write_json


def test_load_gold_labels_includes_only_confirmed_verdicts() -> None:
    labels = load_gold_labels(Path("gold/consensus-v1.json"))

    assert len(labels) == 34
    assert labels[(23, "CP1")].verdict == "0"
    assert labels[(65, "CP12")].verdict == "1"
    assert (23, "CP24") not in labels
    assert (35, "CP35") not in labels
    assert (23, "CP17") not in labels
    assert (23, "CP19") not in labels


def test_load_gold_labels_consensus_v2_has_37_labels_with_re_number() -> None:
    labels = load_gold_labels(Path("gold/consensus-v2.json"))

    assert len(labels) == 37
    assert labels[(65, "CP24")].verdict == "1"
    assert labels[(65, "CP26")].verdict == "0"
    assert labels[(74, "CP26")].verdict == "0"
    assert labels[(23, "CP1")].re_number == "RE-NSW-2020-0144"
    assert labels[(74, "CP26")].re_number == "RE-NSW-2020-0088"


def test_gold_label_accepts_missing_re_number() -> None:
    label = GoldLabel.model_validate(
        {
            "case_id": 1,
            "cp_id": "CP1",
            "verdict": "0",
            "confirmed": True,
            "note": "test",
        }
    )

    assert label.re_number is None


def _gold_file(tmp_path: Path, labels: list[tuple[int, str, str]]) -> Path:
    path = tmp_path / "gold.json"
    payload = {
        "version": "test-v1",
        "labels": [
            {
                "case_id": case_id,
                "cp_id": cp_id,
                "verdict": verdict,
                "confirmed": True,
                "note": "test",
            }
            for case_id, cp_id, verdict in labels
        ],
    }
    atomic_write_json(path, payload)
    return path


def _write_decision(path: Path, case_id: int, cp_id: str, verdict: str) -> None:
    decision = AuditDecision(
        case_id=case_id,
        cp_id=cp_id,
        applicability=(
            Applicability.NOT_APPLICABLE
            if verdict == Verdict.NOT_APPLICABLE
            else Applicability.APPLICABLE
        ),
        regulatory_requirement="requirement",
        policy_citations=["policy-1"],
        supporting_evidence=["evidence-1"],
        contrary_evidence=[],
        contradictions=[],
        verdict=Verdict(verdict),
        reasoning_summary="summary",
        confidence=0.8,
        retrieval_complete=True,
    )
    atomic_write_json(path, decision.model_dump(mode="json"))


def test_evaluate_run_reports_match_mismatch_and_missing(tmp_path: Path) -> None:
    gold = _gold_file(
        tmp_path,
        [(23, "CP1", "0"), (35, "CP1", "1"), (38, "CP1", "0")],
    )
    _write_decision(tmp_path / "final" / "023" / "CP1.json", 23, "CP1", "0")
    _write_decision(tmp_path / "final" / "035" / "CP1.json", 35, "CP1", "0")

    report = evaluate_run(tmp_path, run_id="baseline-a", gold_path=gold)

    assert report["gold_version"] == "test-v1"
    assert report["evaluated_count"] == 2
    assert report["matched_count"] == 1
    assert report["agreement_rate"] == 0.5
    assert report["missing_tasks"] == ["038/CP1"]
    assert report["mismatches"][0]["task"] == "035/CP1"
    assert report["mismatches"][0]["actual_verdict"] == "0"
    assert (tmp_path / "evaluation" / "baseline-a.json").exists()


def test_evaluate_run_reads_method_specific_final_root(tmp_path: Path) -> None:
    gold = _gold_file(tmp_path, [(23, "CP1", "0")])
    final_root = tmp_path / "method-runs" / "bm25-gold-v1" / "final"
    _write_decision(final_root / "023" / "CP1.json", 23, "CP1", "0")

    report = evaluate_run(
        tmp_path,
        run_id="bm25-gold-v1",
        gold_path=gold,
        final_root=final_root,
    )

    assert report["evaluated_count"] == 1
    assert report["matched_count"] == 1


def test_compare_reports_orders_runs_by_agreement_rate(tmp_path: Path) -> None:
    (tmp_path / "evaluation").mkdir()
    atomic_write_json(
        tmp_path / "evaluation" / "slow.json",
        {"agreement_rate": 0.5, "evaluated_count": 4, "matched_count": 2},
    )
    atomic_write_json(
        tmp_path / "evaluation" / "fast.json",
        {"agreement_rate": 0.75, "evaluated_count": 4, "matched_count": 3},
    )

    result = compare_reports(tmp_path, ["slow", "fast"])

    assert [row["run_id"] for row in result["runs"]] == ["fast", "slow"]
