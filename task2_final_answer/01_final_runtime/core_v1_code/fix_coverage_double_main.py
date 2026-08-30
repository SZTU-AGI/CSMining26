#!/usr/bin/env python3
"""Remove duplicate top-level __main__ entry blocks from coverage_v1.py.

Keeps exactly one:
    if __name__ == "__main__":
        main()

Does not modify any function body or coverage semantics.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

TARGET = Path("coverage_v1.py")

if not TARGET.exists():
    raise SystemExit(
        "Missing coverage_v1.py; run from ~/freca/core_v1"
    )

src = TARGET.read_text(
    encoding="utf-8"
)

tree = ast.parse(
    src
)

main_blocks = []

for node in tree.body:
    if not isinstance(
        node,
        ast.If,
    ):
        continue

    test = node.test

    if not (
        isinstance(
            test,
            ast.Compare,
        )
        and isinstance(
            test.left,
            ast.Name,
        )
        and test.left.id
        == "__name__"
        and len(
            test.ops
        )
        == 1
        and isinstance(
            test.ops[
                0
            ],
            ast.Eq,
        )
        and len(
            test.comparators
        )
        == 1
        and isinstance(
            test.comparators[
                0
            ],
            ast.Constant,
        )
        and test.comparators[
            0
        ].value
        == "__main__"
    ):
        continue

    # Require a direct main() call so unrelated __main__ guards are untouched.
    has_main_call = any(
        isinstance(
            stmt,
            ast.Expr,
        )
        and isinstance(
            stmt.value,
            ast.Call,
        )
        and isinstance(
            stmt.value.func,
            ast.Name,
        )
        and stmt.value.func.id
        == "main"
        for stmt in node.body
    )

    if has_main_call:
        main_blocks.append(
            node
        )

if len(
    main_blocks
) <= 1:
    print(
        "coverage_v1.py already has exactly one __main__ block."
    )
    raise SystemExit(
        0
    )

lines = src.splitlines(
    keepends=True
)

# Keep the first block; delete later blocks from bottom to top.
for node in reversed(
    main_blocks[
        1:
    ]
):
    del lines[
        node.lineno - 1:
        node.end_lineno
    ]

patched = "".join(
    lines
)

ast.parse(
    patched
)

backup = Path(
    "coverage_v1.before_main_dedup.py"
)

if not backup.exists():
    shutil.copy2(
        TARGET,
        backup,
    )

TARGET.write_text(
    patched,
    encoding="utf-8",
)

print(
    "Removed duplicate coverage_v1.py __main__ blocks."
)
print(
    "Remaining blocks: 1"
)
print(
    "Backup:",
    backup,
)
