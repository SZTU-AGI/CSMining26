#!/usr/bin/env python3
from __future__ import annotations

"""FRECA V6.5 whole-CP applicability / N/A evaluator.

N/A is fail-closed.  This module never infers non-applicability from missing
support, an empty retrieval result, a filename, or model uncertainty.

A real case can become NOT_APPLICABLE only when:
  1) the checking-point requirement is detected generically as a condition that
     controls the *whole CP* (not merely a sub-clause);
  2) grounded case evidence positively states that the controlling trigger is
     false / out of scope; and
  3) an independent countercheck finds no decisive evidence that the trigger
     actually stands.

No CP-number map or handwritten per-CP N/A table is used.
"""

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import freca_core_v1 as core

SCHEMA = "freca-v6.5-applicability-evaluation-v1"
VERSION = "V6.5_FINAL_NA_1"

WHOLE_PREFIX = re.compile(
    r"^\s*(where\s+applicable|where\s+required|if\s+applicable|if\s+required|when\s+applicable)\s*[,;:]\s*(.+)$",
    re.I | re.S,
)
PREFIX_IF = re.compile(r"^\s*if\s+(.{2,260}?)\s*[,;:]\s*(.+)$", re.I | re.S)
EMBEDDED_IF = re.compile(
    r"^\s*(.{2,260}?)\s*,\s*if\s+(.{2,220}?)\s*,\s*(is|are|must|shall|should|will|may)\b(.+)$",
    re.I | re.S,
)
ANY_CUE = re.compile(
    r"\b(where\s+applicable|where\s+required|if\s+applicable|if\s+required|when\s+applicable|if\s+[^,;]{2,180})\b",
    re.I,
)

STOP = {
    "that","this","with","from","into","must","shall","should","where","when","which","their",
    "there","these","those","have","has","been","being","were","are","was","and","the","for",
    "plants","plant","products","product","establishment","export","operations","operation","appropriate",
    "applicable","required","requirement","requirements","conducted","carried","performed","undertaken",
    "purpose","good","working","order","risk","manage","managed","control","controlled","current",
}
NEG_CUES = re.compile(
    r"\b(not\s+applicable|does\s+not|do\s+not|is\s+not|are\s+not|not\s+used|not\s+installed|"
    r"not\s+carried\s+out|not\s+performed|not\s+undertaken|not\s+conducted|not\s+required|"
    r"no\s+[a-z][a-z0-9 -]{2,80}|none\b|outside\s+(?:the\s+)?scope|excluded\s+from)\b",
    re.I,
)
POS_CUES = re.compile(
    r"\b(installed|in\s+use|used|carried\s+out|performed|undertaken|conducted|operates?|"
    r"present|active|serviceable|operational|required|provided|exists?|existing)\b",
    re.I,
)


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _tokens(text: str) -> list[str]:
    out = []
    for token in re.findall(r"[a-z][a-z0-9-]{3,}", text.lower()):
        if token not in STOP and token not in out:
            out.append(token)
    return out


def _subject_from_body(body: str) -> str:
    text = _norm(body)
    m = re.match(r"(.{2,240}?)\s+(?:is|are|must|shall|should|has|have|will|may)\b", text, re.I)
    return _norm(m.group(1) if m else text[:220])


def _detect_one(text: str) -> dict[str, Any]:
    text = _norm(text)
    if not text:
        return {"kind": "NONE", "trigger": None, "source_text": text}
    m = WHOLE_PREFIX.match(text)
    if m:
        cue = _norm(m.group(1)).lower()
        body = _norm(m.group(2))
        return {
            "kind": "WHOLE",
            "cue": cue,
            "trigger": _subject_from_body(body),
            "source_text": text,
        }
    m = PREFIX_IF.match(text)
    if m:
        cond = _norm(m.group(1))
        # Avoid treating a trailing/subordinate if-clause as a prefix trigger.
        return {"kind": "WHOLE", "cue": "if", "trigger": cond, "source_text": text}
    m = EMBEDDED_IF.match(text)
    if m:
        subject = _norm(m.group(1))
        cond = _norm(m.group(2))
        return {
            "kind": "WHOLE",
            "cue": "embedded-if",
            "trigger": _norm(subject + " " + cond),
            "source_text": text,
        }
    if ANY_CUE.search(text):
        return {"kind": "SUBCLAUSE", "trigger": None, "source_text": text}
    return {"kind": "NONE", "trigger": None, "source_text": text}


