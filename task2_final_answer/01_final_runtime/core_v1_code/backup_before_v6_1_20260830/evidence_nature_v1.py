'''FRECA Core EvidenceNature v1.1.

Minimal deterministic bridge extracted from the frozen Layer-4/5 design:
- event modality: ACTUAL / PLANNED / REQUIRED / CONDITIONAL / ...
- source speech act: PROCEDURE / OBSERVATION / RECORD_ENTRY / ...
- evidence nature
- quoted-span inference scope
- mixed-fact detection

This module is CP-label blind and case-label blind.
It does not decide final compliance.
'''

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Requirement predicate profile
# ---------------------------------------------------------------------------

TARGET_DESIGN_CONSTRUCTION = "DESIGN_CONSTRUCTION"
TARGET_CURRENT_CONDITION = "CURRENT_CONDITION"
TARGET_ACTIVITY_PERFORMED = "ACTIVITY_PERFORMED"
TARGET_RECORDKEEPING = "RECORDKEEPING"
TARGET_PROCEDURE_EXISTS = "PROCEDURE_OR_PLAN_EXISTS"
TARGET_REGISTRATION_STATUS = "REGISTRATION_STATUS"
TARGET_DOCUMENTATION = "DOCUMENTATION"
TARGET_OUTCOME_STATE = "OUTCOME_STATE"
TARGET_UNKNOWN = "UNKNOWN"

TARGET_TIME_ONGOING = "ONGOING_STATE"
TARGET_TIME_EVENT = "SPECIFIED_EVENT"
TARGET_TIME_POINT = "POINT_IN_TIME"
TARGET_TIME_UNKNOWN = "UNKNOWN"


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

    if re.search(
        r"\b(record(?:s|keeping)?|retain(?:ed|ing)? records?|register|log)\b",
        text,
    ):
        add(TARGET_RECORDKEEPING, "REQ_RECORDKEEPING_LANGUAGE")

    if re.search(
        r"\b(procedure|plan|program(?:me)?|system|policy)\b",
        text,
    ) and not {
        TARGET_DESIGN_CONSTRUCTION,
        TARGET_CURRENT_CONDITION,
    }.intersection(kinds):
        add(TARGET_PROCEDURE_EXISTS, "REQ_PROCEDURE_LANGUAGE")

    if re.search(
        r"\b(registration|registered establishment|registered for|registration status)\b",
        text,
    ) and not {
        TARGET_DESIGN_CONSTRUCTION,
        TARGET_CURRENT_CONDITION,
    }.intersection(kinds):
        add(TARGET_REGISTRATION_STATUS, "REQ_REGISTRATION_LANGUAGE")

    if re.search(
        r"\b(document(?:ed|ation)?|written record|documentary)\b",
        text,
    ) and not kinds:
        add(TARGET_DOCUMENTATION, "REQ_DOCUMENTATION_LANGUAGE")

    if re.search(
        r"\b(absence of|free from|not infested|not contaminated|"
        r"no infestation|no contamination)\b",
        text,
    ) and not kinds:
        add(TARGET_OUTCOME_STATE, "REQ_OUTCOME_STATE_LANGUAGE")

    if not kinds:
        kinds = [TARGET_UNKNOWN]
        reasons = ["REQ_TARGET_UNRESOLVED"]

    # Design wording owns the target type even if its purpose mentions cleaning
    # or infestation outcomes.
    if TARGET_DESIGN_CONSTRUCTION in kinds:
        kinds = [
            kind
            for kind in kinds
            if kind not in {
                TARGET_CURRENT_CONDITION,
                TARGET_OUTCOME_STATE,
            }
        ] or [TARGET_DESIGN_CONSTRUCTION]

    if TARGET_CURRENT_CONDITION in kinds and re.search(
        r"\b(kept|maintain(?:ed|ing)?|condition that ensures|controlled appropriately)\b",
        text,
    ):
        target_temporality = TARGET_TIME_ONGOING
    elif TARGET_ACTIVITY_PERFORMED in kinds:
        target_temporality = TARGET_TIME_EVENT
    else:
        target_temporality = TARGET_TIME_UNKNOWN

    return {
        "target_kinds": kinds,
        "target_temporality": target_temporality,
        "reason_codes": reasons,
        "profile_confident": TARGET_UNKNOWN not in kinds,
    }


