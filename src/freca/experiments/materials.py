from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from freca.experiments.models import MaterialSnapshot
from freca.models import CheckpointDefinition, ContentKind, EvidenceChunk
from freca.state import build_cache_key, read_json


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


def load_material_snapshot_from_parsed(
    *,
    parsed_dir: Path,
    case_id: int,
    checkpoints: Sequence[CheckpointDefinition],
) -> MaterialSnapshot:
    """Load policy, current-case chunks, and retained originals from parse artifacts."""
    policy_path = parsed_dir / "policy.json"
    if not policy_path.is_file():
        raise FileNotFoundError(f"missing parsed policy chunks: {policy_path}")
    case_dir = parsed_dir / "cases" / f"{case_id:03d}"
    case_paths = sorted(
        path for path in case_dir.glob("*.json") if not path.name.endswith(".error.json")
    )
    if not case_paths:
        raise FileNotFoundError(f"missing parsed case chunks: {case_dir}")

    policy_chunks = tuple(EvidenceChunk.model_validate(item) for item in read_json(policy_path))
    case_chunks = tuple(
        chunk
        for path in case_paths
        for chunk in (EvidenceChunk.model_validate(item) for item in read_json(path))
    )
    image_paths: list[Path] = []
    for chunk in case_chunks:
        if chunk.content_kind != ContentKind.IMAGE:
            continue
        extracted_path = chunk.metadata.get("extracted_path")
        if not isinstance(extracted_path, str):
            raise ValueError(f"image chunk {chunk.chunk_id} has no extracted_path")
        image_path = Path(extracted_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"missing original image for {chunk.chunk_id}: {image_path}")
        image_paths.append(image_path)

    return build_material_snapshot(
        case_id=case_id,
        checkpoints=checkpoints,
        policy_chunks=policy_chunks,
        case_chunks=case_chunks,
        image_paths=image_paths,
    )
