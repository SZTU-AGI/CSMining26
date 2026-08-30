#!/usr/bin/env python3
"""Install V2 legacy-tail firewall v2.

This version uses AST structure only and does NOT depend on console marker text
such as "BM25 evidence retrieval".
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

V1 = Path("freca_core_v1.py")
V2 = Path("freca_core_v2.py")

NEW_EVALUATE_V2 = '\ndef evaluate_v2(\n    cp_id: str,\n    case_name: str,\n    evidence_top_k: int,\n):\n    """Run the V2 requirement/argument pipeline without the legacy V1 tail."""\n\n    cp = core.get_cp(\n        cp_id\n    )\n\n    contract_path = (\n        CONTRACT_DIR_V2\n        / f"{cp[\'cp_id\']}.json"\n    )\n\n    if not contract_path.exists():\n        raise FileNotFoundError(\n            "No V2 contract found:\\n"\n            f"{contract_path}\\n\\n"\n            "Compile it first."\n        )\n\n    RESULT_DIR_V2.mkdir(\n        parents=True,\n        exist_ok=True,\n    )\n\n    old_result_dir = core.RESULT_DIR\n\n    had_mode = hasattr(\n        core,\n        "FRECA_REQUIREMENT_PIPELINE_ONLY",\n    )\n\n    old_mode = getattr(\n        core,\n        "FRECA_REQUIREMENT_PIPELINE_ONLY",\n        None,\n    )\n\n    core.RESULT_DIR = RESULT_DIR_V2\n    core.FRECA_REQUIREMENT_PIPELINE_ONLY = True\n\n    try:\n        return core.evaluate_case(\n            contract_path,\n            case_name,\n            evidence_top_k,\n        )\n\n    finally:\n        core.RESULT_DIR = old_result_dir\n\n        if had_mode:\n            core.FRECA_REQUIREMENT_PIPELINE_ONLY = old_mode\n        else:\n            delattr(\n                core,\n                "FRECA_REQUIREMENT_PIPELINE_ONLY",\n            )\n'


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
            f"Expected exactly one top-level function {{name}}, "
            f"found {{len(matches)}}"
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


def insert_v1_firewall(
    src: str,
) -> str:
    marker = (
        "FRECA LEGACY FIREWALL — "
        "REQUIREMENT PIPELINE ONLY"
    )

    if marker in src:
        return src

    tree = ast.parse(src)

    funcs = [
        node
        for node in tree.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name == "evaluate_case"
    ]

    if len(funcs) != 1:
        raise RuntimeError(
            "Expected exactly one evaluate_case, "
            f"found {{len(funcs)}}"
        )

    func = funcs[0]

    saw_requirement_call = False
    print_stmt = None

    for stmt in func.body:
        # Look for:
        # requirement_reasoning = run_from_evaluate_locals(...)
        if isinstance(
            stmt,
            ast.Assign,
        ):
            call = stmt.value

            if (
                isinstance(
                    call,
                    ast.Call,
                )
                and isinstance(
                    call.func,
                    ast.Name,
                )
                and call.func.id
                == "run_from_evaluate_locals"
            ):
                saw_requirement_call = True

        # Look for:
        # print_requirement_result(requirement_reasoning)
        if isinstance(
            stmt,
            ast.Expr,
        ):
            call = stmt.value

            if (
                isinstance(
                    call,
                    ast.Call,
                )
                and isinstance(
                    call.func,
                    ast.Name,
                )
                and call.func.id
                == "print_requirement_result"
            ):
                print_stmt = stmt
                break

    if not saw_requirement_call:
        raise RuntimeError(
            "Could not find top-level "
            "run_from_evaluate_locals(...) "
            "inside evaluate_case."
        )

    if print_stmt is None:
        raise RuntimeError(
            "Could not find top-level "
            "print_requirement_result(...) "
            "inside evaluate_case."
        )

    lines = src.splitlines(
        keepends=True
    )

    insert_at = (
        print_stmt.end_lineno
    )

    block = (
        "\n"
        "    # --------------------------------------------------------\n"
        "    # FRECA LEGACY FIREWALL — REQUIREMENT PIPELINE ONLY\n"
        "    # V2 stops here. Legacy V1 atom/final-label logic remains\n"
        "    # available when evaluate_case is called without this flag.\n"
        "    # --------------------------------------------------------\n"
        "    if globals().get(\n"
        "        \"FRECA_REQUIREMENT_PIPELINE_ONLY\",\n"
        "        False,\n"
        "    ):\n"
        "        print(\n"
        "            \"\\n[LEGACY FIREWALL] \"\n"
        "            \"Requirement pipeline complete; \"\n"
        "            \"skipping legacy V1 atom/final-label tail.\"\n"
        "        )\n"
        "        return requirement_reasoning\n"
        "\n"
    )

    lines.insert(
        insert_at,
        block,
    )

    return "".join(lines)


for path in (
    V1,
    V2,
):
    if not path.exists():
        raise SystemExit(
            f"Missing {{path}}; "
            "run from ~/freca/core_v1"
        )

v1_src = V1.read_text(
    encoding="utf-8"
)

v2_src = V2.read_text(
    encoding="utf-8"
)

# Structural preflight only.
ast.parse(v1_src)
ast.parse(v2_src)

if "run_from_evaluate_locals" not in v1_src:
    raise SystemExit(
        "Current freca_core_v1.py does not contain "
        "the requirement reasoning hook."
    )

if "print_requirement_result" not in v1_src:
    raise SystemExit(
        "Current freca_core_v1.py does not contain "
        "the requirement result printer."
    )

patched_v1 = insert_v1_firewall(
    v1_src
)

patched_v2 = replace_top_level_function(
    v2_src,
    "evaluate_v2",
    NEW_EVALUATE_V2,
)

# Validate complete patched modules BEFORE writing.
ast.parse(patched_v1)
ast.parse(patched_v2)

required_v1_after = (
    "FRECA LEGACY FIREWALL — REQUIREMENT PIPELINE ONLY",
    "FRECA_REQUIREMENT_PIPELINE_ONLY",
    "return requirement_reasoning",
)

required_v2_after = (
    "FRECA_REQUIREMENT_PIPELINE_ONLY",
    "return core.evaluate_case",
    "delattr",
)

for marker in required_v1_after:
    if marker not in patched_v1:
        raise RuntimeError(
            "Patched V1 missing marker: "
            + marker
        )

for marker in required_v2_after:
    if marker not in patched_v2:
        raise RuntimeError(
            "Patched V2 missing marker: "
            + marker
        )

backup_v1 = Path(
    "freca_core_v1.before_legacy_firewall_v2.py"
)

backup_v2 = Path(
    "freca_core_v2.before_legacy_firewall_v2.py"
)

if not backup_v1.exists():
    shutil.copy2(
        V1,
        backup_v1,
    )

if not backup_v2.exists():
    shutil.copy2(
        V2,
        backup_v2,
    )

tmp_v1 = Path(
    "freca_core_v1.legacy_firewall_v2.tmp"
)

tmp_v2 = Path(
    "freca_core_v2.legacy_firewall_v2.tmp"
)

tmp_v1.write_text(
    patched_v1,
    encoding="utf-8",
)

tmp_v2.write_text(
    patched_v2,
    encoding="utf-8",
)

# Validate exact bytes that will replace source files.
ast.parse(
    tmp_v1.read_text(
        encoding="utf-8"
    )
)

ast.parse(
    tmp_v2.read_text(
        encoding="utf-8"
    )
)

tmp_v1.replace(V1)
tmp_v2.replace(V2)

print(
    "Installed FRECA V2 legacy firewall v2."
)
print(
    "  V2 evaluate stops after requirement pipeline."
)
print(
    "  Direct V1 evaluate_case keeps legacy diagnostic tail."
)
print(
    "  No console-string marker dependency."
)
print()
print("Backups:")
print(" ", backup_v1)
print(" ", backup_v2)
