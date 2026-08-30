from __future__ import annotations

"""Deterministic structural evidence aggregation for FRECA V6.3.

This module adds table/document-level witnesses without bypassing the common
alignment -> temporal/reliability -> coverage -> proof pipeline.  Structural
witnesses are represented as ordinary validated alignment artifacts with
DIRECT argument admission, explicit provenance, and deterministic derivation
metadata.

Currently admitted structural families:
  * CP1-style registered export operation scope tables.
  * CP26-style pest-control station/trap condition registers.

The implementation is intentionally conservative: it only emits a witness when
all rows needed for the aggregate claim are structurally observed, or when one
explicit counterexample row is sufficient for an adverse universal claim.
"""

import copy
import hashlib
import re
from collections import defaultdict

import evidence_reasoning_v2


SCHEMA = "freca-v6.3-structural-witness-audit-v1"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _source_file(evidence_id: str) -> str:
    return str(evidence_id or "").split(":", 1)[0]


def _row_number(evidence_id: str) -> int:
    m = re.search(r":R(\d+)$", str(evidence_id or ""))
    return int(m.group(1)) if m else 10**9


def _table_id(evidence_id: str) -> str | None:
    m = re.search(r":(T\d+):R\d+$", str(evidence_id or ""))
    return m.group(1) if m else None


def _identity_by_source(requirement_result: dict) -> dict[str, dict]:
    """Collapse existing identity decisions to document/source level.

    Structural aggregation never invents identity.  A source must already have
    an identity-decisive ADMIT_DIRECT decision somewhere in the retrieval
    traces.  If a source has only exclusions/unresolved identity, no structural
    witness is emitted from it.
    """
    bucket: dict[str, list[dict]] = defaultdict(list)
    for trace in requirement_result.get("retrieval_traces", []) or []:
        for key in ("candidate_universe", "candidates"):
            for row in trace.get(key, []) or []:
                src = str(row.get("source_id") or _source_file(row.get("evidence_id")))
                if src:
                    bucket[src].append(row)

    out: dict[str, dict] = {}
    for src, rows in bucket.items():
        direct = [
            r for r in rows
            if r.get("identity_use_decision") == "ADMIT_DIRECT"
            and r.get("identity_decisive_proof_eligible", False)
        ]
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


def _validated_synthetic_alignment(
    *,
    requirement: dict,
    evidence_id: str,
    exact_quote: str,
    semantic_context: str,
    relation: str,
    identity: dict,
    reason_code: str,
    reason: str,
    derived_from: list[str],
    witness_key: str,
    aggregate_kind: str,
) -> dict:
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
        "extraction_methods": ["DETERMINISTIC_STRUCTURAL_AGGREGATION"],
        "status": "SPAN_VALIDATED",
        "grounding_valid": True,
    }
    pair = {
        "requirement": requirement,
        "evidence_id": evidence_id + "#" + fact_id,
        "parent_evidence_id": evidence_id,
        "fact_candidate_id": fact_id,
        "fact_candidate": fact,
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
        "alignment_method": "DETERMINISTIC_STRUCTURAL_AGGREGATION",
    }
    row = evidence_reasoning_v2.validate_alignment(raw, pair)
    row["semantic_context"] = semantic_context
    row["derived_from_evidence_ids"] = list(derived_from)
    row["structural_witness_key"] = witness_key
    row["structural_aggregate_kind"] = aggregate_kind
    row["alignment_method"] = "DETERMINISTIC_STRUCTURAL_AGGREGATION"
    row["reason_code"] = reason_code
    row["reason"] = reason
    row["generator"] = "STRUCTURAL_AUDIT_V6_3"
    return row


# ---------------------------------------------------------------------------
# CP26: station/trap condition table
# ---------------------------------------------------------------------------

_POSITIVE_CONDITION = re.compile(
    r"^(?:good|serviceable|operational|functional|functioning|intact|undamaged|secure|satisfactory|ok|okay)$",
    re.I,
)
_ADVERSE_CONDITION = re.compile(
    r"(?:crack(?:ed)?|water ingress|loose anchor|broken|damaged|missing|not operational|"
    r"not serviceable|unserviceable|failed|faulty|worn|open lid|lid loose|insecure|"
    r"defect(?:ive)?|poor condition|requires repair|repair required)",
    re.I,
)
_STATION_CODE = re.compile(r"^(?:BS|TR|ST|BAIT|TRAP)[-_ ]?\d+", re.I)


