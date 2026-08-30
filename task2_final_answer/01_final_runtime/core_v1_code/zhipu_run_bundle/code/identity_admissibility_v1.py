"""Minimal FRECA Core Layer-5 identity/admissibility pilot.

Design basis
------------
This module intentionally implements a small subset of the frozen Layer-5 design:

* body-first core identity; output/case RE is checked only after core formation;
* canonical RE conflicts outrank name similarity;
* package association is a weak bridge, not identity truth;
* document/source identity and per-chunk identity are separate;
* foreign/unverified material is preserved in the ledger and only its substantive
  proof use is gated;
* no CP text, gold labels, human signature table, filename entity tokens, LLM,
  embeddings, or edit-distance identity matching are used.

Reference-core reuse
--------------------
The canonical RE parser, malformed-RE separation, conservative legal-suffix name
normalisation, and the principle that conflicting registration outranks names are
adapted from freca_reference_core_20260828/src/freca/evidence/identity.py.
The old filename-defined IdentityReference and switchable STRICT/LENIENT/
DIFFERENTIATED policies are deliberately NOT reused because the frozen target
architecture supersedes them.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any


RE_NUMBER_CANONICAL = re.compile(
    r"\bRE-(?P<state>[A-Z]{2,3})-(?P<year>(?:19|20)\d{2})-(?P<serial>\d{4})\b"
)
RE_NUMBER_LOOSE = re.compile(r"\bRE-[A-Z]{2,4}-[A-Z0-9-]{3,12}\b")

LEGAL_SUFFIXES = frozenset(
    {
        "pty",
        "ptyltd",
        "ltd",
        "limited",
        "proprietary",
        "trust",
        "cooperative",
        "coop",
        "co",
        "company",
        "incorporated",
        "inc",
        "nl",
    }
)

# Owner-role patterns only.  They intentionally do not treat arbitrary RE mentions
# (e.g. destination REs in traceability rows) as document-owner claims.
_OWNER_RE_PATTERNS = [
    re.compile(r"(?i)\bRegistration(?:\s+Number)?\s*(?:\||:)?\s*(RE-[A-Z]{2,4}-[A-Z0-9-]{3,12})\b"),
    re.compile(r"(?i)\bRE\s+Number\s*(?:\||:)?\s*(RE-[A-Z]{2,4}-[A-Z0-9-]{3,12})\b"),
    re.compile(r"(?i)\bholds\s+RE\s+Number\s+(RE-[A-Z]{2,4}-[A-Z0-9-]{3,12})\b"),
    re.compile(r"(?i)\bRegistered\s+packhouse\s+(RE-[A-Z]{2,4}-[A-Z0-9-]{3,12})\b"),
    re.compile(r"(?i)\(\s*RE\s+(RE-[A-Z]{2,4}-[A-Z0-9-]{3,12})\s*\)"),
]

_OWNER_NAME_PATTERNS = [
    re.compile(
        r"(?im)^\s*(?:Establishment(?:\s+Name)?|Business\s+Name|Trading\s+Name|Company\s+Name|Entity\s+Name)\s*(?:\||:)\s*([^|\n]+)"
    ),
    re.compile(r"(?i)\bresponsible\s+person\s+for\s+(.+?)\s*\(\s*RE\b"),
    re.compile(r"(?im)^\s*([^|\n]{2,120}?)\s*\|\s*RE\s+Number\s*:"),
    re.compile(r"(?i)^\s*(.+?)\s+is\s+a\s+registered\s+export\s+establishment\b"),
]


def normalise_entity(name: str | None) -> str:
    """Conservative entity key adapted from the reference core."""

    if not name:
        return ""
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    kept = [token for token in tokens if token not in LEGAL_SUFFIXES]
    return "".join(kept or tokens)


def entity_tokens(name: str | None) -> set[str]:
    if not name:
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", name.lower())
        if token not in LEGAL_SUFFIXES and len(token) > 1
    }


def find_re_numbers(text: str) -> tuple[list[str], list[str]]:
    canonical = [m.group(0) for m in RE_NUMBER_CANONICAL.finditer(text)]
    canonical_set = set(canonical)
    malformed = [
        m.group(0)
        for m in RE_NUMBER_LOOSE.finditer(text)
        if m.group(0) not in canonical_set
    ]
    return _dedupe(canonical), _dedupe(malformed)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _source_id(chunk: dict[str, Any]) -> str:
    # `file` is used only as an opaque source grouping key.  Its entity-like
    # filename tokens are never parsed or compared.
    value = chunk.get("file")
    if isinstance(value, str) and value:
        return value

    evidence_id = str(chunk.get("id") or chunk.get("evidence_id") or "")
    match = re.match(r"^(.+?\.(?:docx|xlsx))(?::|$)", evidence_id, flags=re.I)
    return match.group(1) if match else evidence_id


def _evidence_id(chunk: dict[str, Any]) -> str:
    return str(chunk.get("id") or chunk.get("evidence_id") or chunk.get("chunk_id") or "")


def _text(chunk: dict[str, Any]) -> str:
    return str(chunk.get("text") or chunk.get("content") or chunk.get("raw_text") or "")


def _extract_owner_res(text: str) -> tuple[list[str], list[str]]:
    values: list[str] = []
    for pattern in _OWNER_RE_PATTERNS:
        values.extend(m.group(1).upper() for m in pattern.finditer(text))

    values = _dedupe(values)
    canonical = [value for value in values if RE_NUMBER_CANONICAL.fullmatch(value)]
    malformed = [value for value in values if not RE_NUMBER_CANONICAL.fullmatch(value)]
    return canonical, malformed


def _clean_owner_name(value: str) -> str:
    value = value.strip(" \t\r\n|:;,-")
    # Avoid swallowing trailing key/value fields in a single rendered table row.
    value = re.split(
        r"\s{2,}|\s*\|\s*(?:RE\s+Number|Address|Commodity|State|Doc\s+Ref)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    return value


def _extract_owner_names(text: str) -> list[str]:
    names: list[str] = []
    for pattern in _OWNER_NAME_PATTERNS:
        for match in pattern.finditer(text):
            value = _clean_owner_name(match.group(1))
            if value and len(value) <= 160:
                names.append(value)
    return _dedupe(names)


def extract_owner_claims(evidence_chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract body-only source owner claims from current Core evidence chunks."""

    claims: dict[str, dict[str, Any]] = {}

    for chunk in evidence_chunks:
        source_id = _source_id(chunk)
        evidence_id = _evidence_id(chunk)
        text = _text(chunk)
        canonical_res, malformed_res = _extract_owner_res(text)
        owner_names = _extract_owner_names(text)

        row = claims.setdefault(
            source_id,
            {
                "source_id": source_id,
                "canonical_res": [],
                "malformed_res": [],
                "owner_names": [],
                "claim_evidence_ids": [],
                "claim_details": [],
            },
        )

        if canonical_res or malformed_res or owner_names:
            row["claim_evidence_ids"].append(evidence_id)
            row["claim_details"].append(
                {
                    "evidence_id": evidence_id,
                    "canonical_res": canonical_res,
                    "malformed_res": malformed_res,
                    "owner_names": owner_names,
                }
            )

        row["canonical_res"] = _dedupe(row["canonical_res"] + canonical_res)
        row["malformed_res"] = _dedupe(row["malformed_res"] + malformed_res)
        row["owner_names"] = _dedupe(row["owner_names"] + owner_names)

    return claims


