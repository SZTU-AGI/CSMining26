"""Stage E — conditional independent review and reconciliation (proposal §7).

Three properties are asserted throughout:

* review is **conditional** (only on a gate trigger) and **compact** (tightened
  snippets, truncated pack), never a re-send of the whole regulation;
* reconciliation is **conservative** — a blocked or gate-failing review never
  overrides a primary that passed;
* the final decision is always one of the two decisions that were actually
  produced. Reconciliation records disagreement, it never invents a verdict.
"""

from __future__ import annotations

from freca.models import Applicability, Verdict

from freca.ledger.adjudicate import Adjudicator, blocked_decision
from freca.ledger.config import AdjudicationConfig, ReviewConfig, ReviewMode
from freca.ledger.models import CriterionStatus, DecisionStage
from freca.ledger.review import (
    ACCEPT_PRIMARY,
    ACCEPT_PRIMARY_CONFIRMED,
    ACCEPT_PRIMARY_ON_CONFLICT,
    ACCEPT_PRIMARY_REVIEW_BLOCKED,
    ACCEPT_PRIMARY_REVIEW_FAILED_GATES,
    ACCEPT_REVIEW_ON_CONFLICT,
    ACCEPT_REVIEW_PRIMARY_FAILED_GATES,
    ESCALATE_BOTH_GATES_FAILED,
    ReviewCoordinator,
    accept_without_review,
    choose_final,
    compact_rubric,
)

from ledger_helpers import (
    StubJsonClient,
    make_decision,
    make_fact,
    make_gate_report,
    make_pack,
    make_rubric,
)


# --------------------------------------------------------------------------
# compact_rubric — §7 "review gets tightened snippets, not the full text"
# --------------------------------------------------------------------------


def test_compact_rubric_shrinks_snippets_but_keeps_the_citation_contract():
    rubric = make_rubric()
    original = rubric.policy_snippets["policy-1"]
    assert len(original) > 300

    tight = compact_rubric(rubric, snippet_char_limit=200)

    assert len(tight.policy_snippets["policy-1"]) <= 202
    assert tight.policy_snippets["policy-1"].endswith("…")
    # The contract that makes citations checkable must survive compaction.
    assert tight.policy_chunk_ids == rubric.policy_chunk_ids
    assert [c.policy_citations for c in tight.criteria] == [
        c.policy_citations for c in rubric.criteria
    ]
    assert tight.rubric_version == rubric.rubric_version


def test_compact_rubric_leaves_short_snippets_untouched():
    rubric = make_rubric()
    tight = compact_rubric(rubric, snippet_char_limit=100_000)
    assert tight.policy_snippets == rubric.policy_snippets


# --------------------------------------------------------------------------
# choose_final — reconciliation table
# --------------------------------------------------------------------------


def _primary_and_review(*, review_verdict: Verdict = Verdict.COMPLIANT):
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1"), make_fact("F2")])
    primary = make_decision(rubric=rubric, pack=pack)
    review = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=review_verdict,
        stage=DecisionStage.REVIEW,
        contrary=["F2"] if review_verdict == Verdict.NON_COMPLIANT else (),
        status=(
            CriterionStatus.VIOLATED
            if review_verdict == Verdict.NON_COMPLIANT
            else CriterionStatus.SATISFIED
        ),
    )
    return rubric, pack, primary, review


def test_no_review_means_accept_primary():
    _, _, primary, _ = _primary_and_review()
    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(),
        review=None,
        review_gate=None,
        config=ReviewConfig(),
    )
    assert resolution == ACCEPT_PRIMARY
    assert final is primary


def test_blocked_review_never_overrides_the_primary():
    rubric, pack, primary, _ = _primary_and_review()
    blocked = blocked_decision(
        rubric=rubric, pack=pack, reason="client error", stage=DecisionStage.REVIEW
    )
    assert blocked.verdict != primary.verdict  # it *would* disagree

    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(),
        review=blocked,
        review_gate=make_gate_report(passed=False, errors=1),
        config=ReviewConfig(),
    )
    assert resolution == ACCEPT_PRIMARY_REVIEW_BLOCKED
    assert final is primary


