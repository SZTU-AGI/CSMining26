from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import fitz

from freca.models import ContentKind, EvidenceChunk, SourceLocation, SourceRecord
from freca.parsing.chunking import normalize_text, stable_chunk_id
from freca.parsing.mineru import MinerUClient, normalize_content_list


def _run_mineru(source: SourceRecord, output_dir: Path, executable: str) -> tuple[bool, str | None]:
    resolved = shutil.which(executable)
    if resolved is None:
        return False, f"MinerU executable not found: {executable}"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [resolved, "-p", str(source.path), "-o", str(output_dir), "-b", "pipeline"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        return False, message[-1000:]
    return True, None


def parse_pdf(
    source: SourceRecord,
    mineru_output_dir: Path,
    *,
    mineru_executable: str | None = "mineru",
    mineru_client: MinerUClient | None = None,
) -> list[EvidenceChunk]:
    if mineru_client is not None:
        result = mineru_client.parse(source.path, mineru_output_dir)
        blocks = normalize_content_list(result.content_list)
        if not blocks:
            raise RuntimeError("MinerU returned no usable structured content blocks")
        kind_map = {
            "heading": ContentKind.HEADING,
            "table": ContentKind.TABLE,
            "image": ContentKind.IMAGE,
        }
        return [
            EvidenceChunk(
                chunk_id=stable_chunk_id(
                    source,
                    f"page-{block.page:04d}-block-{index:04d}",
                ),
                case_id=source.case_id,
                re_number=source.re_number,
                track=source.track,
                source_id=source.source_id,
                source_file=source.path.name,
                source_type=source.source_type,
                location=SourceLocation(page=block.page),
                content=normalize_text(block.text),
                content_kind=kind_map.get(block.kind, ContentKind.PARAGRAPH),
                parser_name=result.provider,
                parser_version="1",
                source_sha256=source.sha256,
                flags=list(source.flags) + ["mineru_generated"],
                metadata={
                    **block.metadata,
                    "bbox": block.bbox,
                    "heading_level": block.level,
                    "mineru_result_metadata": result.metadata,
                },
            )
            for index, block in enumerate(blocks, start=1)
        ]
    mineru_ok = False
    mineru_error: str | None = "MinerU disabled"
    if mineru_executable:
        mineru_ok, mineru_error = _run_mineru(source, mineru_output_dir, mineru_executable)

    flags = ["mineru_generated"] if mineru_ok else ["mineru_unavailable"]
    document = fitz.open(source.path)
    chunks: list[EvidenceChunk] = []
    try:
        for page_index, page in enumerate(document, start=1):
            content = normalize_text(page.get_text("text"))
            if not content:
                content = "[No extractable page text; page image/OCR review required]"
                page_flags = flags + ["page_text_empty"]
            else:
                page_flags = flags
            locator = f"page-{page_index:04d}"
            chunks.append(
                EvidenceChunk(
                    chunk_id=stable_chunk_id(source, locator),
                    case_id=source.case_id,
                    re_number=source.re_number,
                    track=source.track,
                    source_id=source.source_id,
                    source_file=source.path.name,
                    source_type=source.source_type,
                    location=SourceLocation(page=page_index),
                    content=content,
                    content_kind=ContentKind.PARAGRAPH,
                    parser_name="mineru+pymupdf" if mineru_ok else "pymupdf-fallback",
                    parser_version="1",
                    source_sha256=source.sha256,
                    flags=list(source.flags) + page_flags,
                    metadata={"mineru_error": mineru_error},
                )
            )
    finally:
        document.close()
    return chunks
