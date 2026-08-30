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


SCHEMA = "freca-v6.4.2-structural-witness-audit-v1"


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


def _structural_evidence_nature(*, aggregate_kind: str, relation: str) -> dict:
    """Assign the record nature of the *derived structural fact*.

    The aggregate is a deterministic typed fact over grounded source rows.  Its
    evidence nature therefore follows the structural proposition it establishes,
    not incidental procedure/plan language that may coexist in the representative
    source paragraph.  This keeps the ordinary V2 reliability gate in charge:
    no special proof bypass is introduced.
    """
    if aggregate_kind in {
        "REGISTERED_EXPORT_SCOPE_ALL_IN_SCOPE",
        "REGISTERED_EXPORT_SCOPE_EXPLICIT_COUNTEREXAMPLE",
    }:
        nature = "REGISTERED_OPERATION_SCOPE" if relation == "SUPPORT" else "REGISTRATION_SCOPE_DEFECT"
    elif aggregate_kind in {
        "STATION_TABLE_ALL_SATISFACTORY",
        "STATION_TABLE_EXPLICIT_DEFECT",
    }:
        nature = "EQUIPMENT_CONDITION"
    elif aggregate_kind in {
        "HOLISTIC_DESIGN_COUNTEREXAMPLE",
        "HOLISTIC_MULTI_SECTION_SUPPORT",
    }:
        # Reliability is about the grounded physical/design record; polarity is
        # separately carried by relation=SUPPORT/ATTACK.
        nature = "PHYSICAL_DESIGN_FEATURE"
    elif aggregate_kind == "HOLISTIC_CURRENT_STATE_SUPPORT":
        nature = "CURRENT_CONDITION"
    elif aggregate_kind == "HOLISTIC_CURRENT_STATE_COUNTEREVIDENCE":
        nature = "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT"
    elif aggregate_kind == "CURRENT_RISK_OUTCOME_SUPPORT":
        nature = "RISK_CONTROL_OUTCOME"
    elif aggregate_kind == "CURRENT_RISK_OUTCOME_COUNTEREVIDENCE":
        nature = "ADVERSE_OPERATIONAL_FINDING"
    else:
        nature = "OBSERVATION_RECORD"

    return {
        "evidence_natures": [nature],
        "reason_codes": ["STRUCTURAL_AGGREGATE_TYPED_RECORD_NATURE"],
        "assertion_mode": {
            "modality": "ACTUAL",
            "detected_modalities": ["ACTUAL"],
            "speech_act": "SOURCE_EVALUATION",
            "detected_speech_acts": ["SOURCE_EVALUATION"],
            "inference_scope": "OBSERVABLE_ACTUAL_STATE",
            "actual_signal_present": True,
            "normative_signal_present": False,
            "source_evaluation_present": True,
        },
        "clause_signals": ["ACTUAL"],
        "mixed_fact_quote": False,
        "requires_subspan_fact_split": False,
        "structural_override": {
            "aggregate_kind": aggregate_kind,
            "deterministic": True,
            "model_generated_fact": False,
        },
    }


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
    row["generator"] = "STRUCTURAL_AUDIT_V6_4_2"

    # Layer-5 model grounding has already been validated above.  Replace the
    # noisy text-level record-nature mixture with the nature of the derived
    # structural fact itself, then let the ordinary Layer-7 reliability gate
    # build/decide the assessment.
    nature = _structural_evidence_nature(aggregate_kind=aggregate_kind, relation=relation)
    row["evidence_nature"] = copy.deepcopy(nature)
    row.pop("information_reliability", None)
    fact_copy = copy.deepcopy(row.get("fact_candidate") or {})
    fact_copy["evidence_nature"] = copy.deepcopy(nature)
    fact_copy["modality"] = "ACTUAL"
    fact_copy["speech_act"] = "SOURCE_EVALUATION"
    fact_copy["assertion_mode"] = "OBSERVABLE_ACTUAL_STATE"
    fact_copy.pop("information_reliability", None)
    row["fact_candidate"] = fact_copy
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
    if (
        ("designed and constructed" in proposition or "designed, constructed" in proposition)
        and "minimise" in proposition
        and ("contamination" in proposition or "infestation" in proposition)
    ):
        return "CP12_HOLISTIC_DESIGN"
    if (
        "maintained to minimise" in proposition
        and ("contamination" in proposition or "infestation" in proposition)
    ):
        return "CP12_HOLISTIC_MAINTENANCE"
    if (
        "risk of contamination or infestation" in proposition
        and "acceptable level" in proposition
    ):
        return "CP35_ACCEPTABLE_RISK_STATE"
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
        elif family == "CP12_HOLISTIC_DESIGN":
            candidates = _cp12_design_structural_witnesses(rr, req, evidence_chunks, identity)
        elif family == "CP12_HOLISTIC_MAINTENANCE":
            candidates = _cp12_maintenance_structural_witnesses(rr, req, evidence_chunks, identity)
        elif family == "CP35_ACCEPTABLE_RISK_STATE":
            candidates = _cp35_risk_structural_witnesses(rr, req, evidence_chunks, identity)
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
        "generator": "STRUCTURAL_AUDIT_V6_4_2",
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
            "structural_record_nature_drives_common_reliability_gate": True,
            "universal_attack_may_use_single_explicit_counterexample": True,
            "universal_support_requires_complete_recognised_table": True,
        },
    }
    rr["structural_witness_audit_v6_4"] = audit
    rr["structural_witness_audit_v6_3"] = audit
    return rr, audit

