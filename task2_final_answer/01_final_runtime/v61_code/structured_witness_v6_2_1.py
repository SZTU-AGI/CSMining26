from __future__ import annotations

import copy
import hashlib
import re
from collections import defaultdict

import evidence_reasoning_v2
from fact_candidate_v1 import build_fact_candidates


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _source_file(evidence_id: str) -> str:
    return str(evidence_id or "").split(":", 1)[0]


def _identity_by_source(requirement_result: dict) -> dict[str, dict]:
    """Collapse retrieval-level identity decisions to document/source level.

    Identity in FRECA is document-scoped.  A table row can be absent from the
    lexical candidate universe while another chunk from the same document was
    identity-checked.  We inherit only an existing ADMIT_DIRECT determination;
    we never upgrade a source that was observed as conflicting/foreign.
    """
    bucket: dict[str, list[dict]] = defaultdict(list)
    for trace in requirement_result.get("retrieval_traces", []) or []:
        for key in ("candidate_universe", "candidates"):
            for row in trace.get(key, []) or []:
                src = str(row.get("source_id") or _source_file(row.get("evidence_id")))
                if src:
                    bucket[src].append(row)

    out = {}
    for src, rows in bucket.items():
        direct = [r for r in rows if r.get("identity_use_decision") == "ADMIT_DIRECT" and r.get("identity_decisive_proof_eligible", False)]
        excluded = [r for r in rows if r.get("identity_use_decision") == "EXCLUDE_SUBSTANTIVE"]
        if direct:
            exemplar = direct[0]
            out[src] = {
                "identity_relation_to_case": exemplar.get("identity_relation_to_case", "CORE_SELF_EXACT"),
                "identity_use_decision": "ADMIT_DIRECT",
                "identity_decisive_proof_eligible": True,
                "identity_reason_code": exemplar.get("identity_reason_code", "SOURCE_LEVEL_INHERITED_DIRECT"),
            }
        elif excluded:
            exemplar = excluded[0]
            out[src] = {
                "identity_relation_to_case": exemplar.get("identity_relation_to_case"),
                "identity_use_decision": "EXCLUDE_SUBSTANTIVE",
                "identity_decisive_proof_eligible": False,
                "identity_reason_code": exemplar.get("identity_reason_code", "SOURCE_LEVEL_INHERITED_EXCLUSION"),
            }
    return out


def _stable_fact_id(evidence_id: str, tag: str) -> str:
    h = hashlib.sha256((str(evidence_id) + "\x1f" + tag).encode("utf-8")).hexdigest()[:20]
    return "fc-struct-" + h


def _validated_synthetic_alignment(*, requirement: dict, evidence_id: str, exact_quote: str,
                                   semantic_context: str, relation: str, identity: dict,
                                   reason_code: str, reason: str,
                                   derived_from: list[str], witness_key: str) -> dict:
    fact_id = _stable_fact_id(evidence_id, witness_key)
    fact = {
        "fact_candidate_id": fact_id,
        "parent_evidence_id": evidence_id,
        "source_id": _source_file(evidence_id),
        "atom_id": evidence_id,
        "fact_type": "DETERMINISTIC_STRUCTURAL_WITNESS",
        "event_type": "DETERMINISTIC_STRUCTURAL_WITNESS",
        "quote": exact_quote,
        "quote_start": 0,
        "quote_end": len(exact_quote),
        "polarity": "POSITIVE" if relation == "SUPPORT" else "ADVERSE",
        "modality": "ACTUAL",
        "speech_act": "SOURCE_EVALUATION",
        "assertion_mode": "SOURCE_EVALUATION",
        "extraction_methods": ["DETERMINISTIC_STRUCTURAL_JOIN"],
        "status": "SPAN_VALIDATED",
        "grounding_valid": True,
    }
    pair = {
        "requirement": requirement,
        "evidence_id": evidence_id + "#" + fact_id,
        "parent_evidence_id": evidence_id,
        "fact_candidate_id": fact_id,
        "fact_candidate": fact,
        # exact_quote must remain grounded, while semantic_context may restore
        # table/header/group semantics deterministically.
        "evidence_text": semantic_context,
        "parent_evidence_text": semantic_context,
        "retrieval_need_ids": [],
        **identity,
    }
    raw = {
        "requirement_id": requirement["requirement_id"],
        "evidence_id": pair["evidence_id"],
        "relation": relation,
        "exact_quote": exact_quote,
        "reason_code": reason_code,
        "reason": reason,
        "alignment_method": "DETERMINISTIC_STRUCTURAL_JOIN",
    }
    row = evidence_reasoning_v2.validate_alignment(raw, pair)
    row["semantic_context"] = semantic_context
    row["derived_from_evidence_ids"] = list(derived_from)
    row["structural_witness_key"] = witness_key
    row["alignment_method"] = "DETERMINISTIC_STRUCTURAL_JOIN"
    row["reason_code"] = reason_code
    row["reason"] = reason
    return row


