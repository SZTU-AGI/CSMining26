"""
署名校验与污染识别层。

用户调研表(``文件署名整理表_v2(1).xlsx``)逐案标注各 Track 内嵌的 establishment name
是否与 Track 1 的一致;本模块把它从 xlsx 转成 case × track 的污染索引,并提供:

* :func:`load_user_signature_truth` — 解析 xlsx → ``{re_number: ContaminatedTrackIndex}``;
* :func:`merge_into_case_record` — 把外部 ground truth 叠加到 ``CaseRecord``,生成
  ``contaminated_tracks: dict[int, str]`` 与额外 flag;
* :func:`annotate_chunks` — 通过污染 Track 把 case 下的 chunk 重新打 ``track_contaminated``
  flag,供后续 ``HybridIndex.search`` 在召回阶段决定是否隔离。

设计口径(参见 ``DECISIONS.md`` 第 5 条):

* 只识别,不在清洗阶段擅自抹掉证据;
* ``foreign_farm`` / ``signature_mismatch`` 是污染;``supplier`` 是合法供应链材料,
  不视为污染;
* 解析阶段已打的 ``embedded_re_number_mismatch`` 与本模块的 ground truth 合并,以 ground
  truth 为准、脚本抽出作交叉验证。
"""
from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from freca.models import CaseRecord, EvidenceChunk, SourceRecord
from freca.state import read_json

# xlsx XML 命名空间
_XMLNS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XMLNS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Track 列名在用户表中形如 T1_Registration / T2_HACCP / T3_Pest / T9_Traceability
_TRACK_NAME_TO_NUMBER = {
    "T1_Registration": 1,
    "T2_HACCP": 2,
    "T3_Pest": 3,
    "T4_Management": 4,
    "T5_SiteMap": 5,
    "T6_Hygiene": 6,
    "T7_Bait": 7,
    "T8_Phytosanitary": 8,
    "T9_Traceability": 9,
}


class SignatureTruthLoader:
    """读取用户整理的署名前瞻性污染表。

    用法::

        loader = SignatureTruthLoader()
        truth = loader.load(Path("文件署名整理表_v2(1).xlsx"))
        annotated_case = loader.annotate(case_record, truth)

    由于 ``case_id`` 在表里是 RE Number,需要先用 :func:`re_number_to_case_id` 或
    :func:`merge_into_case_record` 提供的 manifest map 关联。
    """

    _TRACK_NUMBER_RE = re.compile(r"T(\d)_")

    def load(self, path: Path) -> dict[str, "ContaminatedCaseIndex"]:
        """读取表 → ``{re_number: ContaminatedCaseIndex}``。"""
        per_track_rows, case_records = self._read_xlsx(path)
        cases: dict[str, ContaminatedCaseIndex] = {}
        # per_track_rows 优先:per-row ground truth 比 summary 更细
        for row in per_track_rows:
            re_number = row["re_number"]
            relation = _normalize_relation(row["relation"])
            if relation == "consistent":
                continue
            track_number = row["track_number"]
            if track_number is None:
                continue
            entry = cases.setdefault(
                re_number,
                ContaminatedCaseIndex(
                    re_number=re_number,
                    expected_name=row["expected_name"],
                ),
            )
            entry.contaminated[track_number] = relation
        # Case summary 提供补充信息
        for record in case_records:
            entry = cases.setdefault(
                record["re_number"],
                ContaminatedCaseIndex(
                    re_number=record["re_number"],
                    expected_name=record["expected_name"],
                ),
            )
            entry.summary_contaminated_tracks = record["contaminated_tracks_str"]
            entry.is_contaminated = record["is_contaminated"]
        return cases

    def _read_xlsx(
        self, path: Path
    ) -> tuple[list[dict], list[dict]]:
        with zipfile.ZipFile(path) as z:
            workbook_xml = ET.fromstring(z.read("xl/workbook.xml"))
            rels_xml = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            rels_map = {
                r.get("Id"): r.get("Target")
                for r in rels_xml.findall(f"{_XMLNS_REL}Relationship")
            }
            sheets = []
            for sh in workbook_xml.findall(
                ".//a:sheets/a:sheet", _XMLNS
            ):
                rid = sh.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                target = rels_map[rid]
                if not target.startswith("/"):
                    target = "xl/" + target
                else:
                    target = target.lstrip("/")
                rows = _read_sheet(z, target)
                sheets.append(rows)

        per_track_rows: list[dict] = []
        case_records: list[dict] = []
        if sheets:
            for r in sheets[0][1:]:
                if not r or not r[0]:
                    continue
                track_name = (r[1] or "").strip()
                match = self._TRACK_NUMBER_RE.search(track_name)
                per_track_rows.append(
                    {
                        "re_number": (r[0] or "").strip(),
                        "track_name": track_name,
                        "track_number": (
                            int(match.group(1)) if match else None
                        ),
                        "actual_name": (r[3] or "").strip(),
                        "expected_name": (r[4] or "").strip(),
                        "relation": (r[5] or "").strip(),
                    }
                )
        if len(sheets) > 1:
            for r in sheets[1][1:]:
                if not r or not r[0]:
                    continue
                case_records.append(
                    {
                        "re_number": (r[0] or "").strip(),
                        "expected_name": (r[1] or "").strip(),
                        "contam_track_count": (
                            int(r[2])
                            if r[2] and str(r[2]).isdigit()
                            else 0
                        ),
                        "contaminated_tracks_str": (r[3] or "").strip(),
                        "is_contaminated": (r[4] or "").strip(),
                    }
                )
        return per_track_rows, case_records