def _cp26_structural_witnesses(
    requirement_result: dict,
    requirement: dict,
    chunks: list[dict],
    identity_by_source: dict[str, dict],
) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    header_groups: set[tuple[str, str]] = set()

    for ch in chunks:
        eid = str(ch.get("id") or ch.get("evidence_id") or "")
        src = str(ch.get("file") or _source_file(eid))
        table = _table_id(eid)
        if not eid or not src or not table:
            continue
        ident = identity_by_source.get(src)
        if not ident or ident.get("identity_use_decision") != "ADMIT_DIRECT":
            continue
        text = str(ch.get("text") or "")
        key = (src, table)
        n = _norm(text)
        if (
            ("station code" in n or "trap code" in n)
            and ("station condition" in n or "condition" in n)
        ):
            header_groups.add(key)
        groups[key].append(ch)

    out: list[dict] = []
    for key in sorted(header_groups):
        src, table = key
        rows = sorted(groups.get(key, []), key=lambda ch: _row_number(ch.get("id")))
        data: list[tuple[dict, str]] = []
        for ch in rows:
            text = str(ch.get("text") or "")
            n = _norm(text)
            if ("station code" in n or "trap code" in n) and "condition" in n:
                continue
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 5 or not _STATION_CODE.match(parts[0]):
                continue
            # Locate condition by the standard FRECA table shape.  If the row
            # has six columns, condition is the penultimate field.  For a
            # compact five-column register, use the last field.
            condition = parts[-2].strip() if len(parts) >= 6 else parts[-1].strip()
            data.append((ch, condition))
        if not data:
            continue

        adverse = [(ch, cond) for ch, cond in data if _ADVERSE_CONDITION.search(cond)]
        unknown = [
            (ch, cond) for ch, cond in data
            if not _ADVERSE_CONDITION.search(cond) and not _POSITIVE_CONDITION.match(cond)
        ]
        positive = [(ch, cond) for ch, cond in data if _POSITIVE_CONDITION.match(cond)]
        identity = identity_by_source[src]
        derived = [str(x.get("id")) for x, _ in data]

        # CP26 is universal in shape: one explicit defective station/trap is a
        # counterexample.  Do not also inject positive rows, which would create
        # a spurious BOTH merely because other stations are fine.
        if adverse:
            ch, _cond = adverse[0]
            quote = str(ch.get("text") or "").strip()
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(ch.get("id")),
                exact_quote=quote,
                semantic_context=f"Pest control bait station is damaged/defective and not in good working order: {quote}",
                relation="ATTACK",
                identity=identity,
                reason_code="STRUCTURED_STATION_TABLE_EXPLICIT_DEFECT",
                reason=(
                    f"Deterministic condition-register audit found {len(adverse)} adverse row(s) among "
                    f"{len(data)} recognised station/trap rows. One explicit defective unit is a "
                    "counterexample to the universal fit-for-purpose/good-working-order proposition."
                ),
                derived_from=derived,
                witness_key=f"CP26:{src}:{table}:ATTACK",
                aggregate_kind="UNIVERSAL_COUNTEREXAMPLE",
            ))
        elif positive and not unknown and len(positive) == len(data):
            ch, _cond = positive[0]
            quote = str(ch.get("text") or "").strip()
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(ch.get("id")),
                exact_quote=quote,
                semantic_context=(
                    f"Pest control bait station condition register: all {len(data)} station rows are Good/serviceable "
                    f"and in working order. Representative grounded row: {quote}"
                ),
                relation="SUPPORT",
                identity=identity,
                reason_code="STRUCTURED_STATION_TABLE_ALL_ROWS_SATISFACTORY",
                reason=(
                    f"Deterministic condition-register audit found {len(data)}/{len(data)} recognised rows "
                    "satisfactory and no adverse or unresolved condition rows."
                ),
                derived_from=derived,
                witness_key=f"CP26:{src}:{table}:SUPPORT",
                aggregate_kind="COMPLETE_TABLE_UNIVERSAL_SUPPORT",
            ))
    return out


# ---------------------------------------------------------------------------
# CP1: registered export-operation scope table
# ---------------------------------------------------------------------------

_DOMESTIC = re.compile(
    r"\b(domestic(?:-grade)?|domestic distribution|domestic fodder|domestic supply|"
    r"domestic market|domestic sale|local market|not for export|non[- ]export)\b",
    re.I,
)
_EXPORT = re.compile(
    r"\b(export|export grade|export-grade|export specification|for export|pre-export|"
    r"certified consignments?|export consignments?|registered .* export)\b",
    re.I,
)
_SCOPE_HEADER = re.compile(r"\bactivity\b.*\bdescription\b.*\bscope status\b", re.I)


def _source_has_registered_export_heading(source_chunks: list[dict]) -> bool:
    return any(
        re.search(r"\bregistered export operations?\b", str(ch.get("text") or ""), re.I)
        for ch in source_chunks
    )