def test_review_that_fails_its_own_gates_never_overrides_a_clean_primary():
    _, _, primary, review = _primary_and_review(review_verdict=Verdict.NON_COMPLIANT)
    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(passed=True),
        review=review,
        review_gate=make_gate_report(passed=False, errors=2),
        config=ReviewConfig(),
    )
    assert resolution == ACCEPT_PRIMARY_REVIEW_FAILED_GATES
    assert final is primary


def test_clean_review_replaces_a_primary_that_failed_its_gates():
    _, _, primary, review = _primary_and_review(review_verdict=Verdict.NON_COMPLIANT)
    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(passed=False, errors=1),
        review=review,
        review_gate=make_gate_report(passed=True),
        config=ReviewConfig(),
    )
    assert resolution == ACCEPT_REVIEW_PRIMARY_FAILED_GATES
    assert final is review


def test_agreement_between_two_clean_passes_keeps_the_primary():
    _, _, primary, review = _primary_and_review(review_verdict=Verdict.COMPLIANT)
    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(),
        review=review,
        review_gate=make_gate_report(),
        config=ReviewConfig(),
    )
    assert resolution == ACCEPT_PRIMARY_CONFIRMED
    # The primary was decided on the full pack, so it carries richer citations.
    assert final is primary


def test_conflict_between_clean_passes_is_settled_by_configuration():
    _, _, primary, review = _primary_and_review(review_verdict=Verdict.NON_COMPLIANT)

    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(),
        review=review,
        review_gate=make_gate_report(),
        config=ReviewConfig(prefer_review_on_conflict=True),
    )
    assert resolution == ACCEPT_REVIEW_ON_CONFLICT
    assert final is review

    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(),
        review=review,
        review_gate=make_gate_report(),
        config=ReviewConfig(prefer_review_on_conflict=False),
    )
    assert resolution == ACCEPT_PRIMARY_ON_CONFLICT
    assert final is primary


def test_when_both_passes_fail_gates_the_least_broken_record_is_escalated():
    _, _, primary, review = _primary_and_review(review_verdict=Verdict.NON_COMPLIANT)

    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(passed=False, errors=3),
        review=review,
        review_gate=make_gate_report(passed=False, errors=1),
        config=ReviewConfig(),
    )
    assert resolution == ESCALATE_BOTH_GATES_FAILED
    assert final is review

    final, resolution = choose_final(
        primary=primary,
        primary_gate=make_gate_report(passed=False, errors=1),
        review=review,
        review_gate=make_gate_report(passed=False, errors=1),
        config=ReviewConfig(),
    )
    assert resolution == ESCALATE_BOTH_GATES_FAILED
    assert final is primary  # tie goes to the primary, nothing is invented


def test_choose_final_only_ever_returns_a_decision_that_was_produced():
    _, _, primary, review = _primary_and_review(review_verdict=Verdict.NON_COMPLIANT)
    for primary_passed in (True, False):
        for review_passed in (True, False):
            final, _ = choose_final(
                primary=primary,
                primary_gate=make_gate_report(
                    passed=primary_passed, errors=0 if primary_passed else 1
                ),
                review=review,
                review_gate=make_gate_report(
                    passed=review_passed, errors=0 if review_passed else 1
                ),
                config=ReviewConfig(),
            )
            assert final in (primary, review)


# --------------------------------------------------------------------------
# ReviewCoordinator.should_review
# --------------------------------------------------------------------------


def test_review_mode_disabled_skips_review_even_when_triggered():
    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(), config=ReviewConfig(mode=ReviewMode.DISABLED)
    )
    triggered = make_gate_report(triggers=["LOW_CONFIDENCE"])
    assert triggered.needs_review is True
    assert coordinator.should_review(triggered) is False


def test_review_mode_always_reviews_a_clean_decision():
    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(), config=ReviewConfig(mode=ReviewMode.ALWAYS)
    )
    clean = make_gate_report()
    assert clean.needs_review is False
    assert coordinator.should_review(clean) is True


def test_on_trigger_mode_follows_the_gate():
    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(), config=ReviewConfig(mode=ReviewMode.ON_TRIGGER)
    )
    assert coordinator.should_review(make_gate_report()) is False
    assert coordinator.should_review(make_gate_report(triggers=["LOW_CONFIDENCE"])) is True
    # A failed gate always needs a second look.
    assert coordinator.should_review(make_gate_report(passed=False, errors=1)) is True


