#!/usr/bin/env python3
"""FRECA Core alignment relation-preservation patch.

Purpose:
- preserve semantic relation SUPPORT/ATTACK/IRRELEVANT/AMBIGUOUS exactly as
  returned by the constrained alignment model;
- keep typed/identity gates separate as accepted_for_argument;
- never promote alignment to proof.

Expected current Core:
- FRECA boundary fix v2 already installed.
- EVIDENCE_ALIGNMENT_SYSTEM is relation-only.
- validate_alignment contains accepted_for_alignment / accepted_for_proof.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path


TARGET = Path("evidence_reasoning_v2.py")

if not TARGET.exists():
    raise SystemExit(
        "Missing evidence_reasoning_v2.py; run from ~/freca/core_v1"
    )


def replace_top_level_function(
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
        raise RuntimeError(
            f"Expected exactly one top-level function {name}, "
            f"found {len(matches)}"
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


NEW_VALIDATE = r