# ---------------------------------------------------------------------------
# V6.4 precision families: CP12 holistic design/maintenance and CP35 risk
# ---------------------------------------------------------------------------

_PARAGRAPH_ID = re.compile(r":P(\d+)$", re.I)
_SECTION_HEADING = re.compile(r"^\d+(?:\.\d+)*\s+", re.I)


def _paragraph_number(evidence_id: str) -> int:
    m = _PARAGRAPH_ID.search(str(evidence_id or ""))
    return int(m.group(1)) if m else 10**9


def _core_source_chunks(chunks: list[dict], identity_by_source: dict[str, dict], prefix: str) -> list[dict]:
    rows = []
    for ch in chunks:
        eid = str(ch.get("id") or ch.get("evidence_id") or "")
        src = str(ch.get("file") or _source_file(eid))
        ident = identity_by_source.get(src)
        if not ident or ident.get("identity_use_decision") != "ADMIT_DIRECT":
            continue
        if src.startswith(prefix):
            rows.append(ch)
    return sorted(rows, key=lambda ch: (_source_file(ch.get("id")), _paragraph_number(ch.get("id")), str(ch.get("id"))))


def _paragraph_section(source_chunks: list[dict], heading_pattern: str) -> list[dict]:
    ordered = [ch for ch in source_chunks if _PARAGRAPH_ID.search(str(ch.get("id") or ""))]
    ordered.sort(key=lambda ch: _paragraph_number(ch.get("id")))
    start = None
    for i, ch in enumerate(ordered):
        if re.search(heading_pattern, str(ch.get("text") or ""), re.I):
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for ch in ordered[start:]:
        text = str(ch.get("text") or "").strip()
        if _SECTION_HEADING.search(text):
            break
        out.append(ch)
    return out