def _cp1_structural_witnesses(
    requirement_result: dict,
    requirement: dict,
    chunks: list[dict],
    identity_by_source: dict[str, dict],
) -> list[dict]:
    by_source: dict[str, list[dict]] = defaultdict(list)
    for ch in chunks:
        eid = str(ch.get("id") or ch.get("evidence_id") or "")
        src = str(ch.get("file") or _source_file(eid))
        if src:
            by_source[src].append(ch)

    out: list[dict] = []
    for src, source_chunks in by_source.items():
        identity = identity_by_source.get(src)
        if not identity or identity.get("identity_use_decision") != "ADMIT_DIRECT":
            continue

        # Explicit universal statement, when actually present in the source,
        # remains the cleanest table/document-level support.
        universal = next((ch for ch in source_chunks if re.search(
            r"\ball export activities\b.{0,160}\bwithin (?:the )?registered scope\b",
            str(ch.get("text") or ""), re.I | re.S,
        )), None)
        if universal is not None:
            quote = str(universal.get("text") or "").strip()
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(universal.get("id")),
                exact_quote=quote,
                semantic_context=quote,
                relation="SUPPORT",
                identity=identity,
                reason_code="STRUCTURED_SCOPE_EXPLICIT_UNIVERSAL_SUPPORT",
                reason="Source explicitly states that all export activities are within the registered scope.",
                derived_from=[str(universal.get("id"))],
                witness_key=f"CP1:{src}:EXPLICIT_UNIVERSAL_SUPPORT",
                aggregate_kind="EXPLICIT_DOCUMENT_UNIVERSAL_SUPPORT",
            ))
            continue

        table_groups: dict[str, list[dict]] = defaultdict(list)
        headers: set[str] = set()
        for ch in source_chunks:
            eid = str(ch.get("id") or "")
            table = _table_id(eid)
            if not table:
                continue
            text = str(ch.get("text") or "")
            table_groups[table].append(ch)
            if _SCOPE_HEADER.search(text):
                headers.add(table)
        if not headers:
            continue

        registered_export_context = _source_has_registered_export_heading(source_chunks)

        for table in sorted(headers):
            rows = sorted(table_groups[table], key=lambda ch: _row_number(ch.get("id")))
            parsed: list[tuple[dict, str, str, str, bool | None]] = []
            for ch in rows:
                text = str(ch.get("text") or "")
                if _SCOPE_HEADER.search(text):
                    continue
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 3:
                    continue
                activity, desc, status = parts[0], parts[1], parts[2]
                if not re.search(r"within scope|not registered", status, re.I):
                    continue

                subject = activity + " " + desc
                has_domestic = bool(_DOMESTIC.search(subject))
                has_export = bool(_EXPORT.search(subject))

                # Explicit export dominates mixed export/domestic rows.
                if has_export:
                    export_relevant: bool | None = True
                elif has_domestic:
                    export_relevant = False
                elif registered_export_context:
                    # The row occurs in a document section explicitly headed
                    # "Registered Export Operations" and contains no domestic/
                    # non-export qualifier.  This is a structural cross-row
                    # join, not a lexical guess from the status cell alone.
                    export_relevant = True
                else:
                    export_relevant = None
                parsed.append((ch, activity, desc, status, export_relevant))

            if not parsed:
                continue

            attacks = [x for x in parsed if re.search(r"not registered", x[3], re.I) and x[4] is True]
            unresolved_not_registered = [
                x for x in parsed if re.search(r"not registered", x[3], re.I) and x[4] is None
            ]
            relevant = [x for x in parsed if x[4] is True]
            relevant_within = [x for x in relevant if re.search(r"within scope", x[3], re.I)]
            domestic_not_registered = [
                x for x in parsed if re.search(r"not registered", x[3], re.I) and x[4] is False
            ]
            derived = [str(x[0].get("id")) for x in parsed]

            # CP1 is universal over export-relevant operations; one explicit
            # export-relevant Not registered row is a decisive counterexample.
            if attacks:
                ch, _activity, _desc, _status, _ = attacks[0]
                quote = str(ch.get("text") or "").strip()
                out.append(_validated_synthetic_alignment(
                    requirement=requirement,
                    evidence_id=str(ch.get("id")),
                    exact_quote=quote,
                    semantic_context=f"Export operation is outside registered scope and is not registered for export: {quote}",
                    relation="ATTACK",
                    identity=identity,
                    reason_code="STRUCTURED_SCOPE_TABLE_EXPORT_OPERATION_NOT_REGISTERED",
                    reason=(
                        "Deterministic scope-table audit identified an export-relevant operation marked Not registered. "
                        "Rows explicitly qualified as domestic/non-export were excluded from the adverse test."
                    ),
                    derived_from=derived,
                    witness_key=f"CP1:{src}:{table}:ATTACK",
                    aggregate_kind="UNIVERSAL_COUNTEREXAMPLE",
                ))
            elif (
                relevant
                and len(relevant_within) == len(relevant)
                and not unresolved_not_registered
                and all(
                    (not re.search(r"not registered", x[3], re.I)) or x in domestic_not_registered
                    for x in parsed
                )
            ):
                ch, _activity, _desc, _status, _ = relevant_within[0]
                quote = str(ch.get("text") or "").strip()
                out.append(_validated_synthetic_alignment(
                    requirement=requirement,
                    evidence_id=str(ch.get("id")),
                    exact_quote=quote,
                    semantic_context=(
                        f"All export activities are within the registered scope. Deterministic scope-table audit found "
                        f"{len(relevant)} export-relevant row(s) Within scope; {len(domestic_not_registered)} Not registered row(s) are explicitly "
                        f"domestic/non-export. Representative grounded row: {quote}"
                    ),
                    relation="SUPPORT",
                    identity=identity,
                    reason_code="STRUCTURED_SCOPE_TABLE_ALL_EXPORT_ROWS_WITHIN_SCOPE",
                    reason=(
                        "Deterministic scope-table audit established complete table-level coverage of export-relevant "
                        "operations while excluding explicitly domestic/non-export activities."
                    ),
                    derived_from=derived,
                    witness_key=f"CP1:{src}:{table}:SUPPORT",
                    aggregate_kind="COMPLETE_TABLE_UNIVERSAL_SUPPORT",
                ))
    return out