# ---------------------------------------------------------------------------
# Event modality / speech act
# ---------------------------------------------------------------------------

MODALITY_ACTUAL = "ACTUAL"
MODALITY_PLANNED = "PLANNED"
MODALITY_REQUIRED = "REQUIRED"
MODALITY_PERMITTED = "PERMITTED"
MODALITY_CONDITIONAL = "CONDITIONAL"
MODALITY_HYPOTHETICAL = "HYPOTHETICAL"
MODALITY_MIXED = "MIXED"
MODALITY_UNKNOWN = "UNKNOWN"

SPEECH_DOCUMENT_METADATA = "DOCUMENT_METADATA"
SPEECH_DECLARATION = "DECLARATION"
SPEECH_PROCEDURE = "PROCEDURE"
SPEECH_RECORD_ENTRY = "RECORD_ENTRY"
SPEECH_OBSERVATION = "OBSERVATION"
SPEECH_INSTRUCTION = "INSTRUCTION"
SPEECH_SOURCE_EVALUATION = "SOURCE_EVALUATION"
SPEECH_MIXED = "MIXED"
SPEECH_UNKNOWN = "UNKNOWN"

SCOPE_POINT_IN_TIME = "POINT_IN_TIME"
SCOPE_SPECIFIED_EVENT = "SPECIFIED_EVENT"
SCOPE_SPECIFIED_PERIOD = "SPECIFIED_PERIOD"
SCOPE_GENERAL_STATEMENT = "GENERAL_STATEMENT"
SCOPE_UNKNOWN = "UNKNOWN"


def classify_assertion_mode(exact_quote: str) -> dict:
    text = _norm(exact_quote)

    required = bool(re.search(
        r"\b(must|shall|required(?: to| by)?|responsible for|"
        r"are to be|is to be|should|to be (?:cleaned|inspected|maintained|recorded|performed)|"
        r"(?:daily|weekly|monthly|annually) as scheduled)\b",
        text,
    ))

    planned = bool(re.search(
        r"\b(will be|will inspect|will clean|planned|scheduled to|"
        r"proposed|maintenance schedule|action plan)\b",
        text,
    ))

    conditional = bool(re.search(
        r"(?:^|[.;!?]\s+)(?:if|when|unless)\b|\bin the event of\b|\bshould [^.?!;]* occur\b",
        text,
    ))

    permitted = bool(re.search(
        r"\b(?:permitted to|allowed to|may (?=[a-z]))",
        text,
    ))

    source_eval = bool(re.search(
        r"\b(non[- ]compliant|compliant|deficien(?:cy|t)|audit finding|"
        r"pass(?:ed)?|fail(?:ed|ure)?|finding code)\b",
        text,
    ))

    actual_observation = bool(re.search(
        r"\b(was inspected|were inspected|inspection (?:found|identified|confirmed)|"
        r"review (?:found|identified|confirmed)|review .* conducted|"
        r"conducted on \d|observed|noted during inspection|"
        r"trend review identifies|confirmed (?:that )?|"
        r"last service:|completed on|performed on|carried out on|"
        r"cleaned on|inspected on|serviced on|record shows|register entry)\b",
        text,
    ))

    actual_state = bool(re.search(
        r"\b(current pest status|no active infestation|"
        r"no evidence of (?:bird|pest|rodent|insect) entry|"
        r"worn|delaminat(?:ed|ion)|broken|damaged|"
        r"gap(?:s)?|hole(?:s)?|crack(?:ed|s)?|"
        r"maintenance items? (?:have )?accumulated|"
        r"repeated .* activity|delayed .* follow[- ]?up|"
        r"no corrective actions outstanding|"
        r"were confirmed clean|was confirmed clean)\b",
        text,
    ))

    actual = actual_observation or actual_state

    modes = []
    if actual:
        modes.append(MODALITY_ACTUAL)
    if required:
        modes.append(MODALITY_REQUIRED)
    if planned:
        modes.append(MODALITY_PLANNED)
    if permitted:
        modes.append(MODALITY_PERMITTED)
    if conditional:
        modes.append(MODALITY_CONDITIONAL)

    if not modes:
        modality = MODALITY_UNKNOWN
    elif len(modes) == 1:
        modality = modes[0]
    else:
        modality = MODALITY_MIXED

    speeches = []
    if source_eval:
        speeches.append(SPEECH_SOURCE_EVALUATION)
    if actual_observation:
        speeches.append(SPEECH_OBSERVATION)
    if required or planned:
        speeches.append(SPEECH_PROCEDURE)
    if re.search(
        r"\b(register entry|record shows|log entry|certificate|inspection report)\b",
        text,
    ):
        speeches.append(SPEECH_RECORD_ENTRY)
    if not speeches and actual:
        speeches.append(SPEECH_DECLARATION)

    speeches = list(dict.fromkeys(speeches))
    if not speeches:
        speech_act = SPEECH_UNKNOWN
    elif len(speeches) == 1:
        speech_act = speeches[0]
    else:
        speech_act = SPEECH_MIXED

    # Inference scope is about what the quoted statement itself can establish.
    if re.search(
        r"\b(on|dated|conducted)\s+\d{1,2}\s+[a-z]+\s+\d{4}\b",
        text,
    ) or re.search(
        r"\b(last service:|inspection on|review on)\b",
        text,
    ):
        inference_scope = SCOPE_POINT_IN_TIME
    elif re.search(
        r"\b(following each production run|between commodity lots|"
        r"after each|before each)\b",
        text,
    ):
        inference_scope = SCOPE_SPECIFIED_EVENT
    elif re.search(
        r"\b(during (?:the )?(?:season|month|year|period)|"
        r"for the period|throughout the period)\b",
        text,
    ):
        inference_scope = SCOPE_SPECIFIED_PERIOD
    elif modality in {
        MODALITY_REQUIRED,
        MODALITY_PLANNED,
        MODALITY_MIXED,
    }:
        inference_scope = SCOPE_GENERAL_STATEMENT
    else:
        inference_scope = SCOPE_UNKNOWN

    return {
        "modality": modality,
        "detected_modalities": modes,
        "speech_act": speech_act,
        "detected_speech_acts": speeches,
        "inference_scope": inference_scope,
        "actual_signal_present": actual,
        "normative_signal_present": required or planned,
        "source_evaluation_present": source_eval,
    }