def detect_scope(requirement_result: dict) -> dict[str, Any]:
    reqs = list((requirement_result.get("evidence_requirement_plan") or {}).get("requirements") or [])
    decisive = [r for r in reqs if str(r.get("decisiveness") or "").upper() == "DECISIVE"] or reqs
    per = []
    for r in decisive:
        rid = str(r.get("requirement_id") or "")
        candidates = []
        # criterion text can preserve a leading "Where applicable" that the
        # normalized proposition deliberately omits, so inspect both.
        for source_name in ("criterion_quote", "proposition_to_establish"):
            text = _norm(r.get(source_name))
            if text:
                d = _detect_one(text)
                d["requirement_id"] = rid
                d["source_field"] = source_name
                candidates.append(d)
        whole = next((x for x in candidates if x["kind"] == "WHOLE"), None)
        sub = next((x for x in candidates if x["kind"] == "SUBCLAUSE"), None)
        per.append(whole or sub or {"kind": "NONE", "requirement_id": rid, "trigger": None})

    if not per:
        return {"scope": "NON_CONDITIONAL", "trigger_text": None, "requirements": []}
    if all(x.get("kind") == "WHOLE" for x in per):
        triggers = [_norm(x.get("trigger")) for x in per if _norm(x.get("trigger"))]
        trigger = "; ".join(dict.fromkeys(triggers))[:900] if triggers else None
        if not trigger:
            return {"scope": "CONDITIONAL_TRIGGER_UNRESOLVED", "trigger_text": None, "requirements": per}
        return {"scope": "WHOLE_CP_CONDITIONAL", "trigger_text": trigger, "requirements": per}
    if any(x.get("kind") in {"WHOLE", "SUBCLAUSE"} for x in per):
        return {"scope": "SUBCLAUSE_OR_PARTIAL_CONDITIONAL", "trigger_text": None, "requirements": per}
    return {"scope": "NON_CONDITIONAL", "trigger_text": None, "requirements": per}


def _candidate_chunks(chunks: list[dict], trigger: str, requirement_result: dict, *, limit: int = 40) -> list[dict]:
    reqs = list((requirement_result.get("evidence_requirement_plan") or {}).get("requirements") or [])
    context = " ".join(
        [_norm(trigger)]
        + [_norm(r.get("proposition_to_establish")) for r in reqs]
        + [_norm(r.get("criterion_quote")) for r in reqs]
    )
    terms = _tokens(context)
    anchor_terms = _tokens(trigger)[:16] or terms[:16]
    scored = []
    for ch in chunks:
        text = _norm(ch.get("text"))
        if not text:
            continue
        low = text.lower()
        anchor_hits = sum(1 for t in anchor_terms if t in low)
        term_hits = sum(1 for t in terms if t in low)
        if anchor_hits == 0 and term_hits < 2:
            continue
        score = anchor_hits * 8 + term_hits * 2
        if NEG_CUES.search(text):
            score += 12
        if POS_CUES.search(text):
            score += 5
        # Table headers and short rows are often decisive for existence/scope.
        if "|" in text:
            score += 2
        scored.append((score, str(ch.get("id") or ch.get("evidence_id") or ""), text, ch.get("kind")))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [
        {"evidence_id": eid, "text": text[:1200], "kind": kind, "score": score}
        for score, eid, text, kind in scored[:limit]
        if eid
    ]


def _structural_applicability(requirement_result: dict, scope: dict) -> dict | None:
    if scope.get("scope") != "WHOLE_CP_CONDITIONAL":
        return None
    decisive_ids = {
        str(x.get("requirement_id")) for x in scope.get("requirements", []) if x.get("requirement_id")
    }
    rows = []
    for a in requirement_result.get("alignments", []) or []:
        if not a.get("structural_witness_key"):
            continue
        if decisive_ids and str(a.get("requirement_id")) not in decisive_ids:
            continue
        if a.get("argument_truth_bearing") is not True:
            continue
        if str(a.get("relation")) not in {"SUPPORT", "ATTACK"}:
            continue
        quote = _norm(a.get("exact_quote") or (a.get("fact_candidate") or {}).get("quote"))
        if quote:
            rows.append({
                "evidence_id": str(a.get("parent_evidence_id") or a.get("evidence_id") or ""),
                "exact_quote": quote,
                "relation": "APPLICABILITY",
                "basis": "STRUCTURAL_WITNESS_PRESUPPOSES_TRIGGER",
            })
    if rows:
        return {
            "decision": "APPLICABLE",
            "method": "DETERMINISTIC_STRUCTURAL_PRESUPPOSITION",
            "applicability_evidence": rows[:8],
            "non_applicability_evidence": [],
            "countercheck": {"status": "NOT_NEEDED"},
        }
    return None


