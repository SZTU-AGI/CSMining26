#!/usr/bin/env python3
from __future__ import annotations
import ast
import shutil
from pathlib import Path

EVIDENCE = Path("evidence_reasoning_v2.py")
FACTS = Path("fact_candidate_v1.py")

NEW_SYSTEM = 'You are a closed-source fact-to-requirement relation classifier.\n\nYou receive ONE frozen EvidenceRequirement and ONE grounded FactCandidate.\nUse only the supplied requirement, official bindings, and fact.\n\nYour task is ONLY to classify whether the observable fact bears on the\nrequirement.\n\nDo NOT decide:\n- overall CP compliance;\n- applicability;\n- evidence sufficiency;\n- proof standard;\n- whether one fact is enough to establish or defeat the requirement;\n- DIRECT_SUPPORT;\n- CORROBORATION_ONLY;\n- EXPLICIT_VIOLATION;\n- a final 1/0/N/A value.\n\nDo NOT infer facts not stated in the supplied FactCandidate.\nDo NOT use outside knowledge or information from other cases.\nDo NOT override deterministic identity/admissibility metadata.\n\nClassify relation:\n\nSUPPORT\n    The observable fact positively bears on the supplied requirement.\n\nATTACK\n    The observable fact negatively bears on, contradicts, or materially\n    undermines the supplied requirement.\n\nIRRELEVANT\n    The observable fact does not materially bear on the requirement.\n\nAMBIGUOUS\n    The direction depends on an unstated assumption or unclear entity, scope,\n    time, or semantics.\n\nImportant:\n- Relation is not proof sufficiency.\n- ATTACK does not mean the requirement is violated.\n- SUPPORT does not mean the requirement is satisfied.\n- Preserve contradictory facts rather than choosing a global conclusion.\n\nReturn JSON only:\n\n{\n  "alignments": [\n    {\n      "requirement_id": "ER1",\n      "evidence_id": "...",\n      "relation": "SUPPORT|ATTACK|IRRELEVANT|AMBIGUOUS",\n      "exact_quote": "exact substring from supplied FactCandidate",\n      "reason_code": "POSITIVE_BEARING|NEGATIVE_BEARING|IRRELEVANT|SCOPE_DEPENDENT|AMBIGUOUS_SEMANTICS",\n      "reason": "brief explanation of fact-to-requirement relation only"\n    }\n  ]\n}'
NEW_EXPLICIT_POSITIVE = '\ndef _explicit_positive_state(text: str) -> bool:\n    t = _norm(text)\n\n    patterns = (\n        # Explicitly resolved openings/gaps.\n        r"\\ball gaps\\b[^.]{0,120}\\b(?:sealed|filled|closed off)\\b",\n        r"\\bgaps?\\b\\s*(?:is|are|was|were|has been|have been)?\\s*"\n        r"(?:sealed|filled|closed off)\\b",\n        r"\\bopenings?\\b[^.]{0,80}\\b(?:screened|sealed|covered)\\b",\n\n        # Explicit absence / satisfactory actual states.\n        r"\\bno open\\b",\n        r"\\bfree of\\b",\n        r"\\bno evidence of\\b",\n        r"\\bno (?:rodent|pest|bird|insect) activity\\b",\n        r"\\bnegative for\\b",\n        r"\\bbelow threshold\\b",\n        r"\\bwithin acceptable range\\b",\n        r"\\bconfirmed clean\\b",\n        r"\\bserviceable\\b",\n        r"\\bin place and effective\\b",\n        r"\\bmaintained clean\\b",\n    )\n\n    return any(\n        re.search(pattern, t)\n        for pattern in patterns\n    )\n'