# ---------------------------------------------------------------------------
# Evidence nature
# ---------------------------------------------------------------------------

NATURE_PHYSICAL_FEATURE = "PHYSICAL_DESIGN_FEATURE"
NATURE_DESIGN_DEFECT = "DESIGN_OR_CONSTRUCTION_DEFECT"
NATURE_CURRENT_CONDITION = "CURRENT_CONDITION"
NATURE_CURRENT_DEFECT = "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT"
NATURE_ACTIVITY_RECORD = "ACTIVITY_RECORD"
NATURE_OBSERVATION_RECORD = "OBSERVATION_RECORD"
NATURE_REVIEW_FINDING = "REVIEW_FINDING"
NATURE_PROCEDURE_STATEMENT = "PROCEDURE_STATEMENT"
NATURE_PLAN_STATEMENT = "PLAN_STATEMENT"
NATURE_DOCUMENTATION_GAP = "DOCUMENTATION_GAP"
NATURE_RECORD_EXISTS = "RECORD_EXISTS"
NATURE_SOURCE_EVALUATION = "SOURCE_EVALUATION"
NATURE_CONTROL_ABSENCE = "EXPLICIT_CONTROL_ABSENCE"
NATURE_ADVERSE_OPERATIONAL_FINDING = "ADVERSE_OPERATIONAL_FINDING"
NATURE_UNKNOWN = "UNKNOWN"


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|[;\n•]+", text or "")
    return [part.strip() for part in parts if part.strip()]


def _clause_signal(clause: str) -> str:
    text = _norm(clause)

    adverse = bool(re.search(
        r"\b(worn|delaminat(?:ed|ion)|broken|damaged|gap(?:s)?|hole(?:s)?|"
        r"repeated .* activity|delayed .* follow[- ]?up|"
        r"(?<!no )corrective actions? outstanding|active infestation|"
        r"pest activity (?:was )?(?:found|identified|detected)|"
        r"not controlled|control failure)\b",
        text,
    ))

    positive = bool(re.search(
        r"\b(confirmed clean|free of (?:product residue|pest activity|hygiene deficiencies|"
        r"pests?|contamination|harbourage)|no active infestation|"
        r"no evidence of .* entry|within acceptable range|"
        r"maintained clean and free of harbourage|"
        r"all areas are maintained|all .* confirmed clean)\b",
        text,
    ))

    if adverse and positive:
        return "MIXED"
    if adverse:
        return "ADVERSE"
    if positive:
        return "POSITIVE"
    return "NEUTRAL"


