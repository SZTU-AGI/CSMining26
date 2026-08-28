"""Stage E — conditional independent review (proposal §7).

Review is *conditional*, not routine. It runs only when a gate raised a trigger:
a rubric without regulatory basis, missing key supporting/contrary facts, a
same-topic contradiction in the fact ledger, reasoning inconsistent with the
label, low confidence or an invalid citation.

It also runs *compact*. §7 is explicit that the reviewer must receive tightened
regulatory snippets and the relevant facts, not a re-send of the full
regulation plus the entire case folder. :func:`compact_rubric` and
``selection.compact_pack`` implement that.

Reconciliation is deliberately conservative:

* a blocked review never overrides a primary decision;
* a review that fails its own gates never overrides a primary that passed;
* when the two disagree and both pass their gates, ``prefer_review_on_conflict``
  decides, and the disagreement is recorded on the final record either way.

Nothing here ever invents a verdict. The final decision is always one of the
two decisions that were actually produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from freca.ledger.adjudicate import Adjudicator
from freca.ledger.critic import ConflictCritic
from freca.ledger.config import AdjudicationConfig, ReviewConfig, ReviewMode
from freca.ledger.criteria import CURATED_CHUNK_PREFIX
from freca.ledger.gates import evaluate_gates, gate_flags
from freca.ledger.models import (
    CaseFactLedger,
    CheckpointRubric,
    DecisionStage,
    EvidencePack,
    GateReport,
    LedgerDecision,
    TaskOutcome,
)
from freca.ledger.selection import compact_pack, summarize_pack

ACCEPT_PRIMARY = "ACCEPT_PRIMARY"
ACCEPT_PRIMARY_CONFIRMED = "ACCEPT_PRIMARY_CONFIRMED"
ACCEPT_PRIMARY_REVIEW_BLOCKED = "ACCEPT_PRIMARY_REVIEW_BLOCKED"
ACCEPT_PRIMARY_REVIEW_FAILED_GATES = "ACCEPT_PRIMARY_REVIEW_FAILED_GATES"
ACCEPT_PRIMARY_ON_CONFLICT = "ACCEPT_PRIMARY_ON_CONFLICT"
ACCEPT_REVIEW_PRIMARY_FAILED_GATES = "ACCEPT_REVIEW_PRIMARY_FAILED_GATES"
ACCEPT_REVIEW_ON_CONFLICT = "ACCEPT_REVIEW_ON_CONFLICT"
ACCEPT_CRITIC_PRIMARY = "ACCEPT_CRITIC_PRIMARY"
ACCEPT_CRITIC_REVIEW = "ACCEPT_CRITIC_REVIEW"
ESCALATE_BOTH_GATES_FAILED = "ESCALATE_BOTH_GATES_FAILED"


def compact_rubric(rubric: CheckpointRubric, *, snippet_char_limit: int) -> CheckpointRubric:
    """Tighten policy snippets for the review pass (§7).

    Only the rendered text shrinks. ``policy_chunk_ids`` and every criterion's
    citations are preserved, so the rubric's citation contract still holds and
    the reviewer's citations remain checkable against the same clause ids.

    Curated scoring-standard chunks (``curated:`` prefix) are exempt: they are
    the team's authoritative criterion text, and truncating them would make the
    review pass judge against a partial standard.
    """

    snippets = {
        chunk_id: (
            text
            if chunk_id.startswith(CURATED_CHUNK_PREFIX)
            or len(text) <= snippet_char_limit
            else text[:snippet_char_limit] + " …"
        )
        for chunk_id, text in rubric.policy_snippets.items()
    }
    return rubric.model_copy(update={"policy_snippets": snippets})


def _annotate(decision: LedgerDecision, flags: list[str]) -> LedgerDecision:
    merged = sorted({*decision.quality_flags, *flags})
    return decision.model_copy(update={"quality_flags": merged})


def choose_final(
    *,
    primary: LedgerDecision,
    primary_gate: GateReport,
    review: LedgerDecision | None,
    review_gate: GateReport | None,
    config: ReviewConfig,
) -> tuple[LedgerDecision, str]:
    """Pick the deliverable decision and name the reason."""

    if review is None or review_gate is None:
        return primary, ACCEPT_PRIMARY

    if "adjudication_blocked" in review.quality_flags:
        return primary, ACCEPT_PRIMARY_REVIEW_BLOCKED

    agreed = review.verdict == primary.verdict

    if primary_gate.passed and not review_gate.passed:
        return primary, ACCEPT_PRIMARY_REVIEW_FAILED_GATES
    if review_gate.passed and not primary_gate.passed:
        return review, ACCEPT_REVIEW_PRIMARY_FAILED_GATES

    if primary_gate.passed and review_gate.passed:
        if agreed:
            # Same label from both passes: keep the primary, which was decided
            # on the full pack and therefore carries the richer citation set.
            return primary, ACCEPT_PRIMARY_CONFIRMED
        if config.prefer_review_on_conflict:
            return review, ACCEPT_REVIEW_ON_CONFLICT
        return primary, ACCEPT_PRIMARY_ON_CONFLICT

    # Both failed their gates: neither is deliverable. Keep the one with fewer
    # contract errors so the escalation carries the least broken record.
    if len(review_gate.errors) < len(primary_gate.errors):
        return review, ESCALATE_BOTH_GATES_FAILED
    return primary, ESCALATE_BOTH_GATES_FAILED


@dataclass
class ReviewCoordinator:
    """Decides whether to review, runs it compactly, and reconciles."""

    adjudicator: Adjudicator
    config: ReviewConfig = field(default_factory=ReviewConfig)
    adjudication_config: AdjudicationConfig = field(default_factory=AdjudicationConfig)
    critic: ConflictCritic | None = None

    # -- policy -----------------------------------------------------------

    def should_review(self, gate: GateReport) -> bool:
        if self.config.mode == ReviewMode.DISABLED:
            return False
        if self.config.mode == ReviewMode.ALWAYS:
            return True
        return gate.needs_review

    # -- execution --------------------------------------------------------

    def run_review(
        self,
        *,
        rubric: CheckpointRubric,
        pack: EvidencePack,
        ledger: CaseFactLedger | None = None,
    ) -> tuple[LedgerDecision, GateReport, EvidencePack]:
        tight_rubric = compact_rubric(
            rubric, snippet_char_limit=self.config.snippet_char_limit
        )
        tight_pack = compact_pack(
            pack,
            max_facts=self.config.max_facts,
            verbatim_char_limit=self.config.snippet_char_limit,
        )
        decision = self.adjudicator.adjudicate(
            rubric=tight_rubric,
            pack=tight_pack,
            stage=DecisionStage.REVIEW,
        )
        gate = evaluate_gates(
            decision=decision,
            pack=tight_pack,
            rubric=tight_rubric,
            ledger=ledger,
            config=self.adjudication_config,
        )
        return decision, gate, tight_pack

    def resolve(
        self,
        *,
        rubric: CheckpointRubric,
        pack: EvidencePack,
        primary: LedgerDecision,
        primary_gate: GateReport,
        ledger: CaseFactLedger | None = None,
    ) -> TaskOutcome:
        """Produce the full auditable record for one case×CP."""

        review: LedgerDecision | None = None
        review_gate: GateReport | None = None

        if self.should_review(primary_gate):
            review, review_gate, _ = self.run_review(
                rubric=rubric, pack=pack, ledger=ledger
            )

        final, resolution = choose_final(
            primary=primary,
            primary_gate=primary_gate,
            review=review,
            review_gate=review_gate,
            config=self.config,
        )
        critic_record: dict[str, str] = {}
        clean_conflict = (
            review is not None
            and review_gate is not None
            and primary_gate.passed
            and review_gate.passed
            and primary.verdict != review.verdict
        )
        if clean_conflict and self.critic is not None:
            choice, reasoning = self.critic.choose(
                rubric=rubric,
                pack=pack,
                primary=primary,
                primary_gate=primary_gate,
                review=review,
                review_gate=review_gate,
            )
            critic_record = {"choice": choice or "unavailable", "reasoning": reasoning}
            if choice == "primary":
                final, resolution = primary, ACCEPT_CRITIC_PRIMARY
            elif choice == "review":
                final, resolution = review, ACCEPT_CRITIC_REVIEW

        chosen_gate = (
            review_gate if (review is not None and final is review) else primary_gate
        )
        flags = [f"resolution:{resolution.lower()}", *gate_flags(chosen_gate)]
        if review is not None:
            flags.append("independent_review_performed")
            flags.append(
                "review_agreed_with_primary"
                if review.verdict == primary.verdict
                else "review_disagreed_with_primary"
            )
            if primary_gate.review_triggers:
                flags.append(
                    "review_triggers:" + "|".join(primary_gate.review_triggers[:6])
                )

        return TaskOutcome(
            case_id=primary.case_id,
            cp_id=primary.cp_id,
            primary=primary,
            primary_gate=primary_gate,
            review=review,
            review_gate=review_gate,
            final=_annotate(final, flags),
            reviewed=review is not None,
            resolution=resolution,
            critic=critic_record,
            pack_summary={
                **summarize_pack(pack),
                "rubric_version": rubric.rubric_version,
                "review_priority": round(primary_gate.review_priority, 4),
            },
        )


def accept_without_review(
    *,
    rubric: CheckpointRubric,
    pack: EvidencePack,
    primary: LedgerDecision,
    primary_gate: GateReport,
) -> TaskOutcome:
    """Build an outcome for a decision that needs no second pass."""

    flags = [f"resolution:{ACCEPT_PRIMARY.lower()}", *gate_flags(primary_gate)]
    return TaskOutcome(
        case_id=primary.case_id,
        cp_id=primary.cp_id,
        primary=primary,
        primary_gate=primary_gate,
        review=None,
        review_gate=None,
        final=_annotate(primary, flags),
        reviewed=False,
        resolution=ACCEPT_PRIMARY,
        pack_summary={
            **summarize_pack(pack),
            "rubric_version": rubric.rubric_version,
            "review_priority": round(primary_gate.review_priority, 4),
        },
    )


__all__ = [
    "ACCEPT_PRIMARY",
    "ACCEPT_PRIMARY_CONFIRMED",
    "ACCEPT_PRIMARY_ON_CONFLICT",
    "ACCEPT_PRIMARY_REVIEW_BLOCKED",
    "ACCEPT_PRIMARY_REVIEW_FAILED_GATES",
    "ACCEPT_CRITIC_PRIMARY",
    "ACCEPT_CRITIC_REVIEW",
    "ACCEPT_REVIEW_ON_CONFLICT",
    "ACCEPT_REVIEW_PRIMARY_FAILED_GATES",
    "ESCALATE_BOTH_GATES_FAILED",
    "ReviewCoordinator",
    "accept_without_review",
    "choose_final",
    "compact_rubric",
]