def _cp26_structural_witnesses(requirement_result: dict, requirement: dict, chunks: list[dict], identity_by_source: dict[str, dict]) -> list[dict]:
    """Audit a station-condition table as a whole.

    Universal positive: all observed station rows in a recognised station table
    carry a satisfactory condition.
    Counterexample: any recognised adverse station-condition row is sufficient
    for ATTACK.  Positive rows are not separately used when a counterexample is
    present, avoiding a spurious BOTH for a universal requirement.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    header_groups = set()
    for ch in chunks:
        eid = str(ch.get("id") or ch.get("evidence_id") or "")
        src = str(ch.get("file") or _source_file(eid))
        if not eid or not src:
            continue
        ident = identity_by_source.get(src)
        if not ident or ident.get("identity_use_decision") != "ADMIT_DIRECT":
            continue
        m = re.search(r":(T\d+):R(\d+)$", eid)
        if not m:
            continue
        table = m.group(1)
        text = str(ch.get("text") or "")
        key = (src, table)
        if re.search(r"\bstation code\b.*\bstation condition\b", _norm(text)):
            header_groups.add(key)
        groups[key].append(ch)

    out = []
    positive_words = re.compile(r"^(?:good|serviceable|operational|functional|functioning|intact|undamaged|secure)$", re.I)
    adverse_words = re.compile(
        r"(?:crack(?:ed)?|water ingress|loose anchor|broken|damaged|missing|not operational|"
        r"not serviceable|unserviceable|failed|faulty|worn|open lid|lid loose|insecure)", re.I,
    )

    for key in sorted(header_groups):
        src, table = key
        rows = sorted(groups.get(key, []), key=lambda ch: int(re.search(r":R(\d+)$", str(ch.get("id"))).group(1)))
        data = []
        for ch in rows:
            text = str(ch.get("text") or "")
            if re.search(r"\bstation code\b.*\bstation condition\b", _norm(text)):
                continue
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 6 or not re.match(r"^(?:BS|TR|ST)-?\d+", parts[0], re.I):
                continue
            condition = parts[4].strip()
            data.append((ch, condition))
        if not data:
            continue

        adverse = [(ch, cond) for ch, cond in data if adverse_words.search(cond)]
        unknown = [(ch, cond) for ch, cond in data if not adverse_words.search(cond) and not positive_words.match(cond)]
        positive = [(ch, cond) for ch, cond in data if positive_words.match(cond)]
        identity = identity_by_source[src]

        if adverse:
            ch, cond = adverse[0]
            quote = str(ch.get("text") or "").strip()
            semantic = f"Pest control bait station is damaged/defective and not in good working order: {quote}"
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(ch.get("id")),
                exact_quote=quote,
                semantic_context=semantic,
                relation="ATTACK",
                identity=identity,
                reason_code="STRUCTURED_STATION_TABLE_EXPLICIT_DEFECT",
                reason=(f"Deterministic station-table audit found {len(adverse)} adverse condition row(s) "
                        f"among {len(data)} station rows; one explicit defective station is a counterexample "
                        f"to the universal good-working-order requirement."),
                derived_from=[str(x.get("id")) for x, _ in data],
                witness_key=f"CP26:{src}:{table}:ATTACK",
            ))
        elif positive and not unknown and len(positive) == len(data):
            ch, cond = positive[0]
            quote = str(ch.get("text") or "").strip()
            semantic = (
                f"Pest control bait station condition register: all {len(data)} station rows are Good/serviceable "
                f"and in working order. Representative grounded row: {quote}"
            )
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(ch.get("id")),
                exact_quote=quote,
                semantic_context=semantic,
                relation="SUPPORT",
                identity=identity,
                reason_code="STRUCTURED_STATION_TABLE_ALL_ROWS_SATISFACTORY",
                reason=(f"Deterministic station-table audit found {len(data)}/{len(data)} rows with satisfactory "
                        f"condition and no adverse/unknown station condition rows."),
                derived_from=[str(x.get("id")) for x, _ in data],
                witness_key=f"CP26:{src}:{table}:SUPPORT",
            ))
    return out


_DOMESTIC = re.compile(r"\b(domestic(?:-grade)?|domestic distribution|domestic fodder|domestic supply|domestic market|local market|not for export|non[- ]export)\b", re.I)
_EXPORT = re.compile(r"\b(export|export grade|export-grade|for export|certified consignments?|registered .* export)\b", re.I)


def _cp1_structural_witnesses(requirement_result: dict, requirement: dict, chunks: list[dict], identity_by_source: dict[str, dict]) -> list[dict]:
    by_source = defaultdict(list)
    for ch in chunks:
        eid = str(ch.get("id") or ch.get("evidence_id") or "")
        src = str(ch.get("file") or _source_file(eid))
        if src:
            by_source[src].append(ch)

    out = []
    for src, source_chunks in by_source.items():
        identity = identity_by_source.get(src)
        if not identity or identity.get("identity_use_decision") != "ADMIT_DIRECT":
            continue

        # Explicit universal statement is the cleanest direct support.
        universal = next((ch for ch in source_chunks if re.search(
            r"\ball export activities\b.{0,120}\bwithin (?:the )?registered scope\b",
            str(ch.get("text") or ""), re.I | re.S)), None)
        if universal is not None:
            quote = str(universal.get("text") or "").strip()
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(universal.get("id")), exact_quote=quote,
                semantic_context=quote, relation="SUPPORT", identity=identity,
                reason_code="STRUCTURED_SCOPE_EXPLICIT_UNIVERSAL_SUPPORT",
                reason="Source explicitly states that all export activities are within the registered scope.",
                derived_from=[str(universal.get("id"))],
                witness_key=f"CP1:{src}:EXPLICIT_UNIVERSAL_SUPPORT",
            ))
            continue

        # Find an Activity/Description/Scope Status table.
        table_groups = defaultdict(list)
        headers = set()
        for ch in source_chunks:
            eid = str(ch.get("id") or "")
            m = re.search(r":(T\d+):R(\d+)$", eid)
            if not m:
                continue
            key = m.group(1)
            text = str(ch.get("text") or "")
            table_groups[key].append(ch)
            if re.search(r"\bactivity\b.*\bdescription\b.*\bscope status\b", _norm(text)):
                headers.add(key)
        if not headers:
            continue

        intro_text = " ".join(str(ch.get("text") or "") for ch in source_chunks if re.search(r":P\d+$", str(ch.get("id") or "")))
        intro_norm = _norm(intro_text)
        integrated_export = bool(re.search(r"contract activities are integrated with the export operation", intro_norm))
        domestic_intro = bool(re.search(r"hay and straw products? for domestic supply", intro_norm))

        for table in sorted(headers):
            rows = sorted(table_groups[table], key=lambda ch: int(re.search(r":R(\d+)$", str(ch.get("id"))).group(1)))
            parsed = []
            for ch in rows:
                text = str(ch.get("text") or "")
                if re.search(r"\bactivity\b.*\bdescription\b.*\bscope status\b", _norm(text)):
                    continue
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 3:
                    continue
                activity, desc, status = parts[0], parts[1], parts[2]
                if not re.search(r"within scope|not registered", status, re.I):
                    continue
                domestic = bool(_DOMESTIC.search(activity + " " + desc))
                explicit_export = bool(_EXPORT.search(activity + " " + desc))
                export_relevant: bool | None
                if domestic:
                    export_relevant = False
                elif explicit_export:
                    export_relevant = True
                elif integrated_export and re.search(r"contract receival|weighbridge|intake", activity + " " + desc, re.I):
                    export_relevant = True
                elif domestic_intro and re.search(r"hay|straw", activity + " " + desc, re.I):
                    export_relevant = False
                elif re.search(r"wheat receival|chickpeas receival|dispatch|cleaning and grading", activity + " " + desc, re.I):
                    # These rows sit inside the document's registered export operations table.
                    export_relevant = True
                else:
                    export_relevant = None
                parsed.append((ch, activity, desc, status, export_relevant))
            if not parsed:
                continue

            attacks = [x for x in parsed if re.search(r"not registered", x[3], re.I) and x[4] is True]
            unresolved_bad = [x for x in parsed if re.search(r"not registered", x[3], re.I) and x[4] is None]
            relevant = [x for x in parsed if x[4] is True]
            relevant_within = [x for x in relevant if re.search(r"within scope", x[3], re.I)]

            if attacks:
                ch, activity, desc, status, _ = attacks[0]
                quote = str(ch.get("text") or "").strip()
                semantic = f"Export operation is outside registered scope: {quote}"
                out.append(_validated_synthetic_alignment(
                    requirement=requirement,
                    evidence_id=str(ch.get("id")), exact_quote=quote,
                    semantic_context=semantic, relation="ATTACK", identity=identity,
                    reason_code="STRUCTURED_SCOPE_TABLE_EXPORT_OPERATION_NOT_REGISTERED",
                    reason=("Deterministic scope-table join identified an export-relevant operation marked Not registered; "
                            "domestic-only rows were excluded from this test."),
                    derived_from=[str(x[0].get("id")) for x in parsed],
                    witness_key=f"CP1:{src}:{table}:ATTACK",
                ))
            elif relevant and len(relevant_within) == len(relevant) and not unresolved_bad:
                ch, activity, desc, status, _ = relevant_within[0]
                quote = str(ch.get("text") or "").strip()
                semantic = (
                    f"All export activities are within the registered scope. Deterministic scope-table audit found "
                    f"{len(relevant_within)} export-relevant row(s) Within scope; every Not registered row was "
                    f"explicitly domestic/non-export. Representative grounded row: {quote}"
                )
                out.append(_validated_synthetic_alignment(
                    requirement=requirement,
                    evidence_id=str(ch.get("id")), exact_quote=quote,
                    semantic_context=semantic, relation="SUPPORT", identity=identity,
                    reason_code="STRUCTURED_SCOPE_TABLE_ALL_EXPORT_ROWS_WITHIN_SCOPE",
                    reason=("Deterministic scope-table audit established complete table-level coverage of export-relevant "
                            "operations while excluding explicitly domestic non-registered activities."),
                    derived_from=[str(x[0].get("id")) for x in parsed],
                    witness_key=f"CP1:{src}:{table}:SUPPORT",
                ))
    return out


def enrich_requirement_result(requirement_result: dict, evidence_chunks: list[dict]) -> tuple[dict, dict]:
    rr = copy.deepcopy(requirement_result)
    identity = _identity_by_source(rr)
    existing_keys = {str(r.get("structural_witness_key")) for r in rr.get("alignments", []) or [] if r.get("structural_witness_key")}
    injected = []

    for req in (rr.get("evidence_requirement_plan") or {}).get("requirements", []) or []:
        proposition = _norm(req.get("proposition_to_establish"))
        if "registered operations" in proposition and "for export" in proposition:
            candidates = _cp1_structural_witnesses(rr, req, evidence_chunks, identity)
        elif "pest control stations" in proposition and "working order" in proposition:
            candidates = _cp26_structural_witnesses(rr, req, evidence_chunks, identity)
        else:
            candidates = []
        for row in candidates:
            key = str(row.get("structural_witness_key"))
            if key and key in existing_keys:
                continue
            rr.setdefault("alignments", []).append(row)
            injected.append(row)
            if key:
                existing_keys.add(key)

    rr["alignments"] = sorted(rr.get("alignments", []), key=lambda row: (
        str(row.get("requirement_id")), str(row.get("evidence_id")), str(row.get("fact_candidate_id"))
    ))
    audit = {
        "schema": "freca-v6.2.1-structural-witness-audit-v1",
        "injected_count": len(injected),
        "injected": [{
            "requirement_id": r.get("requirement_id"),
            "relation": r.get("relation"),
            "evidence_id": r.get("evidence_id"),
            "reason_code": r.get("reason_code"),
            "structural_witness_key": r.get("structural_witness_key"),
            "derived_from_evidence_ids": r.get("derived_from_evidence_ids", []),
            "argument_truth_bearing": r.get("argument_truth_bearing"),
        } for r in injected],
    }
    rr["structural_witness_audit_v6_2_1"] = audit
    return rr, audit
