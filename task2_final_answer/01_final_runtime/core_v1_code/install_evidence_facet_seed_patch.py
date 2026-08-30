from pathlib import Path

p = Path("evidence_reasoning_v2.py")
s = p.read_text(encoding="utf-8")


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"Start marker not found: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"End marker not found: {end_marker!r}")
    return text[:start] + replacement + text[end:]


system_start = 'EVIDENCE_REQUIREMENT_SYSTEM = r"""'
system_end = '\n\n\ndef _candidate_maps'

new_system = '''EVIDENCE_REQUIREMENT_SYSTEM = r"""
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
'''

s = replace_between(s, system_start, system_end, new_system)

insert_marker = "def make_evidence_requirement_prompt(\n"
seed_builder = '''def build_evidence_facet_seeds(
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


'''

if "def build_evidence_facet_seeds(" not in s:
    idx = s.find(insert_marker)
    if idx < 0:
        raise SystemExit("Could not find make_evidence_requirement_prompt()")
    s = s[:idx] + seed_builder + s[idx:]

prompt_start = "def make_evidence_requirement_prompt(\n"
prompt_end = "\n\ndef validate_evidence_requirements("
new_prompt_func = '''def make_evidence_requirement_prompt(
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
'''

s = replace_between(s, prompt_start, prompt_end, new_prompt_func)

validator_start = "def validate_evidence_requirements(\n"
validator_end = "\n\ndef compile_evidence_requirements("
new_validator = '''def validate_evidence_requirements(
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
'''

s = replace_between(s, validator_start, validator_end, new_validator)

p.write_text(s, encoding="utf-8")
print("Installed deterministic EvidenceRequirement facet-seed patch.")