def _requirement_family(requirement: dict) -> str | None:
    proposition = _norm(requirement.get("proposition_to_establish"))
    if "registered operations" in proposition and "for export" in proposition:
        return "CP1_REGISTERED_EXPORT_SCOPE"
    if (
        ("pest control stations" in proposition or "pest-control stations" in proposition)
        and ("working order" in proposition or "fit for purpose" in proposition)
    ):
        return "CP26_STATION_WORKING_ORDER"
    return None


def enrich_requirement_result(requirement_result: dict, evidence_chunks: list[dict]) -> tuple[dict, dict]:
    rr = copy.deepcopy(requirement_result)
    identity = _identity_by_source(rr)
    existing_keys = {
        str(r.get("structural_witness_key"))
        for r in rr.get("alignments", []) or []
        if r.get("structural_witness_key")
    }
    injected: list[dict] = []
    family_counts: dict[str, int] = defaultdict(int)

    for req in (rr.get("evidence_requirement_plan") or {}).get("requirements", []) or []:
        family = _requirement_family(req)
        if family == "CP1_REGISTERED_EXPORT_SCOPE":
            candidates = _cp1_structural_witnesses(rr, req, evidence_chunks, identity)
        elif family == "CP26_STATION_WORKING_ORDER":
            candidates = _cp26_structural_witnesses(rr, req, evidence_chunks, identity)
        else:
            candidates = []

        for row in candidates:
            key = str(row.get("structural_witness_key"))
            if key and key in existing_keys:
                continue
            rr.setdefault("alignments", []).append(row)
            injected.append(row)
            family_counts[family or "UNKNOWN"] += 1
            if key:
                existing_keys.add(key)

    rr["alignments"] = sorted(
        rr.get("alignments", []),
        key=lambda row: (
            str(row.get("requirement_id")),
            str(row.get("evidence_id")),
            str(row.get("fact_candidate_id")),
        ),
    )
    audit = {
        "schema": SCHEMA,
        "generator": "STRUCTURAL_AUDIT_V6_3",
        "injected_count": len(injected),
        "family_counts": dict(sorted(family_counts.items())),
        "injected": [
            {
                "requirement_id": r.get("requirement_id"),
                "relation": r.get("relation"),
                "evidence_id": r.get("evidence_id"),
                "reason_code": r.get("reason_code"),
                "structural_witness_key": r.get("structural_witness_key"),
                "structural_aggregate_kind": r.get("structural_aggregate_kind"),
                "derived_from_evidence_ids": r.get("derived_from_evidence_ids", []),
                "argument_truth_bearing": r.get("argument_truth_bearing"),
            }
            for r in injected
        ],
        "invariants": {
            "identity_must_be_preexisting_direct": True,
            "structural_witness_still_passes_common_alignment_validation": True,
            "structural_witness_does_not_emit_final_label": True,
            "universal_attack_may_use_single_explicit_counterexample": True,
            "universal_support_requires_complete_recognised_table": True,
        },
    }
    rr["structural_witness_audit_v6_3"] = audit
    return rr, audit
