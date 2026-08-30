from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf


# ============================================================
# Minimal deterministic PolicyUnit parser
#
# Extracted from the full FRECA Layer-1 design.
#
# Current purpose:
#   replace arbitrary 1500-char page chunks with:
#
#       SECTION
#       SUBSECTION
#       PARAGRAPH
#       SUBPARAGRAPH
#
# while preserving:
#   - own text
#   - parent chapeau
#   - section title
#   - cross-page continuation
#
# This is NOT yet the complete production Layer-1 parser.
# ============================================================


SECTION_RE = re.compile(
    r"^(\d{1,2}-\d+[A-Z]?)\s+([A-Z][^\n]*)$"
)

LABEL_RE = re.compile(
    r"^\(([^)]+)\)\s*(.*)$"
)

# Common legal roman subparagraph labels.
# Single letters such as (c) must remain alphabetic paragraphs.
ROMAN_RE = re.compile(
    r"^(?:"
    r"i|ii|iii|iv|v|vi|vii|viii|ix|x|"
    r"xi|xii|xiii|xiv|xv|xvi|xvii|xviii|xix|xx"
    r")$"
)


def normalize_line(
    value: Any,
) -> str:

    text = str(value)
    text = text.replace(
        "\u00a0",
        " ",
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# Page furniture
# ============================================================

def is_page_furniture(
    line: str,
) -> bool:

    if not line:
        return True

    # Printed page number
    if re.fullmatch(
        r"\d+",
        line,
    ):
        return True

    if line.startswith(
        "Export Control (Plants and Plant Products) Rules 2021"
    ):
        return True

    if line.startswith(
        "Compilation No."
    ):
        return True

    if line.startswith(
        "Compilation date:"
    ):
        return True

    if line.startswith(
        "Authorised Version "
    ):
        return True

    # Running header:
    # "Section 4-7B"
    if re.fullmatch(
        r"Section \d{1,2}-\d+[A-Z]?",
        line,
    ):
        return True

    if re.fullmatch(
        r"Registered establishments\s+Chapter \d+",
        line,
    ):
        return True

    if re.fullmatch(
        r"Chapter \d+\s+Registered establishments",
        line,
    ):
        return True

    if re.fullmatch(
        r"(?:Requirements for registration|Conditions of registration)"
        r"\s+Part \d+",
        line,
    ):
        return True

    if re.fullmatch(
        r"Part \d+\s+"
        r"(?:Requirements for registration|Conditions of registration)",
        line,
    ):
        return True

    # Structural Chapter / Part headings are currently
    # not indexed as normative units in this minimal parser.
    if re.fullmatch(
        r"Chapter \d+[—-].+",
        line,
    ):
        return True

    if re.fullmatch(
        r"Part \d+[—-].+",
        line,
    ):
        return True

    return False


# ============================================================
# Label classification
# ============================================================

def classify_label(
    token: str,
    stack: list[dict],
) -> tuple[str, int]:

    token = token.strip()

    # (1), (2), (2A)
    if re.fullmatch(
        r"\d+[A-Z]?",
        token,
    ):
        return (
            "SUBSECTION",
            2,
        )

    deepest_level = (
        stack[-1]["level"]
        if stack
        else 1
    )

    # Roman numerals become SUBPARAGRAPH only when
    # we're already inside a paragraph-level structure.
    #
    # This prevents:
    #   (b) -> (c)
    # from incorrectly treating (c) as Roman 100.
    if (
        ROMAN_RE.fullmatch(token)
        and deepest_level >= 3
    ):
        return (
            "SUBPARAGRAPH",
            4,
        )

    # (a), (b), (c), ...
    return (
        "PARAGRAPH",
        3,
    )


# ============================================================
# Parser
# ============================================================

def extract_policy_units(
    pdf_path: str | Path,
) -> list[dict]:

    pdf_path = Path(
        pdf_path
    )

    doc = pymupdf.open(
        pdf_path
    )

    # --------------------------------------------------------
    # 1. Flatten BODY lines across physical pages.
    #
    # IMPORTANT:
    # page boundaries do NOT reset legal structure.
    # --------------------------------------------------------

    lines = []

    for page_index in range(
        len(doc)
    ):

        page_text = (
            doc[
                page_index
            ]
            .get_text(
                "text"
            )
        )

        for page_line_index, raw_line in enumerate(
            page_text.splitlines(),
            1,
        ):

            text = normalize_line(
                raw_line
            )

            if is_page_furniture(
                text
            ):
                continue

            lines.append(
                {
                    "page":
                        page_index + 1,
                    "page_line":
                        page_line_index,
                    "text":
                        text,
                }
            )

    # --------------------------------------------------------
    # 2. Parse hierarchy.
    # --------------------------------------------------------

    units = []
    unit_by_id = {}

    stack = []

    pending_heading = None

    skip_non_normative = False

    def next_text(
        current_index: int,
    ) -> str:

        for j in range(
            current_index + 1,
            len(lines),
        ):

            candidate = (
                lines[j]["text"]
            )

            if candidate:
                return candidate

        return ""

    for index, record in enumerate(
        lines
    ):

        text = record["text"]

        section_match = (
            SECTION_RE.match(
                text
            )
        )

        label_match = (
            LABEL_RE.match(
                text
            )
        )

        # ----------------------------------------------------
        # Notes / Examples:
        # retain neither as normative units in this minimal
        # Core parser.
        #
        # Full architecture will eventually model them as
        # NOTE / EXAMPLE with normative=False.
        # ----------------------------------------------------

        if re.match(
            r"^(?:Note(?: \d+)?:|Example:)",
            text,
        ):

            skip_non_normative = True
            continue

        # A new formal legal unit ends the Note/Example block.
        if (
            section_match
            or label_match
        ):
            skip_non_normative = False

        elif skip_non_normative:
            continue

        # ----------------------------------------------------
        # SECTION
        # ----------------------------------------------------

        if section_match:

            citation = (
                section_match.group(1)
            )

            title = (
                section_match.group(2)
                .strip()
            )

            unit_id = (
                f"rules2021:{citation}"
            )

            unit = {
                "id":
                    unit_id,
                "citation":
                    citation,
                "unit_type":
                    "SECTION",
                "level":
                    1,
                "title":
                    title,
                "label":
                    citation,
                "own_lines":
                    [],
                "pages":
                    {record["page"]},
                "parent_id":
                    None,
                "child_ids":
                    [],
            }

            units.append(
                unit
            )

            unit_by_id[
                unit_id
            ] = unit

            stack = [
                unit
            ]

            pending_heading = None

            continue

        # ----------------------------------------------------
        # SUBSECTION / PARAGRAPH / SUBPARAGRAPH
        # ----------------------------------------------------

        if label_match:

            if not stack:
                # Text before first section:
                # outside current minimal legal-unit scope.
                continue

            token = (
                label_match.group(1)
                .strip()
            )

            body = (
                label_match.group(2)
                .strip()
            )

            unit_type, level = (
                classify_label(
                    token,
                    stack,
                )
            )

            # Pop sibling / descendant frames.
            while (
                stack
                and stack[-1][
                    "level"
                ] >= level
            ):
                stack.pop()

            if not stack:
                continue

            parent = (
                stack[-1]
            )

            citation = (
                f"{parent['citation']}"
                f"({token})"
            )

            unit_id = (
                f"rules2021:{citation}"
            )

            own_line = (
                f"({token}) {body}"
            ).strip()

            unit = {
                "id":
                    unit_id,
                "citation":
                    citation,
                "unit_type":
                    unit_type,
                "level":
                    level,
                "title":
                    pending_heading,
                "label":
                    f"({token})",
                "own_lines":
                    (
                        [own_line]
                        if own_line
                        else []
                    ),
                "pages":
                    {record["page"]},
                "parent_id":
                    parent["id"],
                "child_ids":
                    [],
            }

            parent[
                "child_ids"
            ].append(
                unit_id
            )

            units.append(
                unit
            )

            unit_by_id[
                unit_id
            ] = unit

            stack.append(
                unit
            )

            pending_heading = None

            continue

        # ----------------------------------------------------
        # Possible internal heading immediately before
        # a formal numbered unit:
        #
        #   Design and construction—general
        #   (4) ...
        #
        # Avoid misclassifying wrapped sentence tails such as:
        #
        #   the kind of plants ...; and
        #   (b) ...
        # ----------------------------------------------------

        following = (
            next_text(
                index
            )
        )

        following_is_unit = bool(
            SECTION_RE.match(
                following
            )
            or LABEL_RE.match(
                following
            )
        )

        title_like = (
            following_is_unit
            and len(text) <= 120
            and not re.search(
                r"[.;:,]",
                text,
            )
            and not re.search(
                r"\b(?:and|or)$",
                text,
                flags=re.I,
            )
        )

        if title_like:

            pending_heading = (
                text
            )

            continue

        # ----------------------------------------------------
        # Continuation line:
        # append to currently open legal unit.
        # ----------------------------------------------------

        if stack:

            stack[-1][
                "own_lines"
            ].append(
                text
            )

            stack[-1][
                "pages"
            ].add(
                record["page"]
            )

    # --------------------------------------------------------
    # 3. Materialise own_text.
    # --------------------------------------------------------

    for unit in units:

        unit[
            "own_text"
        ] = "\n".join(
            line
            for line
            in unit[
                "own_lines"
            ]
            if line
        ).strip()

    # --------------------------------------------------------
    # 4. Build retrieval view:
    #
    # minimal unit own text
    # + immediate ancestor chapeau
    # + section title
    #
    # This is the minimal extraction of D1.14.
    # --------------------------------------------------------

    for unit in units:

        parts = []

        # Find section ancestor.
        section = unit

        while section.get(
            "parent_id"
        ):

            section = (
                unit_by_id[
                    section[
                        "parent_id"
                    ]
                ]
            )

        if section.get(
            "title"
        ):

            parts.append(
                f"{section['citation']} "
                f"{section['title']}"
            )

        if unit.get(
            "title"
        ):

            parts.append(
                unit["title"]
            )

        # Immediate parent = required chapeau for
        # paragraph/subparagraph interpretation.
        if unit.get(
            "parent_id"
        ):

            parent = (
                unit_by_id[
                    unit[
                        "parent_id"
                    ]
                ]
            )

            if parent.get(
                "own_text"
            ):

                parts.append(
                    parent[
                        "own_text"
                    ]
                )

        if unit.get(
            "own_text"
        ):

            parts.append(
                unit[
                    "own_text"
                ]
            )

        # Preserve order while removing exact duplicates.
        seen = set()
        unique_parts = []

        for part in parts:

            if (
                part
                and part
                not in seen
            ):

                seen.add(
                    part
                )

                unique_parts.append(
                    part
                )

        unit[
            "text"
        ] = "\n".join(
            unique_parts
        )

        unit[
            "page"
        ] = (
            min(
                unit[
                    "pages"
                ]
            )
            if unit[
                "pages"
            ]
            else None
        )

        unit[
            "page_end"
        ] = (
            max(
                unit[
                    "pages"
                ]
            )
            if unit[
                "pages"
            ]
            else None
        )

        # JSON-safe
        unit[
            "pages"
        ] = sorted(
            unit[
                "pages"
            ]
        )

        del unit[
            "own_lines"
        ]

    return units


# ============================================================
# CLI sanity check
# ============================================================

if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "pdf"
    )

    parser.add_argument(
        "--prefix",
        default="4-",
        help="citation prefix to display",
    )

    args = parser.parse_args()

    result = (
        extract_policy_units(
            args.pdf
        )
    )

    print(
        f"PolicyUnits: "
        f"{len(result)}"
    )

    for unit in result:

        if unit[
            "citation"
        ].startswith(
            args.prefix
        ):

            print(
                "\n"
                + "=" * 70
            )

            print(
                unit[
                    "id"
                ],
                unit[
                    "unit_type"
                ],
            )

            print(
                unit[
                    "text"
                ]
            )
