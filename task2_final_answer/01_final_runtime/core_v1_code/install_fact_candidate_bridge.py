"""Install FactCandidate/subspan fact-level alignment bridge.

Expected current Core:
- identity/admissibility gate installed;
- EvidenceNature v1.1 installed as evidence_nature_v1.py;
- SUPPORT_VARIANT_UNION_TYPED_PLUS_STRUCTURE installed.

Only these functions are replaced:
  _alignment_pairs
  make_alignment_batch_prompt
  validate_alignment
  _ambiguous_alignment
  align_requirement_evidence
"""
from __future__ import annotations

import ast
from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")
FACTS = Path("fact_candidate_v1.py")

if not TARGET.exists():
    raise SystemExit("Missing evidence_reasoning_v2.py; run from ~/freca/core_v1")
if not FACTS.exists():
    raise SystemExit("Missing fact_candidate_v1.py")

source = TARGET.read_text(encoding="utf-8")
required = (
    "SUPPORT_VARIANT_UNION_TYPED_PLUS_STRUCTURE",
    "identity_decisive_proof_eligible",
    "predicate_compatibility",
    "official_bindings",
)
missing = [x for x in required if x not in source]
if missing:
    raise SystemExit("Unexpected current Core version. Missing markers: " + ", ".join(missing))


def replace_function(src: str, name: str, replacement: str) -> str:
    tree = ast.parse(src)
    matches = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected one top-level {name}, found {len(matches)}")
    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n\n"]
    return "".join(lines)


NEW_ALIGNMENT_PAIRS = r'''
def _alignment_pairs(plan: dict, traces: list[dict]) -> list[dict]:
    from fact_candidate_v1 import build_fact_candidates

    requirements = {x["requirement_id"]: x for x in plan["requirements"]}
    pair_map = {}

    for trace in traces:
        requirement = requirements[trace["requirement_id"]]

        for candidate in trace["candidates"]:
            use_decision = candidate.get("identity_use_decision", "ADMIT_DIRECT")
            if use_decision == "EXCLUDE_SUBSTANTIVE":
                continue

            parent_id = candidate["evidence_id"]
            facts = build_fact_candidates(parent_id, candidate["text"])

            for fact in facts:
                fact_id = fact["fact_candidate_id"]
                alignment_id = parent_id + "#" + fact_id
                key = (trace["requirement_id"], fact_id)

                if key not in pair_map:
                    pair_map[key] = {
                        "requirement": requirement,
                        "evidence_id": alignment_id,
                        "parent_evidence_id": parent_id,
                        "fact_candidate_id": fact_id,
                        "fact_candidate": fact,
                        "evidence_text": fact["quote"],
                        "parent_evidence_text": candidate["text"],
                        "retrieval_need_ids": [],
                        "best_retrieval_score": candidate.get("score"),
                        "identity_relation_to_case": candidate.get(
                            "identity_relation_to_case", "CORE_SELF_EXACT"
                        ),
                        "identity_use_decision": use_decision,
                        "identity_decisive_proof_eligible": candidate.get(
                            "identity_decisive_proof_eligible",
                            use_decision == "ADMIT_DIRECT",
                        ),
                        "identity_reason_code": candidate.get(
                            "identity_reason_code", "IDENTITY_GATE_NOT_PRESENT"
                        ),
                    }

                if trace["need_id"] not in pair_map[key]["retrieval_need_ids"]:
                    pair_map[key]["retrieval_need_ids"].append(trace["need_id"])

    return sorted(
        pair_map.values(),
        key=lambda x: (
            x["requirement"]["requirement_id"],
            x["parent_evidence_id"],
            x["fact_candidate"]["quote_start"],
            x["fact_candidate_id"],
        ),
    )
'''


