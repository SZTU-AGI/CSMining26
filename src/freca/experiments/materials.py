from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from freca.experiments.models import MaterialSnapshot
from freca.models import CheckpointDefinition, EvidenceChunk
from freca.state import build_cache_key


def build_material_snapshot(
    *,
    case_id: int,
    checkpoints: Sequence[CheckpointDefinition],
    policy_chunks: Sequence[EvidenceChunk],
    case_chunks: Sequence[EvidenceChunk],
    image_paths: Sequence[Path] = (),
) -> MaterialSnapshot:
    """Bundle immutable official inputs without semantic filtering or relabelling."""
    if not checkpoints:
        raise ValueError("at least one checkpoint is required")
    if any(chunk.case_id is not None for chunk in policy_chunks):
        raise ValueError("policy chunks must not be assigned to a case")
    if any(chunk.case_id != case_id for chunk in case_chunks):
        raise ValueError(f"all case chunks must belong to case {case_id}")

    chunks = tuple(policy_chunks) + tuple(case_chunks)
    image_path_strings = tuple(str(path) for path in image_paths)
    return MaterialSnapshot(
        case_id=case_id,
        checkpoints=tuple(checkpoints),
        chunks=chunks,
        image_paths=image_path_strings,
        input_sha256=build_cache_key(
            {
                "case_id": case_id,
                "checkpoint_ids": [checkpoint.cp_id for checkpoint in checkpoints],
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                "image_paths": image_path_strings,
            }
        ),
    )
