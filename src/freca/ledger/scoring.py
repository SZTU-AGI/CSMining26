"""Stage E — internal scorecards (proposal §6).

Five independent dimensions, **no weighted total**. This is the whole point of
§6: regulatory auditing contains veto-style facts, so "80 points therefore
compliant" is not a legitimate decision rule. A single missing pest-control
record can decide a case that scores well on every other axis.

What these numbers *are* allowed to do:

* express how good the evidence behind a verdict is;
* decide whether a case×CP needs independent review;
* rank the review queue when reviewer budget is finite.

What they must never do:

* be summed / averaged into one number;
* be compared against a threshold to produce ``1`` / ``0`` / ``N/A``.

:class:`~freca.ledger.models.EvidenceScorecard` deliberately exposes no
``total`` property, and :func:`review_priority` returns a *triage* number that
is explicitly not a compliance score — it rises when the evidence is weak,
which is the opposite polarity of a "quality score".
"""

from __future__ import annotations

from freca.models import Verdict

from freca.ledger.models import (
    CaseFactLedger,
    CheckpointRubric,
    CriterionKind,
    CriterionStatus,
    EvidenceCoverage,
    EvidencePack,
    EvidenceScorecard,
    LedgerDecision,
)

# Quality flags attached by ``adjudicate.normalize_decision`` when it had to
# repair a contract violation. Their presence lowers citation quality.
_REPAIR_FLAGS = frozenset(
    {
        "dropped_unknown_policy_citation",
        "dropped_unknown_fact_citation",
        "dropped_answer_like_support",
        "na_withdrawn_no_policy_basis",
        "na_withdrawn_no_applicability_reasoning",
        "applicability_realigned_to_verdict",
        "verdict_unparsed",
        "applicability_unparsed",
        "confidence_unparsed",
    }
)

_RESOLVED = frozenset(
    {
        CriterionStatus.SATISFIED,
        CriterionStatus.VIOLATED,
        CriterionStatus.NOT_APPLICABLE,
    }
)

VERBATIM_MISSING_FLAG = "verbatim_not_found_in_source"


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(numerator / denominator)


# --------------------------------------------------------------------------
# Dimension 1 — 法规覆盖 / regulatory coverage
# --------------------------------------------------------------------------


def _regulatory_coverage(
    decision: LedgerDecision,
    rubric: CheckpointRubric,
    notes: list[str],
) -> float:
    """Did the verdict actually reach the clauses the rubric is built on?

    Measured as the share of rubric criteria whose own policy citations are
    represented in the decision's citations, then blended with a specific
    check on exception / timing clauses, which are the ones most often skipped.
    """

    cited = set(decision.policy_citations)
    if not cited:
        notes.append("decision cites no policy clause")
        return 0.0

    grounded = sum(
        1 for criterion in rubric.criteria if cited.intersection(criterion.policy_citations)
    )
    score = _ratio(grounded, len(rubric.criteria))

    timing = rubric.criteria_of(CriterionKind.EXCEPTION_TIMING)
    if timing:
        outcomes = {item.criterion_id: item for item in decision.criterion_outcomes}
        addressed = sum(
            1
            for criterion in timing
            if (outcome := outcomes.get(criterion.criterion_id)) is not None
            and outcome.status in _RESOLVED
        )
        if addressed < len(timing):
            notes.append(
                f"{len(timing) - addressed}/{len(timing)} exception or timing clauses "
                "were not addressed"
            )
        score = 0.75 * score + 0.25 * _ratio(addressed, len(timing))

    if rubric.generator.get("degraded"):
        notes.append("rubric was generated in degraded mode")
        score = min(score, 0.5)

    return _clamp(score)


# --------------------------------------------------------------------------
# Dimension 2 — 支持覆盖 / support coverage
# --------------------------------------------------------------------------


def _support_coverage(
    decision: LedgerDecision,
    rubric: CheckpointRubric,
    notes: list[str],
) -> float:
    """Do this case's own facts actually reach the rubric's key conditions?

    A criterion resolved *with* cited facts counts fully; one resolved by
    reasoning alone counts partially; ``not_evidenced`` counts zero.
    For an ``N/A`` verdict only the applicability criteria are in scope — the
    substantive conditions are moot once the clause does not apply.
    """

    outcomes = {item.criterion_id: item for item in decision.criterion_outcomes}
    if decision.verdict == Verdict.NOT_APPLICABLE:
        pool = rubric.criteria_of(CriterionKind.APPLICABILITY) or list(rubric.criteria)
    else:
        pool = list(rubric.criteria)

    earned = 0.0
    unevidenced: list[str] = []
    for criterion in pool:
        outcome = outcomes.get(criterion.criterion_id)
        if outcome is None or outcome.status == CriterionStatus.NOT_EVIDENCED:
            unevidenced.append(criterion.criterion_id)
            continue
        earned += 1.0 if outcome.fact_ids else 0.4

    if unevidenced:
        notes.append(
            f"{len(unevidenced)} criteria carry no case evidence: "
            + ", ".join(unevidenced[:4])
        )
    return _ratio(earned, len(pool))


