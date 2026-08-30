"""FRECA Core FactCandidate/subspan bridge v1.

Minimal implementation of the frozen Layer-4 boundary:
EvidenceAtom/retrieval chunk -> exact-grounded FactCandidate(s) -> D7.8.
No CP labels, case labels, sufficiency, identity, or final compliance decisions.
"""
from __future__ import annotations

import hashlib
import re

from evidence_nature_v1 import (
    assess_alignment_compatibility,
    classify_evidence_nature,
    infer_requirement_predicate_profile,
)

POSITIVE = "POSITIVE"
ADVERSE = "ADVERSE"
NEUTRAL = "NEUTRAL"
MIXED = "MIXED"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _trim(text: str, start: int, end: int):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def _segments(text: str):
    cuts = [0]
    for m in re.finditer(r"(?:[.!?](?=\s|$)|\n+|•+)", text):
        cuts.append(m.end())
    if cuts[-1] != len(text):
        cuts.append(len(text))

    out = []
    for a, b in zip(cuts, cuts[1:]):
        s, e, q = _trim(text, a, b)
        q2 = q.lstrip("•").strip()
        if not q2:
            continue
        rel = text[s:e].find(q2)
        if rel >= 0:
            s += rel
            e = s + len(q2)
        out.append((s, e, q2))
    return out


def _semicolon_segments(text: str, start: int, end: int):
    segment = text[start:end]
    cuts = [0]
    for m in re.finditer(r";+", segment):
        cuts.append(m.end())
    if cuts[-1] != len(segment):
        cuts.append(len(segment))

    out = []
    for a, b in zip(cuts, cuts[1:]):
        s, e, q = _trim(text, start + a, start + b)
        q2 = q.strip(";").strip()
        if not q2:
            continue
        rel = text[s:e].find(q2)
        if rel >= 0:
            s += rel
            e = s + len(q2)
        out.append((s, e, q2))
    return out or [(start, end, text[start:end])]



def _explicit_positive_state(text: str) -> bool:
    t = _norm(text)

    patterns = (
        # Explicitly resolved openings/gaps.
        r"\ball gaps\b[^.]{0,120}\b(?:sealed|filled|closed off)\b",
        r"\bgaps?\b\s*(?:is|are|was|were|has been|have been)?\s*"
        r"(?:sealed|filled|closed off)\b",
        r"\bopenings?\b[^.]{0,80}\b(?:screened|sealed|covered)\b",

        # Explicit absence / satisfactory actual states.
        r"\bno open\b",
        r"\bfree of\b",
        r"\bno evidence of\b",
        r"\bno (?:rodent|pest|bird|insect) activity\b",
        r"\bnegative for\b",
        r"\bbelow threshold\b",
        r"\bwithin acceptable range\b",
        r"\bconfirmed clean\b",
        r"\bserviceable\b",
        r"\bin place and effective\b",
        r"\bmaintained clean\b",
    )

    return any(
        re.search(pattern, t)
        for pattern in patterns
    )



def _instructional_adverse(text: str) -> bool:
    return bool(re.search(
        r"\b(?:inspect|check|monitor|look)\b[^.;]{0,80}"
        r"\b(?:for )?(?:wear|damage|cracks?|holes?|gaps?|pest activity)\b",
        _norm(text),
    ))



def _polarity(quote: str, typed: dict) -> str:
    natures = set(typed.get("evidence_natures", []))
    assertion = typed.get("assertion_mode", {})
    actual = bool(assertion.get("actual_signal_present"))
    signals = set(typed.get("clause_signals", []))

    adverse = bool(
        natures & {
            "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",
            "ADVERSE_OPERATIONAL_FINDING",
            "EXPLICIT_CONTROL_ABSENCE",
            "DOCUMENTATION_GAP",
            "REGISTRATION_SCOPE_DEFECT",
        }
        or signals & {
            "ADVERSE",
            "MIXED",
        }
    )

    positive = bool(
        natures & {
            "CURRENT_CONDITION",
            "PHYSICAL_DESIGN_FEATURE",
            "EQUIPMENT_CONDITION",
            "REGISTERED_OPERATION_SCOPE",
        }
        or signals & {
            "POSITIVE",
            "MIXED",
        }
        or _explicit_positive_state(quote)
    )

    if (
        adverse
        and not actual
        and _instructional_adverse(quote)
    ):
        adverse = False

    explicit_adverse_state = bool(
        re.search(
            r"\b(?:worn|cracked|broken|damaged|"
            r"delaminat(?:ed|ion)|hole(?:s)?|"
            r"repeated .* activity|"
            r"delayed .* follow[- ]?up)\b",
            _norm(quote),
        )
    )

    explicit_resolved_positive = (
        _explicit_positive_state(quote)
    )

    # A generic physical-feature cue (mesh/gasket/seal/etc.) must not turn an
    # explicit actual defect into MIXED.  If the quote says the feature is
    # currently worn/cracked/delaminated/holed and does not also say it is
    # sealed/closed/filled/resolved, the observable state is adverse.
    if (
        adverse
        and actual
        and explicit_adverse_state
        and not explicit_resolved_positive
    ):
        positive = False

    if (
        explicit_resolved_positive
        and "ADVERSE_OPERATIONAL_FINDING"
        not in natures
        and not explicit_adverse_state
    ):
        adverse = False

    if positive and adverse:
        return MIXED
    if adverse:
        return ADVERSE
    if positive:
        return POSITIVE
    return NEUTRAL



