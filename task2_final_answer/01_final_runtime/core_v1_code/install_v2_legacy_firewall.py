#!/usr/bin/env python3
"""Install the V2 legacy-tail firewall.

Normal freca_core_v2 evaluate:
    parse evidence
    -> requirement reasoning / alignment ledger
    -> RETURN
    -> legacy V1 BM25/atom/final-label tail is skipped

Direct V1 execution remains unchanged and can still be used as a diagnostic.

This patch changes orchestration only. It does not change evidence semantics,
FactCandidate, retrieval, alignment, contract logic, Argument, or ProofStandard.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

V1 = Path("freca_core_v1.py")
V2 = Path("freca_core_v2.py")

NEW_EVALUATE_V2 = '\ndef evaluate_v2(\n    cp_id: str,\n    case_name: str,\n    evidence_top_k: int,\n):\n    """Run the V2 requirement/argument pipeline without the legacy V1 tail."""\n\n    cp = core.get_cp(\n        cp_id\n    )\n\n    contract_path = (\n        CONTRACT_DIR_V2\n        / f"{cp[\'cp_id\']}.json"\n    )\n\n    if not contract_path.exists():\n        raise FileNotFoundError(\n            "No V2 contract found:\\n"\n            f"{contract_path}\\n\\n"\n            "Compile it first."\n        )\n\n    RESULT_DIR_V2.mkdir(\n        parents=True,\n        exist_ok=True,\n    )\n\n    old_result_dir = core.RESULT_DIR\n\n    had_mode = hasattr(\n        core,\n        "FRECA_REQUIREMENT_PIPELINE_ONLY",\n    )\n\n    old_mode = getattr(\n        core,\n        "FRECA_REQUIREMENT_PIPELINE_ONLY",\n        None,\n    )\n\n    # V2 owns the current requirement/argument path.\n    # The V1 atom evaluator is retained only as a separate diagnostic path.\n    core.RESULT_DIR = RESULT_DIR_V2\n    core.FRECA_REQUIREMENT_PIPELINE_ONLY = True\n\n    try:\n        return core.evaluate_case(\n            contract_path,\n            case_name,\n            evidence_top_k,\n        )\n\n    finally:\n        core.RESULT_DIR = old_result_dir\n\n        if had_mode:\n            core.FRECA_REQUIREMENT_PIPELINE_ONLY = old_mode\n        else:\n            delattr(\n                core,\n                "FRECA_REQUIREMENT_PIPELINE_ONLY",\n            )\n'


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
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and node.name == name
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level function {{name}}, "
            f"found {{len(matches)}}"
        )

    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [
        replacement.rstrip() + "\n\n"
    ]
    return "".join(lines)


def insert_v1_firewall(src: str) -> str:
    marker = "FRECA LEGACY FIREWALL — REQUIREMENT PIPELINE ONLY"

    if marker in src:
        return src

    tree = ast.parse(src)

    funcs = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "evaluate_case"
    ]

    if len(funcs) != 1:
        raise RuntimeError(
            f"Expected exactly one evaluate_case, found {{len(funcs)}}"
        )

    func = funcs[0]

    target_stmt = None

    for stmt in func.body:
        if not isinstance(stmt, ast.Expr):
            continue

        call = stmt.value

        if not isinstance(call, ast.Call):
            continue

        if (
            isinstance(call.func, ast.Name)
            and call.func.id == "print_requirement_result"
        ):
            target_stmt = stmt
            break

    if target_stmt is None:
        raise RuntimeError(
            "Could not find top-level print_requirement_result(...) "
            "inside evaluate_case."
        )

    lines = src.splitlines(keepends=True)

    insert_at = target_stmt.end_lineno

    block = (
        "\n"
        "    # --------------------------------------------------------\n"
        "    # FRECA LEGACY FIREWALL — REQUIREMENT PIPELINE ONLY\n"
        "    # V2 stops here. The legacy V1 atom path remains available\n"
        "    # only when evaluate_case is called without the V2 run flag.\n"
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


for path in (V1, V2):
    if not path.exists():
        raise SystemExit(
            f"Missing {{path}}; run from ~/freca/core_v1"
        )

v1_src = V1.read_text(encoding="utf-8")
v2_src = V2.read_text(encoding="utf-8")

required_v1 = (
    "run_from_evaluate_locals",
    "print_requirement_result",
    "BM25 evidence retrieval",
)

required_v2 = (
    "def evaluate_v2",
    "core.evaluate_case",
    "RESULT_DIR_V2",
)

missing_v1 = [
    marker
    for marker in required_v1
    if marker not in v1_src
]

missing_v2 = [
    marker
    for marker in required_v2
    if marker not in v2_src
]

if missing_v1:
    raise SystemExit(
        "Unexpected freca_core_v1.py; missing: "
        + ", ".join(missing_v1)
    )

if missing_v2:
    raise SystemExit(
        "Unexpected freca_core_v2.py; missing: "
        + ", ".join(missing_v2)
    )

patched_v1 = insert_v1_firewall(v1_src)

patched_v2 = replace_top_level_function(
    v2_src,
    "evaluate_v2",
    NEW_EVALUATE_V2,
)

# Parse BOTH complete modules before touching disk.
ast.parse(patched_v1)
ast.parse(patched_v2)

for marker in (
    "FRECA LEGACY FIREWALL — REQUIREMENT PIPELINE ONLY",
    "return requirement_reasoning",
):
    if marker not in patched_v1:
        raise RuntimeError(
            "V1 patched source missing marker: " + marker
        )

for marker in (
    "FRECA_REQUIREMENT_PIPELINE_ONLY",
    "return core.evaluate_case",
    "delattr",
):
    if marker not in patched_v2:
        raise RuntimeError(
            "V2 patched source missing marker: " + marker
        )

backup_v1 = Path(
    "freca_core_v1.before_legacy_firewall.py"
)

backup_v2 = Path(
    "freca_core_v2.before_legacy_firewall.py"
)

if not backup_v1.exists():
    shutil.copy2(V1, backup_v1)

if not backup_v2.exists():
    shutil.copy2(V2, backup_v2)

tmp_v1 = Path(
    "freca_core_v1.legacy_firewall.tmp"
)

tmp_v2 = Path(
    "freca_core_v2.legacy_firewall.tmp"
)

tmp_v1.write_text(
    patched_v1,
    encoding="utf-8",
)

tmp_v2.write_text(
    patched_v2,
    encoding="utf-8",
)

# Parse the exact bytes that will be installed.
ast.parse(
    tmp_v1.read_text(encoding="utf-8")
)

ast.parse(
    tmp_v2.read_text(encoding="utf-8")
)

tmp_v1.replace(V1)
tmp_v2.replace(V2)

print("Installed FRECA V2 legacy firewall.")
print("  V2: requirement pipeline only")
print("  V1: legacy tail retained for direct diagnostic use")
print("  Legacy V1 final label will no longer appear in normal V2 evaluate")
print()
print("Backups:")
print(" ", backup_v1)
print(" ", backup_v2)
