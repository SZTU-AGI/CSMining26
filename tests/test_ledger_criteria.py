from pathlib import Path

from openpyxl import Workbook

from freca.ledger.criteria import (
    CURATED_CHUNK_PREFIX,
    CriteriaTable,
    curated_chunk,
)
from freca.models import EvidenceChunk

HEADER = (
    "CP",
    "CP定义(中)",
    "红线R3(中)",
    "评分标准（最终版·含门槛/依据材料）",
    "Act层参考(联网核验·非本地材料)",
    "来源说明",
)


def _fake_xlsx(tmp_path: Path) -> Path:
    long_text = "门槛材料正文。" + "TAIL_MARKER_" + "字" * 2000
    path = tmp_path / "criteria.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "CP评分标准"
    sheet.append(list(HEADER))
    for index in range(1, 42):
        row = [f"CP{index}", "定义", "红线命题", "普通评分标准", "", ""]
        if index == 2:
            row[2] = "红线命题（含门槛）"
            row[3] = long_text
            row[4] = "Act 层参考占位内容"
        sheet.append(row)
    workbook.save(path)
    return path


def test_criteria_table_loads_real_asset() -> None:
    table = CriteriaTable.load(Path("FRECA_41CP_评分标准_最终合并版_材料并入.xlsx"))

    assert len(table.entries) == 41
    assert table.entries["CP1"].redline
    assert table.entries["CP41"].criteria_text
    assert len(table.sha256) == 64


def test_criteria_table_maps_columns_and_excludes_act_layer(tmp_path: Path) -> None:
    table = CriteriaTable.load(_fake_xlsx(tmp_path))

    entry = table.entry("CP2")
    assert entry.redline == "红线命题（含门槛）"
    chunk = curated_chunk(entry, cp_id="CP2", table=table)
    assert chunk.chunk_id == f"{CURATED_CHUNK_PREFIX}CP2"
    assert chunk.flags == ["curated"]
    assert "红线命题（含门槛）" in chunk.content
    assert "TAIL_MARKER_" in chunk.content
    assert "Act 层参考占位内容" not in chunk.content
    assert len(chunk.content) > 1800


def test_curated_chunk_is_a_valid_evidence_chunk(tmp_path: Path) -> None:
    table = CriteriaTable.load(_fake_xlsx(tmp_path))

    chunk = curated_chunk(table.entry("CP2"), cp_id="CP2", table=table)

    assert EvidenceChunk.model_validate(chunk.model_dump()) == chunk


def test_missing_cp_raises_key_error() -> None:
    table = CriteriaTable.load(Path("FRECA_41CP_评分标准_最终合并版_材料并入.xlsx"))

    try:
        table.entry("CP999")
    except KeyError as error:
        assert "CP999" in str(error)
    else:
        raise AssertionError("expected KeyError for missing CP row")
