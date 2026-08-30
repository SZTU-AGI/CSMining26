#!/usr/bin/env python3
from __future__ import annotations

import ast
import shutil
from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")

NEW_VALIDATE = '\ndef validate_alignment(raw: dict, pair: dict) -> dict:\n    """Validate semantic relation; keep argument admission and proof separate."""\n    from evidence_nature_v1 import assess_alignment_compatibility\n\n    requirement_id = pair["requirement"]["requirement_id"]\n    alignment_evidence_id = pair["evidence_id"]\n    parent_evidence_id = pair["parent_evidence_id"]\n\n    if raw.get("requirement_id") != requirement_id:\n        raise ValueError("Alignment returned wrong requirement_id")\n\n    if raw.get("evidence_id") != alignment_evidence_id:\n        raise ValueError("Alignment returned wrong evidence_id")\n\n    model_relation = raw.get("relation")\n\n    if model_relation not in ALIGNMENT_RELATIONS:\n        raise ValueError(\n            f"Invalid alignment relation {model_relation}"\n        )\n\n    exact_quote = str(\n        raw.get("exact_quote", "")\n    ).strip()\n\n    if model_relation != "IRRELEVANT":\n        match_mode = quote_match_mode(\n            exact_quote,\n            pair["evidence_text"],\n        )\n        if match_mode is None:\n            raise ValueError(\n                f"{requirement_id}/{alignment_evidence_id}: "\n                "exact_quote is not grounded in FactCandidate"\n            )\n    else:\n        match_mode = (\n            quote_match_mode(\n                exact_quote,\n                pair["evidence_text"],\n            )\n            if exact_quote\n            else None\n        )\n\n    # Legacy API placeholder only; NOT a proof judgment.\n    compatibility_placeholder = {\n        "SUPPORT": "CORROBORATION_ONLY",\n        "ATTACK": "AMBIGUOUS",\n        "IRRELEVANT": "CONTEXT_ONLY",\n        "AMBIGUOUS": "AMBIGUOUS",\n    }[model_relation]\n\n    compatibility = assess_alignment_compatibility(\n        pair["requirement"],\n        exact_quote,\n        model_relation,\n        compatibility_placeholder,\n    )\n\n    compatibility_decision = compatibility[\n        "compatibility_decision"\n    ]\n\n    identity_use = pair.get(\n        "identity_use_decision",\n        "ADMIT_DIRECT",\n    )\n\n    identity_direct = bool(\n        pair.get(\n            "identity_decisive_proof_eligible",\n            True,\n        )\n    )\n\n    # IMPORTANT:\n    # semantic relation is preserved even when typed compatibility is unresolved.\n    relation = model_relation\n\n    accepted_for_argument = bool(\n        relation in {"SUPPORT", "ATTACK"}\n        and compatibility_decision in {\n            "DIRECT",\n            "CORROBORATIVE",\n        }\n        and identity_use != "EXCLUDE_SUBSTANTIVE"\n    )\n\n    rejection_codes = []\n\n    if relation == "AMBIGUOUS":\n        rejection_codes.append(\n            "ALIGNMENT_RELATION_AMBIGUOUS"\n        )\n    elif relation == "IRRELEVANT":\n        rejection_codes.append(\n            "ALIGNMENT_RELATION_IRRELEVANT"\n        )\n\n    if compatibility_decision == "UNRESOLVED":\n        rejection_codes.append(\n            "PREDICATE_COMPATIBILITY_UNRESOLVED"\n        )\n    elif compatibility_decision == "INCOMPATIBLE":\n        rejection_codes.append(\n            "PREDICATE_COMPATIBILITY_INCOMPATIBLE"\n        )\n\n    if identity_use == "EXCLUDE_SUBSTANTIVE":\n        rejection_codes.append(\n            "IDENTITY_EXCLUDED_SUBSTANTIVE"\n        )\n    elif not identity_direct:\n        rejection_codes.append(\n            "IDENTITY_NOT_DECISIVE"\n        )\n\n    return {\n        "requirement_id": requirement_id,\n        "atom_id": pair["requirement"]["atom_id"],\n        "decisiveness": pair["requirement"]["decisiveness"],\n\n        "evidence_id": parent_evidence_id,\n        "alignment_evidence_id": alignment_evidence_id,\n        "fact_candidate_id": pair["fact_candidate_id"],\n        "fact_candidate": pair["fact_candidate"],\n\n        "retrieval_need_ids": pair["retrieval_need_ids"],\n\n        "identity_relation_to_case":\n            pair.get("identity_relation_to_case"),\n        "identity_use_decision": identity_use,\n        "identity_decisive_proof_eligible": identity_direct,\n        "identity_reason_code":\n            pair.get("identity_reason_code"),\n\n        "alignment_method": str(\n            raw.get("alignment_method", "MODEL")\n        ),\n\n        "model_relation": model_relation,\n        "model_proof_role": None,\n\n        # semantic axis\n        "relation": relation,\n\n        # typed compatibility axis\n        "alignment_strength": compatibility_decision,\n        "predicate_compatibility": compatibility_decision,\n        "predicate_compatibility_reason":\n            compatibility["compatibility_reason_code"],\n        "typed_gate_enforced":\n            compatibility["typed_gate_enforced"],\n\n        "exact_quote": exact_quote,\n        "quote_match_mode": match_mode,\n        "reason_code": str(\n            raw.get("reason_code", "")\n        ),\n        "reason": str(\n            raw.get("reason", "")\n        ),\n\n        "requirement_predicate_profile":\n            compatibility["requirement_profile"],\n        "evidence_nature":\n            compatibility["evidence_nature"],\n\n        # argument-admission axis\n        "accepted_for_argument": accepted_for_argument,\n\n        # temporary legacy alias for existing diagnostics\n        "accepted_for_alignment": accepted_for_argument,\n\n        "rejection_codes": rejection_codes,\n\n        # proof axis is intentionally unavailable here\n        "proof_role": "DEFERRED_TO_ARGUMENT",\n        "accepted_for_proof": False,\n        "proof_deferred_to":\n            "ARGUMENT_AND_PROOF_STANDARD",\n    }\n'
NEW_GATE = '\ndef evaluate_minimal_proof_gate(\n    plan: dict,\n    traces: list[dict],\n    alignments: list[dict],\n) -> dict:\n    """Diagnostic ledger only; no requirement proof before Argument/ProofStandard."""\n\n    traces_by_requirement = {}\n    for trace in traces:\n        traces_by_requirement.setdefault(\n            trace["requirement_id"],\n            [],\n        ).append(trace)\n\n    alignments_by_requirement = {}\n    for alignment in alignments:\n        alignments_by_requirement.setdefault(\n            alignment["requirement_id"],\n            [],\n        ).append(alignment)\n\n    requirement_reports = []\n    decisive_reports = []\n\n    for requirement in plan["requirements"]:\n        rid = requirement["requirement_id"]\n\n        rows = alignments_by_requirement.get(\n            rid,\n            [],\n        )\n\n        semantic_supports = [\n            row\n            for row in rows\n            if row.get("relation") == "SUPPORT"\n        ]\n\n        semantic_attacks = [\n            row\n            for row in rows\n            if row.get("relation") == "ATTACK"\n        ]\n\n        admitted_supports = [\n            row\n            for row in semantic_supports\n            if row.get("accepted_for_argument")\n        ]\n\n        admitted_attacks = [\n            row\n            for row in semantic_attacks\n            if row.get("accepted_for_argument")\n        ]\n\n        ambiguous = [\n            row\n            for row in rows\n            if row.get("relation") == "AMBIGUOUS"\n        ]\n\n        semantic_relation_state = _state_from_pair(\n            bool(semantic_supports),\n            bool(semantic_attacks),\n        )\n\n        argument_input_state = _state_from_pair(\n            bool(admitted_supports),\n            bool(admitted_attacks),\n        )\n\n        requirement_traces = traces_by_requirement.get(\n            rid,\n            [],\n        )\n\n        support_need_present = any(\n            trace["direction"] == "SUPPORT"\n            for trace in requirement_traces\n        )\n\n        attack_need_present = any(\n            trace["direction"] == "ATTACK"\n            for trace in requirement_traces\n        )\n\n        report = {\n            "requirement_id": rid,\n            "atom_id": requirement["atom_id"],\n            "decisiveness": requirement["decisiveness"],\n\n            "semantic_relation_state":\n                semantic_relation_state,\n            "argument_input_state":\n                argument_input_state,\n\n            # legacy diagnostic field: admitted observable state only\n            "raw_state": argument_input_state,\n\n            # no benchmark/legal proof yet\n            "accepted_state": "UNKNOWN",\n            "semantic_support_pass": False,\n            "audit_sufficient_pass": False,\n            "explicit_violation_pass": False,\n\n            "contradiction_state": (\n                "PRESERVED"\n                if semantic_attacks\n                else "NONE"\n            ),\n\n            "support_need_present":\n                support_need_present,\n            "attack_need_present":\n                attack_need_present,\n\n            "coverage_status":\n                "ALIGNMENT_ONLY_ARGUMENT_PENDING",\n            "coverage_pass": False,\n\n            "semantic_support_evidence_ids": [\n                row["evidence_id"]\n                for row in semantic_supports\n            ],\n            "semantic_attack_evidence_ids": [\n                row["evidence_id"]\n                for row in semantic_attacks\n            ],\n\n            "argument_support_evidence_ids": [\n                row["evidence_id"]\n                for row in admitted_supports\n            ],\n            "argument_attack_evidence_ids": [\n                row["evidence_id"]\n                for row in admitted_attacks\n            ],\n\n            "ambiguous_evidence_ids": [\n                row["evidence_id"]\n                for row in ambiguous\n            ],\n\n            "direct_support_evidence_ids": [],\n            "corroboration_evidence_ids": [],\n            "explicit_violation_evidence_ids": [],\n\n            "argument_status": "PENDING",\n            "proof_standard_status": "PENDING",\n        }\n\n        requirement_reports.append(report)\n\n        if requirement["decisiveness"] == "DECISIVE":\n            decisive_reports.append(report)\n\n    if not decisive_reports:\n        raise ValueError(\n            "No decisive EvidenceRequirement reports"\n        )\n\n    return {\n        "schema": "freca-core-alignment-ledger-v4",\n        "pilot_only": True,\n\n        "coverage_complete": False,\n        "coverage_note": (\n            "Semantic relation is preserved separately from "\n            "typed/identity admission to the argument layer. "\n            "ArgumentTemplate and ProofStandard are pending."\n        ),\n\n        "requirement_reports": requirement_reports,\n\n        "satisfaction_state": "UNKNOWN",\n        "violation_state": "UNKNOWN",\n        "candidate_outcome": "UNKNOWN",\n        "candidate_submission_label": None,\n\n        "evaluation_locked": False,\n        "internal_outcome": "UNKNOWN",\n        "submission_label": None,\n\n        "argument_status": "PENDING",\n        "proof_standard_status": "PENDING",\n    }\n'