NEW_PROMPT = r'''
def make_alignment_batch_prompt(pairs: list[dict]) -> str:
    payload = []

    for pair in pairs:
        requirement = pair["requirement"]
        official_bindings = [
            {
                "source": source.get("source"),
                "candidate_id": source.get("candidate_id"),
                "quote": source.get("quote"),
            }
            for source in requirement.get("query_sources", [])
            if source.get("source") == "RULES"
        ]
        fact = pair["fact_candidate"]

        payload.append(
            {
                "requirement": {
                    "requirement_id": requirement["requirement_id"],
                    "facet_seed_id": requirement.get("facet_seed_id"),
                    "atom_id": requirement["atom_id"],
                    "proposition_to_establish": requirement["proposition_to_establish"],
                    "decisiveness": requirement["decisiveness"],
                    "official_bindings": official_bindings,
                },
                "scope": {
                    "identity_relation_to_case": pair.get("identity_relation_to_case"),
                    "identity_use_decision": pair.get("identity_use_decision"),
                    "identity_decisive_proof_eligible": pair.get(
                        "identity_decisive_proof_eligible"
                    ),
                    "scope_instruction": (
                        "Identity/admissibility has already been determined by a separate "
                        "deterministic gate. Do not override it. Judge only this grounded "
                        "FactCandidate against the narrowed requirement and official bindings."
                    ),
                },
                "evidence": {
                    "evidence_id": pair["evidence_id"],
                    "parent_evidence_id": pair["parent_evidence_id"],
                    "fact_candidate_id": pair["fact_candidate_id"],
                    "text": pair["evidence_text"],
                    "typed_fact": {
                        "event_type": fact.get("event_type"),
                        "polarity": fact.get("polarity"),
                        "modality": fact.get("modality"),
                        "speech_act": fact.get("speech_act"),
                        "evidence_natures": fact.get("evidence_nature", {}).get(
                            "evidence_natures", []
                        ),
                    },
                },
            }
        )

    return (
        json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nReturn JSON as {\"alignments\": [one object for each supplied pair]}. "
        + "Echo the supplied evidence_id exactly."
    )
'''


NEW_VALIDATE = r'''
def validate_alignment(raw: dict, pair: dict) -> dict:
    from evidence_nature_v1 import assess_alignment_compatibility, effective_alignment

    requirement_id = pair["requirement"]["requirement_id"]
    alignment_evidence_id = pair["evidence_id"]
    parent_evidence_id = pair["parent_evidence_id"]

    if raw.get("requirement_id") != requirement_id:
        raise ValueError("Alignment returned wrong requirement_id")
    if raw.get("evidence_id") != alignment_evidence_id:
        raise ValueError("Alignment returned wrong evidence_id")

    model_relation = raw.get("relation")
    model_proof_role = raw.get("proof_role")

    if model_relation not in ALIGNMENT_RELATIONS:
        raise ValueError(f"Invalid alignment relation {model_relation}")
    if model_proof_role not in PROOF_ROLES:
        raise ValueError(f"Invalid proof_role {model_proof_role}")

    allowed_role = {
        "SUPPORT": {"DIRECT_SUPPORT", "CORROBORATION_ONLY"},
        "ATTACK": {"EXPLICIT_VIOLATION", "AMBIGUOUS"},
        "IRRELEVANT": {"CONTEXT_ONLY"},
        "AMBIGUOUS": {"AMBIGUOUS"},
    }[model_relation]
    if model_proof_role not in allowed_role:
        raise ValueError(
            f"Inconsistent relation/proof_role: {model_relation}/{model_proof_role}"
        )

    exact_quote = str(raw.get("exact_quote", "")).strip()
    if model_relation != "IRRELEVANT":
        match_mode = quote_match_mode(exact_quote, pair["evidence_text"])
        if match_mode is None:
            raise ValueError(
                f"{requirement_id}/{alignment_evidence_id}: exact_quote is not grounded in FactCandidate"
            )
    else:
        match_mode = (
            quote_match_mode(exact_quote, pair["evidence_text"])
            if exact_quote else None
        )

    compatibility = assess_alignment_compatibility(
        pair["requirement"], exact_quote, model_relation, model_proof_role
    )
    effective = effective_alignment(
        model_relation=model_relation,
        model_proof_role=model_proof_role,
        compatibility=compatibility,
    )

    relation = effective["relation"]
    proof_role = effective["proof_role"]
    semantic_candidate = relation in {"SUPPORT", "ATTACK"} and proof_role != "AMBIGUOUS"
    identity_direct = bool(pair.get("identity_decisive_proof_eligible", True))
    typed_direct = bool(effective["typed_decisive_proof_eligible"])

    return {
        "requirement_id": requirement_id,
        "atom_id": pair["requirement"]["atom_id"],
        "decisiveness": pair["requirement"]["decisiveness"],
        "evidence_id": parent_evidence_id,
        "alignment_evidence_id": alignment_evidence_id,
        "fact_candidate_id": pair["fact_candidate_id"],
        "fact_candidate": pair["fact_candidate"],
        "retrieval_need_ids": pair["retrieval_need_ids"],
        "identity_relation_to_case": pair.get("identity_relation_to_case"),
        "identity_use_decision": pair.get("identity_use_decision"),
        "identity_decisive_proof_eligible": identity_direct,
        "identity_reason_code": pair.get("identity_reason_code"),
        "alignment_method": str(raw.get("alignment_method", "MODEL")),
        "model_relation": model_relation,
        "model_proof_role": model_proof_role,
        "relation": relation,
        "proof_role": proof_role,
        "exact_quote": exact_quote,
        "quote_match_mode": match_mode,
        "reason_code": str(raw.get("reason_code", "")),
        "reason": str(raw.get("reason", "")),
        "requirement_predicate_profile": compatibility["requirement_profile"],
        "evidence_nature": compatibility["evidence_nature"],
        "predicate_compatibility": compatibility["compatibility_decision"],
        "predicate_compatibility_reason": compatibility["compatibility_reason_code"],
        "typed_gate_enforced": compatibility["typed_gate_enforced"],
        "typed_decisive_proof_eligible": typed_direct,
        "accepted_for_proof": semantic_candidate and identity_direct and typed_direct,
    }
'''


