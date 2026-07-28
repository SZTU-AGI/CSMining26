from __future__ import annotations

from freca.experiments.models import ExecutionResult, SilverComparison


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
