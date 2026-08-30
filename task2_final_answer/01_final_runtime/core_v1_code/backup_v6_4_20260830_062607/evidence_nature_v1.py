'''FRECA Core EvidenceNature v1.2.

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
TARGET_REGISTRATION_SCOPE = "REGISTRATION_OPERATION_SCOPE"
TARGET_EQUIPMENT_FITNESS = "EQUIPMENT_FITNESS"
TARGET_RISK_CONTROL_STATE = "RISK_CONTROL_STATE"
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
    """Infer the *proposition* type without letting broad rule text hijack it.

    V6.1 principle: ``proposition_to_establish`` is authoritative.  RULES quotes
    are used only as fallback when the proposition itself cannot be typed.  The
    previous implementation concatenated all rule text before classification;
    generic words such as ``carry out`` and ``system`` then caused one CP to be
    typed simultaneously as ACTIVITY/PROCEDURE/REGISTRATION.  That made the
    compatibility gate accept evidence about the wrong predicate.
    """

    proposition = _norm(str(requirement.get("proposition_to_establish", "")))
    rule_text = _norm(" ".join(
        str(source.get("quote", ""))
        for source in requirement.get("query_sources", [])
        if source.get("source") == "RULES"
    ))

    def infer(text: str) -> tuple[list[str], list[str]]:
        kinds: list[str] = []
        reasons: list[str] = []

        def add(kind: str, reason: str) -> None:
            if kind not in kinds:
                kinds.append(kind)
                reasons.append(reason)

        # Strong, proposition-specific targets first.
        if re.search(
            r"\b(operat(?:e|es|ed|ing) within (?:its )?registered|"
            r"registered operations?|registered functions?|"
            r"scope of (?:the )?registration|registered export operations?)\b",
            text,
        ):
            add(TARGET_REGISTRATION_SCOPE, "REQ_REGISTRATION_OPERATION_SCOPE_LANGUAGE")

        if re.search(
            r"\b(fit for purpose|good working order|serviceable|"
            r"equipment fitness|operational condition)\b",
            text,
        ) and re.search(r"\b(equipment|stations?|traps?|devices?|facility|facilities)\b", text):
            add(TARGET_EQUIPMENT_FITNESS, "REQ_EQUIPMENT_FITNESS_LANGUAGE")

        if re.search(
            r"\b(risk of (?:contamination|infestation)|"
            r"contamination or infestation risk|infestation or contamination risk)\b",
            text,
        ) and (
            "acceptable level" in text
            or bool(re.search(r"\brisk of [^.?!;]{0,120} maintain(?:ed|ing)?\b", text))
        ):
            add(TARGET_RISK_CONTROL_STATE, "REQ_RISK_CONTROL_STATE_LANGUAGE")

        if re.search(
            r"\b(design(?:ed)?|construct(?:ed|ion)?|purpose[- ]built|"
            r"structural|layout|fabric|built so as to|constructed so as to)\b",
            text,
        ):
            add(TARGET_DESIGN_CONSTRUCTION, "REQ_DESIGN_CONSTRUCTION_LANGUAGE")

        if re.search(
            r"\b(kept in (?:a )?condition|kept clean|clean and hygienic|"
            r"maintain(?:ed|ing)? (?:the )?(?:establishment|facility|premises|condition)|"
            r"maintain(?:ed|ing)? (?:so as to|to) (?:minimi[sz]e|prevent|control)|"
            r"pests? (?:and contaminants? )?(?:must be )?controlled|"
            r"contaminants? (?:must be )?controlled|current condition|"
            r"condition that ensures)\b",
            text,
        ):
            add(TARGET_CURRENT_CONDITION, "REQ_CURRENT_CONDITION_LANGUAGE")

        if re.search(
            r"\b(carry out|carried out|perform(?:ed)?|inspect(?:ed|ion)?|"
            r"screen(?:ed|ing)?|monitor(?:ed|ing)?|sample(?:d|ing)?|"
            r"treat(?:ed|ment)?|undertake|conduct(?:ed)?)\b",
            text,
        ) and not kinds:
            add(TARGET_ACTIVITY_PERFORMED, "REQ_ACTIVITY_LANGUAGE")

        if re.search(r"\b(record(?:s|keeping)?|retain(?:ed|ing)? records?|log)\b", text):
            add(TARGET_RECORDKEEPING, "REQ_RECORDKEEPING_LANGUAGE")

        if re.search(r"\b(procedure|plan|program(?:me)?|system|policy)\b", text) and not {
            TARGET_DESIGN_CONSTRUCTION,
            TARGET_CURRENT_CONDITION,
            TARGET_EQUIPMENT_FITNESS,
            TARGET_RISK_CONTROL_STATE,
            TARGET_REGISTRATION_SCOPE,
        }.intersection(kinds):
            add(TARGET_PROCEDURE_EXISTS, "REQ_PROCEDURE_LANGUAGE")

        if re.search(
            r"\b(registration|registered establishment|registered for|registration status)\b",
            text,
        ) and not {
            TARGET_DESIGN_CONSTRUCTION,
            TARGET_CURRENT_CONDITION,
            TARGET_REGISTRATION_SCOPE,
        }.intersection(kinds):
            add(TARGET_REGISTRATION_STATUS, "REQ_REGISTRATION_LANGUAGE")

        if re.search(r"\b(document(?:ed|ation)?|written record|documentary)\b", text) and not kinds:
            add(TARGET_DOCUMENTATION, "REQ_DOCUMENTATION_LANGUAGE")

        if re.search(
            r"\b(absence of|free from|not infested|not contaminated|"
            r"no infestation|no contamination)\b",
            text,
        ) and not kinds:
            add(TARGET_OUTCOME_STATE, "REQ_OUTCOME_STATE_LANGUAGE")

        # Strong target ownership prevents incidental words from broadening the
        # predicate after a specific target has already been found.
        for owner in (
            TARGET_REGISTRATION_SCOPE,
            TARGET_EQUIPMENT_FITNESS,
            TARGET_RISK_CONTROL_STATE,
        ):
            if owner in kinds:
                return [owner], [reasons[kinds.index(owner)]]

        if TARGET_DESIGN_CONSTRUCTION in kinds:
            idx = kinds.index(TARGET_DESIGN_CONSTRUCTION)
            return [TARGET_DESIGN_CONSTRUCTION], [reasons[idx]]

        return kinds, reasons

    kinds, reasons = infer(proposition)
    source = "PROPOSITION"
    if not kinds and rule_text:
        kinds, reasons = infer(rule_text)
        source = "RULE_FALLBACK"

    if not kinds:
        kinds = [TARGET_UNKNOWN]
        reasons = ["REQ_TARGET_UNRESOLVED"]
        source = "UNRESOLVED"

    if any(kind in kinds for kind in {
        TARGET_CURRENT_CONDITION,
        TARGET_EQUIPMENT_FITNESS,
        TARGET_RISK_CONTROL_STATE,
        TARGET_REGISTRATION_SCOPE,
    }):
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
        "profile_source": source,
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
        r"\b(must|shall|(?:is|are|was|were) required(?: to| by)|required to|responsible for|"
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
        r"trend review identifies|confirmed (?:that )?|assessed as compliant|"
        r"current catches|tested negative|sampling negative|"
        r"last service:|completed on|performed on|carried out on|"
        r"cleaned on|inspected on|serviced on|record shows|register entry|"
        r"(?:screening|scalping|sieving|inspection|sampling|treatment)[^.?!;]{0,80}(?:was|were) (?:carried out|performed|completed|conducted)|"
        r"(?:was|were) (?:screened|sieved|sampled|treated|fumigated)|"
        r"screen(?:ing)? records? show|siev(?:e|ing) records? show)\b",
        text,
    ))

    actual_state = bool(re.search(
        r"\b(current pest status|no active infestation|"
        r"no evidence of (?:bird|pest|rodent|insect) entry|"
        r"worn|delaminat(?:ed|ion)|broken|damaged|"
        r"gap(?:s)?|hole(?:s)?|crack(?:ed|s)?|"
        r"maintenance items? (?:have )?accumulated|"
        r"repeated .* activity|delayed .* follow[- ]?up|"
        r"no corrective actions outstanding|no hygiene deficiencies|"
        r"all bait stations serviceable|bait stations? (?:are )?serviceable|"
        r"(?:bait )?stations?[^.?!]{0,100}(?:all )?(?:serviceable|operational|in good working order|fit for purpose)|"
        r"traps?[^.?!]{0,100}(?:all )?(?:serviceable|operational|in good working order|fit for purpose)|"
        r"maintained in an operational condition|in good working order|fit for purpose|"
        r"no changes to the registered establishment .* since the last registration renewal|"
        r"not registered|outside (?:the )?registered (?:scope|operations?)|"
        r"operation(?:s)? outside (?:the )?registration|"
        r"were confirmed clean|was confirmed clean)\b",
        text,
    )) or _equipment_condition_context(text)

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
NATURE_REGISTRATION_STATUS = "REGISTRATION_STATUS_RECORD"
NATURE_OPERATION_SCOPE = "REGISTERED_OPERATION_SCOPE"
NATURE_REGISTRATION_DEFECT = "REGISTRATION_SCOPE_DEFECT"
NATURE_EQUIPMENT_CONDITION = "EQUIPMENT_CONDITION"
NATURE_RISK_CONTROL_OUTCOME = "RISK_CONTROL_OUTCOME"
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
        r"\b(registration suspended|registration cancelled|registration revoked|"
        r"not registered|outside (?:the )?registered (?:scope|operations?)|"
        r"operation(?:s)? outside (?:the )?registration)\b",
        text,
    ):
        add(NATURE_REGISTRATION_DEFECT, "EVID_REGISTRATION_SCOPE_DEFECT")

    if re.search(
        r"\b(no changes to the registered establishment .* since the last registration renewal|"
        r"registration renewal|registered (?:export )?establishment|registration record|"
        r"registration approval|(?:is|are) registered to (?:conduct|handle|perform)|"
        r"(?:is|are) registered for|\bre[- ]?[a-z]{2,3}-\d{4}-\d{4}\b)\b",
        text,
    ):
        add(NATURE_REGISTRATION_STATUS, "EVID_REGISTRATION_STATUS_LANGUAGE")

    if (
        re.search(
            r"\bregistered (?:export )?operations? (?:include|comprise|cover)\b|"
            r"\boperat(?:e|es|ed|ing) within (?:the )?registered\b|"
            r"\b(?:operations?|activities) carried out (?:under|within) (?:the )?registration\b|"
            r"\b(?:all )?(?:export )?(?:operations?|activities) (?:carried out|conducted|performed) [^.?!;]{0,120}\bwithin (?:the )?registered scope\b|"
            r"\b(?:all )?(?:export )?(?:operations?|activities) [^.?!;]{0,120}\bare within (?:the )?registered scope\b",
            text,
        )
        or (
            re.search(r"\bregistered export operations?\b", text)
            and re.search(r"\bused (?:for|in)\b|\boperations? (?:include|cover|comprise)\b", text)
        )
    ):
        add(NATURE_OPERATION_SCOPE, "EVID_REGISTERED_OPERATION_SCOPE_LANGUAGE")

    if re.search(
        r"\b(all bait stations serviceable|bait stations? (?:are )?serviceable|"
        r"stations? (?:are )?serviceable|traps? (?:are )?serviceable|"
        r"(?:bait )?stations?[^.?!]{0,100}(?:all )?(?:serviceable|operational|in good working order|fit for purpose)|"
        r"traps?[^.?!]{0,100}(?:all )?(?:serviceable|operational|in good working order|fit for purpose)|"
        r"in good working order|fit for purpose|maintained in an operational condition|"
        r"operational and serviceable)\b",
        text,
    ):
        add(NATURE_EQUIPMENT_CONDITION, "EVID_EQUIPMENT_CONDITION_LANGUAGE")

    if _equipment_condition_context(text):
        add(NATURE_EQUIPMENT_CONDITION, "EVID_EQUIPMENT_REGISTER_ROW_CONDITION")

    if assertion["actual_signal_present"] and re.search(
        r"\b(within acceptable (?:range|level)|below threshold|no active infestation|"
        r"no hygiene deficiencies|free of (?:pests?|contamination|infestation|harbourage)|"
        r"no evidence of .* entry|assessed as compliant|confirmed compliant)\b",
        text,
    ):
        add(NATURE_RISK_CONTROL_OUTCOME, "EVID_ACTUAL_RISK_CONTROL_OUTCOME")

    if re.search(
        r"\b(smooth|non[- ]porous|cleanable|stainless[- ]steel|"
        r"concrete flooring|steel[- ]frame|purpose[- ]built|"
        r"hinged access panels?|sealed surface|epoxy floor|"
        r"wall lining|mesh|gasket|threshold seal|tamper[- ]resistant|"
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
        r"no corrective actions outstanding|no hygiene deficiencies|"
        r"all bait stations serviceable|maintained in an operational condition|"
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
        r"register entry|record shows|signed on|"
        r"(?:screening|scalping|sieving|inspection|sampling|treatment)[^.?!;]{0,80}(?:was|were) (?:carried out|performed|completed|conducted)|"
        r"(?:was|were) (?:screened|sieved|sampled|treated|fumigated)|"
        r"screen(?:ing)? records? show|siev(?:e|ing) records? show)\b",
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
# Predicate anchor checks
# ---------------------------------------------------------------------------

_ACTIVITY_ANCHOR_GROUPS = (
    (r"\bscreen(?:ed|ing)?\b|\bscalper\b|\bsiev(?:e|ed|ing)\b",
     r"\bscreen(?:ed|ing)?\b|\bscalper\b|\bsiev(?:e|ed|ing)\b"),
    (r"\binspect(?:ed|ion|ing)?\b", r"\binspect(?:ed|ion|ing)?\b"),
    (r"\bmonitor(?:ed|ing)?\b", r"\bmonitor(?:ed|ing)?\b"),
    (r"\bsampl(?:e|ed|ing)\b", r"\bsampl(?:e|ed|ing)\b"),
    (r"\btreat(?:ed|ment|ing)?\b|\bfumigat(?:e|ed|ion|ing)\b",
     r"\btreat(?:ed|ment|ing)?\b|\bfumigat(?:e|ed|ion|ing)\b"),
    (r"\bclean(?:ed|ing)?\b|\bsanitis(?:e|ed|ing|ation)\b",
     r"\bclean(?:ed|ing)?\b|\bsanitis(?:e|ed|ing|ation)\b"),
)


def _activity_anchor_match(requirement: dict, exact_quote: str) -> bool:
    req = _norm(str(requirement.get("proposition_to_establish", "")))
    evid = _norm(exact_quote)
    target_groups = [evid_pat for req_pat, evid_pat in _ACTIVITY_ANCHOR_GROUPS if re.search(req_pat, req)]
    if not target_groups:
        return True
    return any(re.search(pattern, evid) for pattern in target_groups)


def _equipment_subject_match(requirement: dict, exact_quote: str) -> bool:
    req = _norm(str(requirement.get("proposition_to_establish", "")))
    evid = _norm(exact_quote)
    # When the requirement names stations/traps, generic facility condition is
    # not enough.  The evidence must mention the same equipment family.
    if re.search(r"\b(?:bait )?stations?\b|\btraps?\b", req):
        return bool(re.search(r"\b(?:bait )?stations?\b|\btraps?\b", evid))
    return True


def _risk_control_subject_match(exact_quote: str) -> bool:
    """Require a contamination/infestation/hygiene subject for CP-like risk states."""
    evid = _norm(exact_quote)
    return bool(re.search(
        r"\b(pests?|rodents?|insects?|birds?|infestation|contamin(?:ation|ant|ated)?|"
        r"hygiene|hygienic|harbourage|clean(?:liness|ed|ing)?|residue|foreign material|"
        r"mould|mold|pathogen|biosecurity)\b",
        evid,
    ))


def _registration_scope_attack_export_nexus(text: str) -> tuple[bool, str]:
    """Conservatively decide whether a registration defect bears on *export* scope.

    A table row that merely says ``Not registered`` may describe a domestic or
    ancillary operation.  CP1 is about operations/functions used to prepare
    plants or plant products for export, so the adverse fact must retain that
    export nexus.  FactCandidate context is therefore essential.
    """
    t = _norm(text)
    domestic_only = bool(re.search(
        r"\b(domestic(?:-grade)?|domestic distribution|domestic market|local market|"
        r"not for export|non[- ]export|internal use)\b",
        t,
    ))
    if domestic_only:
        return False, "DOMESTIC_ONLY_OPERATION_NOT_EXPORT_SCOPE_VIOLATION"

    export_nexus = bool(re.search(
        r"\b(export|for export|export-grade|export operations?|export activities|"
        r"prescribed plants?|plant products? for export|importing country)\b",
        t,
    ))
    if export_nexus:
        return True, "EXPLICIT_EXPORT_NEXUS"
    return False, "EXPORT_NEXUS_NOT_EXPLICIT"


def _equipment_condition_context(text: str) -> bool:
    """Recognise actual station/trap condition encoded in a rendered table row.

    Spreadsheet facts often look like:
      ``10 | ... | Enclosed bait station | ... | 2025-03-14 | Good — bait present, lid secure``
    The model may quote only the final status cell.  The grounded FactCandidate
    still contains the equipment subject, so type the *fact*, not only the quote.
    """
    t = _norm(text)
    subject = bool(re.search(r"\b(?:bait )?stations?\b|\b(?:snap |pitfall |pheromone )?traps?\b", t))
    status = bool(re.search(
        r"\b(serviceable|operational|functional|functioning|intact|undamaged|"
        r"good working order|fit for purpose|in operational condition|"
        r"lid secure|secure lid|bait present|good condition|condition[: ]+good|"
        r"status[: ]+(?:good|ok|okay|serviceable|operational))\b",
        t,
    ))
    # ``Good — bait present, lid secure`` is common in rendered registers.
    rendered_good = bool(re.search(r"(?:^|[|;]\s*)good\s*[—-]", t))
    return subject and (status or rendered_good)


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
    *,
    fact_context: str | None = None,
) -> dict[str, Any]:
    req = infer_requirement_predicate_profile(requirement)
    # V6.2: typed compatibility operates on the grounded FactCandidate, not on
    # the model-selected exact_quote substring.  exact_quote remains the
    # grounding/audit span, while fact_context restores subject/scope cells that
    # a concise quote may legitimately omit.  FactCandidates have already passed
    # the mixed-fact split boundary, so this does not reintroduce arbitrary
    # document-level context.
    semantic_text = str(fact_context or exact_quote or "")
    evid = classify_evidence_nature(semantic_text)

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

    elif TARGET_REGISTRATION_SCOPE in targets:
        if model_relation == "SUPPORT":
            if NATURE_OPERATION_SCOPE in natures:
                decision = DIRECT
                reason = "REGISTERED_OPERATION_SCOPE_SUPPORTED_BY_SCOPE_EVIDENCE"
            elif natures & {
                NATURE_REGISTRATION_STATUS,
                NATURE_RECORD_EXISTS,
                NATURE_PROCEDURE_STATEMENT,
            }:
                decision = CORROBORATIVE
                reason = "REGISTRATION_STATUS_ALONE_DOES_NOT_PROVE_OPERATION_WITHIN_SCOPE"
            else:
                reason = "REGISTRATION_SCOPE_SUPPORT_NATURE_UNRESOLVED"
        elif model_relation == "ATTACK":
            if NATURE_REGISTRATION_DEFECT in natures:
                has_export_nexus, nexus_reason = _registration_scope_attack_export_nexus(semantic_text)
                if has_export_nexus:
                    decision = DIRECT
                    reason = "REGISTERED_OPERATION_SCOPE_ATTACKED_BY_EXPLICIT_EXPORT_SCOPE_DEFECT"
                elif nexus_reason == "DOMESTIC_ONLY_OPERATION_NOT_EXPORT_SCOPE_VIOLATION":
                    decision = INCOMPATIBLE
                    reason = nexus_reason
                else:
                    decision = CORROBORATIVE
                    reason = "REGISTRATION_DEFECT_WITHOUT_EXPLICIT_EXPORT_NEXUS"
            elif NATURE_DOCUMENTATION_GAP in natures:
                decision = INCOMPATIBLE
                reason = "DOCUMENTATION_GAP_DOES_NOT_PROVE_OPERATION_OUTSIDE_REGISTERED_SCOPE"

    elif TARGET_EQUIPMENT_FITNESS in targets:
        if model_relation == "SUPPORT":
            if (
                NATURE_EQUIPMENT_CONDITION in natures
                and _equipment_subject_match(requirement, semantic_text)
            ) or _equipment_condition_context(semantic_text):
                decision = DIRECT
                reason = "EQUIPMENT_FITNESS_SUPPORTED_BY_GROUNDED_EQUIPMENT_CONDITION_FACT"
            elif NATURE_EQUIPMENT_CONDITION in natures:
                decision = CORROBORATIVE
                reason = "EQUIPMENT_CONDITION_SUBJECT_DOES_NOT_MATCH_REQUIRED_STATION_OR_TRAP"
            elif NATURE_PHYSICAL_FEATURE in natures:
                decision = CORROBORATIVE
                reason = "PHYSICAL_FEATURE_CORROBORATES_BUT_DOES_NOT_PROVE_WORKING_ORDER"
            elif natures & {
                NATURE_ACTIVITY_RECORD,
                NATURE_REVIEW_FINDING,
                NATURE_PROCEDURE_STATEMENT,
                NATURE_RECORD_EXISTS,
            }:
                decision = CORROBORATIVE
                reason = "INSPECTION_OR_PROGRAM_TEXT_DOES_NOT_BY_ITSELF_PROVE_EQUIPMENT_FITNESS"
            else:
                reason = "EQUIPMENT_FITNESS_SUPPORT_NATURE_UNRESOLVED"
        elif model_relation == "ATTACK":
            if NATURE_CURRENT_DEFECT in natures and re.search(
                r"\b(station|trap|bait|equipment|device)\b", _norm(semantic_text)
            ):
                decision = DIRECT
                reason = "EQUIPMENT_FITNESS_ATTACKED_BY_EXPLICIT_EQUIPMENT_DEFECT"
            elif natures & {NATURE_ADVERSE_OPERATIONAL_FINDING, NATURE_CURRENT_DEFECT}:
                decision = CORROBORATIVE
                reason = "OPERATIONAL_PROBLEM_DOES_NOT_BY_ITSELF_PROVE_STATION_OR_TRAP_FAILURE"

    elif TARGET_RISK_CONTROL_STATE in targets:
        if model_relation == "SUPPORT":
            if natures & {
                NATURE_RISK_CONTROL_OUTCOME,
                NATURE_CURRENT_CONDITION,
            } and mode not in {MODALITY_REQUIRED, MODALITY_PLANNED} and _risk_control_subject_match(semantic_text):
                decision = DIRECT
                reason = "RISK_CONTROL_STATE_SUPPORTED_BY_ACTUAL_OUTCOME_EVIDENCE"
            elif natures & {
                NATURE_REVIEW_FINDING,
                NATURE_OBSERVATION_RECORD,
            } and evid["assertion_mode"].get("actual_signal_present") and _risk_control_subject_match(semantic_text):
                decision = DIRECT
                reason = "RISK_CONTROL_STATE_SUPPORTED_BY_ACTUAL_REVIEW_EVIDENCE"
            elif natures & {
                NATURE_RISK_CONTROL_OUTCOME,
                NATURE_CURRENT_CONDITION,
                NATURE_REVIEW_FINDING,
                NATURE_OBSERVATION_RECORD,
            }:
                decision = CORROBORATIVE
                reason = "ACTUAL_STATE_DOES_NOT_NAME_CONTAMINATION_INFESTATION_OR_HYGIENE_SUBJECT"
            elif natures & {
                NATURE_PROCEDURE_STATEMENT,
                NATURE_PLAN_STATEMENT,
                NATURE_RECORD_EXISTS,
                NATURE_PHYSICAL_FEATURE,
            }:
                decision = CORROBORATIVE
                reason = "CONTROL_DESIGN_OR_PROCEDURE_DOES_NOT_BY_ITSELF_PROVE_ACCEPTABLE_RISK"
            else:
                reason = "RISK_CONTROL_SUPPORT_NATURE_UNRESOLVED"
        elif model_relation == "ATTACK":
            if NATURE_CONTROL_ABSENCE in natures and _risk_control_subject_match(semantic_text):
                decision = DIRECT
                reason = "RISK_CONTROL_STATE_ATTACKED_BY_EXPLICIT_CONTROL_ABSENCE"
            elif natures & {
                NATURE_ADVERSE_OPERATIONAL_FINDING,
                NATURE_CURRENT_DEFECT,
            } and _risk_control_subject_match(semantic_text):
                decision = DIRECT
                reason = "RISK_CONTROL_STATE_ATTACKED_BY_EXPLICIT_ADVERSE_ACTUAL_FACT"
            elif natures & {NATURE_CONTROL_ABSENCE, NATURE_ADVERSE_OPERATIONAL_FINDING, NATURE_CURRENT_DEFECT}:
                decision = CORROBORATIVE
                reason = "ADVERSE_FACT_DOES_NOT_NAME_CONTAMINATION_INFESTATION_OR_HYGIENE_SUBJECT"
            elif NATURE_DOCUMENTATION_GAP in natures:
                decision = CORROBORATIVE
                reason = "DOCUMENTATION_GAP_ALONE_DOES_NOT_PROVE_UNACCEPTABLE_RISK"

    elif TARGET_REGISTRATION_STATUS in targets:
        if model_relation == "SUPPORT" and NATURE_REGISTRATION_STATUS in natures:
            decision = DIRECT
            reason = "REGISTRATION_STATUS_SUPPORTED_BY_REGISTRATION_RECORD"
        elif model_relation == "ATTACK" and NATURE_REGISTRATION_DEFECT in natures:
            decision = DIRECT
            reason = "REGISTRATION_STATUS_ATTACKED_BY_EXPLICIT_REGISTRATION_DEFECT"
        elif model_relation == "SUPPORT" and NATURE_RECORD_EXISTS in natures:
            decision = CORROBORATIVE
            reason = "GENERIC_RECORD_REFERENCE_ONLY_CORROBORATES_REGISTRATION_STATUS"

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

            elif NATURE_CURRENT_CONDITION in natures:
                # V6.1: an explicit actual/current state (including a recent
                # inspection that states the present condition) can establish
                # the positive direction of an ongoing maintenance proposition
                # at the evaluation point.  We do not require proof that no
                # contrary state ever existed; a contrary direct fact is
                # preserved separately as BOTH.
                decision = DIRECT
                reason = "ONGOING_CURRENT_CONDITION_SUPPORTED_BY_EXPLICIT_ACTUAL_STATE"

            elif natures & {
                NATURE_REVIEW_FINDING,
                NATURE_OBSERVATION_RECORD,
                NATURE_ACTIVITY_RECORD,
            }:
                # A bare event/inspection record without an explicit state
                # conclusion remains corroborative.
                decision = CORROBORATIVE
                reason = "EVENT_OR_REVIEW_WITHOUT_EXPLICIT_CURRENT_STATE_IS_CORROBORATIVE"

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
            } and _activity_anchor_match(requirement, semantic_text):
                decision = DIRECT
                reason = "ACTIVITY_SUPPORTED_BY_MATCHED_ACTUAL_EVENT_EVIDENCE"
            elif mode == MODALITY_ACTUAL and natures & {
                NATURE_ACTIVITY_RECORD,
                NATURE_OBSERVATION_RECORD,
                NATURE_REVIEW_FINDING,
            }:
                decision = CORROBORATIVE
                reason = "ACTUAL_EVENT_IS_DIFFERENT_FROM_REQUIRED_ACTIVITY"
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

    elif TARGET_OUTCOME_STATE in targets:
        if model_relation == "SUPPORT" and natures & {
            NATURE_CURRENT_CONDITION,
            NATURE_RISK_CONTROL_OUTCOME,
            NATURE_OBSERVATION_RECORD,
            NATURE_REVIEW_FINDING,
        } and mode not in {MODALITY_REQUIRED, MODALITY_PLANNED}:
            decision = DIRECT
            reason = "OUTCOME_STATE_SUPPORTED_BY_ACTUAL_STATE_EVIDENCE"
        elif model_relation == "ATTACK" and natures & {
            NATURE_ADVERSE_OPERATIONAL_FINDING,
            NATURE_CURRENT_DEFECT,
            NATURE_CONTROL_ABSENCE,
        }:
            decision = DIRECT
            reason = "OUTCOME_STATE_ATTACKED_BY_EXPLICIT_ADVERSE_FACT"
        elif model_relation == "SUPPORT" and natures & {
            NATURE_PROCEDURE_STATEMENT,
            NATURE_PLAN_STATEMENT,
        }:
            decision = CORROBORATIVE
            reason = "PROCEDURE_DOES_NOT_BY_ITSELF_PROVE_OUTCOME_STATE"

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