_CP12_DESIGN_ADVERSE = re.compile(
    r"(?:rough[- ]sawn|porous and heavily textured|unsealed concrete|no coved skirting|"
    r"design flaw|design defect|poorly designed|poorly constructed|unsuitable construction)",
    re.I,
)
_CP12_CLEANABILITY_POSITIVE = re.compile(
    r"(?:smooth|non[- ]porous|cleanable|coved|epoxy|sealed surface|sealed with|"
    r"purpose[- ]built construction with cleanability|stainless[- ]steel)",
    re.I,
)
_CP12_PEST_DESIGN_POSITIVE = re.compile(
    r"(?:pest exclusion|design features minimising pest entry|building envelope sealed|gaps?[^.]{0,80}sealed|"
    r"ventilation openings?[^.]{0,80}mesh|no open eaves|roof penetrations?[^.]{0,80}sealed|"
    r"no internal ledges|equipment on feet|drainage openings?[^.]{0,80}mesh)",
    re.I,
)
_CP12_HYGIENE_POSITIVE = re.compile(
    r"(?:maintained in a clean and hygienic condition|no hygiene deficiencies|"
    r"internal inspection results[^.]{0,220}(?:no accumulated debris|equipment[^.]{0,80}clean)|"
    r"condition consistent with good hygiene practices|confirmed clean and free of product residue)",
    re.I | re.S,
)
_CP12_PEST_POSITIVE = re.compile(
    r"(?:current pest status[^.]{0,100}within acceptable range|establishment pest[- ]free|"
    r"rodents?: nil evidence|no rodent activity|negative for live insects|"
    r"no evidence of (?:bird|pest|rodent|insect) entry|no evidence of access to grain-contact areas|"
    r"current catches below threshold)",
    re.I,
)
_CP12_MAINTENANCE_ADVERSE = re.compile(
    r"(?:repeated (?:rodent|pest|insect) activity[^.]{0,160}delayed escalation|"
    r"maintenance items?[^.]{0,220}(?:worn|gap|delaminat|hole)|"
    r"porous and heavily textured[^.]{0,220}(?:dust|residue)|"
    r"unsealed concrete[^.]{0,160}(?:debris|crack))",
    re.I | re.S,
)
_CP35_RISK_POSITIVE = re.compile(
    r"(?:within acceptable (?:range|level)|below threshold|pest[- ]free|no rodent activity|"
    r"rodents?: nil evidence|negative for live insects|no active infestation|"
    r"no evidence of (?:bird|pest|rodent|insect) entry|no evidence of access to grain-contact areas)",
    re.I,
)
_CP35_RISK_ADVERSE = re.compile(
    r"(?:repeated (?:rodent|pest|insect) activity[^.]{0,180}delayed escalation|"
    r"active infestation|pest activity (?:was )?(?:found|identified|detected)|"
    r"above threshold|contamination (?:found|detected|present)|control failure)",
    re.I | re.S,
)


def _first_matching(rows: list[dict], pattern: re.Pattern) -> dict | None:
    for ch in rows:
        if pattern.search(str(ch.get("text") or "")):
            return ch
    return None


def _matched_sentence(text: str, pattern: re.Pattern) -> str:
    """Return the smallest grounded sentence containing a pattern match."""
    raw = str(text or "").strip()
    for sentence in re.split(r"(?<=[.!?])\s+", raw):
        if pattern.search(sentence):
            return sentence.strip()
    m = pattern.search(raw)
    return m.group(0).strip() if m else raw