def _prompt_primary(scope: dict, candidates: list[dict], requirement_result: dict) -> tuple[str, str]:
    reqs = list((requirement_result.get("evidence_requirement_plan") or {}).get("requirements") or [])
    sys = """You are a closed-source applicability classifier for one conditional checking point.
Do NOT judge compliance, satisfaction, violation, or the final 0/1/N/A label.
Use only the supplied grounded case evidence.

Classify the condition controlling the WHOLE checking point:
- APPLICABLE: explicit case evidence positively establishes that the trigger/activity/entity exists or occurs.
- NOT_APPLICABLE: explicit case evidence positively establishes that the trigger is false, the activity is not performed, the entity is not used/present/required, or the case is explicitly outside the relevant scope.
- CONFLICTING: decisive grounded evidence supports both APPLICABLE and NOT_APPLICABLE.
- UNKNOWN: neither side is positively established.

Strict rules:
- Missing evidence is NEVER NOT_APPLICABLE.
- A filename is not evidence.
- A policy/procedure saying what should happen does not by itself prove that the activity actually happens.
- "where applicable" by itself is not evidence of non-applicability.
- Do not infer facts from neighboring cases.
- Every evidence item must cite an evidence_id from the supplied list and an exact_quote that is a literal substring of that evidence text.
Return JSON only with keys decision, applicability_evidence, non_applicability_evidence, reason."""
    req_text = "\n".join(
        f"- {r.get('requirement_id')}: criterion={_norm(r.get('criterion_quote'))!r}; proposition={_norm(r.get('proposition_to_establish'))!r}"
        for r in reqs
    )
    ev = "\n".join(f"[{c['evidence_id']}] {c['text']}" for c in candidates)
    user = f"""WHOLE-CP trigger to evaluate:
{scope.get('trigger_text')}

Requirements:
{req_text}

Grounded candidate evidence:
{ev}

Return:
{{
  "decision": "APPLICABLE|NOT_APPLICABLE|CONFLICTING|UNKNOWN",
  "applicability_evidence": [{{"evidence_id":"...","exact_quote":"..."}}],
  "non_applicability_evidence": [{{"evidence_id":"...","exact_quote":"..."}}],
  "reason": "brief"
}}"""
    return sys, user


def _prompt_countercheck(scope: dict, candidates: list[dict]) -> tuple[str, str]:
    sys = """You are an independent applicability countercheck.
A separate evaluator has found positive evidence of NON_APPLICABILITY. Your only job is to search the supplied grounded evidence for decisive contrary evidence that the whole-CP trigger actually occurs/exists/applies.
Do not judge compliance. Do not infer from filenames. Procedure text alone is not proof of occurrence.
Return FOUND only with a grounded exact quote. Return NOT_FOUND when no supplied evidence positively establishes the trigger. Return UNKNOWN if the supplied evidence is ambiguous.
JSON only."""
    ev = "\n".join(f"[{c['evidence_id']}] {c['text']}" for c in candidates)
    user = f"""Trigger:
{scope.get('trigger_text')}

Grounded candidate evidence:
{ev}

Return:
{{"status":"FOUND|NOT_FOUND|UNKNOWN","evidence":[{{"evidence_id":"...","exact_quote":"..."}}],"reason":"brief"}}"""
    return sys, user


def _index_candidates(candidates: list[dict]) -> dict[str, str]:
    return {str(x["evidence_id"]): str(x["text"]) for x in candidates}


