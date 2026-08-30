from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import freca_core_v1 as core


# ============================================================================
# FRECA Core — minimal evidence-requirement reasoning slice
#
# Extracted from the frozen target architecture:
#   D2.8  EvidenceRequirement
#   D7.1  bidirectional RetrievalNeed
#   D7.8  evidence-to-requirement alignment
#   D7.14 typed proof gates
#
# This module deliberately does NOT implement full Layer 4/5/6/7, Carneades,
# burden rules, full-case coverage, identity graph, or four independent roots.
# It is a pilot adapter around the already working Core parser/retriever/API.
# ============================================================================


PROJECT_ROOT = Path(getattr(core, "PROJECT_ROOT", Path.cwd()))
CONTRACT_DIR = PROJECT_ROOT / "contracts_v2"
RESULT_DIR = PROJECT_ROOT / "results_v2"

EVIDENCE_PLAN_MODEL = getattr(core, "CONTRACT_MODEL", "deepseek-v4-pro")
EVIDENCE_ALIGN_MODEL = getattr(core, "EVIDENCE_MODEL", "deepseek-v4-flash")


# ---------------------------------------------------------------------------
# Exact grounding helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def normalize_ws(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def quote_match_mode(quote: str, source: str) -> str | None:
    quote = str(quote or "")
    source = str(source or "")

    if not quote.strip():
        return None

    if quote in source:
        return "EXACT_RAW"

    qn = normalize_ws(quote)
    sn = normalize_ws(source)

    if qn and qn in sn:
        return "WHITESPACE_NORMALIZED"

    return None


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(core, "save_json"):
        core.save_json(value, path)
        return
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _walk_contract(value, path=()):
    """Yield (path, value) for every node in a JSON-like contract."""
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_contract(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_contract(child, path + (str(index),))


def _looks_like_atom_definition(value):
    if not isinstance(value, dict):
        return False
    descriptive_keys = {
        'proposition', 'display_text', 'criterion_quote',
        'basis_candidate_ids', 'basis_ids', 'legal_basis_ids',
        'relations', 'text',
    }
    return bool(descriptive_keys.intersection(value.keys()))


def normalize_contract_atoms(contract: dict) -> dict[str, dict]:
    """Discover atom definitions recursively without assuming a fixed schema."""
    atoms = {}
    atom_id_re = re.compile(r'^A\d+$')
    explicit_container_keys = {
        'atoms', 'atom_catalog', 'atom_definitions', 'scoring_atoms',
        'propositions', 'proposition_catalog',
    }

    for path, value in _walk_contract(contract):
        if not path or path[-1] not in explicit_container_keys:
            continue
        if isinstance(value, dict):
            for key, child in value.items():
                atom_id = str(key).strip()
                if not atom_id_re.fullmatch(atom_id) or not isinstance(child, dict):
                    continue
                atom = dict(child)
                atom.setdefault('atom_id', atom_id)
                atoms.setdefault(atom_id, atom)
        elif isinstance(value, list):
            for child in value:
                if not isinstance(child, dict):
                    continue
                atom_id = str(
                    child.get('atom_id') or child.get('id')
                    or child.get('proposition_id') or ''
                ).strip()
                if not atom_id_re.fullmatch(atom_id):
                    continue
                if not _looks_like_atom_definition(child):
                    continue
                atom = dict(child)
                atom.setdefault('atom_id', atom_id)
                atoms.setdefault(atom_id, atom)

    for _path, value in _walk_contract(contract):
        if not isinstance(value, dict):
            continue
        for key, child in value.items():
            atom_id = str(key).strip()
            if not atom_id_re.fullmatch(atom_id):
                continue
            if not _looks_like_atom_definition(child):
                continue
            atom = dict(child)
            atom.setdefault('atom_id', atom_id)
            atoms.setdefault(atom_id, atom)

    for _path, value in _walk_contract(contract):
        if not _looks_like_atom_definition(value):
            continue
        atom_id = str(value.get('atom_id') or value.get('id') or '').strip()
        if not atom_id_re.fullmatch(atom_id):
            continue
        atom = dict(value)
        atom.setdefault('atom_id', atom_id)
        atoms.setdefault(atom_id, atom)

    return atoms


def find_contract_component(contract: dict, keys: tuple[str, ...]):
    for _path, value in _walk_contract(contract):
        if not isinstance(value, dict):
            continue
        for key in keys:
            if key in value and value[key] is not None:
                return value[key]
    return None


def normalized_contract_view(contract: dict) -> dict:
    return {
        'atoms': normalize_contract_atoms(contract),
        'satisfaction': find_contract_component(
            contract, ('satisfaction', 'satisfaction_root', 'satisfaction_ast')
        ),
        'logic_basis': find_contract_component(
            contract, ('logic_basis', 'logic_bases')
        ) or [],
    }


def describe_contract_shape(contract: dict) -> dict:
    atom_refs = []
    for path, value in _walk_contract(contract):
        if not isinstance(value, dict):
            continue
        atom_id = value.get('atom_id')
        if isinstance(atom_id, str) and re.fullmatch(r'A\d+', atom_id):
            atom_refs.append({
                'path': '.'.join(path) or '<root>',
                'atom_id': atom_id,
                'keys': sorted(value.keys()),
            })
    view = normalized_contract_view(contract)
    return {
        'top_level_keys': sorted(contract.keys()),
        'discovered_atom_ids': sorted(view['atoms'].keys()),
        'atom_reference_locations': atom_refs[:30],
        'satisfaction': view['satisfaction'],
        'logic_basis': view['logic_basis'],
    }


def chunk_id(chunk: dict) -> str:
    for key in ("id", "evidence_id", "chunk_id"):
        value = chunk.get(key)
        if value:
            return str(value)
    raise ValueError(f"Evidence chunk has no stable id: {chunk.keys()}")


def chunk_text(chunk: dict) -> str:
    for key in ("text", "content", "raw_text"):
        value = chunk.get(key)
        if value is not None:
            return str(value)
    raise ValueError(f"Evidence chunk has no text/content field: {chunk_id(chunk)}")


# ============================================================================
# D2.8 — EvidenceRequirement
# ============================================================================


EVIDENCE_REQUIREMENT_SYSTEM = r"""
You are the EvidenceRequirement compiler in a closed-source compliance system.

You are working BEFORE any case evidence is accessed.
Use only the supplied official checking-point criterion, frozen Core contract,
validated CandidateLedger, validated RuleSetRelation groups, and deterministic
FACET_SEEDS supplied in the input.

Your task is NOT to create new legal obligations and NOT to decide compliance.
Your task is to express each supplied FACET_SEED as one observable evidence
requirement for the already-frozen benchmark atom.

CRITICAL DISTINCTION:

EvidenceRequirement != scoring leaf.

A single frozen ATOM may have MULTIPLE EvidenceRequirements.
Those requirements describe what evidence must be checked to establish different
observable aspects of the SAME benchmark proposition. They do not create new
ALL_OF/ANY_OF contract nodes.

FACET_SEEDS are deterministic and authoritative.

Rules:

- Return EXACTLY ONE DECISIVE EvidenceRequirement for every FACET_SEED.
- Do not merge two different FACET_SEEDS into one requirement.
- Do not split one FACET_SEED into multiple decisive requirements.
- SPECIALIZES groups are deliberately represented as one evidence facet for that
  material aspect.
- SUPPORTS_SAME_CRITERION groups do not create a facet seed by themselves.
- ALTERNATIVE groups form one evidence facet; alternative legal sources must not
  become multiple mandatory requirements.
- CUMULATIVE members may be supplied as separate seeds by the deterministic
  seed builder.
- A requirement must be narrower and more observable than the whole benchmark
  atom whenever multiple FACET_SEEDS exist.
- If multiple FACET_SEEDS exist, do NOT simply repeat the entire CP criterion as
  proposition_to_establish for each seed.

For each requirement:

- facet_seed_id must exactly identify one supplied seed;
- atom_id must equal the seed atom_id;
- decisiveness must be DECISIVE;
- criterion_quote must be an exact quote from the official criterion;
- basis_candidate_ids must be a non-empty subset of the seed candidate IDs;
- source_group_ids must contain the seed source group IDs;
- RULES query_sources may cite only candidate IDs in that seed;
- CP query_sources may cite only exact CP text;
- proposition_to_establish must describe an observable aspect grounded in the
  seed's Rules text and CP text;
- do not mention Tracks, filenames, cases, labels, historical outputs, or expected
  answers;
- do not invent industry practice or extra requirements.

Return JSON only:

{
  "requirements": [
    {
      "requirement_id": "ER1",
      "facet_seed_id": "FS-G1",
      "atom_id": "A1",
      "proposition_to_establish": "observable evidence facet",
      "polarity": "SUPPORT",
      "decisiveness": "DECISIVE",
      "criterion_quote": "exact CP quote",
      "basis_candidate_ids": ["rules2021:..."],
      "source_group_ids": ["G1"],
      "query_sources": [
        {
          "source": "CP",
          "candidate_id": null,
          "quote": "exact CP phrase"
        },
        {
          "source": "RULES",
          "candidate_id": "rules2021:...",
          "quote": "exact Rules phrase"
        }
      ],
      "reason": "brief explanation"
    }
  ]
}
"""



def _candidate_maps(ledger_artifact: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    candidates = {
        item["id"]: item
        for item in ledger_artifact.get("candidates", [])
        if isinstance(item, dict) and item.get("id")
    }
    decisions = {
        item["candidate_id"]: item
        for item in ledger_artifact.get("decisions", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    return candidates, decisions


def build_evidence_facet_seeds(
    rule_set_relation: dict,
    eligible_ids: set[str],
    *,
    atom_id: str,
) -> list[dict]:
    """Derive evidence facets from validated L2-C without changing contract logic."""

    groups = [
        g
        for g in rule_set_relation.get("groups", [])
        if isinstance(g, dict)
    ]

    seeds = []
    seen_keys = set()

    def add_seed(seed_id, source_group_ids, members, seed_relation):
        members = sorted({m for m in members if m in eligible_ids})
        if not members:
            return
        key = (tuple(source_group_ids), tuple(members), seed_relation)
        if key in seen_keys:
            return
        seen_keys.add(key)
        seeds.append(
            {
                "facet_seed_id": seed_id,
                "atom_id": atom_id,
                "source_group_ids": source_group_ids,
                "basis_candidate_ids": members,
                "seed_relation": seed_relation,
            }
        )

    for group in sorted(groups, key=lambda g: str(g.get("group_id", ""))):
        group_id = str(group.get("group_id", "")).strip()
        relation = str(group.get("relation", "")).strip()
        members = [str(x) for x in group.get("member_candidate_ids", [])]

        if relation in {"CONTESTED", "UNRESOLVED"}:
            raise ValueError(
                f"Cannot build evidence facets from unresolved RuleSetRelation "
                f"group {group_id}: {relation}"
            )

        if relation in {"SPECIALIZES", "ALTERNATIVE"}:
            add_seed(f"FS-{group_id}", [group_id], members, relation)

        elif relation == "CUMULATIVE":
            for index, member in enumerate(
                sorted({m for m in members if m in eligible_ids}),
                1,
            ):
                add_seed(
                    f"FS-{group_id}-{index}",
                    [group_id],
                    [member],
                    "CUMULATIVE_MEMBER",
                )

        # SUPPORTS_SAME_CRITERION and CONTEXT_ONLY intentionally do not create
        # independent evidence facets.

    if not seeds:
        add_seed(
            "FS-FALLBACK",
            [],
            sorted(eligible_ids),
            "FALLBACK_SAME_CRITERION",
        )

    unique = []
    seen_specializes_members = set()
    for seed in seeds:
        member_key = tuple(seed["basis_candidate_ids"])
        if (
            seed["seed_relation"] == "SPECIALIZES"
            and member_key in seen_specializes_members
        ):
            continue
        if seed["seed_relation"] == "SPECIALIZES":
            seen_specializes_members.add(member_key)
        unique.append(seed)

    return unique


def make_evidence_requirement_prompt(
    cp: dict,
    contract: dict,
    ledger_artifact: dict,
    rule_set_relation: dict,
) -> str:
    candidates, decisions = _candidate_maps(ledger_artifact)

    eligible = []
    for candidate_id, decision in decisions.items():
        if not (
            decision.get("selected")
            and decision.get("relation") == "PRIMARY_NORM"
            and decision.get("contract_eligible", False)
        ):
            continue

        candidate = candidates.get(candidate_id)
        if not candidate:
            continue

        eligible.append(
            {
                "candidate_id": candidate_id,
                "citation": candidate.get("citation", ""),
                "unit_type": candidate.get("unit_type", ""),
                "own_text": candidate.get("own_text", candidate.get("text", "")),
                "legal_basis_relation": decision.get("legal_basis_relation"),
                "legal_basis_cp_quote": decision.get("legal_basis_cp_quote", ""),
                "legal_basis_policy_quote": decision.get("legal_basis_policy_quote", ""),
            }
        )

    eligible.sort(key=lambda x: x["candidate_id"])
    eligible_ids = {x["candidate_id"] for x in eligible}

    atoms = normalize_contract_atoms(contract)
    if len(atoms) != 1:
        raise ValueError(
            "Minimal evidence-facet compiler currently expects exactly one "
            f"frozen Core atom; discovered {sorted(atoms)}"
        )
    atom_id = next(iter(atoms))

    facet_seeds = build_evidence_facet_seeds(
        rule_set_relation,
        eligible_ids,
        atom_id=atom_id,
    )

    if "normalized_contract_view" in globals():
        frozen_contract = normalized_contract_view(contract)
    else:
        frozen_contract = {
            "atoms": atoms,
            "satisfaction": contract.get("satisfaction"),
            "logic_basis": contract.get("logic_basis", []),
        }

    payload = {
        "official_cp": {
            "cp_id": cp["cp_id"],
            "subelement": cp.get("subelement", ""),
            "criterion": cp["criterion"],
        },
        "frozen_contract": frozen_contract,
        "eligible_primary_norms": eligible,
        "validated_rule_set_relation": rule_set_relation,
        "facet_seeds": facet_seeds,
    }

    return (
        "INPUT_JSON:\\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\\n\\nCompile exactly one DECISIVE EvidenceRequirement per FACET_SEED. "
        + "Return JSON only."
    )


def validate_evidence_requirements(
    raw: dict,
    cp: dict,
    contract: dict,
    ledger_artifact: dict,
    rule_set_relation: dict,
) -> dict:
    requirements = raw.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("EvidenceRequirement compiler returned no requirements list")

    atoms = normalize_contract_atoms(contract)
    if len(atoms) != 1:
        raise ValueError(
            "Minimal validator expects exactly one frozen Core atom; "
            f"discovered {sorted(atoms)}"
        )
    atom_id = next(iter(atoms))

    candidates, decisions = _candidate_maps(ledger_artifact)
    eligible_ids = {
        candidate_id
        for candidate_id, decision in decisions.items()
        if (
            decision.get("selected")
            and decision.get("relation") == "PRIMARY_NORM"
            and decision.get("contract_eligible", False)
        )
    }

    facet_seeds = build_evidence_facet_seeds(
        rule_set_relation,
        eligible_ids,
        atom_id=atom_id,
    )
    seed_map = {seed["facet_seed_id"]: seed for seed in facet_seeds}

    group_ids = {
        group.get("group_id")
        for group in rule_set_relation.get("groups", [])
        if isinstance(group, dict) and group.get("group_id")
    }

    validated = []
    seen_ids = set()
    seen_seed_ids = set()
    seen_semantics = set()

    for item in requirements:
        if not isinstance(item, dict):
            raise ValueError("EvidenceRequirement item must be an object")

        requirement_id = str(item.get("requirement_id", "")).strip()
        if not requirement_id or not _SAFE_ID_RE.fullmatch(requirement_id):
            raise ValueError(f"Invalid requirement_id: {requirement_id!r}")
        if requirement_id in seen_ids:
            raise ValueError(f"Duplicate requirement_id: {requirement_id}")
        seen_ids.add(requirement_id)

        facet_seed_id = str(item.get("facet_seed_id", "")).strip()
        if facet_seed_id not in seed_map:
            raise ValueError(
                f"{requirement_id}: unknown facet_seed_id {facet_seed_id!r}"
            )
        if facet_seed_id in seen_seed_ids:
            raise ValueError(
                f"{requirement_id}: duplicate requirement for seed {facet_seed_id}"
            )
        seen_seed_ids.add(facet_seed_id)
        seed = seed_map[facet_seed_id]

        returned_atom_id = str(item.get("atom_id", "")).strip()
        if returned_atom_id != seed["atom_id"]:
            raise ValueError(
                f"{requirement_id}: atom_id {returned_atom_id!r} does not match "
                f"seed atom {seed['atom_id']!r}"
            )

        proposition = str(item.get("proposition_to_establish", "")).strip()
        if not proposition:
            raise ValueError(f"{requirement_id}: empty proposition_to_establish")
        semantic_key = normalize_ws(proposition).lower()
        if semantic_key in seen_semantics:
            raise ValueError(f"{requirement_id}: duplicate semantic evidence facet")
        seen_semantics.add(semantic_key)

        if (
            len(facet_seeds) > 1
            and semantic_key == normalize_ws(cp["criterion"]).lower()
        ):
            raise ValueError(
                f"{requirement_id}: UNDER_FACTORIZED_EVIDENCE_PLAN: multiple "
                "deterministic facet seeds exist but requirement merely repeats "
                "the whole CP criterion."
            )

        if item.get("polarity") != "SUPPORT":
            raise ValueError(
                f"{requirement_id}: minimal D2.8 catalog expects polarity=SUPPORT"
            )
        if item.get("decisiveness") != "DECISIVE":
            raise ValueError(
                f"{requirement_id}: each deterministic facet seed must compile "
                "to one DECISIVE EvidenceRequirement"
            )

        criterion_quote = str(item.get("criterion_quote", "")).strip()
        criterion_mode = quote_match_mode(criterion_quote, cp["criterion"])
        if criterion_mode is None:
            raise ValueError(f"{requirement_id}: criterion_quote is not grounded")

        basis_candidate_ids = [str(x) for x in item.get("basis_candidate_ids", [])]
        if not basis_candidate_ids:
            raise ValueError(f"{requirement_id}: decisive requirement has no legal basis")
        seed_candidates = set(seed["basis_candidate_ids"])
        outside_seed = set(basis_candidate_ids) - seed_candidates
        if outside_seed:
            raise ValueError(
                f"{requirement_id}: basis candidates outside {facet_seed_id}: "
                f"{sorted(outside_seed)}"
            )

        source_group_ids = [str(x) for x in item.get("source_group_ids", [])]
        unknown_groups = set(source_group_ids) - group_ids
        if unknown_groups:
            raise ValueError(
                f"{requirement_id}: unknown RuleSetRelation groups "
                f"{sorted(unknown_groups)}"
            )
        required_groups = set(seed["source_group_ids"])
        if not required_groups.issubset(set(source_group_ids)):
            raise ValueError(
                f"{requirement_id}: missing seed source groups "
                f"{sorted(required_groups)}"
            )

        query_sources = item.get("query_sources", [])
        if not isinstance(query_sources, list) or not query_sources:
            raise ValueError(f"{requirement_id}: query_sources must be non-empty")

        validated_query_sources = []
        for source in query_sources:
            if not isinstance(source, dict):
                raise ValueError(f"{requirement_id}: invalid query source")

            source_type = source.get("source")
            candidate_id = source.get("candidate_id")
            quote = str(source.get("quote", "")).strip()
            if not quote:
                raise ValueError(f"{requirement_id}: empty query source quote")

            if source_type == "CP":
                if quote_match_mode(quote, cp["criterion"]) is None:
                    raise ValueError(f"{requirement_id}: ungrounded CP query quote")
                candidate_id = None

            elif source_type == "RULES":
                if candidate_id not in seed_candidates:
                    raise ValueError(
                        f"{requirement_id}: Rules query candidate {candidate_id} "
                        f"lies outside facet seed {facet_seed_id}"
                    )
                candidate = candidates.get(candidate_id)
                if not candidate:
                    raise ValueError(
                        f"{requirement_id}: unknown Rules query candidate {candidate_id}"
                    )
                own_text = candidate.get("own_text", candidate.get("text", ""))
                if quote_match_mode(quote, own_text) is None:
                    raise ValueError(
                        f"{requirement_id}: Rules query quote not grounded in {candidate_id}"
                    )
            else:
                raise ValueError(
                    f"{requirement_id}: invalid query source {source_type}"
                )

            validated_query_sources.append(
                {
                    "source": source_type,
                    "candidate_id": candidate_id,
                    "quote": quote,
                }
            )

        validated.append(
            {
                "requirement_id": requirement_id,
                "facet_seed_id": facet_seed_id,
                "atom_id": seed["atom_id"],
                "proposition_to_establish": proposition,
                "polarity": "SUPPORT",
                "decisiveness": "DECISIVE",
                "criterion_quote": criterion_quote,
                "criterion_match_mode": criterion_mode,
                "basis_candidate_ids": basis_candidate_ids,
                "source_group_ids": source_group_ids,
                "seed_relation": seed["seed_relation"],
                "query_sources": validated_query_sources,
                "reason": str(item.get("reason", "")),
            }
        )

    missing_seeds = set(seed_map) - seen_seed_ids
    if missing_seeds:
        raise ValueError(
            "EvidenceRequirement compiler omitted deterministic facet seeds: "
            f"{sorted(missing_seeds)}"
        )
    if len(validated) != len(facet_seeds):
        raise ValueError(
            "EvidenceRequirement count must exactly equal deterministic facet "
            f"seed count: {len(validated)} != {len(facet_seeds)}"
        )

    return {
        "schema": "freca-core-evidence-requirements-v2.1",
        "cp_id": cp["cp_id"],
        "facet_seeds": facet_seeds,
        "requirements": validated,
        "pilot_only": True,
        "notes": [
            "Multiple EvidenceRequirements may attach to one frozen atom.",
            "EvidenceRequirements are proof/coverage facets, not additional contract logic leaves.",
            "Facet seeds are derived deterministically from validated RuleSetRelation.",
            "Full D2.8 typed entity/time/cardinality fields remain deferred in Core pilot.",
        ],
    }


def compile_evidence_requirements(cp_id: str, *, force: bool = False) -> dict:
    cp = core.get_cp(cp_id)
    cp_id = cp["cp_id"]

    plan_path = CONTRACT_DIR / f"{cp_id}_evidence_requirements.json"
    if plan_path.exists() and not force:
        return load_json(plan_path)

    contract = load_json(CONTRACT_DIR / f"{cp_id}.json")
    ledger = load_json(CONTRACT_DIR / f"{cp_id}_candidate_ledger.json")
    relation = load_json(CONTRACT_DIR / f"{cp_id}_rule_set_relation.json")

    raw = core.deepseek_json(
        model=EVIDENCE_PLAN_MODEL,
        system_prompt=EVIDENCE_REQUIREMENT_SYSTEM,
        user_prompt=make_evidence_requirement_prompt(cp, contract, ledger, relation),
        thinking=False,
        max_tokens=6000,
    )

    validated = validate_evidence_requirements(raw, cp, contract, ledger, relation)
    save_json(validated, plan_path)
    return validated


# ============================================================================
# D7.1 — bidirectional RetrievalNeed
# ============================================================================


def build_retrieval_needs(plan: dict) -> list[dict]:
    needs = []

    for requirement in plan["requirements"]:
        query_facets = []
        seen = set()
        for source in requirement["query_sources"]:
            facet = normalize_ws(source["quote"])
            if facet and facet not in seen:
                seen.add(facet)
                query_facets.append(facet)

        # Keep proposition display text as a final deterministic facet. It was
        # compiled before case access and cannot encode a case label.
        proposition = normalize_ws(requirement["proposition_to_establish"])
        if proposition and proposition not in seen:
            query_facets.append(proposition)

        for direction in ("SUPPORT", "ATTACK"):
            needs.append(
                {
                    "need_id": f"{requirement['requirement_id']}.{direction.lower()}",
                    "requirement_id": requirement["requirement_id"],
                    "atom_id": requirement["atom_id"],
                    "direction": direction,
                    "priority_class": (
                        "DECISIVE"
                        if requirement["decisiveness"] == "DECISIVE"
                        else "NON_DECISIVE"
                    ),
                    "query_facets": query_facets,
                    "coverage_requirement": "CANDIDATE_DISCOVERY",
                }
            )

    return needs


def _build_query(facets: list[str]) -> str:
    seen = []
    normalized_seen = set()
    for facet in facets:
        clean = normalize_ws(facet)
        key = clean.lower()
        if clean and key not in normalized_seen:
            normalized_seen.add(key)
            seen.append(clean)
    return " ".join(seen)


def retrieve_requirement_candidates(
    evidence_chunks: list[dict],
    needs: list[dict],
    *,
    top_k: int = 12,
) -> list[dict]:
    # Normalise once so the function works with the current Core chunk format
    # and with the older reference-core EvidenceAtom-like dict format.
    docs = []
    by_id = {}
    for chunk in evidence_chunks:
        cid = chunk_id(chunk)
        text = chunk_text(chunk)
        item = dict(chunk)
        item["id"] = cid
        item["text"] = text
        docs.append(item)
        by_id[cid] = item

    cache: dict[str, list[dict]] = {}
    traces = []

    for need in needs:
        query = _build_query(need["query_facets"])

        # SUPPORT/ATTACK intentionally share the same source-grounded query in
        # the minimal Core. Direction is resolved by the alignment classifier,
        # not by injecting hand-written positive/negative keywords.
        cache_key = need["requirement_id"] + "\n" + query
        if cache_key not in cache:
            cache[cache_key] = core.bm25_rank(query, docs, top_k)

        ranking = cache[cache_key]
        traces.append(
            {
                **need,
                "query": query,
                "top_k": top_k,
                "candidate_count_checked_by_model": len(ranking),
                "retrieval_scan_chunk_count": len(docs),
                "coverage_status": "TOP_K_SEMANTIC_ALIGNMENT_PILOT",
                "candidates": [
                    {
                        "evidence_id": item["id"],
                        "score": item.get("score"),
                        "text": item["text"],
                    }
                    for item in ranking
                ],
            }
        )

    return traces


# ============================================================================
# D7.8 — requirement-level evidence alignment
# ============================================================================


ALIGNMENT_RELATIONS = {"SUPPORT", "ATTACK", "IRRELEVANT", "AMBIGUOUS"}
PROOF_ROLES = {
    "DIRECT_SUPPORT",
    "CORROBORATION_ONLY",
    "EXPLICIT_VIOLATION",
    "CONTEXT_ONLY",
    "AMBIGUOUS",
}


EVIDENCE_ALIGNMENT_SYSTEM = r"""
You are a closed-source evidence-to-requirement alignment classifier.

You receive ONE frozen EvidenceRequirement and ONE exact case-evidence chunk.
Use only those supplied records.

Do NOT decide overall CP compliance, applicability, sufficiency, credibility,
or a final 1/0/N/A value.
Do NOT infer facts not stated in the evidence.
Do NOT use outside knowledge or other cases.

Classify semantic relation:

SUPPORT
    The evidence positively bears on the supplied requirement.

ATTACK
    The evidence explicitly contradicts or materially undermines the supplied
    requirement.

IRRELEVANT
    The evidence does not materially bear on the requirement.

AMBIGUOUS
    The relation depends on an unstated assumption, unclear scope/time/entity,
    or mixed interpretation.

Also classify proof_role:

DIRECT_SUPPORT
    The evidence directly establishes the required observable state, control,
    action, design feature, maintenance condition, or other requirement itself.

CORROBORATION_ONLY
    The evidence is consistent with or provides an outcome/status signal about
    the requirement, but does not by itself establish that the required control,
    design, maintenance, process, or condition exists and is adequate.

EXPLICIT_VIOLATION
    The evidence explicitly states a defect, contrary condition, failure, gap,
    breach, or other fact that directly contradicts a DECISIVE requirement.

CONTEXT_ONLY
    Relevant background but not direct proof.

AMBIGUOUS
    Cannot safely assign one of the above roles.

GENERAL RULE:
Absence of an adverse outcome does not by itself prove that a preventive design,
maintenance regime, control, process, or safeguard exists or is adequate. Such
an outcome is normally CORROBORATION_ONLY unless the requirement itself is
specifically the absence of that outcome.

Likewise, a plan or generic policy statement is not automatically proof that a
specific physical condition or control was actually present when the requirement
asks about the actual condition.

For relation=SUPPORT, proof_role must be DIRECT_SUPPORT or CORROBORATION_ONLY.
For relation=ATTACK, proof_role must be EXPLICIT_VIOLATION or AMBIGUOUS.
For relation=IRRELEVANT, proof_role must be CONTEXT_ONLY.
For relation=AMBIGUOUS, proof_role must be AMBIGUOUS.

Return JSON only. For every supplied requirement/evidence pair return exactly one object:

{
  "alignments": [
    {
      "requirement_id": "ER1",
      "evidence_id": "...",
      "relation": "SUPPORT|ATTACK|IRRELEVANT|AMBIGUOUS",
      "proof_role": "DIRECT_SUPPORT|CORROBORATION_ONLY|EXPLICIT_VIOLATION|CONTEXT_ONLY|AMBIGUOUS",
      "exact_quote": "exact substring from evidence text",
      "reason_code": "EXPLICIT_MATCH|OUTCOME_CORROBORATION|EXPLICIT_CONTRADICTION|CONTEXT_ONLY|SCOPE_DEPENDENT|AMBIGUOUS_SEMANTICS",
      "reason": "brief explanation"
    }
  ]
}
"""


def _alignment_pairs(plan: dict, traces: list[dict]) -> list[dict]:
    requirements = {item["requirement_id"]: item for item in plan["requirements"]}

    # Both directional needs may return the same evidence; model semantic
    # relation is direction-independent, so classify each requirement/evidence
    # pair only once while preserving the originating need IDs.
    pair_map: dict[tuple[str, str], dict] = {}

    for trace in traces:
        requirement = requirements[trace["requirement_id"]]
        for candidate in trace["candidates"]:
            key = (trace["requirement_id"], candidate["evidence_id"])
            if key not in pair_map:
                pair_map[key] = {
                    "requirement": requirement,
                    "evidence_id": candidate["evidence_id"],
                    "evidence_text": candidate["text"],
                    "retrieval_need_ids": [],
                    "best_retrieval_score": candidate.get("score"),
                }
            pair_map[key]["retrieval_need_ids"].append(trace["need_id"])

    return sorted(
        pair_map.values(),
        key=lambda x: (x["requirement"]["requirement_id"], x["evidence_id"]),
    )


def make_alignment_batch_prompt(pairs: list[dict]) -> str:
    payload = []
    for pair in pairs:
        payload.append(
            {
                "requirement": {
                    "requirement_id": pair["requirement"]["requirement_id"],
                    "atom_id": pair["requirement"]["atom_id"],
                    "proposition_to_establish": pair["requirement"]["proposition_to_establish"],
                    "decisiveness": pair["requirement"]["decisiveness"],
                    "criterion_quote": pair["requirement"]["criterion_quote"],
                },
                "evidence": {
                    "evidence_id": pair["evidence_id"],
                    "text": pair["evidence_text"],
                },
            }
        )

    return (
        json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nReturn JSON as {\"alignments\": [one object for each supplied pair]}."
    )


def validate_alignment(raw: dict, pair: dict) -> dict:
    requirement_id = pair["requirement"]["requirement_id"]
    evidence_id = pair["evidence_id"]

    if raw.get("requirement_id") != requirement_id:
        raise ValueError(
            f"Alignment returned wrong requirement_id: {raw.get('requirement_id')} != {requirement_id}"
        )
    if raw.get("evidence_id") != evidence_id:
        raise ValueError(
            f"Alignment returned wrong evidence_id: {raw.get('evidence_id')} != {evidence_id}"
        )

    relation = raw.get("relation")
    proof_role = raw.get("proof_role")
    if relation not in ALIGNMENT_RELATIONS:
        raise ValueError(f"Invalid alignment relation {relation}")
    if proof_role not in PROOF_ROLES:
        raise ValueError(f"Invalid proof_role {proof_role}")

    allowed_role = {
        "SUPPORT": {"DIRECT_SUPPORT", "CORROBORATION_ONLY"},
        "ATTACK": {"EXPLICIT_VIOLATION", "AMBIGUOUS"},
        "IRRELEVANT": {"CONTEXT_ONLY"},
        "AMBIGUOUS": {"AMBIGUOUS"},
    }[relation]
    if proof_role not in allowed_role:
        raise ValueError(
            f"Inconsistent relation/proof_role: {relation}/{proof_role}"
        )

    exact_quote = str(raw.get("exact_quote", "")).strip()
    if relation != "IRRELEVANT":
        match_mode = quote_match_mode(exact_quote, pair["evidence_text"])
        if match_mode is None:
            raise ValueError(
                f"{requirement_id}/{evidence_id}: exact_quote is not grounded"
            )
    else:
        match_mode = (
            quote_match_mode(exact_quote, pair["evidence_text"])
            if exact_quote
            else None
        )

    return {
        "requirement_id": requirement_id,
        "atom_id": pair["requirement"]["atom_id"],
        "decisiveness": pair["requirement"]["decisiveness"],
        "evidence_id": evidence_id,
        "retrieval_need_ids": pair["retrieval_need_ids"],
        "relation": relation,
        "proof_role": proof_role,
        "exact_quote": exact_quote,
        "quote_match_mode": match_mode,
        "reason_code": str(raw.get("reason_code", "")),
        "reason": str(raw.get("reason", "")),
        "accepted_for_proof": relation in {"SUPPORT", "ATTACK"} and proof_role != "AMBIGUOUS",
    }


def _ambiguous_alignment(pair: dict, reason: str) -> dict:
    return {
        "requirement_id": pair["requirement"]["requirement_id"],
        "atom_id": pair["requirement"]["atom_id"],
        "decisiveness": pair["requirement"]["decisiveness"],
        "evidence_id": pair["evidence_id"],
        "retrieval_need_ids": pair["retrieval_need_ids"],
        "relation": "AMBIGUOUS",
        "proof_role": "AMBIGUOUS",
        "exact_quote": "",
        "quote_match_mode": None,
        "reason_code": "ALIGNMENT_VALIDATION_FAILED",
        "reason": reason,
        "accepted_for_proof": False,
    }


def align_requirement_evidence(
    plan: dict,
    traces: list[dict],
    *,
    batch_size: int = 8,
    max_pairs: int | None = None,
) -> list[dict]:
    pairs = _alignment_pairs(plan, traces)
    if max_pairs is not None:
        pairs = pairs[:max_pairs]

    alignments = []
    total_batches = (len(pairs) + batch_size - 1) // batch_size

    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        batch_no = start // batch_size + 1
        print(
            f"    alignment batch {batch_no}/{total_batches}: "
            f"{len(batch)} requirement/evidence pairs"
        )

        raw = core.deepseek_json(
            model=EVIDENCE_ALIGN_MODEL,
            system_prompt=EVIDENCE_ALIGNMENT_SYSTEM,
            user_prompt=make_alignment_batch_prompt(batch),
            thinking=False,
            max_tokens=4500,
        )

        returned = raw.get("alignments")
        if not isinstance(returned, list):
            for pair in batch:
                alignments.append(
                    _ambiguous_alignment(pair, "MODEL_RETURNED_NO_ALIGNMENTS_LIST")
                )
            continue

        returned_map = {}
        for item in returned:
            if not isinstance(item, dict):
                continue
            key = (item.get("requirement_id"), item.get("evidence_id"))
            if key not in returned_map:
                returned_map[key] = item

        for pair in batch:
            key = (pair["requirement"]["requirement_id"], pair["evidence_id"])
            item = returned_map.get(key)
            if item is None:
                alignments.append(
                    _ambiguous_alignment(pair, "MODEL_OMITTED_PAIR")
                )
                continue
            try:
                alignments.append(validate_alignment(item, pair))
            except Exception as error:
                # Grounding/semantic validation failure is preserved as ambiguous;
                # do not loop the same model until it says what we want.
                alignments.append(_ambiguous_alignment(pair, str(error)))

    return alignments


# ============================================================================
# D7.14 — minimal typed ProofGate
# ============================================================================


def _state_from_pair(support: bool, attack: bool) -> str:
    return {
        (False, False): "UNKNOWN",
        (True, False): "TRUE",
        (False, True): "FALSE",
        (True, True): "BOTH",
    }[(support, attack)]


def evaluate_minimal_proof_gate(
    plan: dict,
    traces: list[dict],
    alignments: list[dict],
) -> dict:
    traces_by_requirement: dict[str, list[dict]] = {}
    for trace in traces:
        traces_by_requirement.setdefault(trace["requirement_id"], []).append(trace)

    alignments_by_requirement: dict[str, list[dict]] = {}
    for alignment in alignments:
        alignments_by_requirement.setdefault(alignment["requirement_id"], []).append(alignment)

    requirement_reports = []
    decisive_reports = []

    for requirement in plan["requirements"]:
        rid = requirement["requirement_id"]
        rows = alignments_by_requirement.get(rid, [])

        direct_support = [
            row
            for row in rows
            if row.get("accepted_for_proof")
            and row.get("relation") == "SUPPORT"
            and row.get("proof_role") == "DIRECT_SUPPORT"
        ]
        corroboration = [
            row
            for row in rows
            if row.get("accepted_for_proof")
            and row.get("relation") == "SUPPORT"
            and row.get("proof_role") == "CORROBORATION_ONLY"
        ]
        explicit_violation = [
            row
            for row in rows
            if row.get("accepted_for_proof")
            and row.get("relation") == "ATTACK"
            and row.get("proof_role") == "EXPLICIT_VIOLATION"
        ]
        ambiguous = [row for row in rows if row.get("relation") == "AMBIGUOUS"]

        raw_support = bool(direct_support)
        raw_attack = bool(explicit_violation)
        raw_state = _state_from_pair(raw_support, raw_attack)

        # Minimal D7.14 policy:
        # - explicit contradiction on a DECISIVE evidence facet blocks the
        #   positive AUDIT_SUFFICIENT proof for that facet;
        # - the same contradiction independently passes the EXPLICIT_VIOLATION
        #   gate;
        # - corroboration never upgrades a missing direct support into success.
        if requirement["decisiveness"] == "DECISIVE":
            contradiction_state = "BLOCKING" if explicit_violation else "NONE"

            if explicit_violation:
                accepted_state = "FALSE"
                audit_sufficient_pass = False
                explicit_violation_pass = True
            elif direct_support:
                accepted_state = "TRUE"
                audit_sufficient_pass = True
                explicit_violation_pass = False
            else:
                accepted_state = "UNKNOWN"
                audit_sufficient_pass = False
                explicit_violation_pass = False
        else:
            contradiction_state = "PRESERVED" if explicit_violation else "NONE"
            accepted_state = "UNKNOWN"
            audit_sufficient_pass = False
            explicit_violation_pass = False

        requirement_traces = traces_by_requirement.get(rid, [])
        support_need_present = any(t["direction"] == "SUPPORT" for t in requirement_traces)
        attack_need_present = any(t["direction"] == "ATTACK" for t in requirement_traces)

        report = {
            "requirement_id": rid,
            "atom_id": requirement["atom_id"],
            "decisiveness": requirement["decisiveness"],
            "raw_state": raw_state,
            "accepted_state": accepted_state,
            "semantic_support_pass": audit_sufficient_pass,
            "audit_sufficient_pass": False,
            "explicit_violation_pass": explicit_violation_pass,
            "contradiction_state": contradiction_state,
            "support_need_present": support_need_present,
            "attack_need_present": attack_need_present,
            "coverage_status": "TOP_K_SEMANTIC_ALIGNMENT_PILOT",
            "coverage_pass": False,
            "direct_support_evidence_ids": [row["evidence_id"] for row in direct_support],
            "corroboration_evidence_ids": [row["evidence_id"] for row in corroboration],
            "explicit_violation_evidence_ids": [
                row["evidence_id"] for row in explicit_violation
            ],
            "ambiguous_evidence_ids": [row["evidence_id"] for row in ambiguous],
        }

        requirement_reports.append(report)
        if requirement["decisiveness"] == "DECISIVE":
            decisive_reports.append(report)

    if not decisive_reports:
        raise ValueError("No decisive EvidenceRequirement reports")

    satisfaction_support = all(
        report["semantic_support_pass"]
        for report in decisive_reports
    )
    satisfaction_attack = any(
        report["explicit_violation_pass"]
        for report in decisive_reports
    )

    # Accepted satisfaction state uses the proof gate, not the raw evidence pair.
    # If a decisive explicit violation exists, positive satisfaction proof is
    # blocked even if other documents provide positive support.
    if satisfaction_attack:
        satisfaction_state = "FALSE"
    elif satisfaction_support:
        satisfaction_state = "TRUE"
    else:
        satisfaction_state = "UNKNOWN"

    violation_state = (
        "TRUE"
        if any(report["explicit_violation_pass"] for report in decisive_reports)
        else "UNKNOWN"
    )

    if violation_state == "TRUE":
        candidate_outcome = "VIOLATED"
        candidate_submission_label = 0
    elif satisfaction_state == "TRUE":
        candidate_outcome = "SATISFIED"
        candidate_submission_label = 1
    else:
        candidate_outcome = "UNKNOWN"
        candidate_submission_label = None

    # Full D7 coverage/rebuttal/proof locking is deliberately not claimed by
    # this pilot adapter.  Keep the candidate signal separate from a locked
    # production outcome.
    internal_outcome = "UNKNOWN"
    submission_label = None

    return {
        "schema": "freca-core-minimal-proof-gate-v2",
        "pilot_only": True,
        "coverage_complete": False,
        "coverage_note": (
            "All evidence chunks were available to lexical ranking, but only top-k "
            "candidates were semantically aligned. This is a pilot proof state, "
            "not full D7 coverage completion."
        ),
        "requirement_reports": requirement_reports,
        "satisfaction_state": satisfaction_state,
        "violation_state": violation_state,
        "candidate_outcome": candidate_outcome,
        "candidate_submission_label": candidate_submission_label,
        "evaluation_locked": False,
        "internal_outcome": internal_outcome,
        "submission_label": submission_label,
    }


# ============================================================================
# One-call adapter for the existing Core evidence_chunks
# ============================================================================


def run_requirement_reasoning(
    *,
    cp_id: str,
    case_id: str,
    evidence_chunks: list[dict],
    retrieval_top_k: int = 12,
    force_plan_recompile: bool = False,
) -> dict:
    plan = compile_evidence_requirements(cp_id, force=force_plan_recompile)
    needs = build_retrieval_needs(plan)
    traces = retrieve_requirement_candidates(
        evidence_chunks,
        needs,
        top_k=retrieval_top_k,
    )
    alignments = align_requirement_evidence(plan, traces)
    proof = evaluate_minimal_proof_gate(plan, traces, alignments)

    result = {
        "schema": "freca-core-requirement-reasoning-v2",
        "cp_id": cp_id,
        "case_id": case_id,
        "evidence_requirement_plan": plan,
        "retrieval_needs": needs,
        "retrieval_traces": traces,
        "alignments": alignments,
        "proof_gate": proof,
    }

    output_path = RESULT_DIR / f"{case_id}_{cp_id}_requirement_reasoning_v2.json"
    save_json(result, output_path)
    result["saved_path"] = str(output_path)
    return result


# ============================================================================
# Adapter for the existing freca_core_v1.evaluate_case locals
# ============================================================================


def run_from_evaluate_locals(
    local_vars: dict[str, Any],
    *,
    retrieval_top_k: int = 12,
) -> dict:
    """Bridge into the existing V1 evaluator without depending on exact local names.

    Call this *inside* evaluate_case after evidence parsing:

        from evidence_reasoning_v2 import run_from_evaluate_locals, print_requirement_result
        rr = run_from_evaluate_locals(locals())
        print_requirement_result(rr)

    Local import is intentional: evidence_reasoning_v2 imports freca_core_v1, so
    importing it at freca_core_v1 module top-level would create a circular import.
    """

    evidence_chunks = (
        local_vars.get("all_evidence")
        or local_vars.get("evidence_chunks")
        or local_vars.get("chunks")
        or local_vars.get("evidence")
    )
    if not isinstance(evidence_chunks, list):
        raise RuntimeError(
            "Could not locate parsed evidence chunk list in evaluate_case locals. "
            f"Available keys: {sorted(local_vars)}"
        )

    cp_id = local_vars.get("cp_id")
    if not cp_id:
        cp = local_vars.get("cp")
        if isinstance(cp, dict):
            cp_id = cp.get("cp_id") or cp.get("id")
    if not cp_id:
        contract = local_vars.get("contract")
        if isinstance(contract, dict):
            cp_id = contract.get("cp_id")
    if not cp_id:
        raise RuntimeError(
            "Could not locate cp_id in evaluate_case locals. "
            f"Available keys: {sorted(local_vars)}"
        )

    case_id = local_vars.get("case_id")
    if not case_id:
        case_dir = local_vars.get("case_dir")
        if case_dir is not None:
            case_id = Path(case_dir).name
    if not case_id:
        for key in ("case_query", "case_name", "case"):
            value = local_vars.get(key)
            if isinstance(value, str) and value.strip():
                case_id = Path(value).name
                break
    if not case_id:
        raise RuntimeError(
            "Could not locate case_id/case_dir in evaluate_case locals. "
            f"Available keys: {sorted(local_vars)}"
        )

    return run_requirement_reasoning(
        cp_id=str(cp_id),
        case_id=str(case_id),
        evidence_chunks=evidence_chunks,
        retrieval_top_k=retrieval_top_k,
    )


# ============================================================================
# Console summary helper
# ============================================================================


def print_requirement_result(result: dict) -> None:
    print("\n" + "=" * 72)
    print("REQUIREMENT-LEVEL EVIDENCE REASONING — PILOT")
    print("=" * 72)

    for report in result["proof_gate"]["requirement_reports"]:
        print(
            f"\n{report['requirement_id']} "
            f"[{report['decisiveness']}] "
            f"raw={report['raw_state']} "
            f"accepted={report['accepted_state']}"
        )
        if report["direct_support_evidence_ids"]:
            print("  DIRECT SUPPORT:")
            for evidence_id in report["direct_support_evidence_ids"]:
                print(f"    + {evidence_id}")
        if report["corroboration_evidence_ids"]:
            print("  CORROBORATION:")
            for evidence_id in report["corroboration_evidence_ids"]:
                print(f"    ~ {evidence_id}")
        if report["explicit_violation_evidence_ids"]:
            print("  EXPLICIT VIOLATION:")
            for evidence_id in report["explicit_violation_evidence_ids"]:
                print(f"    - {evidence_id}")
        if report["ambiguous_evidence_ids"]:
            print("  AMBIGUOUS:")
            for evidence_id in report["ambiguous_evidence_ids"]:
                print(f"    ? {evidence_id}")

    proof = result["proof_gate"]
    print("\n" + "-" * 72)
    print("Satisfaction proof :", proof["satisfaction_state"])
    print("Violation proof    :", proof["violation_state"])
    print("Candidate outcome  :", proof["candidate_outcome"])
    print("Candidate label    :", proof["candidate_submission_label"])
    print("Locked outcome     :", proof["internal_outcome"])
    print("Evaluation locked  :", proof["evaluation_locked"])
    print("Coverage complete  :", proof["coverage_complete"])
    print("Saved              :", result.get("saved_path", ""))
    print("-" * 72)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Minimal D2.8 + D7.1 + D7.8 + D7.14 evidence reasoning adapter"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_compile = sub.add_parser("compile-plan")
    p_compile.add_argument("--cp", required=True)
    p_compile.add_argument("--force", action="store_true")

    p_show = sub.add_parser("show-plan")
    p_show.add_argument("--cp", required=True)

    args = parser.parse_args()

    if args.command == "compile-plan":
        result = compile_evidence_requirements(args.cp, force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "show-plan":
        path = CONTRACT_DIR / f"{args.cp}_evidence_requirements.json"
        print(path.read_text(encoding="utf-8"))