# --------------------------------------------------------------------------
# run_review — the second pass is genuinely compact
# --------------------------------------------------------------------------


def _agreeing_payload(fact_id: str = "F1") -> dict:
    return {
        "applicability": "APPLICABLE",
        "verdict": "1",
        "criterion_outcomes": [
            {"criterion_id": "C1", "status": "satisfied", "fact_ids": [fact_id]},
            {"criterion_id": "C2", "status": "satisfied", "fact_ids": [fact_id]},
        ],
        "policy_citations": ["policy-1", "policy-2"],
        "supporting_fact_ids": [fact_id],
        "contrary_fact_ids": [],
        "evidence_coverage": "complete",
        "applicability_reasoning": "",
        "reasoning_summary": "Dated pest control records cover the audit period.",
        "confidence": 0.88,
    }


def _dissenting_payload(fact_id: str = "F1") -> dict:
    return {
        "applicability": "APPLICABLE",
        "verdict": "0",
        "criterion_outcomes": [
            {"criterion_id": "C1", "status": "satisfied", "fact_ids": [fact_id]},
            {"criterion_id": "C2", "status": "violated", "fact_ids": [fact_id]},
        ],
        "policy_citations": ["policy-2"],
        "supporting_fact_ids": [],
        "contrary_fact_ids": [fact_id],
        "evidence_coverage": "complete",
        "applicability_reasoning": "",
        "reasoning_summary": "The recorded treatment falls outside the required interval.",
        "confidence": 0.81,
    }


def test_run_review_sends_a_tightened_rubric_and_a_truncated_pack():
    rubric = make_rubric()
    facts = [make_fact(f"F{index}") for index in range(1, 9)]
    pack = make_pack(rubric=rubric, facts=facts)

    client = StubJsonClient([_agreeing_payload()])
    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(client=client),
        config=ReviewConfig(max_facts=2, snippet_char_limit=200),
    )

    decision, gate, tight_pack = coordinator.run_review(rubric=rubric, pack=pack)

    assert decision.stage == DecisionStage.REVIEW
    assert gate.cp_id == rubric.cp_id
    # §7: the reviewer sees a compact context.
    assert len(tight_pack.facts) == 2
    prompt = client.calls[0]["user"]
    assert "F1" in prompt and "F8" not in prompt
    assert "…" in prompt  # snippets were truncated
    # The citation contract still holds against the same clause ids.
    assert set(decision.policy_citations) <= set(rubric.policy_chunk_ids)


def test_a_failing_review_client_produces_a_blocked_decision_not_a_guess():
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1")])
    client = StubJsonClient([RuntimeError("upstream 503")])
    coordinator = ReviewCoordinator(adjudicator=Adjudicator(client=client))

    decision, gate, _ = coordinator.run_review(rubric=rubric, pack=pack)

    assert "adjudication_blocked" in decision.quality_flags
    assert decision.confidence == 0.0
    assert decision.stage == DecisionStage.REVIEW
    assert gate.passed is False
    assert "ADJUDICATION_BLOCKED" in {finding.code for finding in gate.errors}


# --------------------------------------------------------------------------
# resolve — the auditable record
# --------------------------------------------------------------------------


def _resolve_with(payload, *, primary_confidence: float = 0.30):
    """A primary that trips LOW_CONFIDENCE, then a review from ``payload``."""

    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1"), make_fact("F2")])
    primary = make_decision(rubric=rubric, pack=pack, confidence=primary_confidence)

    from freca.ledger.gates import evaluate_gates

    primary_gate = evaluate_gates(decision=primary, pack=pack, rubric=rubric)
    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(client=StubJsonClient([payload])),
        adjudication_config=AdjudicationConfig(),
    )
    outcome = coordinator.resolve(
        rubric=rubric, pack=pack, primary=primary, primary_gate=primary_gate
    )
    return rubric, pack, primary, primary_gate, outcome


def test_a_clean_primary_is_delivered_without_a_second_pass():
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1")])
    primary = make_decision(rubric=rubric, pack=pack)

    from freca.ledger.gates import evaluate_gates

    gate = evaluate_gates(decision=primary, pack=pack, rubric=rubric)
    assert gate.needs_review is False

    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(client=StubJsonClient([]))  # would raise if called
    )
    outcome = coordinator.resolve(
        rubric=rubric, pack=pack, primary=primary, primary_gate=gate
    )

    assert outcome.reviewed is False
    assert outcome.review is None and outcome.review_gate is None
    assert outcome.resolution == ACCEPT_PRIMARY
    assert "resolution:accept_primary" in outcome.final.quality_flags
    assert "independent_review_performed" not in outcome.final.quality_flags