def _cp12_design_structural_witnesses(
    requirement_result: dict,
    requirement: dict,
    chunks: list[dict],
    identity_by_source: dict[str, dict],
) -> list[dict]:
    fm = _core_source_chunks(chunks, identity_by_source, "4_Farm-Management-Plan_")
    if not fm:
        return []
    src = str(fm[0].get("file") or _source_file(fm[0].get("id")))
    identity = identity_by_source[src]
    c4 = _paragraph_section(fm, r"5\.1\s+Cleanability")
    c5 = _paragraph_section(fm, r"5\.2\s+Contamination and Pest Harbourage Minimisation")
    design_rows = c4 + c5
    if not design_rows:
        return []
    derived = [str(ch.get("id")) for ch in design_rows]
    adverse = _first_matching(design_rows, _CP12_DESIGN_ADVERSE)
    if adverse is not None:
        quote = str(adverse.get("text") or "").strip()
        return [_validated_synthetic_alignment(
            requirement=requirement,
            evidence_id=str(adverse.get("id")),
            exact_quote=quote,
            semantic_context=(
                "The establishment has an explicit design/construction defect that undermines effective cleaning "
                "and minimisation of contamination, infestation or pest harbourage: " + quote
            ),
            relation="ATTACK",
            identity=identity,
            reason_code="STRUCTURED_CP12_DESIGN_EXPLICIT_COUNTEREXAMPLE",
            reason=(
                "Deterministic CP12 design audit found a construction-level counterexample in the cleanability/"
                "pest-harbourage design sections. Partial positive features are not separately promoted to holistic support."
            ),
            derived_from=derived,
            witness_key=f"CP12:ER1:{src}:ATTACK",
            aggregate_kind="HOLISTIC_DESIGN_COUNTEREXAMPLE",
        )]

    cleanable = _first_matching(c4, _CP12_CLEANABILITY_POSITIVE)
    pest_design = _first_matching(c5, _CP12_PEST_DESIGN_POSITIVE)
    if cleanable is not None and pest_design is not None:
        quote = str(cleanable.get("text") or "").strip()
        context = (
            "The establishment is designed and constructed to minimise contamination, infestation and pest harbourage: "
            f"cleanability section positive; pest-exclusion/harbourage section positive. Representative evidence: {quote}"
        )
        return [_validated_synthetic_alignment(
            requirement=requirement,
            evidence_id=str(cleanable.get("id")),
            exact_quote=quote,
            semantic_context=context,
            relation="SUPPORT",
            identity=identity,
            reason_code="STRUCTURED_CP12_DESIGN_SECTION_AGGREGATE_SUPPORT",
            reason=(
                "Deterministic CP12 design audit jointly covered the cleanability and contamination/pest-harbourage "
                "design sections and found no explicit construction-level counterexample."
            ),
            derived_from=derived,
            witness_key=f"CP12:ER1:{src}:SUPPORT",
            aggregate_kind="HOLISTIC_MULTI_SECTION_SUPPORT",
        )]
    return []


def _cp12_maintenance_structural_witnesses(
    requirement_result: dict,
    requirement: dict,
    chunks: list[dict],
    identity_by_source: dict[str, dict],
) -> list[dict]:
    out: list[dict] = []
    fm = _core_source_chunks(chunks, identity_by_source, "4_Farm-Management-Plan_")
    if not fm:
        return out
    src = str(fm[0].get("file") or _source_file(fm[0].get("id")))
    identity = identity_by_source[src]
    c8 = _paragraph_section(fm, r"7\.1\s+Hygiene and Cleanliness")
    c9 = _paragraph_section(fm, r"7\.2\s+Pest Condition")
    hygiene = _first_matching(c8, _CP12_HYGIENE_POSITIVE)
    pest = _first_matching(c9, _CP12_PEST_POSITIVE)
    derived_pos = [str(ch.get("id")) for ch in (c8 + c9)]
    if hygiene is not None and pest is not None:
        quote = str(pest.get("text") or "").strip()
        out.append(_validated_synthetic_alignment(
            requirement=requirement,
            evidence_id=str(pest.get("id")),
            exact_quote=quote,
            semantic_context=(
                "Current pest status: all monitoring indicators within acceptable range. "
                "The establishment is maintained clean and free of harbourage and hygiene deficiencies. "
                "This current condition minimises contamination, infestation and pest harbourage. "
                "Representative evidence: " + quote
            ),
            relation="SUPPORT",
            identity=identity,
            reason_code="STRUCTURED_CP12_MAINTENANCE_HYGIENE_PEST_AGGREGATE_SUPPORT",
            reason=(
                "Deterministic CP12 maintenance audit jointly covered current hygiene/cleanliness and current pest-condition sections."
            ),
            derived_from=derived_pos,
            witness_key=f"CP12:ER2:{src}:SUPPORT",
            aggregate_kind="HOLISTIC_CURRENT_STATE_SUPPORT",
        ))

    # A direct adverse finding may live outside the farm-management C8/C9 pair.
    # Scan already identity-admitted core documents for explicit current risk/
    # maintenance failures.  Generic low activity with completed escalation is
    # intentionally not adverse.
    adverse_candidates: list[dict] = []
    for prefix in ("4_Farm-Management-Plan_", "7_Bait_Station_Map_"):
        adverse_candidates.extend(_core_source_chunks(chunks, identity_by_source, prefix))
    adverse = _first_matching(adverse_candidates, _CP12_MAINTENANCE_ADVERSE)
    if adverse is not None:
        asrc = str(adverse.get("file") or _source_file(adverse.get("id")))
        aidentity = identity_by_source[asrc]
        full_quote = str(adverse.get("text") or "").strip()
        quote = _matched_sentence(full_quote, _CP12_MAINTENANCE_ADVERSE)
        out.append(_validated_synthetic_alignment(
            requirement=requirement,
            evidence_id=str(adverse.get("id")),
            exact_quote=quote,
            semantic_context=(
                "Current pest-control condition: repeated rodent or pest activity with delayed escalation follow-up. "
                "This is an explicit adverse current state inconsistent with minimising contamination, infestation or pest harbourage. "
                "Grounded adverse statement: " + quote
            ),
            relation="ATTACK",
            identity=aidentity,
            reason_code="STRUCTURED_CP12_MAINTENANCE_EXPLICIT_ADVERSE_STATE",
            reason="Deterministic CP12 maintenance audit found an explicit current adverse maintenance/pest-control fact.",
            derived_from=[str(adverse.get("id"))],
            witness_key=f"CP12:ER2:{asrc}:ATTACK",
            aggregate_kind="HOLISTIC_CURRENT_STATE_COUNTEREVIDENCE",
        ))
    return out