NEW_BUILD_FACTS = '\ndef build_fact_candidates(evidence_id: str, text: str) -> list[dict]:\n    """Build grounded facts; split any semantically mixed source atom."""\n    text = str(text or "")\n\n    start = len(text) - len(text.lstrip())\n    end = len(text.rstrip())\n\n    if end <= start:\n        return []\n\n    full_typed = classify_evidence_nature(text)\n    full_fact = _one(\n        evidence_id,\n        text,\n        start,\n        end,\n        text[start:end],\n    )\n\n    full_assertion = full_typed.get("assertion_mode", {})\n\n    needs_split = bool(\n        full_typed.get("requires_subspan_fact_split", False)\n        or full_fact.get("polarity") == MIXED\n        or full_fact.get("modality") == "MIXED"\n        or full_fact.get("speech_act") == "MIXED"\n        or full_assertion.get("modality") == "MIXED"\n        or full_assertion.get("speech_act") == "MIXED"\n    )\n\n    if not needs_split:\n        return [full_fact] if full_fact["grounding_valid"] else []\n\n    pieces = []\n\n    for seg_start, seg_end, seg_quote in _segments(text):\n        seg_typed = classify_evidence_nature(seg_quote)\n        seg_fact = _one(\n            evidence_id,\n            text,\n            seg_start,\n            seg_end,\n            seg_quote,\n        )\n        seg_assertion = seg_typed.get("assertion_mode", {})\n\n        segment_needs_split = bool(\n            seg_typed.get("requires_subspan_fact_split", False)\n            or seg_fact.get("polarity") == MIXED\n            or seg_fact.get("modality") == "MIXED"\n            or seg_fact.get("speech_act") == "MIXED"\n            or seg_assertion.get("modality") == "MIXED"\n            or seg_assertion.get("speech_act") == "MIXED"\n        )\n\n        if segment_needs_split and ";" in seg_quote:\n            pieces.extend(\n                _semicolon_segments(\n                    text,\n                    seg_start,\n                    seg_end,\n                )\n            )\n        else:\n            pieces.append(\n                (\n                    seg_start,\n                    seg_end,\n                    seg_quote,\n                )\n            )\n\n    facts = [\n        _one(\n            evidence_id,\n            text,\n            seg_start,\n            seg_end,\n            seg_quote,\n        )\n        for seg_start, seg_end, seg_quote in pieces\n        if seg_quote.strip()\n    ]\n\n    return [\n        fact\n        for fact in facts\n        if fact["grounding_valid"]\n    ]\n'
NEW_POLARITY = '\ndef _polarity(quote: str, typed: dict) -> str:\n    natures = set(typed.get("evidence_natures", []))\n    assertion = typed.get("assertion_mode", {})\n    actual = bool(assertion.get("actual_signal_present"))\n    signals = set(typed.get("clause_signals", []))\n\n    adverse = bool(\n        natures & {\n            "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",\n            "ADVERSE_OPERATIONAL_FINDING",\n            "EXPLICIT_CONTROL_ABSENCE",\n            "DOCUMENTATION_GAP",\n        }\n        or signals & {\n            "ADVERSE",\n            "MIXED",\n        }\n    )\n\n    positive = bool(\n        natures & {\n            "CURRENT_CONDITION",\n            "PHYSICAL_DESIGN_FEATURE",\n        }\n        or signals & {\n            "POSITIVE",\n            "MIXED",\n        }\n        or _explicit_positive_state(quote)\n    )\n\n    if (\n        adverse\n        and not actual\n        and _instructional_adverse(quote)\n    ):\n        adverse = False\n\n    explicit_adverse_state = bool(\n        re.search(\n            r"\\b(?:worn|cracked|broken|damaged|"\n            r"delaminat(?:ed|ion)|hole(?:s)?|"\n            r"repeated .* activity|"\n            r"delayed .* follow[- ]?up)\\b",\n            _norm(quote),\n        )\n    )\n\n    explicit_resolved_positive = (\n        _explicit_positive_state(quote)\n    )\n\n    # A generic physical-feature cue (mesh/gasket/seal/etc.) must not turn an\n    # explicit actual defect into MIXED.  If the quote says the feature is\n    # currently worn/cracked/delaminated/holed and does not also say it is\n    # sealed/closed/filled/resolved, the observable state is adverse.\n    if (\n        adverse\n        and actual\n        and explicit_adverse_state\n        and not explicit_resolved_positive\n    ):\n        positive = False\n\n    if (\n        explicit_resolved_positive\n        and "ADVERSE_OPERATIONAL_FINDING"\n        not in natures\n        and not explicit_adverse_state\n    ):\n        adverse = False\n\n    if positive and adverse:\n        return MIXED\n    if adverse:\n        return ADVERSE\n    if positive:\n        return POSITIVE\n    return NEUTRAL\n'

