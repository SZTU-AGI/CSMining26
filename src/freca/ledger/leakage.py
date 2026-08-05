"""Answer-leakage guard for the fact ledger (proposal §3).

The competition forbids turning near-answer scenario metadata into checking-point
labels. Track 3 packages in this dataset carry fields such as
``Audit scenario: ...`` and inline notes like ``NOTE: NON-COMPLIANT`` that state
the intended outcome of a case. Those strings are *scenario authoring metadata*,
not farm evidence.

This module detects such passages so that:

1. facts derived from them are marked ``answer_like_field``;
2. the evidence selector drops them from the adjudication pack by default;
3. the decision gate rejects any verdict that leans on them.

Detection is intentionally conservative and pattern-based; it never rewrites a
verdict on its own, it only removes a contaminated shortcut.
"""

from __future__ import annotations

import re

# Scenario-authoring markers: text that announces the intended audit outcome.
_ANSWER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("audit_scenario_field", re.compile(r"\baudit\s*scenario\b", re.IGNORECASE)),
    ("scenario_label", re.compile(r"\bscenario\s*(?:type|label|id|name)?\s*[:=]", re.IGNORECASE)),
    ("explicit_compliance_note", re.compile(r"\bnote\s*[:\-]\s*(?:non[- ]?compliant|compliant)\b", re.IGNORECASE)),
    ("compliance_status_field", re.compile(r"\b(?:compliance|conformity)\s*(?:status|result|outcome)\s*[:=]", re.IGNORECASE)),
    ("non_compliant_marker", re.compile(r"\bnon[- ]?compliance\s*(?:flag|marker|indicator)\b", re.IGNORECASE)),
    ("expected_answer_field", re.compile(r"\b(?:expected|intended|correct)\s*(?:answer|result|verdict|outcome)\b", re.IGNORECASE)),
    ("ground_truth_field", re.compile(r"\bground\s*truth\b|\bgolden\s*(?:answer|label)\b", re.IGNORECASE)),
    ("checkpoint_label_field", re.compile(r"\bcheck(?:ing)?\s*point\s*(?:answer|label|result)\b", re.IGNORECASE)),
    ("verdict_literal_field", re.compile(r"\bverdict\s*[:=]\s*(?:1|0|n/?a)\b", re.IGNORECASE)),
)

ANSWER_LIKE_FLAG = "answer_like_field"


def detect_answer_like(text: str) -> list[str]:
    """Return the leakage marker codes present in ``text`` (possibly empty)."""

    if not text:
        return []
    return sorted({name for name, pattern in _ANSWER_PATTERNS if pattern.search(text)})


def is_answer_like(text: str) -> bool:
    return bool(detect_answer_like(text))


def leakage_flags(text: str) -> list[str]:
    """Flags to attach to a fact extracted from ``text``."""

    markers = detect_answer_like(text)
    if not markers:
        return []
    return [ANSWER_LIKE_FLAG, *(f"leak:{marker}" for marker in markers)]


def strip_answer_like_lines(text: str) -> str:
    """Drop individual lines that announce an intended audit outcome.

    Used when a chunk is mostly legitimate evidence but contains a stray
    scenario annotation. Keeps the surrounding evidence usable instead of
    discarding the whole chunk.
    """

    if not text:
        return text
    kept = [line for line in text.splitlines() if not is_answer_like(line)]
    return "\n".join(kept)


__all__ = [
    "ANSWER_LIKE_FLAG",
    "detect_answer_like",
    "is_answer_like",
    "leakage_flags",
    "strip_answer_like_lines",
]
