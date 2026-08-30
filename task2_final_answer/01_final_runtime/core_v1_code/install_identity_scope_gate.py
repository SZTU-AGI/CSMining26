"""Install the minimal Layer-5 identity gate + facet-scoped D7.8 adapter.

Run from ~/freca/core_v1 after placing identity_admissibility_v1.py beside
this installer.
"""

from __future__ import annotations

import ast
from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")
MODULE = Path("identity_admissibility_v1.py")

if not TARGET.exists():
    raise SystemExit("Missing evidence_reasoning_v2.py in current directory")
if not MODULE.exists():
    raise SystemExit("Missing identity_admissibility_v1.py in current directory")

source = TARGET.read_text(encoding="utf-8")
tree = ast.parse(source)


def replace_function(src: str, name: str, replacement: str) -> str:
    tree = ast.parse(src)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one top-level function {name}, found {len(matches)}")
    node = matches[0]
    lines = src.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    replacement_text = replacement.rstrip() + "\n\n"
    lines[start:end] = [replacement_text]
    return "".join(lines)


NEW_ALIGNMENT_PAIRS = r'''def _alignment_pairs(plan: dict, traces: list[dict]) -> list[dict]:
    requirements = {item["requirement_id"]: item for item in plan["requirements"]}

    # Both directional needs may return the same evidence.  Semantic relation
    # is direction-independent, so classify each requirement/evidence pair only
    # once while preserving originating Need IDs.
    #
    # Layer-5 identity is applied AFTER retrieval but BEFORE semantic alignment.
    # EXCLUDE_SUBSTANTIVE candidates remain in retrieval_traces for audit/gap
    # purposes but are not sent to the D7.8 model as target-entity proof.
    pair_map: dict[tuple[str, str], dict] = {}

    for trace in traces:
        requirement = requirements[trace["requirement_id"]]
        for candidate in trace["candidates"]:
            use_decision = candidate.get("identity_use_decision", "ADMIT_DIRECT")
            if use_decision == "EXCLUDE_SUBSTANTIVE":
                continue

            key = (trace["requirement_id"], candidate["evidence_id"])
            if key not in pair_map:
                pair_map[key] = {
                    "requirement": requirement,
                    "evidence_id": candidate["evidence_id"],
                    "evidence_text": candidate["text"],
                    "retrieval_need_ids": [],
                    "best_retrieval_score": candidate.get("score"),
                    "identity_relation_to_case": candidate.get(
                        "identity_relation_to_case", "CORE_SELF_EXACT"
                    ),
                    "identity_use_decision": use_decision,
                    "identity_decisive_proof_eligible": candidate.get(
                        "identity_decisive_proof_eligible", use_decision == "ADMIT_DIRECT"
                    ),
                    "identity_reason_code": candidate.get(
                        "identity_reason_code", "IDENTITY_GATE_NOT_PRESENT"
                    ),
                }
            pair_map[key]["retrieval_need_ids"].append(trace["need_id"])

    return sorted(
        pair_map.values(),
        key=lambda x: (x["requirement"]["requirement_id"], x["evidence_id"]),
    )
'''


NEW_ALIGNMENT_PROMPT = r'''def make_alignment_batch_prompt(pairs: list[dict]) -> str:
    payload = []
    for pair in pairs:
        requirement = pair["requirement"]

        # IMPORTANT: D7.8 receives only the narrowed EvidenceRequirement and
        # the official bindings belonging to that facet seed.  The broader
        # parent CP criterion is provenance, not alignment scope, and is
        # intentionally omitted here to prevent facet-scope bleed.
        official_bindings = [
            {
                "source": source.get("source"),
                "candidate_id": source.get("candidate_id"),
                "quote": source.get("quote"),
            }
            for source in requirement.get("query_sources", [])
            if source.get("source") == "RULES"
        ]

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
                        "Identity/admissibility has already been determined by a "
                        "separate deterministic gate. Do not override it. Judge "
                        "semantic relation only against this narrowed requirement "
                        "and its supplied official bindings; do not import omitted "
                        "terms from a broader parent criterion."
                    ),
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
'''


NEW_VALIDATE_ALIGNMENT = r'''def validate_alignment(raw: dict, pair: dict) -> dict:
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

    semantic_proof_candidate = (
        relation in {"SUPPORT", "ATTACK"}
        and proof_role != "AMBIGUOUS"
    )
    identity_direct = bool(pair.get("identity_decisive_proof_eligible", True))

    return {
        "requirement_id": requirement_id,
        "atom_id": pair["requirement"]["atom_id"],
        "decisiveness": pair["requirement"]["decisiveness"],
        "evidence_id": evidence_id,
        "retrieval_need_ids": pair["retrieval_need_ids"],
        "identity_relation_to_case": pair.get("identity_relation_to_case"),
        "identity_use_decision": pair.get("identity_use_decision"),
        "identity_decisive_proof_eligible": identity_direct,
        "identity_reason_code": pair.get("identity_reason_code"),
        "relation": relation,
        "proof_role": proof_role,
        "exact_quote": exact_quote,
        "quote_match_mode": match_mode,
        "reason_code": str(raw.get("reason_code", "")),
        "reason": str(raw.get("reason", "")),
        "accepted_for_proof": semantic_proof_candidate and identity_direct,
    }
'''


NEW_RUN = r'''def run_requirement_reasoning(
    *,
    cp_id: str,
    case_id: str,
    evidence_chunks: list[dict],
    retrieval_top_k: int = 12,
    force_plan_recompile: bool = False,
) -> dict:
    from identity_admissibility_v1 import (
        apply_identity_gate_to_traces,
        build_body_first_identity_report,
    )

    plan = compile_evidence_requirements(cp_id, force=force_plan_recompile)
    needs = build_retrieval_needs(plan)
    traces = retrieve_requirement_candidates(
        evidence_chunks,
        needs,
        top_k=retrieval_top_k,
    )

    # Layer-5 pilot is deliberately CP-blind. `case_id` is supplied only as the
    # post-hoc output-identifier consistency check; it does not define core RE.
    identity_report = build_body_first_identity_report(
        evidence_chunks,
        output_identifier=case_id,
    )
    traces = apply_identity_gate_to_traces(traces, identity_report)

    alignments = align_requirement_evidence(plan, traces)
    proof = evaluate_minimal_proof_gate(plan, traces, alignments)

    result = {
        "schema": "freca-core-requirement-reasoning-v2.2",
        "cp_id": cp_id,
        "case_id": case_id,
        "identity_admissibility": identity_report,
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
'''


for name, replacement in [
    ("_alignment_pairs", NEW_ALIGNMENT_PAIRS),
    ("make_alignment_batch_prompt", NEW_ALIGNMENT_PROMPT),
    ("validate_alignment", NEW_VALIDATE_ALIGNMENT),
    ("run_requirement_reasoning", NEW_RUN),
]:
    source = replace_function(source, name, replacement)

TARGET.write_text(source, encoding="utf-8")

# Verify the resulting module is syntactically valid before declaring success.
ast.parse(source)
print("Installed Layer-5 identity gate and facet-scoped D7.8 adapter.")