NEW_VALIDATE = '\ndef validate_alignment(raw: dict, pair: dict) -> dict:\n    """Validate fact-to-requirement relation only."""\n    from evidence_nature_v1 import assess_alignment_compatibility\n\n    requirement_id = pair["requirement"]["requirement_id"]\n    alignment_evidence_id = pair["evidence_id"]\n    parent_evidence_id = pair["parent_evidence_id"]\n\n    if raw.get("requirement_id") != requirement_id:\n        raise ValueError("Alignment returned wrong requirement_id")\n\n    if raw.get("evidence_id") != alignment_evidence_id:\n        raise ValueError("Alignment returned wrong evidence_id")\n\n    model_relation = raw.get("relation")\n\n    if model_relation not in ALIGNMENT_RELATIONS:\n        raise ValueError(\n            f"Invalid alignment relation {model_relation}"\n        )\n\n    exact_quote = str(\n        raw.get(\n            "exact_quote",\n            "",\n        )\n    ).strip()\n\n    if model_relation != "IRRELEVANT":\n        match_mode = quote_match_mode(\n            exact_quote,\n            pair["evidence_text"],\n        )\n\n        if match_mode is None:\n            raise ValueError(\n                f"{requirement_id}/{alignment_evidence_id}: "\n                "exact_quote is not grounded in FactCandidate"\n            )\n    else:\n        match_mode = (\n            quote_match_mode(\n                exact_quote,\n                pair["evidence_text"],\n            )\n            if exact_quote\n            else None\n        )\n\n    compatibility_role = {\n        "SUPPORT": "CORROBORATION_ONLY",\n        "ATTACK": "AMBIGUOUS",\n        "IRRELEVANT": "CONTEXT_ONLY",\n        "AMBIGUOUS": "AMBIGUOUS",\n    }[model_relation]\n\n    compatibility = assess_alignment_compatibility(\n        pair["requirement"],\n        exact_quote,\n        model_relation,\n        compatibility_role,\n    )\n\n    decision = compatibility[\n        "compatibility_decision"\n    ]\n\n    relation = model_relation\n\n    if model_relation in {\n        "SUPPORT",\n        "ATTACK",\n    } and decision in {\n        "INCOMPATIBLE",\n        "UNRESOLVED",\n    }:\n        relation = "AMBIGUOUS"\n\n    identity_use = pair.get(\n        "identity_use_decision",\n        "ADMIT_DIRECT",\n    )\n\n    accepted_for_alignment = bool(\n        relation in {\n            "SUPPORT",\n            "ATTACK",\n        }\n        and decision in {\n            "DIRECT",\n            "CORROBORATIVE",\n        }\n        and identity_use\n        != "EXCLUDE_SUBSTANTIVE"\n    )\n\n    identity_direct = bool(\n        pair.get(\n            "identity_decisive_proof_eligible",\n            True,\n        )\n    )\n\n    return {\n        "requirement_id":\n            requirement_id,\n        "atom_id":\n            pair["requirement"]["atom_id"],\n        "decisiveness":\n            pair["requirement"]["decisiveness"],\n\n        "evidence_id":\n            parent_evidence_id,\n        "alignment_evidence_id":\n            alignment_evidence_id,\n        "fact_candidate_id":\n            pair["fact_candidate_id"],\n        "fact_candidate":\n            pair["fact_candidate"],\n\n        "retrieval_need_ids":\n            pair["retrieval_need_ids"],\n\n        "identity_relation_to_case":\n            pair.get(\n                "identity_relation_to_case"\n            ),\n        "identity_use_decision":\n            identity_use,\n        "identity_decisive_proof_eligible":\n            identity_direct,\n        "identity_reason_code":\n            pair.get(\n                "identity_reason_code"\n            ),\n\n        "alignment_method":\n            str(\n                raw.get(\n                    "alignment_method",\n                    "MODEL",\n                )\n            ),\n\n        "model_relation":\n            model_relation,\n        "model_proof_role":\n            None,\n\n        "relation":\n            relation,\n        "alignment_strength":\n            decision,\n        "proof_role":\n            "DEFERRED_TO_ARGUMENT",\n\n        "exact_quote":\n            exact_quote,\n        "quote_match_mode":\n            match_mode,\n        "reason_code":\n            str(\n                raw.get(\n                    "reason_code",\n                    "",\n                )\n            ),\n        "reason":\n            str(\n                raw.get(\n                    "reason",\n                    "",\n                )\n            ),\n\n        "requirement_predicate_profile":\n            compatibility[\n                "requirement_profile"\n            ],\n        "evidence_nature":\n            compatibility[\n                "evidence_nature"\n            ],\n        "predicate_compatibility":\n            decision,\n        "predicate_compatibility_reason":\n            compatibility[\n                "compatibility_reason_code"\n            ],\n        "typed_gate_enforced":\n            compatibility[\n                "typed_gate_enforced"\n            ],\n\n        "accepted_for_alignment":\n            accepted_for_alignment,\n        "accepted_for_proof":\n            False,\n        "proof_deferred_to":\n            "ARGUMENT_AND_PROOF_STANDARD",\n    }\n'
NEW_AMBIGUOUS = '\ndef _ambiguous_alignment(pair: dict, reason: str) -> dict:\n    return {\n        "requirement_id":\n            pair["requirement"]["requirement_id"],\n        "atom_id":\n            pair["requirement"]["atom_id"],\n        "decisiveness":\n            pair["requirement"]["decisiveness"],\n\n        "evidence_id":\n            pair.get(\n                "parent_evidence_id",\n                pair["evidence_id"],\n            ),\n        "alignment_evidence_id":\n            pair["evidence_id"],\n        "fact_candidate_id":\n            pair.get("fact_candidate_id"),\n        "fact_candidate":\n            pair.get("fact_candidate"),\n\n        "retrieval_need_ids":\n            pair["retrieval_need_ids"],\n\n        "identity_relation_to_case":\n            pair.get(\n                "identity_relation_to_case"\n            ),\n        "identity_use_decision":\n            pair.get(\n                "identity_use_decision"\n            ),\n        "identity_decisive_proof_eligible":\n            pair.get(\n                "identity_decisive_proof_eligible",\n                False,\n            ),\n\n        "alignment_method":\n            "VALIDATOR",\n        "model_relation":\n            None,\n        "model_proof_role":\n            None,\n\n        "relation":\n            "AMBIGUOUS",\n        "alignment_strength":\n            "UNRESOLVED",\n        "proof_role":\n            "DEFERRED_TO_ARGUMENT",\n\n        "exact_quote":\n            "",\n        "quote_match_mode":\n            None,\n        "reason_code":\n            "ALIGNMENT_VALIDATION_FAILED",\n        "reason":\n            reason,\n\n        "accepted_for_alignment":\n            False,\n        "accepted_for_proof":\n            False,\n        "proof_deferred_to":\n            "ARGUMENT_AND_PROOF_STANDARD",\n    }\n'
NEW_GATE = '\ndef evaluate_minimal_proof_gate(\n    plan: dict,\n    traces: list[dict],\n    alignments: list[dict],\n) -> dict:\n    """Alignment-only diagnostic reducer until Argument/ProofStandard exists."""\n    traces_by_requirement = {}\n\n    for trace in traces:\n        traces_by_requirement.setdefault(\n            trace["requirement_id"],\n            [],\n        ).append(trace)\n\n    alignments_by_requirement = {}\n\n    for alignment in alignments:\n        alignments_by_requirement.setdefault(\n            alignment["requirement_id"],\n            [],\n        ).append(alignment)\n\n    requirement_reports = []\n    decisive_reports = []\n\n    for requirement in plan["requirements"]:\n        rid = requirement["requirement_id"]\n        rows = alignments_by_requirement.get(\n            rid,\n            [],\n        )\n\n        supports = [\n            row\n            for row in rows\n            if row.get(\n                "accepted_for_alignment"\n            )\n            and row.get(\n                "relation"\n            ) == "SUPPORT"\n        ]\n\n        attacks = [\n            row\n            for row in rows\n            if row.get(\n                "accepted_for_alignment"\n            )\n            and row.get(\n                "relation"\n            ) == "ATTACK"\n        ]\n\n        ambiguous = [\n            row\n            for row in rows\n            if row.get(\n                "relation"\n            ) == "AMBIGUOUS"\n        ]\n\n        raw_state = _state_from_pair(\n            bool(supports),\n            bool(attacks),\n        )\n\n        requirement_traces = (\n            traces_by_requirement.get(\n                rid,\n                [],\n            )\n        )\n\n        support_need_present = any(\n            trace["direction"] == "SUPPORT"\n            for trace in requirement_traces\n        )\n\n        attack_need_present = any(\n            trace["direction"] == "ATTACK"\n            for trace in requirement_traces\n        )\n\n        report = {\n            "requirement_id":\n                rid,\n            "atom_id":\n                requirement["atom_id"],\n            "decisiveness":\n                requirement["decisiveness"],\n\n            "raw_state":\n                raw_state,\n            "accepted_state":\n                "UNKNOWN",\n\n            "semantic_support_pass":\n                False,\n            "audit_sufficient_pass":\n                False,\n            "explicit_violation_pass":\n                False,\n\n            "contradiction_state":\n                (\n                    "PRESERVED"\n                    if attacks\n                    else "NONE"\n                ),\n\n            "support_need_present":\n                support_need_present,\n            "attack_need_present":\n                attack_need_present,\n\n            "coverage_status":\n                "ALIGNMENT_ONLY_ARGUMENT_PENDING",\n            "coverage_pass":\n                False,\n\n            "direct_support_evidence_ids":\n                [],\n            "corroboration_evidence_ids":\n                [],\n            "explicit_violation_evidence_ids":\n                [],\n            "ambiguous_evidence_ids":\n                [\n                    row["evidence_id"]\n                    for row in ambiguous\n                ],\n\n            "support_relation_evidence_ids":\n                [\n                    row["evidence_id"]\n                    for row in supports\n                ],\n            "attack_relation_evidence_ids":\n                [\n                    row["evidence_id"]\n                    for row in attacks\n                ],\n\n            "argument_status":\n                "PENDING",\n            "proof_standard_status":\n                "PENDING",\n        }\n\n        requirement_reports.append(\n            report\n        )\n\n        if (\n            requirement["decisiveness"]\n            == "DECISIVE"\n        ):\n            decisive_reports.append(\n                report\n            )\n\n    if not decisive_reports:\n        raise ValueError(\n            "No decisive EvidenceRequirement reports"\n        )\n\n    return {\n        "schema":\n            "freca-core-alignment-only-gate-v3",\n        "pilot_only":\n            True,\n\n        "coverage_complete":\n            False,\n        "coverage_note":\n            (\n                "Fact-to-requirement relations have been aligned, "\n                "but ArgumentTemplate and ProofStandard are not yet "\n                "connected. No relation is promoted to decisive proof."\n            ),\n\n        "requirement_reports":\n            requirement_reports,\n\n        "satisfaction_state":\n            "UNKNOWN",\n        "violation_state":\n            "UNKNOWN",\n        "candidate_outcome":\n            "UNKNOWN",\n        "candidate_submission_label":\n            None,\n\n        "evaluation_locked":\n            False,\n        "internal_outcome":\n            "UNKNOWN",\n        "submission_label":\n            None,\n\n        "argument_status":\n            "PENDING",\n        "proof_standard_status":\n            "PENDING",\n    }\n'