def classify_evidence_nature(exact_quote: str) -> dict:
    text = _norm(exact_quote)
    assertion = classify_assertion_mode(exact_quote)

    kinds: list[str] = []
    reasons: list[str] = []

    def add(kind: str, reason: str) -> None:
        if kind not in kinds:
            kinds.append(kind)
            reasons.append(reason)

    if assertion["source_evaluation_present"]:
        add(NATURE_SOURCE_EVALUATION, "EVID_SOURCE_EVALUATION_LANGUAGE")

    if re.search(
        r"\b(not documented|not included|no .* documented|"
        r"no .* records? (?:are )?documented|no .* section|"
        r"documentation (?:is )?absent|missing documentation)\b",
        text,
    ):
        add(NATURE_DOCUMENTATION_GAP, "EVID_DOCUMENTATION_GAP_LANGUAGE")

    if re.search(
        r"\b(no controls? (?:exist|are in place|to prevent)|"
        r"without any controls?|control measures? (?:are )?absent)\b",
        text,
    ):
        add(NATURE_CONTROL_ABSENCE, "EVID_EXPLICIT_CONTROL_ABSENCE")

    if re.search(
        r"\b(design flaw|design defect|poorly designed|not designed|"
        r"construction defect|poorly constructed|not constructed|"
        r"structurally inadequate|unsuitable construction)\b",
        text,
    ):
        add(NATURE_DESIGN_DEFECT, "EVID_EXPLICIT_DESIGN_DEFECT")

    if re.search(
        r"\b(worn|wear|gap(?:s)?|hole(?:s)?|delaminat(?:ed|ion)|"
        r"broken|damaged|crack(?:ed|s)?|leak(?:ing|s)?|"
        r"maintenance items? (?:have )?accumulated|missing seal|"
        r"open penetration|pest entry)\b",
        text,
    ):
        add(NATURE_CURRENT_DEFECT, "EVID_CURRENT_DEFECT_LANGUAGE")

    if re.search(
        r"\b(repeated .* activity|delayed .* follow[- ]?up|"
        r"(?<!no )corrective actions? outstanding|control failure|"
        r"trend review identifies)\b",
        text,
    ):
        add(
            NATURE_ADVERSE_OPERATIONAL_FINDING,
            "EVID_ADVERSE_OPERATIONAL_FINDING",
        )

    if re.search(
        r"\b(smooth|non[- ]porous|cleanable|stainless[- ]steel|"
        r"concrete flooring|steel[- ]frame|purpose[- ]built|"
        r"hinged access panels?|sealed surface|epoxy floor|"
        r"wall lining|mesh|gasket|threshold seal|installed|"
        r"all joints sealed|no open eaves|roof penetrations .* sealed)\b",
        text,
    ):
        add(NATURE_PHYSICAL_FEATURE, "EVID_PHYSICAL_FEATURE_LANGUAGE")

    # Actual condition signals are admitted only when the quote contains an
    # ACTUAL signal.  Normative "responsible for maintaining X clean" must not
    # become CURRENT_CONDITION merely because the desired state is named.
    if assertion["actual_signal_present"] and re.search(
        r"\b(current pest status|within acceptable range|no active infestation|"
        r"no evidence of (?:bird|pest|rodent|insect) entry|"
        r"confirmed clean|were confirmed clean|was confirmed clean|"
        r"free of (?:product residue|pest activity|hygiene deficiencies|"
        r"pests?|contamination|harbourage)|"
        r"no corrective actions outstanding|"
        r"maintained clean and free of harbourage)\b",
        text,
    ):
        add(NATURE_CURRENT_CONDITION, "EVID_ACTUAL_CURRENT_CONDITION_LANGUAGE")

    if assertion["speech_act"] in {
        SPEECH_OBSERVATION,
        SPEECH_MIXED,
    } and assertion["actual_signal_present"]:
        add(NATURE_OBSERVATION_RECORD, "EVID_OBSERVATION_LANGUAGE")

    if re.search(
        r"\b(review .* conducted|review (?:found|identified|confirmed)|"
        r"trend review identifies|inspection (?:found|identified|confirmed)|"
        r"was inspected|were inspected)\b",
        text,
    ):
        add(NATURE_REVIEW_FINDING, "EVID_REVIEW_OR_INSPECTION_FINDING")

    if re.search(
        r"\b(completed on|performed on|carried out on|cleaned on|"
        r"inspected on|serviced on|last service:|recorded on|"
        r"register entry|record shows|signed on)\b",
        text,
    ):
        add(NATURE_ACTIVITY_RECORD, "EVID_SPECIFIC_ACTIVITY_RECORD")

    if re.search(
        r"\b(record|register|log|certificate|inspection report)\b",
        text,
    ) and not (
        NATURE_DOCUMENTATION_GAP in kinds
        or NATURE_ACTIVITY_RECORD in kinds
    ):
        add(NATURE_RECORD_EXISTS, "EVID_RECORD_REFERENCE")

    if re.search(
        r"\b(plan|management plan|site plan|action plan|schedule)\b",
        text,
    ) and assertion["modality"] in {
        MODALITY_PLANNED,
        MODALITY_REQUIRED,
        MODALITY_MIXED,
    }:
        add(NATURE_PLAN_STATEMENT, "EVID_PLAN_LANGUAGE")

    if assertion["normative_signal_present"] or re.search(
        r"\b(procedure|program(?:me)?|policy|sop)\b",
        text,
    ):
        add(NATURE_PROCEDURE_STATEMENT, "EVID_PROCEDURE_OR_NORMATIVE_LANGUAGE")

    clause_signals = [_clause_signal(c) for c in _split_clauses(exact_quote)]
    has_adverse = any(x in {"ADVERSE", "MIXED"} for x in clause_signals)
    has_positive = any(x in {"POSITIVE", "MIXED"} for x in clause_signals)

    mixed_fact_quote = has_adverse and has_positive

    if not kinds:
        kinds = [NATURE_UNKNOWN]
        reasons = ["EVID_NATURE_UNRESOLVED"]

    return {
        "evidence_natures": kinds,
        "reason_codes": reasons,
        "assertion_mode": assertion,
        "clause_signals": clause_signals,
        "mixed_fact_quote": mixed_fact_quote,
        "requires_subspan_fact_split": mixed_fact_quote,
    }


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------

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
    mode = evid["assertion_mode"]["modality"]
    inference_scope = evid["assertion_mode"]["inference_scope"]

    decision = UNRESOLVED
    reason = "COMPATIBILITY_NOT_TYPED"

    # A single quoted span containing both positive and adverse actual facts
    # must first be split into FactCandidates.  Do not allow one model relation
    # to erase the opposite clause.
    if evid["requires_subspan_fact_split"]:
        decision = UNRESOLVED
        reason = "MIXED_FACT_QUOTE_REQUIRES_SUBSPAN_FACT_SPLIT"

    elif TARGET_DESIGN_CONSTRUCTION in targets:
        if model_relation == "SUPPORT":
            if NATURE_PHYSICAL_FEATURE in natures:
                decision = DIRECT
                reason = "DESIGN_SUPPORTED_BY_PHYSICAL_FEATURE"
            elif natures & {
                NATURE_PROCEDURE_STATEMENT,
                NATURE_PLAN_STATEMENT,
                NATURE_CURRENT_CONDITION,
                NATURE_ACTIVITY_RECORD,
                NATURE_REVIEW_FINDING,
            }:
                decision = CORROBORATIVE
                reason = "DESIGN_NOT_DIRECTLY_ESTABLISHED_BY_OPERATIONAL_EVIDENCE"
            else:
                reason = "DESIGN_SUPPORT_NATURE_UNRESOLVED"

        elif model_relation == "ATTACK":
            if NATURE_DESIGN_DEFECT in natures:
                decision = DIRECT
                reason = "DESIGN_ATTACKED_BY_EXPLICIT_DESIGN_DEFECT"
            elif natures & {
                NATURE_CURRENT_DEFECT,
                NATURE_ADVERSE_OPERATIONAL_FINDING,
            }:
                decision = INCOMPATIBLE
                reason = "CURRENT_OR_OPERATIONAL_DEFECT_DOES_NOT_BY_ITSELF_PROVE_DESIGN_DEFECT"
            elif NATURE_DOCUMENTATION_GAP in natures:
                decision = INCOMPATIBLE
                reason = "DOCUMENTATION_GAP_DOES_NOT_BY_ITSELF_PROVE_DESIGN_DEFECT"
            else:
                reason = "DESIGN_ATTACK_NATURE_UNRESOLVED"

    elif TARGET_CURRENT_CONDITION in targets:
        if model_relation == "SUPPORT":
            # Required/planned procedure text can only corroborate an ongoing
            # actual-condition proposition.
            if mode in {
                MODALITY_REQUIRED,
                MODALITY_PLANNED,
            }:
                decision = CORROBORATIVE
                reason = "NORMATIVE_OR_PLANNED_CONTROL_DOES_NOT_PROVE_ACTUAL_CONDITION"

            # A mixed actual+normative sentence is also non-decisive until
            # separated into facts.
            elif mode == MODALITY_MIXED:
                decision = CORROBORATIVE
                reason = "MIXED_MODALITY_QUOTE_NOT_DIRECT_FOR_CURRENT_CONDITION"

            elif natures & {
                NATURE_REVIEW_FINDING,
                NATURE_OBSERVATION_RECORD,
                NATURE_CURRENT_CONDITION,
                NATURE_ACTIVITY_RECORD,
            }:
                # Positive point-in-time evidence does not by itself prove an
                # ongoing "is kept/maintained" state over the relevant period.
                if req["target_temporality"] == TARGET_TIME_ONGOING:
                    decision = CORROBORATIVE
                    reason = "POINT_OR_EVENT_EVIDENCE_DOES_NOT_ALONE_PROVE_ONGOING_CONDITION"
                else:
                    decision = DIRECT
                    reason = "CURRENT_CONDITION_SUPPORTED_BY_ACTUAL_EVIDENCE"

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
            # One explicit adverse actual state can be a counterexample to an
            # ongoing maintenance condition.  This is intentionally asymmetric
            # with positive point-in-time evidence.
            if natures & {
                NATURE_CURRENT_DEFECT,
                NATURE_ADVERSE_OPERATIONAL_FINDING,
                NATURE_CONTROL_ABSENCE,
            } and NATURE_DOCUMENTATION_GAP not in natures:
                decision = DIRECT
                reason = "ONGOING_CONDITION_ATTACKED_BY_EXPLICIT_ADVERSE_ACTUAL_FACT"
            elif NATURE_DOCUMENTATION_GAP in natures:
                decision = INCOMPATIBLE
                reason = "DOCUMENTATION_GAP_IS_NOT_ACTUAL_CONDITION_FAILURE"
            elif natures & {
                NATURE_REVIEW_FINDING,
                NATURE_OBSERVATION_RECORD,
            }:
                decision = CORROBORATIVE
                reason = "ADVERSE_REVIEW_REQUIRES_EXPLICIT_ADVERSE_FACT"
            else:
                reason = "CURRENT_CONDITION_ATTACK_NATURE_UNRESOLVED"

    elif TARGET_ACTIVITY_PERFORMED in targets:
        if model_relation == "SUPPORT":
            if mode == MODALITY_ACTUAL and natures & {
                NATURE_ACTIVITY_RECORD,
                NATURE_OBSERVATION_RECORD,
                NATURE_REVIEW_FINDING,
            }:
                decision = DIRECT
                reason = "ACTIVITY_SUPPORTED_BY_ACTUAL_EVENT_EVIDENCE"
            elif natures & {
                NATURE_PROCEDURE_STATEMENT,
                NATURE_PLAN_STATEMENT,
            } or mode in {
                MODALITY_REQUIRED,
                MODALITY_PLANNED,
            }:
                decision = CORROBORATIVE
                reason = "PLAN_OR_PROCEDURE_DOES_NOT_PROVE_ACTIVITY_OCCURRED"
        elif model_relation == "ATTACK" and NATURE_DOCUMENTATION_GAP in natures:
            decision = INCOMPATIBLE
            reason = "MISSING_DOCUMENTATION_DOES_NOT_PROVE_ACTIVITY_DID_NOT_OCCUR"

    elif targets & {
        TARGET_RECORDKEEPING,
        TARGET_DOCUMENTATION,
    }:
        if model_relation == "SUPPORT" and natures & {
            NATURE_RECORD_EXISTS,
            NATURE_ACTIVITY_RECORD,
        }:
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
