"""Install EvidenceNature × predicate-compatibility gate into evidence_reasoning_v2.py."""

from __future__ import annotations

import ast
from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")
MODULE = Path("evidence_nature_v1.py")

if not TARGET.exists():
    raise SystemExit("Missing evidence_reasoning_v2.py")
if not MODULE.exists():
    raise SystemExit("Missing evidence_nature_v1.py")

source = TARGET.read_text(encoding="utf-8")


def replace_function(src: str, name: str, replacement: str) -> str:
    tree = ast.parse(src)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected exactly one top-level function {name}, found {len(matches)}"
        )
    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1 : node.end_lineno] = [replacement.rstrip() + "\n\n"]
    return "".join(lines)


NEW_VALIDATE_ALIGNMENT = r'''def validate_alignment(raw: dict, pair: dict) -> dict:
    from evidence_nature_v1 import (
        assess_alignment_compatibility,
        effective_alignment,
    )

    requirement_id = pair["requirement"]["requirement_id"]
    evidence_id = pair["evidence_id"]

    if raw.get("requirement_id") != requirement_id:
        raise ValueError(
            f"Alignment returned wrong requirement_id: "
            f"{raw.get('requirement_id')} != {requirement_id}"
        )
    if raw.get("evidence_id") != evidence_id:
        raise ValueError(
            f"Alignment returned wrong evidence_id: "
            f"{raw.get('evidence_id')} != {evidence_id}"
        )

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
            f"Inconsistent relation/proof_role: "
            f"{model_relation}/{model_proof_role}"
        )

    exact_quote = str(raw.get("exact_quote", "")).strip()
    if model_relation != "IRRELEVANT":
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

    compatibility = assess_alignment_compatibility(
        pair["requirement"],
        exact_quote,
        model_relation,
        model_proof_role,
    )
    effective = effective_alignment(
        model_relation=model_relation,
        model_proof_role=model_proof_role,
        compatibility=compatibility,
    )

    relation = effective["relation"]
    proof_role = effective["proof_role"]

    semantic_proof_candidate = (
        relation in {"SUPPORT", "ATTACK"}
        and proof_role != "AMBIGUOUS"
    )
    identity_direct = bool(
        pair.get("identity_decisive_proof_eligible", True)
    )
    typed_direct = bool(
        effective["typed_decisive_proof_eligible"]
    )

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

        # Preserve model output separately from effective proof state.
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

        "accepted_for_proof": (
            semantic_proof_candidate
            and identity_direct
            and typed_direct
        ),
    }
'''

source = replace_function(source, "validate_alignment", NEW_VALIDATE_ALIGNMENT)
TARGET.write_text(source, encoding="utf-8")
ast.parse(source)
print("Installed EvidenceNature × predicate compatibility gate.")