for path in (EVIDENCE, FACTS):
    if not path.exists():
        raise SystemExit(f"Missing {path}; run from ~/freca/core_v1")

def replace_top_level_function(src, name, replacement):
    tree = ast.parse(src)
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level function {name}, found {len(matches)}"
        )
    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n\n"]
    return "".join(lines)

def replace_top_level_assignment(src, name, value):
    tree = ast.parse(src)
    matches = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                matches.append(node)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                matches.append(node)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level assignment {name}, found {len(matches)}"
        )
    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [f"{name} = {value!r}\n"]
    return "".join(lines)

evidence_src = EVIDENCE.read_text(encoding="utf-8")
fact_src = FACTS.read_text(encoding="utf-8")

for marker in (
    "EVIDENCE_ALIGNMENT_SYSTEM",
    "validate_alignment",
    "_ambiguous_alignment",
    "evaluate_minimal_proof_gate",
    "fact_candidate_id",
):
    if marker not in evidence_src:
        raise SystemExit(f"Unexpected evidence_reasoning_v2.py; missing {marker}")

for marker in (
    "build_fact_candidates",
    "_explicit_positive_state",
    "_polarity",
    "_segments",
    "_semicolon_segments",
    "_one",
    "MIXED",
):
    if marker not in fact_src:
        raise SystemExit(f"Unexpected fact_candidate_v1.py; missing {marker}")

