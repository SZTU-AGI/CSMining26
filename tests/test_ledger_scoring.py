"""Stage E — the five independent scoring dimensions (proposal §6).

The rule being protected here is that these numbers rank *review priority*,
never compliance. A test suite is the right place to keep that honest: if
someone later adds a weighted total, several of these assertions break.
"""

from __future__ import annotations

from freca.models import Applicability, Verdict

from freca.ledger.models import (
    CaseFactLedger,
    ContradictionKind,
    CriterionKind,
    CriterionOutcome,
    CriterionStatus,
    EvidenceCoverage,
    RubricCriterion,
)
from freca.ledger.scoring import (
    build_scorecard,
    describe_scorecard,
    review_priority,
    scorecard_summary,
)

from ledger_helpers import (
    VERBATIM_MISSING_FLAG,
    make_contradiction,
    make_decision,
    make_fact,
    make_pack,
    make_rubric,
    perfect_scorecard,
)


def _clean_case():
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1"), make_fact("f2")])
    decision = make_decision(rubric=rubric, pack=pack)
    return rubric, pack, decision


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_every_dimension_stays_within_the_unit_interval() -> None:
    rubric, pack, decision = _clean_case()
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    for name, value in card.as_dimensions().items():
        assert 0.0 <= value <= 1.0, f"{name} out of range: {value}"


def test_summary_never_exposes_an_aggregate() -> None:
    rubric, pack, decision = _clean_case()
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    summary = scorecard_summary(card)
    assert len(summary) == 5
    assert not {"total", "score", "overall", "weighted"} & set(summary)
    assert "regulatory_coverage=" in describe_scorecard(card)


# --------------------------------------------------------------------------
# Dimension 1 — regulatory coverage
# --------------------------------------------------------------------------


def test_regulatory_coverage_is_zero_without_any_policy_citation() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NON_COMPLIANT,
        policy_citations=[],
        status=CriterionStatus.VIOLATED,
    )
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert card.regulatory_coverage == 0.0
    assert "decision cites no policy clause" in card.notes


def test_degraded_rubric_caps_regulatory_coverage() -> None:
    rubric = make_rubric(degraded="no_model_client")
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack)
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert card.regulatory_coverage <= 0.5
    assert "rubric was generated in degraded mode" in card.notes


def test_unaddressed_timing_clause_lowers_regulatory_coverage() -> None:
    criteria = [
        RubricCriterion(
            criterion_id="C1",
            kind=CriterionKind.APPLICABILITY,
            statement="applies",
            policy_citations=["policy-1"],
        ),
        RubricCriterion(
            criterion_id="C2",
            kind=CriterionKind.SUPPORTING,
            statement="records kept",
            policy_citations=["policy-2"],
        ),
        RubricCriterion(
            criterion_id="C3",
            kind=CriterionKind.EXCEPTION_TIMING,
            statement="treatment within 21 days of export",
            policy_citations=["policy-2"],
        ),
    ]
    rubric = make_rubric(criteria=criteria)
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    addressed = make_decision(rubric=rubric, pack=pack)
    skipped = make_decision(
        rubric=rubric,
        pack=pack,
        outcomes=[
            CriterionOutcome(
                criterion_id="C1", status=CriterionStatus.SATISFIED, fact_ids=["f1"]
            ),
            CriterionOutcome(
                criterion_id="C2", status=CriterionStatus.SATISFIED, fact_ids=["f1"]
            ),
            CriterionOutcome(criterion_id="C3", status=CriterionStatus.NOT_EVIDENCED),
        ],
    )

    high = build_scorecard(decision=addressed, pack=pack, rubric=rubric)
    low = build_scorecard(decision=skipped, pack=pack, rubric=rubric)
    assert low.regulatory_coverage < high.regulatory_coverage
    assert any("timing clauses" in note for note in low.notes)


# --------------------------------------------------------------------------
# Dimension 2 — support coverage
# --------------------------------------------------------------------------