def _candidate_vector(
    re_number: str,
    sources: set[str],
    claims: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    direct_claim_count = sum(
        1
        for source_id in sources
        for detail in claims[source_id]["claim_details"]
        if re_number in detail["canonical_res"]
    )
    return {
        "re_number": re_number,
        "distinct_sources": len(sources),
        "direct_claim_count": direct_claim_count,
        "source_ids": sorted(sources),
    }


def build_body_first_identity_report(
    evidence_chunks: list[dict[str, Any]],
    *,
    output_identifier: str | None = None,
) -> dict[str, Any]:
    """Form the case core identity from body owner claims before output-ID comparison."""

    claims = extract_owner_claims(evidence_chunks)
    re_sources: dict[str, set[str]] = defaultdict(set)

    for source_id, claim in claims.items():
        for re_number in claim["canonical_res"]:
            re_sources[re_number].add(source_id)

    candidates = [
        _candidate_vector(re_number, sources, claims)
        for re_number, sources in re_sources.items()
    ]
    candidates.sort(
        key=lambda item: (
            -item["distinct_sources"],
            -item["direct_claim_count"],
            item["re_number"],
        )
    )

    eligible = [item for item in candidates if item["distinct_sources"] >= 2]
    core_re: str | None = None
    core_status = "CORE_UNRESOLVED"

    if eligible:
        top = eligible[0]
        second = eligible[1] if len(eligible) > 1 else None
        if second is None or top["distinct_sources"] > second["distinct_sources"]:
            core_re = top["re_number"]
            core_status = "CORE_SUPPORTED"
        else:
            core_status = "CORE_CONFLICTED"

    # Core names are body names from sources that support the winning canonical RE.
    core_names: list[str] = []
    if core_re:
        for source_id, claim in claims.items():
            if core_re in claim["canonical_res"]:
                core_names.extend(claim["owner_names"])
    core_names = _dedupe(core_names)
    core_name_keys = sorted({normalise_entity(name) for name in core_names if normalise_entity(name)})

    source_relations: dict[str, dict[str, Any]] = {}
    for source_id, claim in claims.items():
        canonical = set(claim["canonical_res"])
        names = claim["owner_names"]
        name_keys = {normalise_entity(name) for name in names if normalise_entity(name)}

        if core_re is None:
            relation = "UNKNOWN"
            status = "UNKNOWN"
        elif core_re in canonical and any(value != core_re for value in canonical):
            relation = "MIXED"
            status = "CONFLICTED"
        elif core_re in canonical:
            relation = "CORE_SELF_EXACT"
            status = "RESOLVED"
        elif canonical:
            relation = "FOREIGN_CONFLICTING_REGISTRATION"
            status = "RESOLVED"
        elif name_keys and core_name_keys and any(key in core_name_keys for key in name_keys):
            relation = "CORE_SELF_ENTITY_ONLY"
            status = "CONDITIONAL"
        elif names or claim["malformed_res"]:
            # Frozen D5.3/D5.4: a different name or malformed RE without a
            # conflicting canonical RE is not enough to declare FOREIGN.
            relation = "CASE_ASSOCIATED_UNVERIFIED"
            status = "CONDITIONAL"
        else:
            relation = "CASE_ASSOCIATED_OWNER_UNKNOWN"
            status = "CONDITIONAL"

        source_relations[source_id] = {
            "source_id": source_id,
            "relation_to_case": relation,
            "status": status,
            "canonical_res": claim["canonical_res"],
            "malformed_res": claim["malformed_res"],
            "owner_names": names,
            "claim_evidence_ids": claim["claim_evidence_ids"],
        }

    output_identifier_match: bool | None = None
    assembly_status = "NOT_CHECKED"
    if output_identifier and core_re:
        output_identifier_match = output_identifier == core_re
        assembly_status = (
            "OUTPUT_IDENTIFIER_MATCH"
            if output_identifier_match
            else "CASE_ASSEMBLY_IDENTITY_CONFLICT"
        )

    return {
        "schema": "freca-core-identity-admissibility-v1",
        "core_status": core_status,
        "core_re": core_re,
        "core_names": core_names,
        "core_name_keys": core_name_keys,
        "candidate_clusters": candidates,
        "source_relations": source_relations,
        "output_identifier": output_identifier,
        "output_identifier_match": output_identifier_match,
        "assembly_status": assembly_status,
        "rules": [
            "BODY_FIRST_CORE_IDENTITY",
            "CANONICAL_RE_HARD_PRIORITY",
            "NO_FILENAME_ENTITY_IDENTITY",
            "PACKAGE_ASSOCIATION_IS_WEAK_BRIDGE",
            "MALFORMED_RE_IS_NOT_CANONICAL_FOREIGN_PROOF",
            "FOREIGN_CONTENT_PRESERVED_BUT_NOT_SUBSTANTIVE_PROOF",
        ],
    }


def _candidate_source_id(candidate: dict[str, Any]) -> str:
    if candidate.get("source_id"):
        return str(candidate["source_id"])
    evidence_id = str(candidate.get("evidence_id") or "")
    match = re.match(r"^(.+?\.(?:docx|xlsx))(?::|$)", evidence_id, flags=re.I)
    return match.group(1) if match else evidence_id


def _chunk_owner_relation(text: str, source_relation: str, report: dict[str, Any]) -> str:
    canonical, _malformed = _extract_owner_res(text)
    names = _extract_owner_names(text)
    core_re = report.get("core_re")
    core_name_keys = set(report.get("core_name_keys") or [])

    if core_re and canonical:
        values = set(canonical)
        if core_re in values and any(value != core_re for value in values):
            return "MIXED"
        if core_re in values:
            return "CORE_SELF_EXACT"
        return "FOREIGN_CONFLICTING_REGISTRATION"

    if names and core_name_keys:
        name_keys = {normalise_entity(name) for name in names if normalise_entity(name)}
        if any(key in core_name_keys for key in name_keys):
            return "CORE_SELF_ENTITY_ONLY"

    # If a foreign/unverified source explicitly mentions the core identity in
    # this chunk, preserve it as a possible about-core/counterparty fact rather
    # than deleting it.  Without full L4 event-subject extraction it stays
    # conditional, never direct.
    if core_re and source_relation in {
        "FOREIGN_CONFLICTING_REGISTRATION",
        "CASE_ASSOCIATED_UNVERIFIED",
        "MIXED",
    }:
        if core_re in text:
            return "COUNTERPARTY_LINKED"
        lowered = normalise_entity(text)
        if any(key and key in lowered for key in core_name_keys):
            return "CONTRACTOR_ABOUT_CORE"

    return source_relation


def _use_decision(relation: str, report: dict[str, Any]) -> tuple[str, bool, str]:
    if report.get("core_status") != "CORE_SUPPORTED":
        return "ADMIT_CONDITIONAL", False, "CORE_IDENTITY_NOT_LOCKED"
    if report.get("assembly_status") == "CASE_ASSEMBLY_IDENTITY_CONFLICT":
        return "ADMIT_CONDITIONAL", False, "OUTPUT_IDENTIFIER_CONFLICT"

    if relation == "CORE_SELF_EXACT":
        return "ADMIT_DIRECT", True, "CORE_SELF_EXACT"
    if relation in {
        "CORE_SELF_ENTITY_ONLY",
        "CASE_ASSOCIATED_UNVERIFIED",
        "CASE_ASSOCIATED_OWNER_UNKNOWN",
        "COUNTERPARTY_LINKED",
        "CONTRACTOR_ABOUT_CORE",
        "MIXED",
        "UNKNOWN",
    }:
        return "ADMIT_CONDITIONAL", False, relation
    if relation == "FOREIGN_CONFLICTING_REGISTRATION":
        return "EXCLUDE_SUBSTANTIVE", False, "FOREIGN_CANONICAL_RE_CONFLICT"
    return "ADMIT_CONDITIONAL", False, "UNRESOLVED_IDENTITY"



def apply_identity_gate_to_traces(
    traces: list[dict[str, Any]],
    identity_report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Annotate the full retrieval universe, then mirror annotations to context.

    Nothing is deleted.  The candidate universe is the audit/coverage ledger;
    ``candidates`` is only the context-packed subset used by the current
    semantic alignment adapter.
    """

    out = copy.deepcopy(
        traces
    )

    source_relations = (
        identity_report.get(
            "source_relations"
        )
        or {}
    )

    def annotate(
        candidate: dict[str, Any],
    ) -> None:
        source_id = (
            _candidate_source_id(
                candidate
            )
        )

        source = (
            source_relations.get(
                source_id,
                {
                    "relation_to_case":
                        "CASE_ASSOCIATED_OWNER_UNKNOWN",
                    "status":
                        "CONDITIONAL",
                },
            )
        )

        relation = (
            _chunk_owner_relation(
                str(
                    candidate.get(
                        "text"
                    )
                    or ""
                ),
                str(
                    source.get(
                        "relation_to_case"
                    )
                    or "UNKNOWN"
                ),
                identity_report,
            )
        )

        (
            decision,
            decisive_ok,
            reason,
        ) = _use_decision(
            relation,
            identity_report,
        )

        candidate[
            "source_id"
        ] = source_id

        candidate[
            "identity_relation_to_case"
        ] = relation

        candidate[
            "identity_use_decision"
        ] = decision

        candidate[
            "identity_decisive_proof_eligible"
        ] = decisive_ok

        candidate[
            "identity_reason_code"
        ] = reason

    for trace in out:
        universe = trace.get(
            "candidate_universe"
        )

        if not isinstance(
            universe,
            list,
        ):
            universe = trace.get(
                "candidates",
                [],
            )

        direct = 0
        conditional = 0
        excluded = 0

        for candidate in universe:
            annotate(
                candidate
            )

            decision = candidate.get(
                "identity_use_decision"
            )

            if (
                decision
                == "ADMIT_DIRECT"
            ):
                direct += 1

            elif (
                decision
                == "EXCLUDE_SUBSTANTIVE"
            ):
                excluded += 1

            else:
                conditional += 1

        # Rebuild the context subset from the already-annotated universe so the
        # same evidence ID cannot receive two different identity decisions.
        by_id = {
            str(
                candidate.get(
                    "evidence_id"
                )
            ): candidate
            for candidate
            in universe
        }

        old_context = trace.get(
            "candidates",
            [],
        )

        rebuilt_context = []

        for context_candidate in old_context:
            evidence_id = str(
                context_candidate.get(
                    "evidence_id"
                )
            )

            base = by_id.get(
                evidence_id
            )

            if base is None:
                # Backward-compatible defensive path.
                copy_candidate = (
                    copy.deepcopy(
                        context_candidate
                    )
                )

                annotate(
                    copy_candidate
                )

                rebuilt_context.append(
                    copy_candidate
                )
                continue

            copy_candidate = (
                copy.deepcopy(
                    base
                )
            )

            # Preserve context-only metadata.
            for key in (
                "context_rank",
                "selected_for_model_context",
            ):
                if key in context_candidate:
                    copy_candidate[
                        key
                    ] = context_candidate[
                        key
                    ]

            rebuilt_context.append(
                copy_candidate
            )

        trace[
            "candidate_universe"
        ] = universe

        trace[
            "candidates"
        ] = rebuilt_context

        trace[
            "identity_gate_summary"
        ] = {
            "universe_count":
                len(
                    universe
                ),
            "admit_direct":
                direct,
            "admit_conditional":
                conditional,
            "exclude_substantive":
                excluded,
            "summary_scope":
                "CANDIDATE_UNIVERSE",
        }

    return out

