"""Stage E — hard gates and review triggers (proposal §7).

The §7 red line under test: contract violations produce ``ERROR`` findings and
evidence problems produce review triggers, but **neither ever rewrites the
business label**. Every test that raises a finding also asserts the verdict is
untouched.
"""

from __future__ import annotations

from freca.models import Applicability, Verdict

from freca.ledger.adjudicate import blocked_decision
from freca.ledger.config import AdjudicationConfig
from freca.ledger.gates import evaluate_gates, gate_flags, summarize_gate
from freca.ledger.models import (
    CaseFactLedger,
    ContradictionKind,
    CriterionOutcome,
    CriterionStatus,
    EvidenceCoverage,
)

from ledger_helpers import (
    VERBATIM_MISSING_FLAG,
    make_contradiction,
    make_decision,
    make_fact,
    make_pack,
    make_rubric,
)


def _codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


def _error_codes(report) -> set[str]:
    return {finding.code for finding in report.errors}


def _clean():
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1"), make_fact("f2")])
    decision = make_decision(rubric=rubric, pack=pack)
    return rubric, pack, decision


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def test_a_well_formed_decision_passes_every_gate() -> None:
    rubric, pack, decision = _clean()
    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)

    assert report.passed is True
    assert report.errors == []
    assert report.review_triggers == []
    assert report.needs_review is False
    assert 0.0 <= report.review_priority <= 1.0


# --------------------------------------------------------------------------
# Dual citation (§7)
# --------------------------------------------------------------------------


def test_missing_policy_citation_fails_the_gate() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack, policy_citations=[])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "MISSING_POLICY_CITATION" in _error_codes(report)
    assert report.passed is False
    assert decision.verdict == Verdict.COMPLIANT  # gate never flips the label


def test_missing_case_citation_fails_the_gate() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        supporting=[],
        outcomes=[
            CriterionOutcome(criterion_id="C1", status=CriterionStatus.SATISFIED),
            CriterionOutcome(criterion_id="C2", status=CriterionStatus.SATISFIED),
        ],
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "MISSING_CASE_CITATION" in _error_codes(report)


def test_dual_citation_can_be_relaxed_by_configuration() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack, policy_citations=[])

    report = evaluate_gates(
        decision=decision,
        pack=pack,
        rubric=rubric,
        config=AdjudicationConfig(require_dual_citation=False),
    )
    assert "MISSING_POLICY_CITATION" not in _error_codes(report)


# --------------------------------------------------------------------------
# N/A (§7)
# --------------------------------------------------------------------------


def test_not_applicable_without_applicability_reasoning_fails() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning="   ",
        status=CriterionStatus.NOT_APPLICABLE,
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "NA_WITHOUT_APPLICABILITY_REASONING" in _error_codes(report)


def test_well_explained_not_applicable_passes_without_case_citations() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning="Clause 12 applies only to abattoirs, not to grain stores.",
        supporting=[],
        status=CriterionStatus.NOT_APPLICABLE,
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert report.errors == []
    assert report.passed is True


def test_withdrawn_not_applicable_leaves_a_warning_trail() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning="Clause 12 applies only to abattoirs.",
        flags=["na_withdrawn_no_policy_basis"],
        status=CriterionStatus.NOT_APPLICABLE,
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "NA_WITHDRAWN" in _codes(report)


# --------------------------------------------------------------------------
# Citation integrity (§3, §7)
# --------------------------------------------------------------------------


def test_policy_citation_outside_the_rubric_context_fails() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric, pack=pack, policy_citations=["policy-1", "policy-999"]
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "POLICY_CITATION_OUT_OF_RUBRIC" in _error_codes(report)


def test_citation_to_a_fact_outside_the_pack_fails() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack, supporting=["ghost-fact"])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "CITATION_UNRESOLVED" in _error_codes(report)


def test_citation_belonging_to_another_case_fails() -> None:
    rubric = make_rubric()
    foreign = make_fact("f-other", case_id=2)
    pack = make_pack(rubric=rubric, facts=[foreign], case_id=1)
    decision = make_decision(rubric=rubric, pack=pack, supporting=["f-other"])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "CITATION_FOREIGN_CASE" in _error_codes(report)


def test_untraceable_citation_fails() -> None:
    rubric = make_rubric()
    fact = make_fact("f1").model_copy(update={"chunk_id": ""})
    pack = make_pack(rubric=rubric, facts=[fact])
    decision = make_decision(rubric=rubric, pack=pack, supporting=["f1"])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "CITATION_NOT_TRACEABLE" in _error_codes(report)


def test_unverified_verbatim_is_a_warning_that_still_forces_review() -> None:
    rubric = make_rubric()
    pack = make_pack(
        rubric=rubric, facts=[make_fact("f1", flags=[VERBATIM_MISSING_FLAG])]
    )
    decision = make_decision(rubric=rubric, pack=pack, supporting=["f1"])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "CITATION_VERBATIM_UNVERIFIED" not in _error_codes(report)
    assert "CITATION_VERBATIM_UNVERIFIED" in report.review_triggers
    assert report.needs_review is True


