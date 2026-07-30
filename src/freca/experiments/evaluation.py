from __future__ import annotations

from collections.abc import Sequence

from freca.experiments.models import (
    ExecutionResult,
    SilverComparison,
    SilverReference,
    SilverTier,
)


def compare_to_reference(
    *,
    candidate: ExecutionResult,
    reference: ExecutionResult,
) -> SilverComparison:
    """Measure agreement with a frozen LLM reference, never official accuracy."""
    candidate_by_cp = {verdict.cp_id: verdict.verdict for verdict in candidate.verdicts}
    reference_by_cp = {verdict.cp_id: verdict.verdict for verdict in reference.verdicts}
    shared = tuple(
        sorted(candidate_by_cp.keys() & reference_by_cp.keys(), key=lambda cp_id: int(cp_id[2:]))
    )
    matched = tuple(
        cp_id for cp_id in shared if candidate_by_cp[cp_id] == reference_by_cp[cp_id]
    )
    return SilverComparison(
        shared_checkpoints=shared,
        matched_checkpoints=matched,
        silver_agreement=len(matched) / len(shared) if shared else 0.0,
    )


def compare_to_silver(
    *,
    candidate: ExecutionResult,
    reference: SilverReference,
    anchored_tiers: Sequence[SilverTier] = (SilverTier.ANOMALY_RULE, SilverTier.HUMAN),
) -> SilverComparison:
    """Compare a candidate run against the layered silver reference.

    Only CPs whose silver entry is in ``anchored_tiers`` (default: anomaly-rule
    and human-labelled) count toward ``silver_agreement``. Weak-consensus entries
    are excluded because their source has no external anchor — counting them
    would make "agreement" circular (the candidate is compared against itself).
    """
    anchored = set(anchored_tiers)
    candidate_by_cp = {verdict.cp_id: verdict.verdict for verdict in candidate.verdicts}
    case_entries = reference.entries.get(str(candidate.unit.case_id), {})
    shared: list[str] = []
    matched: list[str] = []
    for cp_id, candidate_verdict in sorted(
        candidate_by_cp.items(), key=lambda item: int(item[0][2:])
    ):
        entry = case_entries.get(cp_id)
        if entry is None or entry.tier not in anchored:
            continue
        shared.append(cp_id)
        if candidate_verdict == entry.verdict:
            matched.append(cp_id)
    return SilverComparison(
        shared_checkpoints=tuple(shared),
        matched_checkpoints=tuple(matched),
        silver_agreement=len(matched) / len(shared) if shared else 0.0,
    )