NEW_AMBIGUOUS = r'''
def _ambiguous_alignment(pair: dict, reason: str) -> dict:
    return {
        "requirement_id": pair["requirement"]["requirement_id"],
        "atom_id": pair["requirement"]["atom_id"],
        "decisiveness": pair["requirement"]["decisiveness"],
        "evidence_id": pair.get("parent_evidence_id", pair["evidence_id"]),
        "alignment_evidence_id": pair["evidence_id"],
        "fact_candidate_id": pair.get("fact_candidate_id"),
        "fact_candidate": pair.get("fact_candidate"),
        "retrieval_need_ids": pair["retrieval_need_ids"],
        "identity_relation_to_case": pair.get("identity_relation_to_case"),
        "identity_use_decision": pair.get("identity_use_decision"),
        "identity_decisive_proof_eligible": pair.get(
            "identity_decisive_proof_eligible", False
        ),
        "relation": "AMBIGUOUS",
        "proof_role": "AMBIGUOUS",
        "exact_quote": "",
        "quote_match_mode": None,
        "reason_code": "ALIGNMENT_VALIDATION_FAILED",
        "reason": reason,
        "alignment_method": "VALIDATOR",
        "accepted_for_proof": False,
    }
'''


NEW_ALIGN = r'''
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
        batch = pairs[start:start + batch_size]
        batch_no = start // batch_size + 1
        print(
            f"    alignment batch {batch_no}/{total_batches}: "
            f"{len(batch)} fact/proposition pairs"
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
                alignments.append(_ambiguous_alignment(pair, "MODEL_OMITTED_PAIR"))
                continue
            try:
                alignments.append(validate_alignment(item, pair))
            except Exception as error:
                alignments.append(_ambiguous_alignment(pair, str(error)))

    return sorted(
        alignments,
        key=lambda row: (
            row["requirement_id"],
            row["evidence_id"],
            row.get("fact_candidate", {}).get("quote_start", 0),
            row.get("fact_candidate_id", ""),
        ),
    )
'''
for name, replacement in (
    ("_alignment_pairs", NEW_ALIGNMENT_PAIRS),
    ("make_alignment_batch_prompt", NEW_PROMPT),
    ("validate_alignment", NEW_VALIDATE),
    ("_ambiguous_alignment", NEW_AMBIGUOUS),
    ("align_requirement_evidence", NEW_ALIGN),
):
    source = replace_function(source, name, replacement)

ast.parse(source)
TARGET.write_text(source, encoding="utf-8")
print("Installed FactCandidate/subspan fact-level alignment bridge.")