patched_evidence = replace_top_level_assignment(
    evidence_src,
    "EVIDENCE_ALIGNMENT_SYSTEM",
    NEW_SYSTEM,
)
patched_evidence = replace_top_level_function(
    patched_evidence,
    "validate_alignment",
    NEW_VALIDATE,
)
patched_evidence = replace_top_level_function(
    patched_evidence,
    "_ambiguous_alignment",
    NEW_AMBIGUOUS,
)
patched_evidence = replace_top_level_function(
    patched_evidence,
    "evaluate_minimal_proof_gate",
    NEW_GATE,
)
patched_facts = replace_top_level_function(
    fact_src,
    "_explicit_positive_state",
    NEW_EXPLICIT_POSITIVE,
)
patched_facts = replace_top_level_function(
    patched_facts,
    "_polarity",
    NEW_POLARITY,
)
patched_facts = replace_top_level_function(
    patched_facts,
    "build_fact_candidates",
    NEW_BUILD_FACTS,
)

# Hard validation BEFORE writing.
ast.parse(patched_evidence)
ast.parse(patched_facts)

if "Also classify proof_role:" in NEW_SYSTEM:
    raise RuntimeError("New prompt still asks for proof_role")
if '"proof_role"' in NEW_SYSTEM:
    raise RuntimeError("New prompt return schema still contains proof_role")
