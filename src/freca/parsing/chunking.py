from __future__ import annotations

import hashlib
import re

from freca.models import SourceRecord


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def stable_chunk_id(source: SourceRecord, locator: str) -> str:
    safe_locator = re.sub(r"[^a-zA-Z0-9]+", "-", locator).strip("-").lower()
    digest = hashlib.sha256(
        f"{source.source_id}|{source.sha256}|{locator}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{source.source_id}_{safe_locator}_{digest}"
