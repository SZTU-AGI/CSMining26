"""Schema-level invariants of the ledger architecture (proposal §3–§7).

These are the rules the proposal calls red lines. They are asserted here at the
model layer, because a rule that only lives in a prompt is not a rule.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from freca.models import Applicability, Verdict

from freca.ledger.models import (
    CaseFactLedger,
    CheckpointRubric,
    CriterionKind,
    CriterionOutcome,
    CriterionStatus,
    EvidenceScorecard,
    EvidenceView,
    FactPolarity,
    FactRecord,
    LedgerDecision,
    RubricCriterion,
)

from ledger_helpers import make_fact, make_rubric


# --------------------------------------------------------------------------
# §4 — extraction may not pre-judge compliance
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["supporting", "contrary", "compliant", "non_compliant", "violation", "fail"],
)
def test_fact_polarity_refuses_pre_judged_values(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        FactRecord(
            fact_id="f1",
            case_id=1,
            topic="records",
            claim="a claim",
            polarity=value,
            source_file="track-1.docx",
            source_id="src",
            chunk_id="chunk",
        )
    assert "must not pre-judge polarity" in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    ["undecided", "not_decided", "supporting_or_contrary_not_decided", "neutral"],
)
def test_fact_polarity_accepts_only_undecided_aliases(value: str) -> None:
    fact = FactRecord(
        fact_id="f1",
        case_id=1,
        topic="records",
        claim="a claim",
        polarity=value,
        source_file="track-1.docx",
        source_id="src",
        chunk_id="chunk",
    )
    assert fact.polarity is FactPolarity.UNDECIDED


def test_fact_polarity_enum_has_a_single_legal_member() -> None:
    assert [member.value for member in FactPolarity] == ["undecided"]


def test_answer_like_fact_is_never_citable_for_support() -> None:
    fact = make_fact("f1", flags=["answer_like_field", "leak:audit_scenario_field"])
    assert fact.is_answer_like is True
    assert fact.citable_for_support is False


def test_contaminated_fact_is_not_citable_for_support() -> None:
    fact = make_fact("f1", flags=["exclude_from_compliance_evidence"])
    assert fact.is_contaminated is True
    assert fact.citable_for_support is False


def test_fact_locator_exposes_file_and_position() -> None:
    fact = make_fact("f1", source_file="track-5.docx")
    assert fact.locator() == "track-5.docx paragraph_index=3"


def test_ledger_rejects_facts_belonging_to_another_case() -> None:
    with pytest.raises(ValidationError) as excinfo:
        CaseFactLedger(case_id=1, facts=[make_fact("f1", case_id=2)])
    assert "do not belong to case 1" in str(excinfo.value)


def test_ledger_rejects_duplicate_fact_ids() -> None:
    with pytest.raises(ValidationError) as excinfo:
        CaseFactLedger(case_id=1, facts=[make_fact("f1"), make_fact("f1")])
    assert "duplicate fact_id" in str(excinfo.value)


# --------------------------------------------------------------------------
# §5 — the rubric must be citation-complete
# --------------------------------------------------------------------------


def test_rubric_rejects_citations_outside_its_retrieval_context() -> None:
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
            policy_citations=["policy-999"],
        ),
    ]
    with pytest.raises(ValidationError) as excinfo:
        make_rubric(criteria=criteria, policy_chunk_ids=["policy-1"])
    assert "outside its retrieval context" in str(excinfo.value)


def test_rubric_requires_an_applicability_and_a_supporting_criterion() -> None:
    only_supporting = [
        RubricCriterion(
            criterion_id="C1",
            kind=CriterionKind.SUPPORTING,
            statement="records kept",
            policy_citations=["policy-1"],
        )
    ]
    with pytest.raises(ValidationError) as excinfo:
        make_rubric(criteria=only_supporting, policy_chunk_ids=["policy-1"])
    assert "applicability criterion" in str(excinfo.value)


def test_rubric_criterion_ids_must_be_unique() -> None:
    duplicated = [
        RubricCriterion(
            criterion_id="C1",
            kind=CriterionKind.APPLICABILITY,
            statement="applies",
            policy_citations=["policy-1"],
        ),
        RubricCriterion(
            criterion_id="C1",
            kind=CriterionKind.SUPPORTING,
            statement="records kept",
            policy_citations=["policy-1"],
        ),
    ]
    with pytest.raises(ValidationError) as excinfo:
        make_rubric(criteria=duplicated, policy_chunk_ids=["policy-1"])
    assert "unique" in str(excinfo.value)


def test_rubric_criterion_requires_at_least_one_policy_citation() -> None:
    with pytest.raises(ValidationError):
        RubricCriterion(
            criterion_id="C1",
            kind=CriterionKind.SUPPORTING,
            statement="records kept",
            policy_citations=[],
        )


# --------------------------------------------------------------------------
# §7 — N/A is a regulatory conclusion, not an evidence shortage
# --------------------------------------------------------------------------


def test_not_applicable_verdict_requires_not_applicable_applicability() -> None:
    with pytest.raises(ValidationError) as excinfo:
        LedgerDecision(
            case_id=1,
            cp_id="CP1",
            applicability=Applicability.UNKNOWN,
            verdict=Verdict.NOT_APPLICABLE,
            policy_citations=["policy-1"],
        )
    assert "N/A requires NOT_APPLICABLE applicability" in str(excinfo.value)


def test_not_applicable_verdict_requires_a_policy_citation() -> None:
    with pytest.raises(ValidationError) as excinfo:
        LedgerDecision(
            case_id=1,
            cp_id="CP1",
            applicability=Applicability.NOT_APPLICABLE,
            verdict=Verdict.NOT_APPLICABLE,
            policy_citations=[],
        )
    assert "N/A requires at least one policy citation" in str(excinfo.value)


def test_not_applicable_applicability_cannot_carry_a_business_verdict() -> None:
    with pytest.raises(ValidationError) as excinfo:
        LedgerDecision(
            case_id=1,
            cp_id="CP1",
            applicability=Applicability.NOT_APPLICABLE,
            verdict=Verdict.COMPLIANT,
        )
    assert "requires an N/A verdict" in str(excinfo.value)


def test_cited_fact_ids_merges_supporting_and_contrary() -> None:
    decision = LedgerDecision(
        case_id=1,
        cp_id="CP1",
        applicability=Applicability.APPLICABLE,
        verdict=Verdict.NON_COMPLIANT,
        supporting_fact_ids=["f1"],
        contrary_fact_ids=["f2"],
        criterion_outcomes=[
            CriterionOutcome(criterion_id="C1", status=CriterionStatus.VIOLATED)
        ],
    )
    assert decision.cited_fact_ids == ["f1", "f2"]


# --------------------------------------------------------------------------
# §6 — five dimensions, no weighted total
# --------------------------------------------------------------------------


def test_scorecard_exposes_five_dimensions_and_no_total() -> None:
    card = EvidenceScorecard(
        regulatory_coverage=0.9,
        support_coverage=0.8,
        contrary_strength=0.1,
        citation_quality=0.7,
        evidence_integrity=0.95,
    )
    assert set(card.as_dimensions()) == {
        "regulatory_coverage",
        "support_coverage",
        "contrary_strength",
        "citation_quality",
        "evidence_integrity",
    }
    assert "total" not in EvidenceScorecard.model_fields
    assert not hasattr(card, "total")
    assert not hasattr(card, "score")


# --------------------------------------------------------------------------
# §8 — evidence views
# --------------------------------------------------------------------------


def test_evidence_view_signature_ignores_the_method_name() -> None:
    first = EvidenceView(
        method="run-a",
        model_signature="m1",
        context_construction="full-context",
        retrieval_scope="policy-index",
    )
    second = first.model_copy(update={"method": "run-b"})
    assert first.view_signature() == second.view_signature()

    different = first.model_copy(update={"retrieval_scope": "case-chunk-index"})
    assert different.view_signature() != first.view_signature()
