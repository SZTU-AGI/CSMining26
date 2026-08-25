"""Stage D — rubric-anchored adjudication (proposal §5.5, §7).

The adjudicator receives exactly three things: the runtime rubric, the policy
snippets the rubric cites, and the compact fact pack. It returns `1`, `0` or
`N/A` together with the citations that justify it.

Contract enforced after the model responds
------------------------------------------
* **Dual citation (§7).** A `1` or a `0` must carry at least one policy citation
  *and* at least one case-fact citation. Citations that are not in the rubric /
  pack are stripped before the check, so an invented chunk id cannot satisfy it.
* **`N/A` is a legal conclusion, not a shrug (§7).** `N/A` requires
  ``NOT_APPLICABLE`` applicability plus an applicability explanation grounded in
  policy. "Nothing retrieved" or "materials incomplete" cannot produce `N/A`;
  those become quality flags and, if severe, a `0` with the flag attached.
* **Leakage (§3).** Facts flagged ``answer_like_field`` are removed from the
  supporting set. If the model leaned on them the decision is flagged rather
  than silently rewritten.
* **Contaminated evidence.** Another establishment's paperwork can never be the
  sole support for a compliant verdict.

The verdict itself is the model's, taken against the regulation. This module
does not compute it, and there is no rule here of the form "criterion X
satisfied ⇒ 1".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from freca.llm import JsonChatClient
from freca.models import Applicability, Verdict

from freca.ledger.config import AdjudicationConfig
from freca.ledger.models import (
    CheckpointRubric,
    CriterionOutcome,
    CriterionStatus,
    DecisionStage,
    EvidenceCoverage,
    EvidencePack,
    LedgerDecision,
)
from freca.ledger.selection import citable_fact_ids, contaminated_only, render_pack

ADJUDICATION_PROMPT_VERSION = "adjudicate-v1"

_ADJUDICATION_SYSTEM = """You audit ONE checking point for ONE case, using only the supplied rubric,
policy text and fact pack. You have no other knowledge of this case.

Procedure:
1. Applicability first. Decide from the rubric's applicability criteria and the cited policy text
   whether the checking point applies to this establishment.
2. For each rubric criterion, state a status: "satisfied", "violated", "not_evidenced" or
   "not_applicable", and list the fact_id values that justify it.
3. Then give the checking-point verdict:
   - "1" the case facts show the requirement is met;
   - "0" the case facts show a breach, or a required condition is contradicted or demonstrably
     absent in the material;
   - "N/A" the regulation itself makes this checking point inapplicable to this establishment.

Hard rules:
- "N/A" is a legal conclusion about applicability. Missing evidence, unretrieved material,
  parse failure, or your own uncertainty are NEVER "N/A"; report them in quality_flags.
- Every "1" and every "0" needs at least one policy citation AND at least one fact_id from the
  pack. Cite only ids that appear in the supplied material.
- A fact flagged "answer_like_field" is scenario-authoring metadata written by the exercise
  author, not farm evidence. Never rely on it; never restate it as a finding.
- A fact flagged "exclude_from_compliance_evidence" belongs to a different establishment. It may
  be contrary evidence, but it can never be the sole support for "1".
- Contradictions in the pack must be addressed in reasoning_summary, not ignored.
- confidence reflects how well the cited facts settle the rubric, not how plausible the story is.

Return only an object matching the supplied JSON schema."""

_SCOPE_AWARE_EVIDENCE_RULES = """

Evidence-scope rules:
- A fact marked as another establishment's material can never support this establishment.
- A global identity contradiction is a quality risk, not a reason to discard a self-contained,
  current-establishment fact whose own source and subject are clear.
- For a design, construction, facility or equipment requirement, an execution incident, cleaning
  lapse or procedure text alone does not prove a design or facility requirement is breached.
  A contrary fact must directly describe the relevant design or facility condition.
