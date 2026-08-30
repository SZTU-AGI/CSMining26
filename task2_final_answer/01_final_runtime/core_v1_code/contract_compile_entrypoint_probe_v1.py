#!/usr/bin/env python3
"""Inspect the CURRENT server-side freca_core_v2.py compile entrypoints.

Zero API. No imports required for the target module. Pure AST/source inspection.

Prints:
- top-level functions whose names/source mention compile/contract/ledger/retrieval
- signatures
- argparse subcommands discovered from source
- likely single-CP compile call sites
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


KEYWORDS = (
    "compile",
    "contract",
    "candidate_ledger",
    "rule_set_relation",
    "retriev",
    "grounded",
)


def fn_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    parts = []

    posonly = list(getattr(node.args, "posonlyargs", []))
    normal = list(node.args.args)
    defaults = [None] * (len(posonly) + len(normal) - len(node.args.defaults)) + list(node.args.defaults)

    all_pos = posonly + normal

    for arg, default in zip(all_pos, defaults):
        text = arg.arg
        if default is not None:
            try:
                text += "=" + ast.unparse(default)
            except Exception:
                text += "=..."
        parts.append(text)

    if node.args.vararg:
        parts.append("*" + node.args.vararg.arg)
    elif node.args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        text = arg.arg
        if default is not None:
            try:
                text += "=" + ast.unparse(default)
            except Exception:
                text += "=..."
        parts.append(text)

    if node.args.kwarg:
        parts.append("**" + node.args.kwarg.arg)

    return f"{node.name}(" + ", ".join(parts) + ")"


def source_segment(lines: list[str], node: ast.AST) -> str:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return ""
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("freca_core_v2.py"),
    )
    args = parser.parse_args()

    if not args.target.exists():
        raise SystemExit(f"Missing target: {args.target}")

    source = args.target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    print("=" * 92)
    print("FRECA CONTRACT COMPILE ENTRYPOINT PROBE V1")
    print("=" * 92)
    print("Target:", args.target)
    print()

    funcs = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    candidates = []

    for node in funcs:
        segment = source_segment(lines, node)
        haystack = (node.name + "\n" + segment[:8000]).lower()

        if any(keyword in haystack for keyword in KEYWORDS):
            candidates.append(node)

    print("TOP-LEVEL CANDIDATE FUNCTIONS")
    print("-" * 92)

    for node in candidates:
        print(
            f"L{node.lineno:<5} {fn_signature(node)}"
        )

        segment = source_segment(lines, node)

        markers = []
        for marker in (
            "get_cp(",
            "deepseek_json(",
            "CandidateLedger",
            "candidate_ledger",
            "rule_set_relation",
            "save_json(",
            "CONTRACT_DIR_V2",
            "CONTRACT_DIR",
        ):
            if marker in segment:
                markers.append(marker)

        if markers:
            print("       markers:", ", ".join(markers))

    print()
    print("ARGPARSE SUBCOMMANDS")
    print("-" * 92)

    found_subcommands = []

    # Static string scan is more robust to different parser variable names.
    for match in re.finditer(
        r'\.add_parser\(\s*["\']([^"\']+)["\']',
        source,
    ):
        cmd = match.group(1)
        if cmd not in found_subcommands:
            found_subcommands.append(cmd)

    if found_subcommands:
        for cmd in found_subcommands:
            print(" ", cmd)
    else:
        print("  <none discovered>")

    print()
    print("LIKELY COMPILE CALL SITES")
    print("-" * 92)

    call_lines = []
    for idx, line in enumerate(lines, start=1):
        lower = line.lower()
        if (
            ("compile" in lower or "grounded" in lower)
            and not line.lstrip().startswith("#")
        ):
            call_lines.append((idx, line.rstrip()))

    for idx, line in call_lines[:120]:
        print(f"L{idx:<5} {line}")

    if len(call_lines) > 120:
        print(f"... {len(call_lines) - 120} more compile-related lines omitted")

    print()
    print("NEXT")
    print("-" * 92)
    print(
        "Use the exact single-CP compile function/subcommand above to build "
        "a fail-collecting CP1..CP41 orchestrator. Do not guess the CLI."
    )


if __name__ == "__main__":
    main()