def replace_top_level_function(
    src: str,
    name: str,
    replacement: str,
) -> str:
    tree = ast.parse(src)

    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == name
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level function {{name}}, "
            f"found {{len(matches)}}"
        )

    node = matches[0]
    lines = src.splitlines(keepends=True)

    lines[
        node.lineno - 1:
        node.end_lineno
    ] = [
        replacement.rstrip() + "\n\n"
    ]

    return "".join(lines)


if not TARGET.exists():
    raise SystemExit(
        "Missing evidence_reasoning_v2.py; "
        "run from ~/freca/core_v1"
    )

source = TARGET.read_text(encoding="utf-8")

required_before = (
    "DEFERRED_TO_ARGUMENT",
    "accepted_for_alignment",
    "accepted_for_proof",
    "ALIGNMENT_ONLY_ARGUMENT_PENDING",
)

missing = [
    marker
    for marker in required_before
    if marker not in source
]

if missing:
    raise SystemExit(
        "Current evidence_reasoning_v2.py does not look like "
        "boundary-fix-v2. Missing: "
        + ", ".join(missing)
    )

patched = replace_top_level_function(
    source,
    "validate_alignment",
    NEW_VALIDATE,
)

patched = replace_top_level_function(
    patched,
    "evaluate_minimal_proof_gate",
    NEW_GATE,
)

# Parse the complete patched module BEFORE writing.
ast.parse(patched)

required_after = (
    '"accepted_for_argument"',
    '"rejection_codes"',
    "freca-core-alignment-ledger-v4",
    "semantic_relation_state",
    "argument_input_state",
)

missing_after = [
    marker
    for marker in required_after
    if marker not in patched
]

if missing_after:
    raise RuntimeError(
        "Patched module missing required markers: "
        + ", ".join(missing_after)
    )

backup = Path(
    "evidence_reasoning_v2.before_relation_preservation_v2.py"
)

if not backup.exists():
    shutil.copy2(TARGET, backup)

tmp = Path(
    "evidence_reasoning_v2.relation_preservation_v2.tmp"
)

tmp.write_text(
    patched,
    encoding="utf-8",
)

# Parse the exact bytes that will be installed.
ast.parse(
    tmp.read_text(encoding="utf-8")
)

tmp.replace(TARGET)

print("Installed alignment relation-preservation v2.")
print("  relation: preserved")
print("  accepted_for_argument: separate typed/identity gate")
print("  accepted_for_proof: always False")
print("  ledger: semantic_relation_state + argument_input_state")
print()
print("Backup:", backup)
