"""Deterministic contradiction detection inside one case ledger (proposal §4.1).

The proposal lists "矛盾" as a first-class fact type: mutually conflicting
statements on the same subject, missing records, and identity/address/RE
mismatches. Detecting these *before* adjudication matters because a conflict is
precisely the situation where a single-pass verdict is least trustworthy — §7
makes an unresolved same-topic conflict a review trigger.

Everything here is deterministic and domain-generic:

* it compares values that the documents themselves state;
* it never decides which side of a conflict is correct;
* it never emits a checking-point verdict.

Four detectors
--------------
``IDENTITY_MISMATCH``
    More than one RE number, or more than one establishment name, appears in
    the same case's materials.
``CROSS_DOCUMENT_VALUE``
    The same ``Label: value`` key carries different values in different source
    files (for example a lighting reading of 550 lux in one document and
    300 lux in another).
``SAME_TOPIC_CONFLICT``
    The same label is asserted affirmatively in one place and negatively in
    another ("bait stations inspected" vs "no bait station inspection").
``MISSING_RECORD``
    A track present in the case layout produced no chunks, or a whole material
    topic has no facts at all.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Sequence

from freca.models import EvidenceChunk

from freca.ledger.models import ContradictionKind, FactContradiction, FactRecord

_RE_NUMBER = re.compile(r"\bRE-[A-Z]{2,3}-\d{4}-\d{4}\b")
_LABEL_VALUE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z /&'()\.-]{2,48}?)\s*[:=]\s*(?P<value>.+)$")
_ESTABLISHMENT_LABELS = (
    "establishment name",
    "registered establishment",
    "business name",
    "trading name",
    "company name",
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WS = re.compile(r"\s+")

_NEGATIVE_MARKERS = (
    "not available",
    "not provided",
    "not recorded",
    "not maintained",
    "not present",
    "not sighted",
    "no record",
    "no records",
    "none",
    "nil",
    "absent",
    "missing",
    "unavailable",
    "never",
)
_POSITIVE_MARKERS = (
    "available",
    "provided",
    "recorded",
    "maintained",
    "present",
    "sighted",
    "completed",
    "in place",
    "yes",
)

# Labels that are naturally multi-valued and would otherwise raise noise.
_IGNORED_LABELS = frozenset(
    {
        "date",
        "dates",
        "time",
        "signature",
        "signed",
        "reviewed by",
        "prepared by",
        "approved by",
        "notes",
        "note",
        "comments",
        "remarks",
        "page",
        "item",
        "no",
        "ref",
        "reference",
        "description",
        "location",
        "area",
        "shed",
        "condition",
        "activity level",
        "product",
        "quantity",
        "amount",
    }
)


def _normalize(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip()


def _label_value(text: str) -> tuple[str, str] | None:
    match = _LABEL_VALUE.match(_normalize(text))
    if match is None:
        return None
    label = _normalize(match.group("label")).casefold()
    value = _normalize(match.group("value"))
    if not label or not value or label in _IGNORED_LABELS:
        return None
    return label, value


def _canonical_value(value: str) -> str:
    """Compare values on their numeric content when they carry numbers."""

    numbers = _NUMBER.findall(value)
    if numbers:
        return "|".join(numbers)
    return _normalize(value).casefold().rstrip(".;,")


def _polarity_of(value: str) -> str | None:
    lowered = f" {_normalize(value).casefold()} "
    for marker in _NEGATIVE_MARKERS:
        if f" {marker} " in lowered or lowered.strip().startswith(marker):
            return "negative"
    for marker in _POSITIVE_MARKERS:
        if f" {marker} " in lowered or lowered.strip().startswith(marker):
            return "positive"
    return None


def _identity_mismatch(case_id: int, facts: Sequence[FactRecord]) -> list[FactContradiction]:
    findings: list[FactContradiction] = []

    by_re: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        for match in _RE_NUMBER.findall(f"{fact.verbatim}\n{fact.claim}"):
            by_re[match].append(fact.fact_id)
    if len(by_re) > 1:
        values = sorted(by_re)
        findings.append(
            FactContradiction(
                contradiction_id=f"case-{case_id:03d}-identity-re",
                case_id=case_id,
                kind=ContradictionKind.IDENTITY_MISMATCH,
                topic="registration",
                fact_ids=sorted({fid for ids in by_re.values() for fid in ids})[:12],
                detail=(
                    "materials carry more than one RE number: " + ", ".join(values)
                ),
                severity="BLOCKER",
            )
        )

    by_name: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        parsed = _label_value(fact.claim)
        if parsed is None:
            continue
        label, value = parsed
        if label in _ESTABLISHMENT_LABELS:
            by_name[_normalize(value).casefold()].append(fact.fact_id)
    if len(by_name) > 1:
        findings.append(
            FactContradiction(
                contradiction_id=f"case-{case_id:03d}-identity-name",
                case_id=case_id,
                kind=ContradictionKind.IDENTITY_MISMATCH,
                topic="registration",
                fact_ids=sorted({fid for ids in by_name.values() for fid in ids})[:12],
                detail=(
                    "materials name more than one establishment: "
                    + ", ".join(sorted(by_name))
                ),
                severity="BLOCKER",
            )
        )
    return findings


def _value_conflicts(case_id: int, facts: Sequence[FactRecord]) -> list[FactContradiction]:
    grouped: dict[tuple[str, str], dict[str, list[FactRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for fact in facts:
        parsed = _label_value(fact.claim)
        if parsed is None:
            continue
        label, value = parsed
        grouped[(fact.topic, label)][_canonical_value(value)].append(fact)

    findings: list[FactContradiction] = []
    for (topic, label), buckets in sorted(grouped.items()):
        if len(buckets) < 2:
            continue
        sources = {
            fact.source_file for group in buckets.values() for fact in group
        }
        polarities = {
            polarity
            for canonical in buckets
            if (polarity := _polarity_of(canonical)) is not None
        }
        fact_ids = sorted(
            {fact.fact_id for group in buckets.values() for fact in group}
        )[:12]
        rendered = ", ".join(sorted(buckets)[:4])

        if polarities == {"positive", "negative"}:
            findings.append(
                FactContradiction(
                    contradiction_id=f"case-{case_id:03d}-conflict-{topic}-{_slug(label)}",
                    case_id=case_id,
                    kind=ContradictionKind.SAME_TOPIC_CONFLICT,
                    topic=topic,
                    fact_ids=fact_ids,
                    detail=(
                        f"'{label}' is both asserted and denied within topic "
                        f"'{topic}': {rendered}"
                    ),
                    severity="REVIEW",
                )
            )
        elif len(sources) > 1:
            findings.append(
                FactContradiction(
                    contradiction_id=f"case-{case_id:03d}-crossdoc-{topic}-{_slug(label)}",
                    case_id=case_id,
                    kind=ContradictionKind.CROSS_DOCUMENT_VALUE,
                    topic=topic,
                    fact_ids=fact_ids,
                    detail=(
                        f"'{label}' has different values across documents: {rendered}"
                    ),
                    severity="REVIEW",
                )
            )
    return findings


def _missing_records(
    case_id: int,
    facts: Sequence[FactRecord],
    chunks: Iterable[EvidenceChunk],
) -> list[FactContradiction]:
    present_tracks = {chunk.track for chunk in chunks if chunk.track is not None}
    missing = sorted(track for track in range(1, 10) if track not in present_tracks)
    if not missing:
        return []
    return [
        FactContradiction(
            contradiction_id=f"case-{case_id:03d}-missing-tracks",
            case_id=case_id,
            kind=ContradictionKind.MISSING_RECORD,
            topic="records",
            fact_ids=[],
            detail=(
                "no parsed material for track(s): "
                + ", ".join(str(track) for track in missing)
            ),
            severity="REVIEW",
        )
    ]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:40] or "item"


def detect_contradictions(
    *,
    case_id: int,
    facts: Sequence[FactRecord],
    chunks: Iterable[EvidenceChunk] = (),
) -> list[FactContradiction]:
    """Run every detector and return findings in a stable order."""

    chunk_list = list(chunks)
    findings = [
        *_identity_mismatch(case_id, facts),
        *_value_conflicts(case_id, facts),
        *_missing_records(case_id, facts, chunk_list),
    ]
    unique: dict[str, FactContradiction] = {}
    for finding in findings:
        unique.setdefault(finding.contradiction_id, finding)
    return sorted(unique.values(), key=lambda item: item.contradiction_id)


__all__ = ["detect_contradictions"]