# --------------------------------------------------------------------------
# Dimension 3 — 反证强度 / contrary strength
# --------------------------------------------------------------------------


def _contrary_strength(
    decision: LedgerDecision,
    pack: EvidencePack,
    ledger: CaseFactLedger | None,
    notes: list[str],
) -> float:
    """How strong is the evidence pointing *against* compliance?

    High is not "bad" and low is not "good" — this dimension exists so a
    ``1`` verdict sitting on top of strong contrary signal gets pulled into
    review instead of being quietly accepted.
    """

    score = 0.0
    violated = [
        item for item in decision.criterion_outcomes if item.status == CriterionStatus.VIOLATED
    ]
    if violated:
        score += 0.2 + 0.4 * _ratio(len(violated), max(1, len(decision.criterion_outcomes)))
        notes.append(f"{len(violated)} criteria judged violated")

    if decision.contrary_fact_ids:
        score += 0.2

    blockers = [item for item in pack.contradictions if item.severity == "BLOCKER"]
    if blockers:
        score += 0.25
        notes.append(f"{len(blockers)} blocking ledger contradictions")
    elif pack.contradictions:
        score += 0.15

    missing = list(ledger.missing_tracks) if ledger is not None else []
    if missing:
        score += min(0.2, 0.05 * len(missing))
        notes.append(f"missing tracks: {', '.join(str(track) for track in missing)}")

    return _clamp(score)


# --------------------------------------------------------------------------
# Dimension 4 — 引用质量 / citation quality
# --------------------------------------------------------------------------


def _citation_quality(
    decision: LedgerDecision,
    pack: EvidencePack,
    rubric: CheckpointRubric,
    notes: list[str],
) -> float:
    """§7 dual citation: regulation *and* a locatable fact from this case."""

    score = 0.0
    index = pack.fact_index()

    if decision.policy_citations:
        known = [
            citation
            for citation in decision.policy_citations
            if citation in set(rubric.policy_chunk_ids)
        ]
        score += 0.3 * _ratio(len(known), len(decision.policy_citations))
    else:
        notes.append("no policy citation")

    cited_facts = decision.cited_fact_ids
    if cited_facts:
        resolvable = [fact_id for fact_id in cited_facts if fact_id in index]
        score += 0.3 * _ratio(len(resolvable), len(cited_facts))

        traceable = [
            fact_id
            for fact_id in resolvable
            if index[fact_id].source_file
            and index[fact_id].chunk_id
            and index[fact_id].case_id == decision.case_id
        ]
        score += 0.2 * _ratio(len(traceable), max(1, len(resolvable)))

        verbatim_ok = [
            fact_id
            for fact_id in resolvable
            if VERBATIM_MISSING_FLAG not in index[fact_id].quality_flags
        ]
        score += 0.1 * _ratio(len(verbatim_ok), max(1, len(resolvable)))
        if len(verbatim_ok) < len(resolvable):
            notes.append("some cited facts could not be matched verbatim to their source")
    else:
        notes.append("no case-level citation")

    if decision.verdict == Verdict.NOT_APPLICABLE:
        if decision.applicability_reasoning:
            score += 0.1
        else:
            notes.append("N/A without applicability reasoning")
    elif decision.reasoning_summary:
        score += 0.1

    repairs = _REPAIR_FLAGS.intersection(decision.quality_flags)
    if repairs:
        score -= 0.1 * len(repairs)
        notes.append("citation repairs applied: " + ", ".join(sorted(repairs)))

    return _clamp(score)


# --------------------------------------------------------------------------
# Dimension 5 — 证据完整性 / evidence integrity
# --------------------------------------------------------------------------


