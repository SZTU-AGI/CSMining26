from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import re

from rank_bm25 import BM25Okapi

from freca.experiments.models import MaterialSnapshot, Track3Condition
from freca.models import CheckpointDefinition, ContentKind, EvidenceChunk
from freca.state import build_cache_key, read_json


_AUDIT_SCENARIO_RE = re.compile(
    r"(Audit scenario:\s*).*?(?=\s*\|\s*[A-Z]+\d+=|$)", re.DOTALL
)


def mask_audit_scenario(content: str) -> str:
    """Redact the narrative after the Track 3 "Audit scenario:" label, preserving cell structure.

    The label is retained so the model still knows a scenario exists; only the near-answer
    narrative (e.g. "Fully compliant", "Active insect infestation - not pest-free") is replaced
    with ``[REDACTED]``. Cell boundaries like `` | B14=`` are left intact.
    """
    return _AUDIT_SCENARIO_RE.sub(r"\1[REDACTED]", content, count=1)


def _apply_track3_condition(
    chunks: tuple[EvidenceChunk, ...], condition: Track3Condition
) -> tuple[EvidenceChunk, ...]:
    if condition == Track3Condition.RAW:
        return chunks
    masked: list[EvidenceChunk] = []
    for chunk in chunks:
        if "Audit scenario" in chunk.content:
            masked.append(chunk.model_copy(update={"content": mask_audit_scenario(chunk.content)}))
        else:
            masked.append(chunk)
    return tuple(masked)


def build_material_snapshot(
    *,
    case_id: int,
    checkpoints: Sequence[CheckpointDefinition],
    policy_chunks: Sequence[EvidenceChunk],
    case_chunks: Sequence[EvidenceChunk],
    image_paths: Sequence[Path] = (),
    track3_condition: Track3Condition = Track3Condition.RAW,
) -> MaterialSnapshot:
    """Bundle immutable official inputs without semantic filtering or relabelling."""
    if not checkpoints:
        raise ValueError("at least one checkpoint is required")
    if any(chunk.case_id is not None for chunk in policy_chunks):
        raise ValueError("policy chunks must not be assigned to a case")
    if any(chunk.case_id != case_id for chunk in case_chunks):
        raise ValueError(f"all case chunks must belong to case {case_id}")

    chunks = _apply_track3_condition(
        tuple(policy_chunks) + tuple(case_chunks), track3_condition
    )
    image_path_strings = tuple(str(path) for path in image_paths)
    return MaterialSnapshot(
        case_id=case_id,
        checkpoints=tuple(checkpoints),
        chunks=chunks,
        image_paths=image_path_strings,
        track3_condition=track3_condition,
        input_sha256=build_cache_key(
            {
                "case_id": case_id,
                "checkpoint_ids": [checkpoint.cp_id for checkpoint in checkpoints],
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                "image_paths": image_path_strings,
                "track3_condition": track3_condition.value,
            }
        ),
    )


def load_material_snapshot_from_parsed(
    *,
    parsed_dir: Path,
    case_id: int,
    checkpoints: Sequence[CheckpointDefinition],
    track3_condition: Track3Condition = Track3Condition.RAW,
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
        track3_condition=track3_condition,
    )


def select_automatic_retrieval_material(
    snapshot: MaterialSnapshot,
    *,
    checkpoint_ids: tuple[str, ...],
    per_scope_limit: int = 12,
) -> MaterialSnapshot:
    """Apply one generic lexical selector; no CP-specific rule or source map is used."""
    if per_scope_limit < 1:
        raise ValueError("per_scope_limit must be at least one")
    checkpoint_by_id = {checkpoint.cp_id: checkpoint for checkpoint in snapshot.checkpoints}
    if set(checkpoint_ids) - checkpoint_by_id.keys():
        raise ValueError("requested checkpoints are absent from the material snapshot")
    selected_checkpoints = tuple(checkpoint_by_id[cp_id] for cp_id in checkpoint_ids)
    query_tokens = _tokens(" ".join(checkpoint.text for checkpoint in selected_checkpoints))
    policy_chunks = tuple(chunk for chunk in snapshot.chunks if chunk.case_id is None)
    case_chunks = tuple(chunk for chunk in snapshot.chunks if chunk.case_id == snapshot.case_id)
    selected_policy = _select_top_chunks(policy_chunks, query_tokens, per_scope_limit)
    selected_case = _select_top_chunks(case_chunks, query_tokens, per_scope_limit)
    return build_material_snapshot(
        case_id=snapshot.case_id,
        checkpoints=selected_checkpoints,
        policy_chunks=selected_policy,
        case_chunks=selected_case,
        image_paths=[Path(image_path) for image_path in snapshot.image_paths],
        track3_condition=snapshot.track3_condition,
    )


def _select_top_chunks(
    chunks: tuple[EvidenceChunk, ...], query_tokens: list[str], limit: int
) -> tuple[EvidenceChunk, ...]:
    if len(chunks) <= limit:
        return chunks
    corpus = [_tokens(chunk.content) for chunk in chunks]
    scores = BM25Okapi(corpus).get_scores(query_tokens)
    query_terms = set(query_tokens)
    ranked = sorted(
        (
            (float(score), len(query_terms & set(tokens)), chunk)
            for score, tokens, chunk in zip(scores, corpus, chunks, strict=True)
        ),
        key=lambda item: (-item[0], -item[1], item[2].chunk_id),
    )
    return tuple(chunk for _, _, chunk in ranked[:limit])


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold()) or ["_"]
