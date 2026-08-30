#!/usr/bin/env python3
"""Install FRECA argument-admission channel patch.

Adds:
- argument_admission_channel = DIRECT | CONDITIONAL | REJECTED
- argument_truth_bearing = True only for DIRECT
- three-level diagnostic ledger:
  semantic / argument-visible / direct-truth-bearing

Does NOT change:
- retrieval
- FactCandidate
- semantic relation
- EvidenceNature classification
- identity classification
- contract
- ArgumentTemplate
- ProofStandard
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")

NEW_VALIDATE = '\ndef validate_alignment(raw: dict, pair: dict) -> dict:\n    """Validate semantic relation and publish a typed argument-admission channel.\n\n    Separation:\n        relation\n            = semantic direction only\n\n        argument_admission_channel\n            = DIRECT | CONDITIONAL | REJECTED\n\n        argument_truth_bearing\n            = True only for DIRECT\n\n        accepted_for_proof\n            = always False at alignment layer\n    """\n    from evidence_nature_v1 import (\n        assess_alignment_compatibility,\n    )\n\n    requirement_id = pair[\n        "requirement"\n    ][\n        "requirement_id"\n    ]\n\n    alignment_evidence_id = pair[\n        "evidence_id"\n    ]\n\n    parent_evidence_id = pair[\n        "parent_evidence_id"\n    ]\n\n    if raw.get(\n        "requirement_id"\n    ) != requirement_id:\n        raise ValueError(\n            "Alignment returned wrong requirement_id"\n        )\n\n    if raw.get(\n        "evidence_id"\n    ) != alignment_evidence_id:\n        raise ValueError(\n            "Alignment returned wrong evidence_id"\n        )\n\n    model_relation = raw.get(\n        "relation"\n    )\n\n    if (\n        model_relation\n        not in ALIGNMENT_RELATIONS\n    ):\n        raise ValueError(\n            f"Invalid alignment relation "\n            f"{model_relation}"\n        )\n\n    exact_quote = str(\n        raw.get(\n            "exact_quote",\n            "",\n        )\n    ).strip()\n\n    if (\n        model_relation\n        != "IRRELEVANT"\n    ):\n        match_mode = quote_match_mode(\n            exact_quote,\n            pair[\n                "evidence_text"\n            ],\n        )\n\n        if match_mode is None:\n            raise ValueError(\n                f"{requirement_id}/"\n                f"{alignment_evidence_id}: "\n                "exact_quote is not grounded "\n                "in FactCandidate"\n            )\n    else:\n        match_mode = (\n            quote_match_mode(\n                exact_quote,\n                pair[\n                    "evidence_text"\n                ],\n            )\n            if exact_quote\n            else None\n        )\n\n    # Legacy EvidenceNature API placeholder only.\n    # It is NOT a proof judgment.\n    compatibility_placeholder = {\n        "SUPPORT":\n            "CORROBORATION_ONLY",\n        "ATTACK":\n            "AMBIGUOUS",\n        "IRRELEVANT":\n            "CONTEXT_ONLY",\n        "AMBIGUOUS":\n            "AMBIGUOUS",\n    }[\n        model_relation\n    ]\n\n    compatibility = (\n        assess_alignment_compatibility(\n            pair[\n                "requirement"\n            ],\n            exact_quote,\n            model_relation,\n            compatibility_placeholder,\n        )\n    )\n\n    compatibility_decision = (\n        compatibility[\n            "compatibility_decision"\n        ]\n    )\n\n    identity_use = pair.get(\n        "identity_use_decision",\n        "ADMIT_DIRECT",\n    )\n\n    identity_direct = bool(\n        pair.get(\n            "identity_decisive_proof_eligible",\n            identity_use == "ADMIT_DIRECT",\n        )\n    )\n\n    # Semantic direction is never rewritten by identity/typed uncertainty.\n    relation = model_relation\n\n    semantic_argument_candidate = bool(\n        relation\n        in {\n            "SUPPORT",\n            "ATTACK",\n        }\n        and compatibility_decision\n        in {\n            "DIRECT",\n            "CORROBORATIVE",\n        }\n    )\n\n    # ------------------------------------------------------------\n    # Layer5 -> Layer7 argument admission channel\n    # ------------------------------------------------------------\n    # DIRECT:\n    #   may directly seed an OBSERVABLE_CASE_FACT support/attack state.\n    #\n    # CONDITIONAL:\n    #   remains visible to Argument/repair, but cannot itself seed truth.\n    #\n    # REJECTED:\n    #   retained in semantic/audit ledger only.\n    # ------------------------------------------------------------\n    if not semantic_argument_candidate:\n        argument_admission_channel = (\n            "REJECTED"\n        )\n\n    elif (\n        identity_use\n        == "ADMIT_DIRECT"\n        and identity_direct\n    ):\n        argument_admission_channel = (\n            "DIRECT"\n        )\n\n    elif (\n        identity_use\n        in {\n            "ADMIT_DIRECT",\n            "ADMIT_CONDITIONAL",\n        }\n    ):\n        argument_admission_channel = (\n            "CONDITIONAL"\n        )\n\n    else:\n        argument_admission_channel = (\n            "REJECTED"\n        )\n\n    accepted_for_argument = (\n        argument_admission_channel\n        in {\n            "DIRECT",\n            "CONDITIONAL",\n        }\n    )\n\n    argument_truth_bearing = (\n        argument_admission_channel\n        == "DIRECT"\n    )\n\n    rejection_codes = []\n    admission_reason_codes = []\n\n    if relation == "AMBIGUOUS":\n        rejection_codes.append(\n            "ALIGNMENT_RELATION_AMBIGUOUS"\n        )\n\n    elif relation == "IRRELEVANT":\n        rejection_codes.append(\n            "ALIGNMENT_RELATION_IRRELEVANT"\n        )\n\n    if (\n        compatibility_decision\n        == "UNRESOLVED"\n    ):\n        rejection_codes.append(\n            "PREDICATE_COMPATIBILITY_UNRESOLVED"\n        )\n\n    elif (\n        compatibility_decision\n        == "INCOMPATIBLE"\n    ):\n        rejection_codes.append(\n            "PREDICATE_COMPATIBILITY_INCOMPATIBLE"\n        )\n\n    if (\n        identity_use\n        == "EXCLUDE_SUBSTANTIVE"\n    ):\n        rejection_codes.append(\n            "IDENTITY_EXCLUDED_SUBSTANTIVE"\n        )\n\n    elif (\n        identity_use\n        == "CONTEXT_ONLY"\n    ):\n        rejection_codes.append(\n            "IDENTITY_CONTEXT_ONLY"\n        )\n\n    elif (\n        identity_use\n        == "GAP_SIGNAL_ONLY"\n    ):\n        rejection_codes.append(\n            "IDENTITY_GAP_SIGNAL_ONLY"\n        )\n\n    elif (\n        identity_use\n        == "ADMIT_CONDITIONAL"\n    ):\n        admission_reason_codes.append(\n            "IDENTITY_CONDITIONAL_CHANNEL"\n        )\n\n    if (\n        identity_use\n        == "ADMIT_DIRECT"\n        and not identity_direct\n    ):\n        admission_reason_codes.append(\n            "IDENTITY_DIRECT_NOT_TRUTH_ELIGIBLE"\n        )\n\n    if (\n        argument_admission_channel\n        == "DIRECT"\n    ):\n        admission_reason_codes.append(\n            "ARGUMENT_DIRECT_ADMISSION"\n        )\n\n    elif (\n        argument_admission_channel\n        == "CONDITIONAL"\n    ):\n        admission_reason_codes.append(\n            "ARGUMENT_CONDITIONAL_ADMISSION"\n        )\n\n    else:\n        admission_reason_codes.append(\n            "ARGUMENT_REJECTED"\n        )\n\n    return {\n        "requirement_id":\n            requirement_id,\n        "atom_id":\n            pair[\n                "requirement"\n            ][\n                "atom_id"\n            ],\n        "decisiveness":\n            pair[\n                "requirement"\n            ][\n                "decisiveness"\n            ],\n\n        "evidence_id":\n            parent_evidence_id,\n        "alignment_evidence_id":\n            alignment_evidence_id,\n        "fact_candidate_id":\n            pair[\n                "fact_candidate_id"\n            ],\n        "fact_candidate":\n            pair[\n                "fact_candidate"\n            ],\n\n        "retrieval_need_ids":\n            pair[\n                "retrieval_need_ids"\n            ],\n\n        "identity_relation_to_case":\n            pair.get(\n                "identity_relation_to_case"\n            ),\n        "identity_use_decision":\n            identity_use,\n        "identity_decisive_proof_eligible":\n            identity_direct,\n        "identity_reason_code":\n            pair.get(\n                "identity_reason_code"\n            ),\n\n        "alignment_method":\n            str(\n                raw.get(\n                    "alignment_method",\n                    "MODEL",\n                )\n            ),\n\n        "model_relation":\n            model_relation,\n        "model_proof_role":\n            None,\n\n        # semantic axis\n        "relation":\n            relation,\n\n        # typed predicate axis\n        "alignment_strength":\n            compatibility_decision,\n        "predicate_compatibility":\n            compatibility_decision,\n        "predicate_compatibility_reason":\n            compatibility[\n                "compatibility_reason_code"\n            ],\n        "typed_gate_enforced":\n            compatibility[\n                "typed_gate_enforced"\n            ],\n\n        "exact_quote":\n            exact_quote,\n        "quote_match_mode":\n            match_mode,\n        "reason_code":\n            str(\n                raw.get(\n                    "reason_code",\n                    "",\n                )\n            ),\n        "reason":\n            str(\n                raw.get(\n                    "reason",\n                    "",\n                )\n            ),\n\n        "requirement_predicate_profile":\n            compatibility[\n                "requirement_profile"\n            ],\n        "evidence_nature":\n            compatibility[\n                "evidence_nature"\n            ],\n\n        # argument-admission axis\n        "argument_admission_channel":\n            argument_admission_channel,\n        "accepted_for_argument":\n            accepted_for_argument,\n        "argument_truth_bearing":\n            argument_truth_bearing,\n        "argument_review_required":\n            (\n                argument_admission_channel\n                == "CONDITIONAL"\n            ),\n        "argument_admission_reason_codes":\n            admission_reason_codes,\n\n        "rejection_codes":\n            rejection_codes,\n\n        # Temporary legacy alias.\n        # It means visible to the argument layer, not proof.\n        "accepted_for_alignment":\n            accepted_for_argument,\n\n        # No direct alignment can establish proof.\n        "proof_role":\n            "DEFERRED_TO_ARGUMENT",\n        "accepted_for_proof":\n            False,\n        "proof_deferred_to":\n            "ARGUMENT_AND_PROOF_STANDARD",\n    }\n'
NEW_GATE = '\ndef evaluate_minimal_proof_gate(\n    plan: dict,\n    traces: list[dict],\n    alignments: list[dict],\n) -> dict:\n    """Diagnostic evidence/argument-admission ledger only.\n\n    Three separate views are preserved:\n\n    1. semantic_relation_state\n       all semantically aligned SUPPORT/ATTACK facts;\n\n    2. argument_visible_state\n       DIRECT + CONDITIONAL facts visible to the argument layer;\n\n    3. direct_argument_input_state\n       DIRECT facts only; these may seed OBSERVABLE_CASE_FACT state.\n\n    CONDITIONAL evidence is preserved but is not truth-bearing until an\n    explicit assumption/repair policy resolves it.\n    """\n\n    traces_by_requirement = {}\n\n    for trace in traces:\n        traces_by_requirement.setdefault(\n            trace[\n                "requirement_id"\n            ],\n            [],\n        ).append(\n            trace\n        )\n\n    alignments_by_requirement = {}\n\n    for alignment in alignments:\n        alignments_by_requirement.setdefault(\n            alignment[\n                "requirement_id"\n            ],\n            [],\n        ).append(\n            alignment\n        )\n\n    requirement_reports = []\n    decisive_reports = []\n\n    for requirement in plan[\n        "requirements"\n    ]:\n        rid = requirement[\n            "requirement_id"\n        ]\n\n        rows = (\n            alignments_by_requirement.get(\n                rid,\n                [],\n            )\n        )\n\n        semantic_supports = [\n            row\n            for row in rows\n            if row.get(\n                "relation"\n            ) == "SUPPORT"\n        ]\n\n        semantic_attacks = [\n            row\n            for row in rows\n            if row.get(\n                "relation"\n            ) == "ATTACK"\n        ]\n\n        visible_supports = [\n            row\n            for row in semantic_supports\n            if row.get(\n                "argument_admission_channel"\n            )\n            in {\n                "DIRECT",\n                "CONDITIONAL",\n            }\n        ]\n\n        visible_attacks = [\n            row\n            for row in semantic_attacks\n            if row.get(\n                "argument_admission_channel"\n            )\n            in {\n                "DIRECT",\n                "CONDITIONAL",\n            }\n        ]\n\n        direct_supports = [\n            row\n            for row in semantic_supports\n            if row.get(\n                "argument_admission_channel"\n            ) == "DIRECT"\n            and row.get(\n                "argument_truth_bearing"\n            )\n        ]\n\n        direct_attacks = [\n            row\n            for row in semantic_attacks\n            if row.get(\n                "argument_admission_channel"\n            ) == "DIRECT"\n            and row.get(\n                "argument_truth_bearing"\n            )\n        ]\n\n        conditional_supports = [\n            row\n            for row in semantic_supports\n            if row.get(\n                "argument_admission_channel"\n            ) == "CONDITIONAL"\n        ]\n\n        conditional_attacks = [\n            row\n            for row in semantic_attacks\n            if row.get(\n                "argument_admission_channel"\n            ) == "CONDITIONAL"\n        ]\n\n        ambiguous = [\n            row\n            for row in rows\n            if row.get(\n                "relation"\n            ) == "AMBIGUOUS"\n        ]\n\n        semantic_relation_state = (\n            _state_from_pair(\n                bool(\n                    semantic_supports\n                ),\n                bool(\n                    semantic_attacks\n                ),\n            )\n        )\n\n        argument_visible_state = (\n            _state_from_pair(\n                bool(\n                    visible_supports\n                ),\n                bool(\n                    visible_attacks\n                ),\n            )\n        )\n\n        direct_argument_input_state = (\n            _state_from_pair(\n                bool(\n                    direct_supports\n                ),\n                bool(\n                    direct_attacks\n                ),\n            )\n        )\n\n        conditional_argument_state = (\n            _state_from_pair(\n                bool(\n                    conditional_supports\n                ),\n                bool(\n                    conditional_attacks\n                ),\n            )\n        )\n\n        requirement_traces = (\n            traces_by_requirement.get(\n                rid,\n                [],\n            )\n        )\n\n        support_need_present = any(\n            trace[\n                "direction"\n            ] == "SUPPORT"\n            for trace in requirement_traces\n        )\n\n        attack_need_present = any(\n            trace[\n                "direction"\n            ] == "ATTACK"\n            for trace in requirement_traces\n        )\n\n        report = {\n            "requirement_id":\n                rid,\n            "atom_id":\n                requirement[\n                    "atom_id"\n                ],\n            "decisiveness":\n                requirement[\n                    "decisiveness"\n                ],\n\n            # Complete semantic evidence ledger.\n            "semantic_relation_state":\n                semantic_relation_state,\n\n            # DIRECT + CONDITIONAL remain visible to Argument.\n            "argument_visible_state":\n                argument_visible_state,\n\n            # Only DIRECT facts may seed observable truth.\n            "direct_argument_input_state":\n                direct_argument_input_state,\n\n            # CONDITIONAL facts remain unresolved until repair/assumption policy.\n            "conditional_argument_state":\n                conditional_argument_state,\n\n            # Backward-compatible name now explicitly means DIRECT-only.\n            "argument_input_state":\n                direct_argument_input_state,\n\n            # Legacy diagnostic field also means DIRECT-only observable state.\n            "raw_state":\n                direct_argument_input_state,\n\n            # No legal/benchmark proof yet.\n            "accepted_state":\n                "UNKNOWN",\n            "semantic_support_pass":\n                False,\n            "audit_sufficient_pass":\n                False,\n            "explicit_violation_pass":\n                False,\n\n            "contradiction_state":\n                (\n                    "PRESERVED"\n                    if semantic_attacks\n                    else "NONE"\n                ),\n\n            "support_need_present":\n                support_need_present,\n            "attack_need_present":\n                attack_need_present,\n\n            "coverage_status":\n                "ALIGNMENT_ONLY_ARGUMENT_PENDING",\n            "coverage_pass":\n                False,\n\n            "semantic_support_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in semantic_supports\n                ],\n            "semantic_attack_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in semantic_attacks\n                ],\n\n            "argument_visible_support_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in visible_supports\n                ],\n            "argument_visible_attack_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in visible_attacks\n                ],\n\n            "direct_argument_support_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in direct_supports\n                ],\n            "direct_argument_attack_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in direct_attacks\n                ],\n\n            "conditional_argument_support_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in conditional_supports\n                ],\n            "conditional_argument_attack_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in conditional_attacks\n                ],\n\n            "ambiguous_evidence_ids":\n                [\n                    row[\n                        "evidence_id"\n                    ]\n                    for row\n                    in ambiguous\n                ],\n\n            # Legacy decisive lists intentionally empty.\n            "direct_support_evidence_ids":\n                [],\n            "corroboration_evidence_ids":\n                [],\n            "explicit_violation_evidence_ids":\n                [],\n\n            "argument_status":\n                "PENDING",\n            "proof_standard_status":\n                "PENDING",\n        }\n\n        requirement_reports.append(\n            report\n        )\n\n        if (\n            requirement[\n                "decisiveness"\n            ]\n            == "DECISIVE"\n        ):\n            decisive_reports.append(\n                report\n            )\n\n    if not decisive_reports:\n        raise ValueError(\n            "No decisive EvidenceRequirement reports"\n        )\n\n    return {\n        "schema":\n            "freca-core-argument-admission-ledger-v5",\n        "pilot_only":\n            True,\n\n        "coverage_complete":\n            False,\n        "coverage_note":\n            (\n                "Semantic relation, argument visibility, and direct "\n                "truth-bearing admission are separate. CONDITIONAL evidence "\n                "is preserved but cannot seed observable truth until an "\n                "explicit repair/assumption policy resolves it."\n            ),\n\n        "requirement_reports":\n            requirement_reports,\n\n        "satisfaction_state":\n            "UNKNOWN",\n        "violation_state":\n            "UNKNOWN",\n        "candidate_outcome":\n            "UNKNOWN",\n        "candidate_submission_label":\n            None,\n\n        "evaluation_locked":\n            False,\n        "internal_outcome":\n            "UNKNOWN",\n        "submission_label":\n            None,\n\n        "argument_status":\n            "PENDING",\n        "proof_standard_status":\n            "PENDING",\n    }\n'


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
    lines = src.splitlines(
        keepends=True
    )

    lines[
        node.lineno - 1:
        node.end_lineno
    ] = [
        replacement.rstrip()
        + "\n\n"
    ]

    return "".join(lines)


if not TARGET.exists():
    raise SystemExit(
        "Missing evidence_reasoning_v2.py; "
        "run from ~/freca/core_v1"
    )

source = TARGET.read_text(
    encoding="utf-8"
)

# Structural preflight for the currently frozen alignment/proof separation.
for marker in (
    "accepted_for_argument",
    "accepted_for_proof",
    "DEFERRED_TO_ARGUMENT",
    "semantic_relation_state",
):
    if marker not in source:
        raise SystemExit(
            "Unexpected evidence_reasoning_v2.py; "
            f"missing marker: {{marker}}"
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

# Parse complete module before writing.
ast.parse(patched)

required_after = (
    '"argument_admission_channel"',
    '"argument_truth_bearing"',
    '"argument_visible_state"',
    '"direct_argument_input_state"',
    '"conditional_argument_state"',
    "freca-core-argument-admission-ledger-v5",
)

for marker in required_after:
    if marker not in patched:
        raise RuntimeError(
            "Patched module missing marker: "
            + marker
        )

backup = Path(
    "evidence_reasoning_v2.before_argument_admission_channel.py"
)

if not backup.exists():
    shutil.copy2(
        TARGET,
        backup,
    )

tmp = Path(
    "evidence_reasoning_v2.argument_admission_channel.tmp"
)

tmp.write_text(
    patched,
    encoding="utf-8",
)

ast.parse(
    tmp.read_text(
        encoding="utf-8"
    )
)

tmp.replace(TARGET)

print(
    "Installed FRECA argument-admission channel patch."
)
print(
    "  semantic relation: unchanged"
)
print(
    "  DIRECT: visible + truth-bearing"
)
print(
    "  CONDITIONAL: visible + not truth-bearing"
)
print(
    "  REJECTED: audit/semantic ledger only"
)
print(
    "  accepted_for_proof: always False"
)
print()
print(
    "Backup:",
    backup,
)
