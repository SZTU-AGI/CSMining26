
"""Upgrade FactCandidate split trigger to v1.1.

The current v1 splits only when EvidenceNature says mixed positive/adverse.
This patch also splits when the source atom has MIXED modality or MIXED speech
act, because different actual/planned/procedural events must not share one
FactCandidate.

It replaces only build_fact_candidates().
"""

from __future__ import annotations

import ast
from pathlib import Path

TARGET = Path("fact_candidate_v1.py")

if not TARGET.exists():
    raise SystemExit(
        "Missing fact_candidate_v1.py; run from ~/freca/core_v1"
    )

source = TARGET.read_text(
    encoding="utf-8"
)

for marker in (
    "requires_subspan_fact_split",
    "build_fact_candidates",
    "_segments",
    "_semicolon_segments",
):
    if marker not in source:
        raise SystemExit(
            f"Unexpected fact_candidate_v1.py; missing {marker}"
        )


def replace_function(
    src: str,
    name: str,
    replacement: str,
) -> str:
    tree = ast.parse(src)
    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected one top-level {name}, found {len(matches)}"
        )
    node = matches[0]
    lines = src.splitlines(
        keepends=True
    )
    lines[
        node.lineno - 1:
        node.end_lineno
    ] = [
        replacement.rstrip()
        + "\n\n"
    ]
    return "".join(lines)


NEW_BUILD = r