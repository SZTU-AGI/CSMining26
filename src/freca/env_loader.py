"""Map the existing local MiniMax ``.env`` fields to FRECA runtime variables."""
from __future__ import annotations

import os
from pathlib import Path


_ENV_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "llm_key": ("FRECA_AUDIT_API_KEY",),
    "llm_url": ("FRECA_AUDIT_BASE_URL",),
    "llm_model": ("FRECA_AUDIT_MODEL",),
}


def find_env_file(start: Path) -> Path | None:
    """Return the nearest parent ``.env`` file, if one exists."""
    current = start.resolve()
    for parent in (current, *current.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def apply_env_file(path: Path) -> None:
    """Load supported non-versioned runtime settings without overriding the shell."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        field, value = line.split("=", 1)
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        for env_name in _ENV_FIELD_MAP.get(field.strip(), ()):
            os.environ.setdefault(env_name, value)
