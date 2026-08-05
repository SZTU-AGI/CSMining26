"""Stage C — compact evidence pack construction (proposal §5.4, §7).

The adjudicator never sees the nine source documents. It sees a *pack*: the
subset of the case's fact ledger that the rubric's own criteria ask about, plus
every contradiction the ledger recorded.

Two problems this solves
------------------------
Prompt size
    A full-materials prompt for one case runs to roughly 600 KB (§2), which is
    both expensive and, empirically, permissive — the model finds something
    agreeable in the noise. A pack of ~28 facts is small enough to reason over
    and large enough to carry the contrary evidence.

Attributable selection
    Every packed fact records *which criterion* pulled it in and *why*
    (``matched_criteria``, ``match_reasons``). Criteria that pulled in nothing
    are reported as ``uncovered_criteria`` rather than silently disappearing,
    which is what lets the gate distinguish "the regulation is satisfied" from
    "we never looked".

Scoring is lexical and deterministic. It is a *routing* mechanism between the
ledger and the rubric, not a judgement: it decides what the adjudicator reads,
never what the adjudicator concludes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from freca.ledger.config import SelectionConfig
from freca.ledger.leakage import ANSWER_LIKE_FLAG
from freca.ledger.models import (
    CaseFactLedger,
    CheckpointRubric,
    CriterionKind,
    EvidencePack,
    FactRecord,
    PackedFact,
    RubricCriterion,
)
from freca.ledger.taxonomy import classify_topic

CONTAMINATION_FLAG = "exclude_from_compliance_evidence"

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """
    a an and any are as at be been being but by can could do does for from has have
    if in into is it its may must not of on or shall should such that the their then
    there these this those to under upon was were what when where which while who
    with within would establishment requirement required evidence record records
    """.split()
)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall((text or "").casefold())
        if len(token) > 2 and token not in _STOPWORDS
    }


@dataclass(frozen=True)
class CriterionProfile:
    """Pre-computed lexical profile of one rubric criterion."""

    criterion_id: str
    kind: CriterionKind
    tokens: frozenset[str]
    topics: frozenset[str]
    categories: frozenset[str]

    @classmethod
    def build(cls, criterion: RubricCriterion) -> CriterionProfile:
        text = " ".join(
            (
                criterion.statement,
                " ".join(criterion.facts_to_verify),
            )
        )
        topics = {classify_topic(criterion.statement)}
        for item in criterion.facts_to_verify:
            topics.add(classify_topic(item))
        topics.discard("unclassified")
        return cls(
            criterion_id=criterion.criterion_id,
            kind=criterion.kind,
            tokens=frozenset(_tokens(text)),
            topics=frozenset(topics),
            categories=frozenset(criterion.required_evidence_categories),
        )


def score_fact(
    fact: FactRecord,
    profile: CriterionProfile,
    config: SelectionConfig,
) -> tuple[float, list[str]]:
    """Score one fact against one criterion; return the score and its reasons."""

    fact_tokens = _tokens(f"{fact.claim} {fact.value} {fact.verbatim}")
    if not fact_tokens or not profile.tokens:
        return 0.0, []
    overlap = fact_tokens & profile.tokens
    if not overlap:
        return 0.0, []

    score = len(overlap) / (len(profile.tokens) ** 0.5)
    reasons = [f"lexical:{len(overlap)}"]

    if fact.topic in profile.topics:
        score += config.topic_bonus
        reasons.append(f"topic:{fact.topic}")
    matched_categories = set(fact.evidence_categories) & profile.categories
    if matched_categories:
        score += config.category_bonus * len(matched_categories)
        reasons.append("category:" + ",".join(sorted(matched_categories)))
    if profile.kind == CriterionKind.CONTRARY and fact.is_contaminated:
        # Contaminated material is another establishment's paperwork: it can
        # never support this establishment, but it is real contrary signal.
        score += config.category_bonus
        reasons.append("contaminated_supports_contrary")
    return round(score, 4), reasons


def _truncate(fact: FactRecord, limit: int) -> FactRecord:
    if len(fact.verbatim) <= limit:
        return fact
    return fact.model_copy(update={"verbatim": fact.verbatim[:limit] + " …[truncated]"})


def _eligible(fact: FactRecord, config: SelectionConfig) -> tuple[bool, str]:
    if ANSWER_LIKE_FLAG in fact.quality_flags and not config.include_answer_like:
        return False, "answer_like_field"
    if fact.is_contaminated and not config.include_contaminated:
        return False, "contaminated"
    return True, ""


def build_evidence_pack(
    *,
    ledger: CaseFactLedger,
    rubric: CheckpointRubric,
    config: SelectionConfig | None = None,
) -> EvidencePack:
    """Select the facts the adjudicator may consider for one case × CP."""

    settings = config or SelectionConfig()
    profiles = [CriterionProfile.build(criterion) for criterion in rubric.criteria]

    excluded = 0
    candidates: list[FactRecord] = []
    for fact in ledger.facts:
        ok, _reason = _eligible(fact, settings)
        if not ok:
            excluded += 1
            continue
        candidates.append(fact)

    scored: dict[str, dict[str, Any]] = {}
    per_criterion: dict[str, list[tuple[float, FactRecord]]] = defaultdict(list)

    for fact in candidates:
        total = 0.0
        matched: list[str] = []
        reasons: list[str] = []
        for profile in profiles:
            score, why = score_fact(fact, profile, settings)
            if score <= 0:
                continue
            total += score
            matched.append(profile.criterion_id)
            reasons.extend(f"{profile.criterion_id}:{item}" for item in why)
            per_criterion[profile.criterion_id].append((score, fact))
        if not matched:
            continue
        scored[fact.fact_id] = {
            "fact": fact,
            "relevance": round(total, 4),
            "matched": matched,
            "reasons": reasons,
        }

    # Guarantee a floor of coverage per criterion before global ranking, so a
    # narrow criterion is never crowded out by a verbose one.
    selected_ids: list[str] = []
    for criterion_id in (profile.criterion_id for profile in profiles):
        ranked = sorted(
            per_criterion.get(criterion_id, []),
            key=lambda item: (-item[0], item[1].fact_id),
        )
        for _score, fact in ranked[: settings.min_facts_per_criterion]:
            if fact.fact_id not in selected_ids:
                selected_ids.append(fact.fact_id)

    remaining = sorted(
        (entry for key, entry in scored.items() if key not in selected_ids),
        key=lambda entry: (-entry["relevance"], entry["fact"].fact_id),
    )
    for entry in remaining:
        if len(selected_ids) >= settings.max_facts:
            break
        selected_ids.append(entry["fact"].fact_id)
    selected_ids = selected_ids[: settings.max_facts]

    packed = [
        PackedFact(
            fact=_truncate(scored[fact_id]["fact"], settings.verbatim_char_limit),
            relevance=scored[fact_id]["relevance"],
            matched_criteria=sorted(set(scored[fact_id]["matched"])),
            match_reasons=sorted(set(scored[fact_id]["reasons"]))[:12],
        )
        for fact_id in selected_ids
        if fact_id in scored
    ]
    packed.sort(key=lambda item: (-item.relevance, item.fact.fact_id))

    coverage = {
        profile.criterion_id: sum(
            1 for item in packed if profile.criterion_id in item.matched_criteria
        )
        for profile in profiles
    }
    uncovered = sorted(key for key, count in coverage.items() if count == 0)

    contradictions = list(ledger.contradictions) if settings.include_all_contradictions else []

    integrity_notes = list(ledger.quality_flags)
    if ledger.missing_tracks:
        integrity_notes.append(
            "missing_tracks:" + ",".join(str(track) for track in ledger.missing_tracks)
        )
    if any(item.fact.is_contaminated for item in packed):
        integrity_notes.append("pack_contains_contaminated_evidence")
    if excluded:
        integrity_notes.append(f"excluded_facts:{excluded}")

    return EvidencePack(
        case_id=ledger.case_id,
        cp_id=rubric.cp_id,
        rubric_version=rubric.rubric_version,
        facts=packed,
        contradictions=contradictions,
        integrity_notes=sorted(set(integrity_notes)),
        coverage_by_criterion=coverage,
        uncovered_criteria=uncovered,
        ledger_fact_count=len(ledger.facts),
        excluded_fact_count=excluded,
        selection_trace=[
            {
                "criterion_id": profile.criterion_id,
                "kind": profile.kind.value,
                "topics": sorted(profile.topics),
                "candidates": len(per_criterion.get(profile.criterion_id, [])),
                "selected": coverage[profile.criterion_id],
            }
            for profile in profiles
        ],
    )


def compact_pack(pack: EvidencePack, *, max_facts: int, verbatim_char_limit: int) -> EvidencePack:
    """Shrink a pack for the review stage (§7: review uses compact context)."""

    kept = pack.facts[:max_facts]
    return pack.model_copy(
        update={
            "facts": [
                item.model_copy(
                    update={"fact": _truncate(item.fact, verbatim_char_limit)}
                )
                for item in kept
            ]
        }
    )


def facts_by_ids(pack: EvidencePack, ids: Iterable[str]) -> list[FactRecord]:
    index = pack.fact_index()
    return [index[fact_id] for fact_id in ids if fact_id in index]


def render_pack(pack: EvidencePack, *, rubric: CheckpointRubric) -> str:
    """Human- and model-readable rendering used by Stage D and the review."""

    lines: list[str] = []
    lines.append("REGULATORY RUBRIC (derived at runtime from official rules)")
    lines.append(f"cp_id: {rubric.cp_id} | element: {rubric.element_title}")
    lines.append(f"checking point: {rubric.checkpoint_text}")
    if rubric.applicability_note:
        lines.append(f"applicability note: {rubric.applicability_note}")
    for criterion in rubric.criteria:
        lines.append(
            f"- [{criterion.kind.value}] {criterion.criterion_id}: {criterion.statement}"
        )
        lines.append(f"  policy_citations: {', '.join(criterion.policy_citations)}")
        if criterion.facts_to_verify:
            lines.append(f"  facts_to_verify: {'; '.join(criterion.facts_to_verify)}")
        if criterion.required_evidence_categories:
            lines.append(
                "  required_evidence_categories: "
                + ", ".join(criterion.required_evidence_categories)
            )

    lines.append("")
    lines.append("POLICY TEXT")
    for chunk_id, snippet in sorted(rubric.policy_snippets.items()):
        lines.append(f"[{chunk_id}] {snippet}")

    lines.append("")
    lines.append(f"CASE FACT PACK (case {pack.case_id})")
    if not pack.facts:
        lines.append("(no ledger fact matched any rubric criterion)")
    for item in pack.facts:
        fact = item.fact
        flags = ",".join(fact.quality_flags) or "-"
        marker = " ⚠CONTAMINATED" if fact.is_contaminated else ""
        lines.append(
            f"[{fact.fact_id}]{marker} topic={fact.topic} criteria={','.join(item.matched_criteria)}"
        )
        lines.append(f"  claim: {fact.claim}")
        if fact.value:
            lines.append(f"  value: {fact.value}")
        lines.append(f"  source: {fact.locator()} (chunk {fact.chunk_id}, track {fact.track})")
        lines.append(f"  verbatim: {fact.verbatim}")
        lines.append(f"  quality_flags: {flags}")

    if pack.contradictions:
        lines.append("")
        lines.append("LEDGER CONTRADICTIONS")
        for contradiction in pack.contradictions:
            lines.append(
                f"[{contradiction.contradiction_id}] {contradiction.kind.value} "
                f"({contradiction.severity}) topic={contradiction.topic}: {contradiction.detail}"
            )

    lines.append("")
    lines.append("EVIDENCE COVERAGE")
    lines.append(
        "criteria with no matching case fact: "
        + (", ".join(pack.uncovered_criteria) if pack.uncovered_criteria else "none")
    )
    if pack.integrity_notes:
        lines.append("integrity notes: " + "; ".join(pack.integrity_notes))
    return "\n".join(lines)


def contaminated_only(pack: EvidencePack) -> bool:
    """True when every packed fact is another establishment's paperwork."""

    if not pack.facts:
        return False
    return all(item.fact.is_contaminated for item in pack.facts)


def citable_fact_ids(pack: EvidencePack) -> set[str]:
    """Facts that may back a *supporting* citation (§3: no answer-like text)."""

    return {item.fact.fact_id for item in pack.facts if item.fact.citable_for_support}


def summarize_pack(pack: EvidencePack) -> dict[str, Any]:
    return {
        "facts": len(pack.facts),
        "ledger_facts": pack.ledger_fact_count,
        "excluded_facts": pack.excluded_fact_count,
        "contradictions": len(pack.contradictions),
        "uncovered_criteria": list(pack.uncovered_criteria),
        "integrity_notes": list(pack.integrity_notes),
        "contaminated_facts": sum(1 for item in pack.facts if item.fact.is_contaminated),
    }


def criterion_ids(rubric: CheckpointRubric) -> Sequence[str]:
    return [criterion.criterion_id for criterion in rubric.criteria]


__all__ = [
    "CriterionProfile",
    "build_evidence_pack",
    "citable_fact_ids",
    "compact_pack",
    "contaminated_only",
    "criterion_ids",
    "facts_by_ids",
    "render_pack",
    "score_fact",
    "summarize_pack",
]