def _evidence_integrity(
    decision: LedgerDecision,
    pack: EvidencePack,
    ledger: CaseFactLedger | None,
    notes: list[str],
) -> float:
    """Missing files, foreign-establishment paperwork, parse failures."""

    score = 1.0

    if ledger is not None:
        if ledger.missing_tracks:
            score -= min(0.3, 0.08 * len(ledger.missing_tracks))
        if not ledger.facts:
            score -= 0.4
            notes.append("fact ledger is empty for this case")
        if "extraction_truncated" in ledger.quality_flags:
            score -= 0.05

    if pack.facts:
        contaminated = sum(1 for item in pack.facts if item.fact.is_contaminated)
        if contaminated:
            share = contaminated / len(pack.facts)
            score -= 0.25 * share
            notes.append(
                f"{contaminated}/{len(pack.facts)} packed facts belong to another establishment"
            )
        unverified = sum(
            1 for item in pack.facts if VERBATIM_MISSING_FLAG in item.fact.quality_flags
        )
        if unverified:
            score -= 0.2 * (unverified / len(pack.facts))
        if any(item.fact.is_answer_like for item in pack.facts):
            score -= 0.1
            notes.append("pack still contains answer-like text")
    else:
        score -= 0.35
        notes.append("evidence pack is empty")

    blockers = [item for item in pack.contradictions if item.severity == "BLOCKER"]
    if blockers:
        score -= 0.25

    if pack.integrity_notes:
        score -= min(0.15, 0.05 * len(pack.integrity_notes))

    if decision.evidence_coverage == EvidenceCoverage.INSUFFICIENT:
        score -= 0.1
    if "adjudication_blocked" in decision.quality_flags:
        score -= 0.5
        notes.append("adjudication did not run")

    return _clamp(score)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def build_scorecard(
    *,
    decision: LedgerDecision,
    pack: EvidencePack,
    rubric: CheckpointRubric,
    ledger: CaseFactLedger | None = None,
) -> EvidenceScorecard:
    """Compute the five §6 dimensions for one case×CP decision."""

    notes: list[str] = []
    card = EvidenceScorecard(
        regulatory_coverage=_regulatory_coverage(decision, rubric, notes),
        support_coverage=_support_coverage(decision, rubric, notes),
        contrary_strength=_contrary_strength(decision, pack, ledger, notes),
        citation_quality=_citation_quality(decision, pack, rubric, notes),
        evidence_integrity=_evidence_integrity(decision, pack, ledger, notes),
        notes=notes,
    )
    return card


def review_priority(
    *,
    scorecard: EvidenceScorecard,
    decision: LedgerDecision,
    error_count: int = 0,
    trigger_count: int = 0,
    confidence_threshold: float = 0.65,
) -> float:
    """Triage number in ``[0, 1]`` — higher means "look at this one first".

    This is *not* a compliance score and must never be thresholded into a
    verdict. It combines evidence weakness with contract violations so a
    limited reviewer budget can be spent where it changes the most.
    """

    weakness = (
        0.28 * (1.0 - scorecard.citation_quality)
        + 0.22 * (1.0 - scorecard.support_coverage)
        + 0.20 * (1.0 - scorecard.evidence_integrity)
        + 0.15 * (1.0 - scorecard.regulatory_coverage)
    )

    # The contrary dimension has opposite polarity for the two label directions,
    # and both directions must raise priority so neither error class is buried:
    #   - a COMPLIANT verdict with STRONG contrary evidence risks a missed breach
    #     (false compliance) — the evidence points the other way;
    #   - a NON_COMPLIANT verdict with WEAK contrary evidence risks a missed
    #     compliance (false negative) — the case was judged non-compliant with
    #     little evidence of non-compliance to back it.
    # Under symmetric overall-accuracy scoring both errors cost the same. The
    # earlier form added only 0.05*contrary for a non-compliant verdict, which
    # meant the most suspicious non-compliant verdicts (little contrary support)
    # got the LOWEST review priority — exactly the false negatives that should be
    # reviewed first. Note support_coverage cannot play this role: it already
    # appears in the generic weakness term with the opposite polarity (low
    # support = weak evidence = review), so reusing it here would self-cancel.
    if decision.verdict == Verdict.COMPLIANT:
        weakness += 0.15 * scorecard.contrary_strength
    elif decision.verdict == Verdict.NON_COMPLIANT:
        weakness += 0.15 * (1.0 - scorecard.contrary_strength)
    # N/A carries no business label to contradict; it relies on the generic
    # weakness terms above (low confidence, citation gaps, integrity findings).

    if decision.confidence < confidence_threshold:
        weakness += 0.10 * (confidence_threshold - decision.confidence)

    weakness += min(0.25, 0.12 * error_count)
    weakness += min(0.10, 0.02 * trigger_count)
    return _clamp(weakness)


def scorecard_summary(scorecard: EvidenceScorecard) -> dict[str, float]:
    """Dimensions only — intentionally no aggregate key."""

    return dict(scorecard.as_dimensions())


def describe_scorecard(scorecard: EvidenceScorecard) -> str:
    parts = [
        f"{name}={value:.2f}" for name, value in scorecard.as_dimensions().items()
    ]
    return " ".join(parts)


__all__ = [
    "build_scorecard",
    "describe_scorecard",
    "review_priority",
    "scorecard_summary",
]