def test_criteria_resolved_by_reasoning_alone_score_lower_than_cited_ones() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    with_facts = make_decision(rubric=rubric, pack=pack)
    without_facts = make_decision(
        rubric=rubric,
        pack=pack,
        outcomes=[
            CriterionOutcome(criterion_id="C1", status=CriterionStatus.SATISFIED),
            CriterionOutcome(criterion_id="C2", status=CriterionStatus.SATISFIED),
        ],
    )

    cited = build_scorecard(decision=with_facts, pack=pack, rubric=rubric)
    reasoned = build_scorecard(decision=without_facts, pack=pack, rubric=rubric)
    assert cited.support_coverage == 1.0
    assert reasoned.support_coverage == 0.4


def test_unevidenced_criteria_drive_support_coverage_down() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        outcomes=[
            CriterionOutcome(
                criterion_id="C1", status=CriterionStatus.SATISFIED, fact_ids=["f1"]
            ),
            CriterionOutcome(criterion_id="C2", status=CriterionStatus.NOT_EVIDENCED),
        ],
    )
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert card.support_coverage == 0.5
    assert any("carry no case evidence" in note for note in card.notes)


def test_not_applicable_scores_only_the_applicability_criteria() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning="The clause applies only to abattoirs.",
        outcomes=[
            CriterionOutcome(
                criterion_id="C1",
                status=CriterionStatus.NOT_APPLICABLE,
                fact_ids=["f1"],
            ),
            CriterionOutcome(criterion_id="C2", status=CriterionStatus.NOT_EVIDENCED),
        ],
    )
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    # C2 is moot once the clause does not apply, so coverage stays at 1.0.
    assert card.support_coverage == 1.0


# --------------------------------------------------------------------------
# Dimension 3 — contrary strength
# --------------------------------------------------------------------------


def test_contrary_strength_rises_with_violations_and_blocking_contradictions() -> None:
    rubric = make_rubric()
    facts = [make_fact("f1"), make_fact("f2")]
    quiet_pack = make_pack(rubric=rubric, facts=facts)
    loud_pack = make_pack(
        rubric=rubric,
        facts=facts,
        contradictions=[
            make_contradiction(
                kind=ContradictionKind.IDENTITY_MISMATCH, severity="BLOCKER"
            )
        ],
    )
    quiet = make_decision(rubric=rubric, pack=quiet_pack)
    loud = make_decision(
        rubric=rubric,
        pack=loud_pack,
        verdict=Verdict.NON_COMPLIANT,
        status=CriterionStatus.VIOLATED,
        contrary=["f2"],
    )

    low = build_scorecard(decision=quiet, pack=quiet_pack, rubric=rubric)
    high = build_scorecard(decision=loud, pack=loud_pack, rubric=rubric)
    assert low.contrary_strength == 0.0
    assert high.contrary_strength > low.contrary_strength
    assert any("blocking ledger contradictions" in note for note in high.notes)


def test_missing_tracks_add_contrary_signal_without_changing_the_verdict() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack)
    ledger = CaseFactLedger(case_id=1, missing_tracks=[2, 3])

    card = build_scorecard(
        decision=decision, pack=pack, rubric=rubric, ledger=ledger
    )
    assert card.contrary_strength > 0.0
    assert decision.verdict == Verdict.COMPLIANT  # scoring never rewrites the label


# --------------------------------------------------------------------------
# Dimension 4 — citation quality
# --------------------------------------------------------------------------


def test_repairs_lower_citation_quality() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    clean = make_decision(rubric=rubric, pack=pack)
    repaired = make_decision(
        rubric=rubric,
        pack=pack,
        flags=["dropped_unknown_policy_citation", "dropped_answer_like_support"],
    )

    good = build_scorecard(decision=clean, pack=pack, rubric=rubric)
    poor = build_scorecard(decision=repaired, pack=pack, rubric=rubric)
    assert poor.citation_quality < good.citation_quality
    assert any("citation repairs applied" in note for note in poor.notes)


def test_unverified_verbatim_lowers_citation_quality() -> None:
    rubric = make_rubric()
    pack = make_pack(
        rubric=rubric, facts=[make_fact("f1", flags=[VERBATIM_MISSING_FLAG])]
    )
    decision = make_decision(rubric=rubric, pack=pack)
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert card.citation_quality < 1.0
    assert any("verbatim" in note for note in card.notes)