def test_a_triggered_review_that_agrees_is_recorded_as_confirmation():
    _, _, primary, primary_gate, outcome = _resolve_with(_agreeing_payload())

    assert "LOW_CONFIDENCE" in primary_gate.review_triggers
    assert outcome.reviewed is True
    assert outcome.resolution == ACCEPT_PRIMARY_CONFIRMED
    assert outcome.final.verdict == primary.verdict
    flags = outcome.final.quality_flags
    assert "independent_review_performed" in flags
    assert "review_agreed_with_primary" in flags
    assert any(flag.startswith("review_triggers:") for flag in flags)
    assert "resolution:accept_primary_confirmed" in flags


def test_a_triggered_review_that_disagrees_is_recorded_not_hidden():
    _, _, primary, _, outcome = _resolve_with(_dissenting_payload())

    assert outcome.review is not None
    assert outcome.review.verdict == Verdict.NON_COMPLIANT
    assert outcome.resolution == ACCEPT_REVIEW_ON_CONFLICT
    assert outcome.final.verdict == Verdict.NON_COMPLIANT
    assert "review_disagreed_with_primary" in outcome.final.quality_flags
    # Both passes stay on the record for audit.
    assert outcome.primary.verdict == Verdict.COMPLIANT
    assert outcome.primary is primary


def test_resolve_never_mutates_the_decisions_it_reconciles():
    _, _, primary, _, outcome = _resolve_with(_agreeing_payload())

    assert "independent_review_performed" not in primary.quality_flags
    assert outcome.final is not primary
    # Annotation only adds flags; nothing about the judgement changes.
    assert outcome.final.verdict == primary.verdict
    assert outcome.final.applicability == primary.applicability
    assert outcome.final.policy_citations == primary.policy_citations
    assert outcome.final.supporting_fact_ids == primary.supporting_fact_ids
    assert set(primary.quality_flags) <= set(outcome.final.quality_flags)


def test_outcome_carries_the_pack_summary_and_triage_priority():
    rubric, pack, _, primary_gate, outcome = _resolve_with(_agreeing_payload())

    summary = outcome.pack_summary
    assert summary["rubric_version"] == rubric.rubric_version
    assert summary["review_priority"] == round(primary_gate.review_priority, 4)
    assert summary["facts"] == len(pack.facts)
    # §6: triage priority is not a compliance score and no total is emitted.
    assert "total" not in summary
    assert "score" not in summary


def test_accept_without_review_builds_a_complete_record():
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1")])
    primary = make_decision(rubric=rubric, pack=pack)

    from freca.ledger.gates import evaluate_gates

    gate = evaluate_gates(decision=primary, pack=pack, rubric=rubric)
    outcome = accept_without_review(
        rubric=rubric, pack=pack, primary=primary, primary_gate=gate
    )

    assert outcome.reviewed is False
    assert outcome.resolution == ACCEPT_PRIMARY
    assert outcome.case_id == primary.case_id and outcome.cp_id == primary.cp_id
    assert outcome.final.verdict == primary.verdict
    assert "resolution:accept_primary" in outcome.final.quality_flags
    assert outcome.pack_summary["rubric_version"] == rubric.rubric_version


def test_not_applicable_primary_survives_reconciliation_unchanged():
    """§7: ``N/A`` is a legal conclusion; review must not quietly reinterpret it."""

    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1")])
    primary = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning=(
            "The cited clause restricts this requirement to slaughter "
            "establishments; this premises is a cold store."
        ),
        supporting=[],
        confidence=0.9,
    )

    from freca.ledger.gates import evaluate_gates

    gate = evaluate_gates(decision=primary, pack=pack, rubric=rubric)
    outcome = accept_without_review(
        rubric=rubric, pack=pack, primary=primary, primary_gate=gate
    )

    assert outcome.final.verdict == Verdict.NOT_APPLICABLE
    assert outcome.final.applicability == Applicability.NOT_APPLICABLE
    assert outcome.final.applicability_reasoning == primary.applicability_reasoning