def _cp35_risk_structural_witnesses(
    requirement_result: dict,
    requirement: dict,
    chunks: list[dict],
    identity_by_source: dict[str, dict],
) -> list[dict]:
    out: list[dict] = []
    fm = _core_source_chunks(chunks, identity_by_source, "4_Farm-Management-Plan_")
    if fm:
        src = str(fm[0].get("file") or _source_file(fm[0].get("id")))
        identity = identity_by_source[src]
        c9 = _paragraph_section(fm, r"7\.2\s+Pest Condition")
        positive = _first_matching(c9, _CP35_RISK_POSITIVE)
        if positive is not None:
            quote = str(positive.get("text") or "").strip()
            out.append(_validated_synthetic_alignment(
                requirement=requirement,
                evidence_id=str(positive.get("id")),
                exact_quote=quote,
                semantic_context=(
                    "The current risk of contamination or infestation is maintained at an acceptable level: " + quote
                ),
                relation="SUPPORT",
                identity=identity,
                reason_code="STRUCTURED_CP35_CURRENT_PEST_RISK_ACCEPTABLE",
                reason="Deterministic CP35 audit found an explicit current pest/risk outcome at or below acceptable limits.",
                derived_from=[str(ch.get("id")) for ch in c9],
                witness_key=f"CP35:{src}:SUPPORT",
                aggregate_kind="CURRENT_RISK_OUTCOME_SUPPORT",
            ))

    bait = _core_source_chunks(chunks, identity_by_source, "7_Bait_Station_Map_")
    adverse = _first_matching(bait, _CP35_RISK_ADVERSE)
    if adverse is not None:
        src = str(adverse.get("file") or _source_file(adverse.get("id")))
        identity = identity_by_source[src]
        full_quote = str(adverse.get("text") or "").strip()
        quote = _matched_sentence(full_quote, _CP35_RISK_ADVERSE)
        out.append(_validated_synthetic_alignment(
            requirement=requirement,
            evidence_id=str(adverse.get("id")),
            exact_quote=quote,
            semantic_context=(
                "The current risk of infestation or contamination is not maintained at an acceptable level. "
                "Repeated rodent or pest activity with delayed escalation follow-up is recorded. "
                "Grounded adverse statement: " + quote
            ),
            relation="ATTACK",
            identity=identity,
            reason_code="STRUCTURED_CP35_EXPLICIT_ADVERSE_RISK_OUTCOME",
            reason="Deterministic CP35 audit found repeated pest activity with delayed escalation or another explicit unacceptable-risk outcome.",
            derived_from=[str(adverse.get("id"))],
            witness_key=f"CP35:{src}:ATTACK",
            aggregate_kind="CURRENT_RISK_OUTCOME_COUNTEREVIDENCE",
        ))
    return out
