"""Minimal FRECA EvidenceNature × RequirementPredicate compatibility gate.

CP-blind, case-label blind, deterministic and trace-preserving.
The LLM alignment remains a semantic candidate generator; this module only
checks whether the quoted evidence kind is appropriate as *direct* proof for
the kind of proposition under test.
"""

from __future__ import annotations

import re
from typing import Any

TARGET_DESIGN_CONSTRUCTION = "DESIGN_CONSTRUCTION"
TARGET_CURRENT_CONDITION = "CURRENT_CONDITION"
TARGET_ACTIVITY_PERFORMED = "ACTIVITY_PERFORMED"
TARGET_RECORDKEEPING = "RECORDKEEPING"
TARGET_PROCEDURE_EXISTS = "PROCEDURE_OR_PLAN_EXISTS"
TARGET_REGISTRATION_STATUS = "REGISTRATION_STATUS"
TARGET_DOCUMENTATION = "DOCUMENTATION"
TARGET_OUTCOME_STATE = "OUTCOME_STATE"
TARGET_UNKNOWN = "UNKNOWN"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def infer_requirement_predicate_profile(requirement: dict) -> dict:
    parts = [str(requirement.get("proposition_to_establish", ""))]
    for source in requirement.get("query_sources", []):
        if source.get("source") == "RULES":
            parts.append(str(source.get("quote", "")))

    text = _norm(" ".join(parts))
    kinds: list[str] = []
    reasons: list[str] = []

    def add(kind: str, reason: str) -> None:
        if kind not in kinds:
            kinds.append(kind)
            reasons.append(reason)

    if re.search(
        r"\b(design(?:ed)?|construct(?:ed|ion)?|purpose[- ]built|"
        r"structural|layout|fabric|built so as to|constructed so as to)\b",
        text,
    ):
        add(TARGET_DESIGN_CONSTRUCTION, "REQ_DESIGN_CONSTRUCTION_LANGUAGE")

    if re.search(
        r"\b(kept in (?:a )?condition|kept clean|clean and hygienic|"
        r"maintain(?:ed|ing)? (?:the )?(?:establishment|facility|premises|condition)|"
        r"pests? (?:and contaminants? )?(?:must be )?controlled|"
        r"contaminants? (?:must be )?controlled|current condition|condition that ensures)\b",
        text,
    ):
        add(TARGET_CURRENT_CONDITION, "REQ_CURRENT_CONDITION_LANGUAGE")

    if re.search(
        r"\b(carry out|perform(?:ed)?|inspect(?:ed|ion)?|monitor(?:ed|ing)?|"
        r"sample(?:d|ing)?|treat(?:ed|ment)?|undertake|conduct(?:ed)?)\b",
        text,
    ) and not kinds:
        add(TARGET_ACTIVITY_PERFORMED, "REQ_ACTIVITY_LANGUAGE")

    if re.search(r"\b(record(?:s|keeping)?|retain(?:ed|ing)? records?|register|log)\b", text):
        add(TARGET_RECORDKEEPING, "REQ_RECORDKEEPING_LANGUAGE")

    if re.search(r"\b(procedure|plan|program(?:me)?|system|policy)\b", text) and not {
        TARGET_DESIGN_CONSTRUCTION,
        TARGET_CURRENT_CONDITION,
    }.intersection(kinds):
        add(TARGET_PROCEDURE_EXISTS, "REQ_PROCEDURE_LANGUAGE")

    if re.search(
        r"\b(registration|registered establishment|registered for|registration status)\b",
        text,
    ) and not {TARGET_DESIGN_CONSTRUCTION, TARGET_CURRENT_CONDITION}.intersection(kinds):
        add(TARGET_REGISTRATION_STATUS, "REQ_REGISTRATION_LANGUAGE")

    if re.search(r"\b(document(?:ed|ation)?|written record|documentary)\b", text) and not kinds:
        add(TARGET_DOCUMENTATION, "REQ_DOCUMENTATION_LANGUAGE")

    if re.search(
        r"\b(absence of|free from|not infested|not contaminated|no infestation|no contamination)\b",
        text,
    ) and not kinds:
        add(TARGET_OUTCOME_STATE, "REQ_OUTCOME_STATE_LANGUAGE")

    if not kinds:
        kinds = [TARGET_UNKNOWN]
        reasons = ["REQ_TARGET_UNRESOLVED"]

    # A design proposition can mention cleaning/pest outcomes as its purpose.
    # Explicit design/construction language owns the target type.
    if TARGET_DESIGN_CONSTRUCTION in kinds:
        kinds = [
            kind for kind in kinds
            if kind not in {TARGET_CURRENT_CONDITION, TARGET_OUTCOME_STATE}
        ] or [TARGET_DESIGN_CONSTRUCTION]

    return {
        "target_kinds": kinds,
        "reason_codes": reasons,
        "profile_confident": TARGET_UNKNOWN not in kinds,
    }