def test_answer_like_text_can_never_support_a_verdict() -> None:
    rubric = make_rubric()
    leaked = make_fact("f-leak", flags=["answer_like_field", "leak:audit_scenario_field"])
    pack = make_pack(rubric=rubric, facts=[leaked])
    decision = make_decision(rubric=rubric, pack=pack, supporting=["f-leak"])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "ANSWER_LIKE_SUPPORT" in _error_codes(report)
    assert report.passed is False


def test_compliance_built_on_foreign_paperwork_is_flagged_for_review() -> None:
    rubric = make_rubric()
    contaminated = make_fact("f-x", flags=["exclude_from_compliance_evidence"])
    pack = make_pack(rubric=rubric, facts=[contaminated])
    decision = make_decision(rubric=rubric, pack=pack, supporting=["f-x"])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "COMPLIANT_ON_FOREIGN_PAPERWORK" in report.review_triggers
    assert "COMPLIANT_ON_FOREIGN_PAPERWORK" not in _error_codes(report)


# --------------------------------------------------------------------------
# Review triggers (§7) — never business labels
# --------------------------------------------------------------------------


def test_ledger_contradictions_trigger_review_without_failing_the_gate() -> None:
    rubric = make_rubric()
    pack = make_pack(
        rubric=rubric,
        facts=[make_fact("f1")],
        contradictions=[
            make_contradiction(kind=ContradictionKind.IDENTITY_MISMATCH),
            make_contradiction(
                kind=ContradictionKind.SAME_TOPIC_CONFLICT, severity="REVIEW"
            ),
            make_contradiction(
                kind=ContradictionKind.CROSS_DOCUMENT_VALUE, severity="REVIEW"
            ),
        ],
    )
    decision = make_decision(rubric=rubric, pack=pack)

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert report.passed is True
    assert {
        "IDENTITY_MISMATCH",
        "SAME_TOPIC_CONFLICT",
        "CROSS_DOCUMENT_VALUE_CONFLICT",
    } <= set(report.review_triggers)
    assert decision.verdict == Verdict.COMPLIANT


def test_missing_records_are_a_trigger_not_a_verdict() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack)
    ledger = CaseFactLedger(case_id=1, missing_tracks=[4, 5])

    report = evaluate_gates(
        decision=decision, pack=pack, rubric=rubric, ledger=ledger
    )
    assert "MISSING_RECORDS" in report.review_triggers
    assert report.passed is True


def test_degraded_rubric_triggers_review() -> None:
    rubric = make_rubric(degraded="no_model_client")
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack)

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "RUBRIC_DEGRADED" in report.review_triggers


def test_uncovered_criteria_and_insufficient_coverage_trigger_review() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")], uncovered=["C2"])
    decision = make_decision(
        rubric=rubric, pack=pack, coverage=EvidenceCoverage.INSUFFICIENT
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "UNCOVERED_CRITERIA" in report.review_triggers
    assert "EVIDENCE_INSUFFICIENT" in report.review_triggers


def test_compliant_verdict_contradicted_by_its_own_criteria_triggers_review() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        outcomes=[
            CriterionOutcome(
                criterion_id="C1", status=CriterionStatus.SATISFIED, fact_ids=["f1"]
            ),
            CriterionOutcome(
                criterion_id="C2", status=CriterionStatus.VIOLATED, fact_ids=["f1"]
            ),
        ],
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "VERDICT_REASONING_INCONSISTENT" in report.review_triggers
    assert decision.verdict == Verdict.COMPLIANT


def test_non_compliant_without_any_contrary_signal_triggers_review() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric, pack=pack, verdict=Verdict.NON_COMPLIANT
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "NON_COMPLIANT_WITHOUT_CONTRARY" in report.review_triggers


def test_low_confidence_and_repairs_trigger_review() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        confidence=0.2,
        flags=["dropped_unknown_fact_citation"],
    )

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "LOW_CONFIDENCE" in report.review_triggers
    assert "NORMALIZATION_REPAIRS" in report.review_triggers


# --------------------------------------------------------------------------
# Blocked adjudication
# --------------------------------------------------------------------------


def test_blocked_adjudication_is_recorded_as_an_error_not_a_guess() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = blocked_decision(rubric=rubric, pack=pack, reason="no model client")

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    assert "ADJUDICATION_BLOCKED" in _error_codes(report)
    assert report.passed is False
    assert report.needs_review is True
    assert "adjudication_blocked" in decision.quality_flags


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_gate_flags_are_namespaced_quality_flags() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack, policy_citations=[])

    report = evaluate_gates(decision=decision, pack=pack, rubric=rubric)
    flags = gate_flags(report)
    assert "gate:missing_policy_citation" in flags
    assert all(flag.startswith("gate:") for flag in flags)


def test_gate_summary_reports_dimensions_without_a_total() -> None:
    rubric, pack, decision = _clean()
    summary = summarize_gate(evaluate_gates(decision=decision, pack=pack, rubric=rubric))

    assert summary["passed"] is True
    assert summary["errors"] == []
    assert set(summary["scorecard"]) == {
        "regulatory_coverage",
        "support_coverage",
        "contrary_strength",
        "citation_quality",
        "evidence_integrity",
    }