if '"accepted_for_proof":\n            False' not in patched_evidence:
    raise RuntimeError("accepted_for_proof=False invariant missing")
if "ARGUMENT_AND_PROOF_STANDARD" not in patched_evidence:
    raise RuntimeError("proof deferral marker missing")
if 'full_fact.get("modality") == "MIXED"' not in patched_facts:
    raise RuntimeError("FactCandidate MIXED modality trigger missing")
if "explicit actual defect into MIXED" not in patched_facts:
    raise RuntimeError("FactCandidate adverse-state precedence patch missing")
if "closed off" not in patched_facts:
    raise RuntimeError("Resolved-gap positive-state grammar patch missing")

backup_e = Path("evidence_reasoning_v2.before_boundary_fix_v2.py")
backup_f = Path("fact_candidate_v1.before_boundary_fix_v2.py")

if not backup_e.exists():
    shutil.copy2(EVIDENCE, backup_e)
if not backup_f.exists():
    shutil.copy2(FACTS, backup_f)

tmp_e = Path("evidence_reasoning_v2.boundary_fix_v2.tmp")
tmp_f = Path("fact_candidate_v1.boundary_fix_v2.tmp")

tmp_e.write_text(patched_evidence, encoding="utf-8")
tmp_f.write_text(patched_facts, encoding="utf-8")

# Validate bytes to be installed.
ast.parse(tmp_e.read_text(encoding="utf-8"))
ast.parse(tmp_f.read_text(encoding="utf-8"))

tmp_e.replace(EVIDENCE)
tmp_f.replace(FACTS)

print("Installed FRECA boundary fix v2.")
print("  - FactCandidate split trigger v1.1")
print("  - alignment model relation-only")
print("  - alignment rows never accepted as proof")
print("  - minimal proof gate is alignment-only")
print("Backups:")
print(f"  {backup_e}")
print(f"  {backup_f}")