def test_missing_case_citation_is_recorded_as_a_note() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack, supporting=[])
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert "no case-level citation" in card.notes


# --------------------------------------------------------------------------
# Dimension 5 — evidence integrity
# --------------------------------------------------------------------------


def test_blocked_adjudication_collapses_evidence_integrity() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    blocked = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NON_COMPLIANT,
        confidence=0.0,
        coverage=EvidenceCoverage.INSUFFICIENT,
        flags=["adjudication_blocked"],
        status=CriterionStatus.NOT_EVIDENCED,
    )
    card = build_scorecard(decision=blocked, pack=pack, rubric=rubric)
    assert card.evidence_integrity <= 0.4
    assert "adjudication did not run" in card.notes


def test_empty_pack_and_empty_ledger_both_reduce_integrity() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[])
    decision = make_decision(rubric=rubric, pack=pack, supporting=[])
    ledger = CaseFactLedger(case_id=1)

    card = build_scorecard(
        decision=decision, pack=pack, rubric=rubric, ledger=ledger
    )
    assert card.evidence_integrity < 0.4
    assert "evidence pack is empty" in card.notes
    assert "fact ledger is empty for this case" in card.notes


def test_contaminated_evidence_reduces_integrity() -> None:
    rubric = make_rubric()
    pack = make_pack(
        rubric=rubric,
        facts=[make_fact("f1", flags=["exclude_from_compliance_evidence"])],
    )
    decision = make_decision(rubric=rubric, pack=pack)
    card = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert card.evidence_integrity < 1.0
    assert any("another establishment" in note for note in card.notes)


# --------------------------------------------------------------------------
# Review priority (triage, not compliance)
# --------------------------------------------------------------------------


def test_review_priority_rises_as_evidence_weakens() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack)

    strong = review_priority(scorecard=perfect_scorecard(), decision=decision)
    weak = review_priority(
        scorecard=perfect_scorecard().model_copy(
            update={
                "citation_quality": 0.1,
                "support_coverage": 0.2,
                "evidence_integrity": 0.3,
            }
        ),
        decision=decision,
    )
    assert weak > strong


def test_contradictory_evidence_raises_priority_for_both_labels() -> None:
    """The contrary dimension flips polarity with the label, treated symmetrically.

    A compliant verdict is suspect when contrary evidence is STRONG (a missed
    breach); a non-compliant verdict is suspect when contrary evidence is WEAK
    (a false negative — judged non-compliant without much evidence of it). Both
    must raise review priority so neither error class is buried.
    """
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    compliant = make_decision(rubric=rubric, pack=pack)
    non_compliant = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NON_COMPLIANT,
        status=CriterionStatus.VIOLATED,
        contrary=["f1"],
    )

    # Compliant verdict: strong contrary evidence raises priority over a quiet card.
    quiet = perfect_scorecard()
    loud_contrary = perfect_scorecard().model_copy(update={"contrary_strength": 0.9})
    assert review_priority(scorecard=loud_contrary, decision=compliant) > review_priority(
        scorecard=quiet, decision=compliant
    )

    # Non-compliant verdict: WEAK contrary evidence raises priority over strong
    # contrary evidence — a non-compliant verdict sitting on little evidence of
    # non-compliance is the false-negative signal. Previously this case got the
    # lowest priority.
    strong_contrary = perfect_scorecard().model_copy(update={"contrary_strength": 0.9})
    weak_contrary = perfect_scorecard().model_copy(update={"contrary_strength": 0.1})
    assert review_priority(
        scorecard=weak_contrary, decision=non_compliant
    ) > review_priority(scorecard=strong_contrary, decision=non_compliant)


def test_review_priority_is_bounded_and_grows_with_contract_errors() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])
    decision = make_decision(rubric=rubric, pack=pack)

    base = review_priority(scorecard=perfect_scorecard(), decision=decision)
    with_errors = review_priority(
        scorecard=perfect_scorecard(),
        decision=decision,
        error_count=3,
        trigger_count=6,
    )
    assert 0.0 <= base <= with_errors <= 1.0
    assert with_errors > base
