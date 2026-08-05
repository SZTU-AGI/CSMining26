"""Generic material topics and evidence categories for the fact ledger.

Scope discipline (proposal §3, §4.1)
-----------------------------------
This taxonomy describes **what kind of information a document carries**
(registration data, facility measurements, pest-control records, ...). It is a
filing system for source material.

It is explicitly *not*:

* a mapping from a checking point to a required answer;
* a rule saying "topic X present ⇒ CP_n is 1";
* a list of thresholds or pass conditions.

No entry in this module references a CP id, and nothing here can produce a
``1 / 0 / N/A`` label. Compliance meaning is attached later, at runtime, by
:mod:`freca.ledger.rubric`, which derives requirements only from retrieved
official regulation text.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Topics — derived from proposal §4.1 "优先抽取的事实类型"
# --------------------------------------------------------------------------

TOPIC_REGISTRATION = "registration"
TOPIC_FACILITIES = "facilities"
TOPIC_SANITATION_PEST = "sanitation_pest"
TOPIC_RECORDS = "records"
TOPIC_TRACEABILITY_QUARANTINE = "traceability_quarantine"
TOPIC_PERSONNEL = "personnel"
TOPIC_UNCLASSIFIED = "unclassified"

TOPICS: tuple[str, ...] = (
    TOPIC_REGISTRATION,
    TOPIC_FACILITIES,
    TOPIC_SANITATION_PEST,
    TOPIC_RECORDS,
    TOPIC_TRACEABILITY_QUARANTINE,
    TOPIC_PERSONNEL,
    TOPIC_UNCLASSIFIED,
)

TOPIC_DESCRIPTIONS: dict[str, str] = {
    TOPIC_REGISTRATION: (
        "business scope, registration status, suspension or expiry, responsible "
        "person, address, and change history"
    ),
    TOPIC_FACILITIES: (
        "lighting values, hand-washing and toilets, drainage, inspection benches, "
        "grading or sieving equipment, and waste facilities"
    ),
    TOPIC_SANITATION_PEST: (
        "cleaning schedules, bait station status, pest activity, corrective "
        "actions, and chemical location or locking status"
    ),
    TOPIC_RECORDS: (
        "record dates, retention periods, language, signatures, legibility, and "
        "treatment or rejection entries"
    ),
    TOPIC_TRACEABILITY_QUARANTINE: (
        "receiving, lots, stock movement, treatment, dispatch, seals, and "
        "importing-country requirements"
    ),
    TOPIC_PERSONNEL: (
        "staffing, training, supervision, health declarations, and hygiene "
        "instruction of workers"
    ),
    TOPIC_UNCLASSIFIED: "material that does not map to a listed topic",
}

_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    TOPIC_REGISTRATION: (
        "registration", "registered", "register", "re number", "re-", "licence",
        "license", "scope", "suspend", "suspension", "expiry", "expire", "valid",
        "renewal", "certificate of registration", "establishment name", "address",
        "premises", "proprietor", "owner", "responsible person", "amendment",
    ),
    TOPIC_FACILITIES: (
        "lighting", "lux", "light", "illumination", "hand wash", "handwash",
        "hand-wash", "toilet", "sanitary convenience", "washbasin", "drainage",
        "drain", "inspection bench", "bench", "table", "sieve", "sieving",
        "grading", "sorting", "waste", "bin", "refuse", "floor", "wall",
        "ceiling", "ventilation", "storage area", "shed", "packhouse",
        "equipment", "container", "pallet",
    ),
    TOPIC_SANITATION_PEST: (
        "clean", "cleaning", "sanitation", "hygiene", "pest", "bait", "bait station",
        "rodent", "insect", "trap", "infestation", "fumigation", "pesticide",
        "chemical", "chemical store", "lockable", "locked", "disinfect",
        "corrective action", "monitoring", "contamination", "spray",
    ),
    TOPIC_RECORDS: (
        "record", "records", "log", "logbook", "register of", "retention",
        "retained", "kept for", "signature", "signed", "initial", "legible",
        "language", "english", "date", "dated", "entry", "form", "checklist",
        "document control", "version", "review date", "rejection", "reject",
    ),
    TOPIC_TRACEABILITY_QUARANTINE: (
        "traceab", "lot", "batch", "consignment", "receiv", "dispatch",
        "shipment", "seal", "seal number", "stock", "inventory", "movement",
        "treatment", "phytosanitary", "quarantine", "importing country",
        "export", "declaration", "certificate", "origin", "grower",
    ),
    TOPIC_PERSONNEL: (
        "staff", "worker", "employee", "personnel", "training", "trained",
        "supervis", "operator", "health", "illness", "protective clothing",
        "ppe", "glove", "instruction", "competen",
    ),
}

# --------------------------------------------------------------------------
# Evidence categories — the *form* of proof, requested by rubric criteria
# --------------------------------------------------------------------------

EVIDENCE_CATEGORIES: tuple[str, ...] = (
    "registration_document",
    "measurement_value",
    "dated_record",
    "signed_record",
    "schedule_or_plan",
    "corrective_action",
    "photo_or_description",
    "declaration_or_certificate",
    "inventory_or_movement",
    "narrative_statement",
)

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "measurement_value",
        re.compile(
            r"\b\d+(?:\.\d+)?\s*(?:lux|lx|mm|cm|m|m2|m²|kg|g|l|ml|%|ppm|°c|c|hpa)\b",
            re.IGNORECASE,
        ),
    ),
    ("dated_record", re.compile(r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b")),
    ("signed_record", re.compile(r"\bsign(?:ed|ature)\b|\binitial(?:led|s)\b", re.IGNORECASE)),
    ("schedule_or_plan", re.compile(r"\bschedule\b|\bplan\b|\bfrequency\b|\bweekly\b|\bmonthly\b|\bdaily\b|\bannual\b", re.IGNORECASE)),
    ("corrective_action", re.compile(r"\bcorrective\b|\bremedial\b|\bfollow[- ]?up\b|\baction taken\b", re.IGNORECASE)),
    ("declaration_or_certificate", re.compile(r"\bcertificat|\bdeclarat|\bphytosanitary\b|\bpermit\b", re.IGNORECASE)),
    ("inventory_or_movement", re.compile(r"\bstock\b|\binventory\b|\bmovement\b|\bdispatch\b|\breceiv|\bconsignment\b|\blot\b|\bbatch\b", re.IGNORECASE)),
    ("registration_document", re.compile(r"\bRE-[A-Z]{2,3}-\d{4}-\d{4}\b|\bregistration\b|\bregistered\b", re.IGNORECASE)),
    ("photo_or_description", re.compile(r"\bphoto\b|\bimage\b|\bpicture\b|\bfigure\b", re.IGNORECASE)),
)


def classify_topic(text: str) -> str:
    """Assign the best-matching material topic by keyword density.

    Returns ``TOPIC_UNCLASSIFIED`` when nothing matches. Ties break by the
    declaration order in :data:`TOPICS` so results stay deterministic.
    """

    lowered = (text or "").casefold()
    if not lowered.strip():
        return TOPIC_UNCLASSIFIED
    best_topic = TOPIC_UNCLASSIFIED
    best_score = 0
    for topic in TOPICS:
        keywords = _TOPIC_KEYWORDS.get(topic)
        if not keywords:
            continue
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score > best_score:
            best_topic = topic
            best_score = score
    return best_topic


def normalize_topic(value: str | None) -> str:
    """Map free-form model output onto the closed topic vocabulary."""

    if not value:
        return TOPIC_UNCLASSIFIED
    text = str(value).strip().casefold().replace(" ", "_").replace("-", "_")
    if text in TOPICS:
        return text
    for topic in TOPICS:
        if topic in text or text in topic:
            return topic
    inferred = classify_topic(str(value))
    return inferred


def detect_evidence_categories(text: str) -> list[str]:
    """Label the *form* of evidence a passage provides."""

    found: list[str] = []
    for name, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text or ""):
            found.append(name)
    if not found:
        found.append("narrative_statement")
    return sorted(set(found))


def normalize_evidence_categories(values: list[str] | None) -> list[str]:
    if not values:
        return []
    allowed = set(EVIDENCE_CATEGORIES)
    normalized = []
    for value in values:
        text = str(value).strip().casefold().replace(" ", "_").replace("-", "_")
        if text in allowed:
            normalized.append(text)
    return sorted(set(normalized))


__all__ = [
    "EVIDENCE_CATEGORIES",
    "TOPICS",
    "TOPIC_DESCRIPTIONS",
    "TOPIC_FACILITIES",
    "TOPIC_PERSONNEL",
    "TOPIC_RECORDS",
    "TOPIC_REGISTRATION",
    "TOPIC_SANITATION_PEST",
    "TOPIC_TRACEABILITY_QUARANTINE",
    "TOPIC_UNCLASSIFIED",
    "classify_topic",
    "detect_evidence_categories",
    "normalize_evidence_categories",
    "normalize_topic",
]
