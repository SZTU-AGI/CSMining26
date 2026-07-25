"""农场 case 解析 → chunk。优先 Docling，失败降级轻量库。

异常 case 天然容错：
- 缺 Track1（Farm 文档）→ 该 track 无 chunk
- 双农场合并（18 文件）→ 全部解析，文件名含农场编号，元数据按 file 区分
"""
import os
import re
from .fallback import fallback_parse
from .chunking import chunk_text


def _track_of(filename: str) -> str:
    m = re.match(r"(\d+)_", filename)
    return m.group(1) if m else "?"


def _parse_one(path: str, use_docling: bool) -> str:
    if use_docling:
        try:
            from docling.document_converter import DocumentConverter
            res = DocumentConverter().convert(path)
            return res.document.export_to_markdown()
        except Exception:
            pass
    return fallback_parse(path)


def parse_case(case_dir: str, use_docling: bool = True,
               chunk_size: int = 400, chunk_overlap: int = 50):
    """解析一个 case 的全部证据文件为 chunk（含元数据）。

    返回 list[dict]: {case_id, track, file, chunk_index, text}
    """
    case_id = os.path.basename(case_dir.rstrip("/\\"))
    recs = []
    for f in sorted(os.listdir(case_dir)):
        fp = os.path.join(case_dir, f)
        if not os.path.isfile(fp):
            continue
        text = _parse_one(fp, use_docling)
        if not text.strip():
            continue
        track = _track_of(f)
        for i, ch in enumerate(chunk_text(text, chunk_size, chunk_overlap)):
            recs.append({
                "case_id": case_id,
                "track": track,
                "file": f,
                "chunk_index": i,
                "text": ch,
            })
    return recs
