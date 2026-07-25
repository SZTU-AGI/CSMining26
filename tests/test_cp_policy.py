from pathlib import Path

from freca.cp import build_policy_source, load_checkpoints
from freca.parsing.pdf import parse_pdf


def test_loads_all_official_checkpoints_with_element_and_section_context() -> None:
    root = Path(__file__).parents[1]
    checkpoints = load_checkpoints(root / "checkingpoints_all_elements_onesheet.xlsx")

    assert [checkpoint.cp_id for checkpoint in checkpoints] == [
        f"CP{index}" for index in range(1, 42)
    ]
    assert checkpoints[0].element_id == 1
    assert checkpoints[7].element_id == 2
    assert checkpoints[16].element_id == 3
    assert checkpoints[28].element_id == 4
    assert checkpoints[0].section_title == "1.1 Export operations"
    assert all(checkpoint.text.strip() for checkpoint in checkpoints)
    assert checkpoints[0].cell == "A3"
    assert checkpoints[-1].cell == "AO3"


def test_policy_fallback_produces_one_provenance_chunk_per_page(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    source = build_policy_source(
        root / "1-Export Control (Plants and Plant Products)Rules 2021.pdf"
    )

    chunks = parse_pdf(source, tmp_path / "mineru", mineru_executable=None)

    assert len(chunks) == 132
    assert chunks[0].location.page == 1
    assert chunks[-1].location.page == 132
    assert all(chunk.case_id is None for chunk in chunks)
    assert all(chunk.source_id == "policy-rules-2021" for chunk in chunks)