def _validate_items(items: Any, candidates: list[dict], trigger: str) -> list[dict]:
    idx = _index_candidates(candidates)
    anchors = _tokens(trigger)[:16]
    out = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("evidence_id") or "")
        quote = _norm(item.get("exact_quote"))
        text = idx.get(eid)
        if not eid or not quote or text is None or quote not in text:
            continue
        low = text.lower()
        if anchors and not any(a in low for a in anchors):
            continue
        out.append({"evidence_id": eid, "exact_quote": quote})
    return out


def _fingerprint(scope: dict, candidates: list[dict], model: str) -> str:
    payload = json.dumps({"version": VERSION, "scope": scope, "candidates": candidates, "model": model}, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate(
    requirement_result: dict,
    chunks: list[dict],
    *,
    cache_path: Path | None = None,
    model: str | None = None,
) -> dict:
    scope = detect_scope(requirement_result)
    base = {
        "schema": SCHEMA,
        "version": VERSION,
        "scope": scope.get("scope"),
        "trigger_text": scope.get("trigger_text"),
        "scope_analysis": scope,
        "decision": "NOT_EVALUATED",
        "method": "NONE",
        "applicability_evidence": [],
        "non_applicability_evidence": [],
        "countercheck": {"status": "NOT_RUN"},
        "model_calls": 0,
    }
    if scope.get("scope") != "WHOLE_CP_CONDITIONAL":
        base["decision"] = (
            "NON_CONDITIONAL" if scope.get("scope") == "NON_CONDITIONAL"
            else "NOT_WHOLE_CP_CONDITIONAL"
        )
        base["method"] = "DETERMINISTIC_SCOPE_CLASSIFICATION"
        return base

    structural = _structural_applicability(requirement_result, scope)
    if structural:
        base.update(structural)
        return base

    candidates = _candidate_chunks(chunks, str(scope.get("trigger_text") or ""), requirement_result)
    base["candidate_count"] = len(candidates)
    base["candidate_evidence_ids"] = [x["evidence_id"] for x in candidates]
    if not candidates:
        base["decision"] = "UNKNOWN"
        base["method"] = "NO_GROUNDED_TRIGGER_CANDIDATES"
        return base

    model = model or os.environ.get("FRECA_APPLICABILITY_MODEL") or core.ALIGNMENT_MODEL
    fp = _fingerprint(scope, candidates, model)
    base["input_fingerprint"] = fp
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("input_fingerprint") == fp and cached.get("schema") == SCHEMA:
                return cached
        except Exception:
            pass

    sys, user = _prompt_primary(scope, candidates, requirement_result)
    try:
        raw = core.deepseek_json(
            model=model,
            system_prompt=sys,
            user_prompt=user,
            thinking=False,
            max_tokens=1800,
        )
        base["model_calls"] += 1
    except Exception as exc:
        base["decision"] = "UNKNOWN"
        base["method"] = "MODEL_FAILURE_FAIL_CLOSED"
        base["model_error"] = type(exc).__name__ + ": " + str(exc)
        raw = {}

    app = _validate_items(raw.get("applicability_evidence"), candidates, str(scope.get("trigger_text") or ""))
    nonapp = _validate_items(raw.get("non_applicability_evidence"), candidates, str(scope.get("trigger_text") or ""))
    decision = str(raw.get("decision") or "UNKNOWN").upper()
    if decision not in {"APPLICABLE", "NOT_APPLICABLE", "CONFLICTING", "UNKNOWN"}:
        decision = "UNKNOWN"
    # Grounding gates: the label cannot outrun its cited evidence.
    if decision == "APPLICABLE" and not app:
        decision = "UNKNOWN"
    if decision == "NOT_APPLICABLE" and not nonapp:
        decision = "UNKNOWN"
    if decision == "CONFLICTING" and not (app and nonapp):
        decision = "UNKNOWN"
    if app and nonapp:
        decision = "CONFLICTING"

    base.update({
        "decision": decision,
        "method": "MODEL_GROUNDED_APPLICABILITY",
        "applicability_evidence": app,
        "non_applicability_evidence": nonapp,
        "primary_reason": _norm(raw.get("reason"))[:1200],
        "model": model,
    })

    # Positive N/A gets a separate contrary-applicability countercheck.
    if decision == "NOT_APPLICABLE":
        csys, cuser = _prompt_countercheck(scope, candidates)
        try:
            craw = core.deepseek_json(
                model=model,
                system_prompt=csys,
                user_prompt=cuser,
                thinking=False,
                max_tokens=1000,
            )
            base["model_calls"] += 1
            status = str(craw.get("status") or "UNKNOWN").upper()
            ce = _validate_items(craw.get("evidence"), candidates, str(scope.get("trigger_text") or ""))
            if status == "FOUND" and not ce:
                status = "UNKNOWN"
            if status not in {"FOUND", "NOT_FOUND", "UNKNOWN"}:
                status = "UNKNOWN"
            base["countercheck"] = {
                "status": status,
                "applicability_counterevidence": ce,
                "reason": _norm(craw.get("reason"))[:1200],
            }
            if status == "FOUND":
                base["decision"] = "CONFLICTING"
                base["applicability_evidence"] = ce
            elif status != "NOT_FOUND":
                # Countercheck uncertainty blocks N/A rather than guessing.
                base["decision"] = "UNKNOWN"
        except Exception as exc:
            base["countercheck"] = {
                "status": "UNKNOWN",
                "error": type(exc).__name__ + ": " + str(exc),
            }
            base["decision"] = "UNKNOWN"

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return base


def apply_to_contract(contract_bundle: dict, evaluation: dict) -> dict:
    result = copy.deepcopy(contract_bundle)
    body = result.get("contract") if isinstance(result.get("contract"), dict) else result
    if evaluation.get("scope") != "WHOLE_CP_CONDITIONAL":
        return result
    decision = str(evaluation.get("decision") or "UNKNOWN")
    if decision == "APPLICABLE":
        app, nonapp = True, False
    elif decision == "NOT_APPLICABLE":
        app, nonapp = False, True
    elif decision == "CONFLICTING":
        app, nonapp = True, True
    else:
        app, nonapp = False, False
    body["applicability"] = {"op": "CONST", "value": app}
    body["non_applicability"] = {"op": "CONST", "value": nonapp}
    body["v6_5_applicability_override"] = {
        "version": VERSION,
        "decision": decision,
        "scope": evaluation.get("scope"),
        "trigger_text": evaluation.get("trigger_text"),
        "positive_na_requires_countercheck": True,
    }
    return result


def self_test() -> None:
    def rr(criterion: str, proposition: str):
        return {"evidence_requirement_plan": {"requirements": [{
            "requirement_id": "ER1", "decisiveness": "DECISIVE",
            "criterion_quote": criterion, "proposition_to_establish": proposition,
        }]}}

    # Whole CP prefix conditional.
    x = detect_scope(rr(
        "Where applicable, pest control stations and traps are fit for purpose.",
        "Pest control stations and traps are fit for purpose.",
    ))
    assert x["scope"] == "WHOLE_CP_CONDITIONAL", x

    # Embedded whole-activity conditional.
    x = detect_scope(rr(
        "manage contamination by large contaminants",
        "Screening of plants, if carried out at the establishment, is conducted appropriately.",
    ))
    assert x["scope"] == "WHOLE_CP_CONDITIONAL", x

    # Conditional phrase only modifies one conjunct; whole CP remains applicable.
    x = detect_scope(rr(
        "The establishment operates within registered operations and, where applicable, registered functions.",
        "The establishment operates within registered operations and, where applicable, registered functions.",
    ))
    assert x["scope"] == "SUBCLAUSE_OR_PARTIAL_CONDITIONAL", x

    # No conditional.
    x = detect_scope(rr("Facilities are clean.", "Facilities are clean."))
    assert x["scope"] == "NON_CONDITIONAL", x

    base = {"applicability": {"op": "CONST", "value": True}, "non_applicability": {"op": "CONST", "value": False}}
    c = apply_to_contract(base, {"scope": "WHOLE_CP_CONDITIONAL", "decision": "NOT_APPLICABLE", "trigger_text": "x"})
    assert c["applicability"]["value"] is False and c["non_applicability"]["value"] is True
    c = apply_to_contract(base, {"scope": "WHOLE_CP_CONDITIONAL", "decision": "UNKNOWN", "trigger_text": "x"})
    assert c["applicability"]["value"] is False and c["non_applicability"]["value"] is False
    print("applicability_v6_5 self-tests: PASS (6/6)")


if __name__ == "__main__":
    self_test()
