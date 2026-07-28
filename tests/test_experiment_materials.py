import json
from pathlib import Path

from freca.experiments.materials import (
    build_material_snapshot,
    load_material_snapshot_from_parsed,
    select_automatic_retrieval_material,
)
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    SourceLocation,
    SourceType,
)


def _checkpoint(cp_id: str) -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element-1",
        section_title="Official section",
        text=f"Official checkpoint {cp_id}",
        source_file="checkingpoints_all_elements_onesheet.xlsx",
        cell="A3",
    )


def _chunk(chunk_id: str, content: str, *, case_id: int | None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        source_id="policy-rules-2021" if case_id is None else "case-7-track-1",
        source_file="official.pdf" if case_id is None else "track-1.docx",
        source_type=SourceType.PDF if case_id is None else SourceType.DOCX,
        location=SourceLocation(page=1),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )


def test_material_snapshot_keeps_official_chunks_images_and_hashes() -> None:
    snapshot = build_material_snapshot(
        case_id=7,
        checkpoints=[_checkpoint("CP1")],
        policy_chunks=[_chunk("policy:page:1", "official policy", case_id=None)],
        case_chunks=[_chunk("case:7:track1", "farm evidence", case_id=7)],
        image_paths=[Path("case-7-photo.png")],
    )

    assert snapshot.chunk_ids == ("policy:page:1", "case:7:track1")
    assert snapshot.image_paths == ("case-7-photo.png",)
    assert len(snapshot.input_sha256) == 64


def test_material_snapshot_rejects_evidence_for_a_different_case() -> None:
    try:
        build_material_snapshot(
            case_id=7,
            checkpoints=[_checkpoint("CP1")],
            policy_chunks=[],
            case_chunks=[_chunk("case:8:track1", "wrong farm", case_id=8)],
        )
    except ValueError as error:
        assert "case 7" in str(error)
    else:
        raise AssertionError("expected foreign case evidence to be rejected")


def test_material_snapshot_loads_current_case_and_original_images_from_parsed_artifacts(
    tmp_path: Path,
) -> None:
    parsed = tmp_path / "parsed"
    policy = _chunk("policy:page:1", "official policy", case_id=None)
    case = _chunk("case:7:track1", "farm evidence", case_id=7)
    image_path = tmp_path / "official-image.png"
    image_path.write_bytes(b"png")
    image = case.model_copy(
        update={
            "chunk_id": "case:7:image:1",
            "content_kind": ContentKind.IMAGE,
            "metadata": {"extracted_path": str(image_path)},
        }
    )
    (parsed / "cases" / "007").mkdir(parents=True)
    (parsed / "policy.json").write_text(
        json.dumps([policy.model_dump(mode="json")]), encoding="utf-8"
    )
    (parsed / "cases" / "007" / "track-1.json").write_text(
        json.dumps([case.model_dump(mode="json"), image.model_dump(mode="json")]),
        encoding="utf-8",
    )

    snapshot = load_material_snapshot_from_parsed(
        parsed_dir=parsed,
        case_id=7,
        checkpoints=[_checkpoint("CP1")],
    )

    assert snapshot.chunk_ids == ("policy:page:1", "case:7:track1", "case:7:image:1")
    assert snapshot.image_paths == (str(image_path),)


def test_automatic_retrieval_selects_chunks_by_official_checkpoint_text() -> None:
    checkpoint = _checkpoint("CP1").model_copy(
        update={"text": "Traceability records must identify consignments"}
    )
    snapshot = build_material_snapshot(
        case_id=7,
        checkpoints=[checkpoint],
        policy_chunks=[
            _chunk("policy:trace", "Traceability records identify consignments", case_id=None),
            _chunk("policy:pests", "Pest controls are documented", case_id=None),
        ],
        case_chunks=[
            _chunk("case:7:trace", "Consignment traceability record 2026", case_id=7),
            _chunk("case:7:water", "Irrigation water analysis", case_id=7),
        ],
    )

    selected = select_automatic_retrieval_material(
        snapshot,
        checkpoint_ids=("CP1",),
        per_scope_limit=1,
    )

    assert selected.chunk_ids == ("policy:trace", "case:7:trace")
    assert selected.input_sha256 != snapshot.input_sha256
