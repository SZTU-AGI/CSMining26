"""Load contest-side credentials from a .env file into the process environment.

We intentionally do not depend on ``python-dotenv`` — the .env format is
trivial enough to parse directly, and the mapping below is the contract that
maps the user's private .env (``llm_key``, ``llm_url``, ``llm_model``,
``embedding_url``) onto the env variable names the FRECA config reads
(``FRECA_AUDIT_API_KEY``, ``FRECA_AUDIT_BASE_URL`` …). Existing process env
values always win so operators can override per-run.
"""
from __future__ import annotations

import os
from pathlib import Path

# Map .env field -> list of env variable names to populate (in order).
# We populate the FRECA_AUDIT_* family so ``config.yaml`` ``api_key_env``
# references resolve.
_ENV_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "llm_key": ("FRECA_AUDIT_API_KEY",),
    "llm_url": ("FRECA_AUDIT_BASE_URL",),
    "llm_model": ("FRECA_AUDIT_MODEL",),
}


def find_env_file(start: Path | None = None) -> Path | None:
    """Walk upwards from ``start`` (or cwd) until a .env file is found."""
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def _parse_env_lines(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        fields[key] = value
    return fields


def apply_env_file(env_path: Path, *, env: dict[str, str] | None = None) -> dict[str, str]:
    """Populate ``env`` (defaulting to ``os.environ``) from a parsed .env.

    Existing entries in ``env`` are never overwritten — process-level values win
    over .env contents so operators can override per-run without editing files.
    """
    target = env if env is not None else dict(os.environ)
    fields = _parse_env_lines(env_path.read_text(encoding="utf-8"))
    for field_name, value in fields.items():
        for env_name in _ENV_FIELD_MAP.get(field_name, ()):
            target.setdefault(env_name, value)
    if env is None:
        for field_name, value in fields.items():
            for env_name in _ENV_FIELD_MAP.get(field_name, ()):
                os.environ.setdefault(env_name, value)
    return target