- For an execution, product-flow, batch or release requirement, a procedure alone is insufficient;
  use an actual record for the current establishment and product.
"""


_ADJUDICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "applicability",
        "verdict",
        "criterion_outcomes",
        "policy_citations",
        "supporting_fact_ids",
        "contrary_fact_ids",
        "evidence_coverage",
        "applicability_reasoning",
        "reasoning_summary",
        "confidence",
    ],
    "properties": {
        "applicability": {
            "type": "string",
            "enum": [item.value for item in Applicability],
        },
        "verdict": {"type": "string", "enum": [item.value for item in Verdict]},
        "criterion_outcomes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion_id", "status"],
                "properties": {
                    "criterion_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [item.value for item in CriterionStatus],
                    },
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
        },
        "policy_citations": {"type": "array", "items": {"type": "string"}},
        "supporting_fact_ids": {"type": "array", "items": {"type": "string"}},
        "contrary_fact_ids": {"type": "array", "items": {"type": "string"}},
        "contradiction_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_coverage": {
            "type": "string",
            "enum": [item.value for item in EvidenceCoverage],
        },
        "applicability_reasoning": {"type": "string"},
        "reasoning_summary": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "quality_flags": {"type": "array", "items": {"type": "string"}},
    },
}


def build_adjudication_messages(
    *,
    rubric: CheckpointRubric,
    pack: EvidencePack,
    scope_aware: bool = False,
) -> tuple[str, str]:
    user = "\n\n".join(
        (
            f"CASE {pack.case_id} — CHECKING POINT {rubric.cp_id}",
            render_pack(pack, rubric=rubric),
            "Valid policy citation ids: " + ", ".join(sorted(rubric.policy_chunk_ids)),
            "Valid fact ids: "
            + (", ".join(sorted(pack.fact_ids)) if pack.facts else "(none)"),
        )
    )
    system = _ADJUDICATION_SYSTEM
    if scope_aware:
        system += _SCOPE_AWARE_EVIDENCE_RULES
    return system, user


def _clean_ids(values: Any, allowed: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: list[str] = []
    for value in values:
        text = str(value).strip()
        if text in allowed and text not in seen:
            seen.append(text)
    return seen


def _parse_outcomes(values: Any, *, rubric: CheckpointRubric, allowed_facts: set[str]):
    known = {criterion.criterion_id for criterion in rubric.criteria}
    outcomes: list[CriterionOutcome] = []
    if not isinstance(values, list):
        values = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        criterion_id = str(item.get("criterion_id", "")).strip()
        if criterion_id not in known or criterion_id in seen:
            continue
        try:
            status = CriterionStatus(str(item.get("status", "")).strip().casefold())
        except ValueError:
            status = CriterionStatus.NOT_EVIDENCED
        seen.add(criterion_id)
        outcomes.append(
            CriterionOutcome(
                criterion_id=criterion_id,
                status=status,
                fact_ids=_clean_ids(item.get("fact_ids"), allowed_facts),
                note=str(item.get("note", "")).strip()[:400],
            )
        )
    for criterion_id in known - seen:
        outcomes.append(
            CriterionOutcome(
                criterion_id=criterion_id,
                status=CriterionStatus.NOT_EVIDENCED,
                note="adjudicator did not report this criterion",
            )
        )
    return sorted(outcomes, key=lambda item: item.criterion_id)


def normalize_decision(
    payload: dict[str, Any],
    *,
    rubric: CheckpointRubric,
    pack: EvidencePack,
    config: AdjudicationConfig,
    stage: DecisionStage = DecisionStage.PRIMARY,
) -> LedgerDecision:
    """Turn a raw model response into a validated, contract-checked decision.

    Repairs performed here are *conservative*: they can withdraw an
    unsupportable claim (strip an invented citation, refuse an unexplained
    ``N/A``) but they never invent one, and every repair leaves a quality flag.
    """

    allowed_policy = set(rubric.policy_chunk_ids)
    allowed_facts = set(pack.fact_ids)
    citable = citable_fact_ids(pack)
    allowed_contradictions = {item.contradiction_id for item in pack.contradictions}

    flags: list[str] = [
        str(flag).strip()
        for flag in (payload.get("quality_flags") or [])
        if str(flag).strip()
    ]

    raw_policy = payload.get("policy_citations")
    policy_citations = _clean_ids(raw_policy, allowed_policy)
    if isinstance(raw_policy, list) and len(raw_policy) > len(policy_citations):
        flags.append("dropped_unknown_policy_citation")

    raw_support = payload.get("supporting_fact_ids")
    supporting = _clean_ids(raw_support, allowed_facts)
    if isinstance(raw_support, list) and len(raw_support) > len(supporting):
        flags.append("dropped_unknown_fact_citation")

    leaked = [fact_id for fact_id in supporting if fact_id not in citable]
    if leaked:
        supporting = [fact_id for fact_id in supporting if fact_id in citable]
        flags.append("dropped_answer_like_support")

    contrary = _clean_ids(payload.get("contrary_fact_ids"), allowed_facts)
    contradiction_ids = _clean_ids(payload.get("contradiction_ids"), allowed_contradictions)

    try:
        applicability = Applicability(str(payload.get("applicability", "")).strip().upper())
    except ValueError:
        applicability = Applicability.UNKNOWN
        flags.append("applicability_unparsed")
    try:
        verdict = Verdict(str(payload.get("verdict", "")).strip())
    except ValueError:
        verdict = Verdict.NON_COMPLIANT
        flags.append("verdict_unparsed")

    applicability_reasoning = str(payload.get("applicability_reasoning", "")).strip()

    # §7: N/A must be a policy conclusion. Withdraw it otherwise.
    if verdict == Verdict.NOT_APPLICABLE:
        if applicability != Applicability.NOT_APPLICABLE:
            verdict = Verdict.NON_COMPLIANT
            flags.append("na_withdrawn_nonlegal_applicability")
        elif not policy_citations:
            verdict = Verdict.NON_COMPLIANT
            applicability = Applicability.UNKNOWN
            flags.append("na_withdrawn_no_policy_basis")
        elif not applicability_reasoning:
            verdict = Verdict.NON_COMPLIANT
            applicability = Applicability.UNKNOWN
            flags.append("na_withdrawn_no_applicability_reasoning")
        else:
            applicability = Applicability.NOT_APPLICABLE
    elif applicability == Applicability.NOT_APPLICABLE:
        # A business verdict with NOT_APPLICABLE applicability is incoherent.
        applicability = Applicability.APPLICABLE
        flags.append("applicability_realigned_to_verdict")

    if verdict != Verdict.NOT_APPLICABLE and config.require_dual_citation:
        if not policy_citations:
            flags.append("missing_policy_citation")
        if not supporting and not contrary:
            flags.append("missing_case_citation")

    if verdict == Verdict.COMPLIANT:
        supporting_facts = [
            item.fact for item in pack.facts if item.fact.fact_id in set(supporting)
        ]
        if supporting_facts and all(fact.is_contaminated for fact in supporting_facts):
            flags.append("compliant_supported_only_by_contaminated_evidence")
        if contaminated_only(pack):
            flags.append("pack_contains_only_contaminated_evidence")

    try:
        coverage = EvidenceCoverage(str(payload.get("evidence_coverage", "")).strip().casefold())
    except ValueError:
        coverage = EvidenceCoverage.PARTIAL
    if pack.uncovered_criteria and coverage == EvidenceCoverage.COMPLETE:
        coverage = EvidenceCoverage.PARTIAL
        flags.append("coverage_downgraded_uncovered_criteria")

    confidence = payload.get("confidence", 0.0)
    try:
        confidence = min(max(float(confidence), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
        flags.append("confidence_unparsed")

    return LedgerDecision(
        case_id=pack.case_id,
        cp_id=rubric.cp_id,
        applicability=applicability,
        verdict=verdict,
        criterion_outcomes=_parse_outcomes(
            payload.get("criterion_outcomes"),
            rubric=rubric,
            allowed_facts=allowed_facts,
        ),
        policy_citations=policy_citations,
        supporting_fact_ids=supporting,
        contrary_fact_ids=contrary,
        contradiction_ids=contradiction_ids,
        evidence_coverage=coverage,
        applicability_reasoning=applicability_reasoning[:1200],
        reasoning_summary=str(payload.get("reasoning_summary", "")).strip()[:2000],
        confidence=confidence,
        quality_flags=sorted(set(flags)),
        stage=stage,
        rubric_version=rubric.rubric_version,
    )


def blocked_decision(
    *,
    rubric: CheckpointRubric,
    pack: EvidencePack,
    reason: str,
    stage: DecisionStage = DecisionStage.PRIMARY,
) -> LedgerDecision:
    """A recorded failure, not a guess.

    When the adjudicator cannot run we still emit a decision object so the run
    is complete and auditable, but it carries ``adjudication_blocked`` and zero
    confidence, which makes the gate fail and forces review. It is never
    presented as a considered verdict.
    """

    return LedgerDecision(
        case_id=pack.case_id,
        cp_id=rubric.cp_id,
        applicability=Applicability.UNKNOWN,
        verdict=Verdict.NON_COMPLIANT,
        criterion_outcomes=[
            CriterionOutcome(
                criterion_id=criterion.criterion_id,
                status=CriterionStatus.NOT_EVIDENCED,
                note="adjudication did not run",
            )
            for criterion in rubric.criteria
        ],
        policy_citations=[],
        supporting_fact_ids=[],
        contrary_fact_ids=[],
        evidence_coverage=EvidenceCoverage.INSUFFICIENT,
        applicability_reasoning="",
        reasoning_summary=f"adjudication blocked: {reason}",
        confidence=0.0,
        quality_flags=["adjudication_blocked"],
        stage=stage,
        rubric_version=rubric.rubric_version,
    )


@dataclass
class Adjudicator:
    """Stage D driver."""

    client: JsonChatClient | None = None
    config: AdjudicationConfig = field(default_factory=AdjudicationConfig)
    prompt_version: str = ADJUDICATION_PROMPT_VERSION

    def adjudicate(
        self,
        *,
        rubric: CheckpointRubric,
        pack: EvidencePack,
        stage: DecisionStage = DecisionStage.PRIMARY,
    ) -> LedgerDecision:
        if rubric.cp_id != pack.cp_id:
            raise ValueError("rubric and pack refer to different checking points")
        if self.client is None:
            return blocked_decision(
                rubric=rubric,
                pack=pack,
                reason="no adjudication model client configured",
                stage=stage,
            )
        system, user = build_adjudication_messages(
            rubric=rubric,
            pack=pack,
            scope_aware=self.config.scope_aware_evidence,
        )
        try:
            payload = self.client.complete_json(
                system=system,
                user=user,
                schema=_ADJUDICATION_SCHEMA,
            )
        except Exception as exc:  # noqa: BLE001 - a failed call must stay visible
            return blocked_decision(
                rubric=rubric,
                pack=pack,
                reason=f"{type(exc).__name__}: {exc}",
                stage=stage,
            )
        return normalize_decision(
            payload,
            rubric=rubric,
            pack=pack,
            config=self.config,
            stage=stage,
        )


def outcomes_by_status(
    decision: LedgerDecision,
    status: CriterionStatus,
) -> Sequence[CriterionOutcome]:
    return [item for item in decision.criterion_outcomes if item.status == status]


__all__ = [
    "ADJUDICATION_PROMPT_VERSION",
    "Adjudicator",
    "blocked_decision",
    "build_adjudication_messages",
    "normalize_decision",
    "outcomes_by_status",
]