NATURE_PHYSICAL_FEATURE = "PHYSICAL_DESIGN_FEATURE"
NATURE_DESIGN_DEFECT = "DESIGN_OR_CONSTRUCTION_DEFECT"
NATURE_CURRENT_CONDITION = "CURRENT_CONDITION"
NATURE_CURRENT_DEFECT = "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT"
NATURE_ACTIVITY_RECORD = "ACTIVITY_RECORD"
NATURE_OBSERVATION_RECORD = "OBSERVATION_RECORD"
NATURE_PROCEDURE_STATEMENT = "PROCEDURE_STATEMENT"
NATURE_PLAN_STATEMENT = "PLAN_STATEMENT"
NATURE_DOCUMENTATION_GAP = "DOCUMENTATION_GAP"
NATURE_RECORD_EXISTS = "RECORD_EXISTS"
NATURE_SOURCE_EVALUATION = "SOURCE_EVALUATION"
NATURE_CONTROL_ABSENCE = "EXPLICIT_CONTROL_ABSENCE"
NATURE_UNKNOWN = "UNKNOWN"


def classify_evidence_nature(exact_quote: str) -> dict:
    text = _norm(exact_quote)
    kinds: list[str] = []
    reasons: list[str] = []

    def add(kind: str, reason: str) -> None:
        if kind not in kinds:
            kinds.append(kind)
            reasons.append(reason)

    if re.search(
        r"\b(non[- ]compliant|compliant|deficien(?:cy|t)|fail(?:ed|ure)?|"
        r"pass(?:ed)?|audit finding|finding code)\b",
        text,
    ):
        add(NATURE_SOURCE_EVALUATION, "EVID_SOURCE_EVALUATION_LANGUAGE")

    if re.search(
        r"\b(not documented|not included|no .* documented|no .* records? (?:are )?documented|"
        r"no .* section|documentation (?:is )?absent|missing documentation)\b",
        text,
    ):
        add(NATURE_DOCUMENTATION_GAP, "EVID_DOCUMENTATION_GAP_LANGUAGE")

    if re.search(
        r"\b(no controls? (?:exist|are in place|to prevent)|without any controls?|"
        r"control measures? (?:are )?absent)\b",
        text,
    ):
        add(NATURE_CONTROL_ABSENCE, "EVID_EXPLICIT_CONTROL_ABSENCE")

    if re.search(
        r"\b(design flaw|design defect|poorly designed|not designed|construction defect|"
        r"poorly constructed|not constructed|structurally inadequate|unsuitable construction)\b",
        text,
    ):
        add(NATURE_DESIGN_DEFECT, "EVID_EXPLICIT_DESIGN_DEFECT")

    if re.search(
        r"\b(worn|wear|gap(?:s)?|hole(?:s)?|delaminat(?:ed|ion)|broken|damaged|"
        r"crack(?:ed|s)?|leak(?:ing|s)?|maintenance items? (?:have )?accumulated|"
        r"missing seal|open penetration|pest entry)\b",
        text,
    ):
        add(NATURE_CURRENT_DEFECT, "EVID_CURRENT_DEFECT_LANGUAGE")

    if re.search(
        r"\b(smooth|non[- ]porous|cleanable|stainless[- ]steel|concrete flooring|"
        r"steel[- ]frame|purpose[- ]built|hinged access panels?|sealed surface|epoxy floor|"
        r"wall lining|mesh|gasket|threshold seal|installed)\b",
        text,
    ):
        add(NATURE_PHYSICAL_FEATURE, "EVID_PHYSICAL_FEATURE_LANGUAGE")

    if re.search(
        r"\b(current pest status|within acceptable range|no active infestation|"
        r"no evidence of (?:bird|pest|rodent|insect) entry|is clean|are clean|kept clean|"
        r"clean and hygienic|free from (?:pests?|contamination)|condition (?:was|is) satisfactory)\b",
        text,
    ):
        add(NATURE_CURRENT_CONDITION, "EVID_CURRENT_CONDITION_LANGUAGE")

    if re.search(
        r"\b(observed|observation|inspection found|inspection identified|noted during inspection|"
        r"at inspection|visual inspection (?:found|identified))\b",
        text,
    ):
        add(NATURE_OBSERVATION_RECORD, "EVID_OBSERVATION_LANGUAGE")

    if re.search(
        r"\b(completed on|performed on|carried out on|cleaned on|inspected on|serviced on|"
        r"last service|recorded on|register entry|record shows|signed on|dated \d{1,2}\b)\b",
        text,
    ):
        add(NATURE_ACTIVITY_RECORD, "EVID_SPECIFIC_ACTIVITY_RECORD")

    if re.search(r"\b(record|register|log|certificate|inspection report)\b", text) and not (
        NATURE_DOCUMENTATION_GAP in kinds or NATURE_ACTIVITY_RECORD in kinds
    ):
        add(NATURE_RECORD_EXISTS, "EVID_RECORD_REFERENCE")

    if re.search(r"\b(plan|management plan|site plan|action plan|schedule)\b", text):
        add(NATURE_PLAN_STATEMENT, "EVID_PLAN_LANGUAGE")

    if re.search(
        r"\b(procedure|program(?:me)?|policy|sop\b|required\b|must\b|shall\b|should\b|"
        r"to be (?:cleaned|inspected|maintained|performed))",
        text,
    ):
        add(NATURE_PROCEDURE_STATEMENT, "EVID_PROCEDURE_OR_NORMATIVE_LANGUAGE")

    if not kinds:
        kinds = [NATURE_UNKNOWN]
        reasons = ["EVID_NATURE_UNRESOLVED"]

    return {"evidence_natures": kinds, "reason_codes": reasons}