class ContaminatedCaseIndex:
    """单个 case 的污染索引。

    ``contaminated`` 的 key 是 Track 编号 (1..9),value 是关系字符串。
    ``summary_contaminated_tracks`` 保留表里汇总列的原始污染 Track 列表(逗号字符串),
    用于人审时的直观参考。
    """

    def __init__(
        self, *, re_number: str, expected_name: str = ""
    ) -> None:
        self.re_number = re_number
        self.expected_name = expected_name
        self.contaminated: dict[int, str] = {}
        self.summary_contaminated_tracks: str = ""
        self.is_contaminated: str = ""

    @property
    def contaminated_track_numbers(self) -> list[int]:
        return sorted(self.contaminated)

    @property
    def is_foreign(self) -> bool:
        """是否至少有一个 Track 被他家农场的证据替换。"""
        return any(rel == "foreign_farm" for rel in self.contaminated.values())

    def to_dict(self) -> dict:
        return {
            "re_number": self.re_number,
            "expected_name": self.expected_name,
            "contaminated": dict(sorted(self.contaminated.items())),
            "summary_contaminated_tracks": self.summary_contaminated_tracks,
            "is_contaminated": self.is_contaminated,
        }


def _normalize_relation(value: str | None) -> str:
    """把表里"关系"列归一为 ``consistent`` / ``supplier`` / ``foreign_farm`` / 其他。"""
    text = (value or "").strip()
    if text in {"一致"}:
        return "consistent"
    # 供应商类: 例如表里写过"供应商"
    if "供应商" in text or "供应" in text:
        return "supplier"
    # 别家农场/外农
    if any(token in text for token in ("外农", "別", "别家", "外来")):
        return "foreign_farm"
    return text or "unknown"


def _read_sheet(z: zipfile.ZipFile, target: str) -> list[list[str]]:
    """从 zipfile 中读取一个 sheet,所有 cell 当作 inline str 解析。"""
    root = ET.fromstring(z.read(target))
    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", _XMLNS):
        values: list[str] = []
        for cell in row.findall("a:c", _XMLNS):
            inline = cell.find("a:is/a:t", _XMLNS)
            if inline is not None and inline.text is not None:
                values.append(inline.text)
            else:
                v = cell.find("a:v", _XMLNS)
                values.append(v.text if v is not None and v.text else "")
        rows.append(values)
    return rows


def load_user_signature_truth(path: Path) -> dict[str, ContaminatedCaseIndex]:
    """便捷封装:从路径直接读取。"""
    return SignatureTruthLoader().load(path)


def merge_into_case_record(
    case: CaseRecord,
    truth: dict[str, ContaminatedCaseIndex] | None,
) -> CaseRecord:
    """把 ground truth 叠加到 ``CaseRecord``。

    - 增加 ``contaminated_tracks`` 字段(``track_number -> relation``);
    - 若有任何污染 Track,追加 ``signature_foreign`` flag;
    - 在 ``metadata.expected_establishment_name`` 写入 Track 1 的预期名称。
    """
    if not truth:
        return case
    index = truth.get(case.re_number)
    flags = list(case.flags)
    contaminated = dict(case.contaminated_tracks)
    metadata = dict(case.metadata)
    if index is not None:
        if index.expected_name:
            metadata["expected_establishment_name"] = index.expected_name
        metadata["signature_truth"] = index.to_dict()
        if index.contaminated:
            for track_number, relation in index.contaminated.items():
                contaminated[track_number] = relation
            if not any(flag.startswith("track_contaminated:") for flag in flags):
                for track_number in sorted(index.contaminated):
                    relation = index.contaminated[track_number]
                    flags.append(f"track_contaminated:{track_number}:{relation}")
            if index.is_foreign and "signature_foreign" not in flags:
                flags.append("signature_foreign")
    return case.model_copy(
        update={
            "contaminated_tracks": contaminated,
            "flags": flags,
            "metadata": metadata,
        }
    )


def annotate_chunks(
    chunks: Iterable[EvidenceChunk],
    case: CaseRecord,
) -> list[EvidenceChunk]:
    """把污染 Track 的 chunk 重新打 ``track_contaminated`` flag。

    与 ``EvidenceChunk`` 原始 flags 合并;不修改其它字段。返回新列表。
    """
    contaminated_tracks = set(case.contaminated_tracks)
    if not contaminated_tracks:
        return list(chunks)
    annotated: list[EvidenceChunk] = []
    for chunk in chunks:
        if chunk.track is not None and chunk.track in contaminated_tracks:
            relation = case.contaminated_tracks[chunk.track]
            extra = [
                f"track_contaminated:{chunk.track}:{relation}",
                "exclude_from_compliance_evidence",
            ]
            merged = list(chunk.flags) + [
                flag for flag in extra if flag not in chunk.flags
            ]
            metadata = dict(chunk.metadata)
            metadata["track_contamination_relation"] = relation
            annotated.append(
                chunk.model_copy(
                    update={"flags": merged, "metadata": metadata}
                )
            )
        else:
            annotated.append(chunk)
    return annotated


def contamination_summary_from_truth(
    truth: dict[str, ContaminatedCaseIndex] | None,
) -> dict:
    """把 ground truth 序列化成 manifest-process-friendly 报告。"""
    if not truth:
        return {"cases_total": 0, "foreign_cases": 0, "by_track": {}}
    by_track: defaultdict[int, int] = defaultdict(int)
    foreign_cases = 0
    for index in truth.values():
        for track_number, relation in index.contaminated.items():
            by_track[track_number] += 1
        if index.is_foreign:
            foreign_cases += 1
    return {
        "cases_total": len(truth),
        "foreign_cases": foreign_cases,
        "by_track": dict(sorted(by_track.items())),
    }


__all__ = [
    "SignatureTruthLoader",
    "ContaminatedCaseIndex",
    "load_user_signature_truth",
    "merge_into_case_record",
    "annotate_chunks",
    "contamination_summary_from_truth",
]