def _event_type(typed: dict, polarity: str) -> str:
    natures = set(typed.get("evidence_natures", []))
    if polarity == ADVERSE and natures & {
        "ADVERSE_OPERATIONAL_FINDING",
        "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",
    }:
        return "ADVERSE_CONDITION"
    mapping = (
        ("DOCUMENTATION_GAP", "DOCUMENTATION_GAP"),
        ("EXPLICIT_CONTROL_ABSENCE", "CONTROL_ABSENCE"),
        ("DESIGN_OR_CONSTRUCTION_DEFECT", "DESIGN_DEFECT"),
        ("PHYSICAL_DESIGN_FEATURE", "PHYSICAL_FEATURE"),
        ("REVIEW_FINDING", "REVIEW_FINDING"),
        ("REGISTRATION_SCOPE_DEFECT", "REGISTRATION_SCOPE_DEFECT"),
        ("EQUIPMENT_CONDITION", "EQUIPMENT_CONDITION"),
        ("OBSERVATION_RECORD", "OBSERVATION"),
        ("CURRENT_CONDITION", "CURRENT_CONDITION"),
        ("ACTIVITY_RECORD", "ACTIVITY_RECORD"),
        ("PROCEDURE_STATEMENT", "PROCEDURE_STATEMENT"),
        ("PLAN_STATEMENT", "PLAN_STATEMENT"),
        ("RECORD_EXISTS", "RECORD_REFERENCE"),
    )
    for nature, event in mapping:
        if nature in natures:
            return event
    return "SOURCE_CLAIM"


def _assertion_mode(typed: dict) -> str:
    a = typed.get("assertion_mode", {})
    speech = a.get("speech_act")
    modality = a.get("modality")
    if speech == "SOURCE_EVALUATION":
        return "SOURCE_EVALUATION"
    if speech == "OBSERVATION":
        return "OBSERVATION_NOTE"
    if speech == "RECORD_ENTRY":
        return "ACTIVITY_RECORD"
    if modality == "ACTUAL":
        return "SOURCE_DECLARATION"
    if speech == "PROCEDURE" or modality in {
        "REQUIRED", "PLANNED", "CONDITIONAL", "PERMITTED", "MIXED"
    }:
        return "SOURCE_DECLARATION"
    return "UNKNOWN"


def _stable_id(evidence_id, start, end, quote, event_type, polarity, modality, speech):
    payload = "\x1f".join(map(str, (
        evidence_id, start, end, quote, event_type, polarity, modality, speech
    )))
    return "fc-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _one(evidence_id: str, parent_text: str, start: int, end: int, quote: str) -> dict:
    typed = classify_evidence_nature(quote)
    polarity = _polarity(quote, typed)
    assertion = typed.get("assertion_mode", {})
    event_type = _event_type(typed, polarity)
    fid = _stable_id(
        evidence_id, start, end, quote, event_type, polarity,
        assertion.get("modality", "UNKNOWN"), assertion.get("speech_act", "UNKNOWN")
    )
    return {
        "fact_candidate_id": fid,
        "parent_evidence_id": evidence_id,
        "source_id": evidence_id.split(":", 1)[0],
        "atom_id": evidence_id,
        "fact_type": event_type,
        "event_type": event_type,
        "quote": quote,
        "quote_start": start,
        "quote_end": end,
        "polarity": polarity,
        "modality": assertion.get("modality", "UNKNOWN"),
        "speech_act": assertion.get("speech_act", "UNKNOWN"),
        "assertion_mode": _assertion_mode(typed),
        "evidence_nature": typed,
        "extraction_methods": ["RULE"],
        "status": "EXTRACTION_CONFLICT" if polarity == MIXED else "SPAN_VALIDATED",
        "grounding_valid": 0 <= start <= end <= len(parent_text) and parent_text[start:end] == quote,
    }



def build_fact_candidates(evidence_id: str, text: str) -> list[dict]:
    """Build grounded facts; split any semantically mixed source atom."""
    text = str(text or "")

    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())

    if end <= start:
        return []

    full_typed = classify_evidence_nature(text)
    full_fact = _one(
        evidence_id,
        text,
        start,
        end,
        text[start:end],
    )

    full_assertion = full_typed.get("assertion_mode", {})

    needs_split = bool(
        full_typed.get("requires_subspan_fact_split", False)
        or full_fact.get("polarity") == MIXED
        or full_fact.get("modality") == "MIXED"
        or full_fact.get("speech_act") == "MIXED"
        or full_assertion.get("modality") == "MIXED"
        or full_assertion.get("speech_act") == "MIXED"
    )

    if not needs_split:
        return [full_fact] if full_fact["grounding_valid"] else []

    pieces = []

    for seg_start, seg_end, seg_quote in _segments(text):
        seg_typed = classify_evidence_nature(seg_quote)
        seg_fact = _one(
            evidence_id,
            text,
            seg_start,
            seg_end,
            seg_quote,
        )
        seg_assertion = seg_typed.get("assertion_mode", {})

        segment_needs_split = bool(
            seg_typed.get("requires_subspan_fact_split", False)
            or seg_fact.get("polarity") == MIXED
            or seg_fact.get("modality") == "MIXED"
            or seg_fact.get("speech_act") == "MIXED"
            or seg_assertion.get("modality") == "MIXED"
            or seg_assertion.get("speech_act") == "MIXED"
        )

        if segment_needs_split and ";" in seg_quote:
            pieces.extend(
                _semicolon_segments(
                    text,
                    seg_start,
                    seg_end,
                )
            )
        else:
            pieces.append(
                (
                    seg_start,
                    seg_end,
                    seg_quote,
                )
            )

    facts = [
        _one(
            evidence_id,
            text,
            seg_start,
            seg_end,
            seg_quote,
        )
        for seg_start, seg_end, seg_quote in pieces
        if seg_quote.strip()
    ]

    return [
        fact
        for fact in facts
        if fact["grounding_valid"]
    ]