DIRECT = "DIRECT"
CORROBORATIVE = "CORROBORATIVE"
INCOMPATIBLE = "INCOMPATIBLE"
UNRESOLVED = "UNRESOLVED"


def assess_alignment_compatibility(
    requirement: dict,
    exact_quote: str,
    model_relation: str,
    model_proof_role: str,
) -> dict[str, Any]:
    req = infer_requirement_predicate_profile(requirement)
    evid = classify_evidence_nature(exact_quote)
    targets = set(req["target_kinds"])
    natures = set(evid["evidence_natures"])

    decision = UNRESOLVED
    reason = "COMPATIBILITY_NOT_TYPED"

    if TARGET_DESIGN_CONSTRUCTION in targets:
        if model_relation == "SUPPORT":
            if NATURE_PHYSICAL_FEATURE in natures:
                decision = DIRECT
                reason = "DESIGN_SUPPORTED_BY_PHYSICAL_FEATURE"
            elif natures & {
                NATURE_PROCEDURE_STATEMENT,
                NATURE_PLAN_STATEMENT,
                NATURE_CURRENT_CONDITION,
                NATURE_ACTIVITY_RECORD,
            }:
                decision = CORROBORATIVE
                reason = "DESIGN_NOT_DIRECTLY_ESTABLISHED_BY_OPERATIONAL_EVIDENCE"
            else:
                reason = "DESIGN_SUPPORT_NATURE_UNRESOLVED"

        elif model_relation == "ATTACK":
            if NATURE_DESIGN_DEFECT in natures:
                decision = DIRECT
                reason = "DESIGN_ATTACKED_BY_EXPLICIT_DESIGN_DEFECT"
            elif NATURE_CURRENT_DEFECT in natures:
                decision = INCOMPATIBLE
                reason = "CURRENT_MAINTENANCE_DEFECT_DOES_NOT_BY_ITSELF_PROVE_DESIGN_DEFECT"
            elif NATURE_DOCUMENTATION_GAP in natures:
                decision = INCOMPATIBLE
                reason = "DOCUMENTATION_GAP_DOES_NOT_BY_ITSELF_PROVE_DESIGN_DEFECT"
            else:
                reason = "DESIGN_ATTACK_NATURE_UNRESOLVED"

    elif TARGET_CURRENT_CONDITION in targets:
        if model_relation == "SUPPORT":
            if natures & {
                NATURE_CURRENT_CONDITION,
                NATURE_OBSERVATION_RECORD,
                NATURE_ACTIVITY_RECORD,
            }:
                decision = DIRECT
                reason = "CURRENT_CONDITION_SUPPORTED_BY_CURRENT_OR_PERFORMED_EVIDENCE"
            elif natures & {
                NATURE_PHYSICAL_FEATURE,
                NATURE_PROCEDURE_STATEMENT,
                NATURE_PLAN_STATEMENT,
                NATURE_RECORD_EXISTS,
            }:
                decision = CORROBORATIVE
                reason = "DESIGN_OR_PROCEDURE_DOES_NOT_BY_ITSELF_PROVE_CURRENT_CONDITION"
            else:
                reason = "CURRENT_CONDITION_SUPPORT_NATURE_UNRESOLVED"

        elif model_relation == "ATTACK":
            if natures & {
                NATURE_CURRENT_DEFECT,
                NATURE_OBSERVATION_RECORD,
                NATURE_CONTROL_ABSENCE,
            } and NATURE_DOCUMENTATION_GAP not in natures:
                decision = DIRECT
                reason = "CURRENT_CONDITION_ATTACKED_BY_EXPLICIT_ADVERSE_CONDITION"
            elif NATURE_CURRENT_DEFECT in natures:
                decision = CORROBORATIVE
                reason = "ADVERSE_CONDITION_MIXED_WITH_NON_SUBSTANTIVE_LANGUAGE"
            elif NATURE_DOCUMENTATION_GAP in natures:
                decision = INCOMPATIBLE
                reason = "DOCUMENTATION_GAP_IS_NOT_ACTUAL_CONDITION_FAILURE"
            else:
                reason = "CURRENT_CONDITION_ATTACK_NATURE_UNRESOLVED"

    elif TARGET_ACTIVITY_PERFORMED in targets:
        if model_relation == "SUPPORT":
            if natures & {NATURE_ACTIVITY_RECORD, NATURE_OBSERVATION_RECORD}:
                decision = DIRECT
                reason = "ACTIVITY_SUPPORTED_BY_PERFORMED_OR_OBSERVED_EVIDENCE"
            elif natures & {NATURE_PROCEDURE_STATEMENT, NATURE_PLAN_STATEMENT}:
                decision = CORROBORATIVE
                reason = "PLAN_OR_PROCEDURE_DOES_NOT_PROVE_ACTIVITY_OCCURRED"
        elif model_relation == "ATTACK" and NATURE_DOCUMENTATION_GAP in natures:
            decision = INCOMPATIBLE
            reason = "MISSING_DOCUMENTATION_DOES_NOT_PROVE_ACTIVITY_DID_NOT_OCCUR"

    elif targets & {TARGET_RECORDKEEPING, TARGET_DOCUMENTATION}:
        if model_relation == "SUPPORT" and natures & {NATURE_RECORD_EXISTS, NATURE_ACTIVITY_RECORD}:
            decision = DIRECT
            reason = "RECORD_REQUIREMENT_SUPPORTED_BY_RECORD_EVIDENCE"
        elif model_relation == "ATTACK" and NATURE_DOCUMENTATION_GAP in natures:
            decision = DIRECT
            reason = "DOCUMENTATION_REQUIREMENT_ATTACKED_BY_DOCUMENTATION_GAP"

    elif TARGET_PROCEDURE_EXISTS in targets:
        if model_relation == "SUPPORT" and natures & {
            NATURE_PROCEDURE_STATEMENT,
            NATURE_PLAN_STATEMENT,
        }:
            decision = DIRECT
            reason = "PROCEDURE_REQUIREMENT_SUPPORTED_BY_PROCEDURE_TEXT"
        elif model_relation == "ATTACK" and NATURE_DOCUMENTATION_GAP in natures:
            decision = CORROBORATIVE
            reason = "NOT_DOCUMENTED_DOES_NOT_NECESSARILY_MEAN_NO_PROCEDURE_EXISTS"

    if decision == UNRESOLVED and not req["profile_confident"]:
        reason = "REQUIREMENT_PROFILE_UNRESOLVED"

    return {
        "requirement_profile": req,
        "evidence_nature": evid,
        "compatibility_decision": decision,
        "compatibility_reason_code": reason,
        "typed_gate_enforced": req["profile_confident"],
    }


def effective_alignment(
    *,
    model_relation: str,
    model_proof_role: str,
    compatibility: dict,
) -> dict:
    decision = compatibility["compatibility_decision"]
    relation = model_relation
    proof_role = model_proof_role
    decisive_eligible = True

    if decision == DIRECT:
        pass
    elif decision == CORROBORATIVE:
        if model_relation == "SUPPORT":
            relation = "SUPPORT"
            proof_role = "CORROBORATION_ONLY"
        elif model_relation == "ATTACK":
            relation = "AMBIGUOUS"
            proof_role = "AMBIGUOUS"
            decisive_eligible = False
    elif decision == INCOMPATIBLE:
        relation = "AMBIGUOUS"
        proof_role = "AMBIGUOUS"
        decisive_eligible = False
    elif decision == UNRESOLVED and compatibility.get("typed_gate_enforced"):
        relation = "AMBIGUOUS"
        proof_role = "AMBIGUOUS"
        decisive_eligible = False

    return {
        "relation": relation,
        "proof_role": proof_role,
        "typed_decisive_proof_eligible": decisive_eligible,
    }
