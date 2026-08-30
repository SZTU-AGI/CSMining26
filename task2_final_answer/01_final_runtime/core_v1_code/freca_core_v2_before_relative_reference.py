from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import freca_core_v1 as core
from policy_units import extract_policy_units


# ============================================================
# FRECA Core V2
#
# Main change from V1:
#
# Rules retrieval
#   -> CandidateRelationDecision
#   -> validated CandidateLedger
#   -> grounded contract compilation
#
# This is a minimal extraction from the full Layer-2 design.
# ============================================================


PROJECT_ROOT = core.PROJECT_ROOT

CONTRACT_DIR_V2 = (
    PROJECT_ROOT / "contracts_v2"
)

RESULT_DIR_V2 = (
    PROJECT_ROOT / "results_v2"
)


RELATIONS = {
    "PRIMARY_NORM",
    "APPLICABILITY",
    "EXCEPTION_OR_DEEMING",
    "DEFINITION",
    "CROSS_REFERENCE_DEPENDENCY",
    "STRUCTURAL_CONTEXT",
    "CP_OPERATIONALIZATION_SUPPORT",
    "IRRELEVANT",
    "UNRESOLVED",
}


SELECTED_RELATIONS = {
    "PRIMARY_NORM",
    "APPLICABILITY",
    "EXCEPTION_OR_DEEMING",
    "DEFINITION",
    "CROSS_REFERENCE_DEPENDENCY",
    "STRUCTURAL_CONTEXT",
    "CP_OPERATIONALIZATION_SUPPORT",
}


# ============================================================
# Exact grounding
# ============================================================

def whitespace_normalize(
    value: Any,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def quote_match_mode(
    quote: str,
    source: str,
) -> str | None:
    """
    Full architecture rule:
      EXACT_RAW
      or
      WHITESPACE_NORMALIZED

    No fuzzy / semantic quote matching.
    """

    quote = str(quote)
    source = str(source)

    if quote and quote in source:
        return "EXACT_RAW"

    q = whitespace_normalize(
        quote
    )

    s = whitespace_normalize(
        source
    )

    if q and q in s:
        return (
            "WHITESPACE_NORMALIZED"
        )

    return None


# ============================================================
# Minimal multi-channel PolicyUnit retrieval
#
# Extracted from full Layer-2 D2.10/D2.11.
#
# We deliberately keep only lexical channels in Core V2:
#
#   CRITERION_OWN
#   CRITERION_CONTEXT
#   SUBELEMENT_CONTEXT
#   COMBINED_CONTEXT
#
# Then fuse rankings with RRF.
#
# Embedding / definition expansion / heading index are deferred.
# ============================================================

def strip_group_number(
    text: str,
) -> str:

    text = str(text).strip()

    return re.sub(
        r"^\s*"
        r"\d+(?:\.\d+)*"
        r"\s*"
        r"[-–—:]?"
        r"\s*",
        "",
        text,
    ).strip()


def rank_policy_view(
    query: str,
    units: list[dict],
    *,
    view: str,
    top_k: int,
) -> list[dict]:

    docs = []

    for unit in units:

        item = dict(
            unit
        )

        if view == "OWN":

            item["text"] = (
                unit.get(
                    "own_text",
                    "",
                )
            )

        elif view == "CONTEXT":

            item["text"] = (
                unit["text"]
            )

        else:

            raise ValueError(
                f"Unknown retrieval "
                f"view: {view}"
            )

        docs.append(
            item
        )

    return core.bm25_rank(
        query,
        docs,
        top_k,
    )


def retrieve_policy_candidates(
    cp: dict,
    units: list[dict],
    *,
    final_top_k: int,
) -> list[dict]:

    criterion = (
        cp["criterion"]
        .strip()
    )

    subelement = (
        strip_group_number(
            cp.get(
                "subelement",
                "",
            )
        )
    )

    # Per-channel depth.
    #
    # Full design proposes 30 as an initial
    # lexical-channel value. We keep that here.
    channel_top_k = 30

    channels = []

    # --------------------------------------------------------
    # 1. Criterion against OWN text only.
    #
    # This prevents sibling units from receiving a large score
    # purely because they inherit the same parent chapeau.
    # --------------------------------------------------------

    channels.append(
        (
            "CRITERION_OWN",
            rank_policy_view(
                criterion,
                units,
                view="OWN",
                top_k=channel_top_k,
            ),
        )
    )

    # --------------------------------------------------------
    # 2. Criterion against own + necessary context.
    # --------------------------------------------------------

    channels.append(
        (
            "CRITERION_CONTEXT",
            rank_policy_view(
                criterion,
                units,
                view="CONTEXT",
                top_k=channel_top_k,
            ),
        )
    )

    # --------------------------------------------------------
    # 3. Official subelement title is independent.
    #
    # It must not overwrite criterion retrieval.
    # --------------------------------------------------------

    if subelement:

        channels.append(
            (
                "SUBELEMENT_CONTEXT",
                rank_policy_view(
                    subelement,
                    units,
                    view="CONTEXT",
                    top_k=channel_top_k,
                ),
            )
        )

    # --------------------------------------------------------
    # 4. Combined query remains only an additional channel.
    # --------------------------------------------------------

    combined = " ".join(
        part
        for part in [
            subelement,
            criterion,
        ]
        if part
    )

    if combined:

        channels.append(
            (
                "COMBINED_CONTEXT",
                rank_policy_view(
                    combined,
                    units,
                    view="CONTEXT",
                    top_k=channel_top_k,
                ),
            )
        )

    # --------------------------------------------------------
    # Reciprocal Rank Fusion
    #
    # Equal channel weights in this minimal Core.
    # We intentionally do not tune weights per CP.
    # --------------------------------------------------------

    rrf_k = 60.0

    fused = {}

    original_by_id = {
        unit["id"]:
            unit
        for unit in units
    }

    for (
        channel_name,
        ranking,
    ) in channels:

        for rank, result in enumerate(
            ranking,
            1,
        ):

            unit_id = (
                result["id"]
            )

            if unit_id not in fused:

                fused[
                    unit_id
                ] = {
                    "rrf_score":
                        0.0,
                    "retrieval_signals":
                        [],
                }

            contribution = (
                1.0
                / (
                    rrf_k
                    + rank
                )
            )

            fused[
                unit_id
            ][
                "rrf_score"
            ] += contribution

            fused[
                unit_id
            ][
                "retrieval_signals"
            ].append(
                {
                    "channel":
                        channel_name,
                    "rank":
                        rank,
                    "raw_score":
                        result.get(
                            "score"
                        ),
                    "rrf_contribution":
                        contribution,
                }
            )

    candidates = []

    for (
        unit_id,
        fused_info,
    ) in fused.items():

        item = dict(
            original_by_id[
                unit_id
            ]
        )

        # Existing downstream code expects "score".
        item["score"] = (
            fused_info[
                "rrf_score"
            ]
        )

        item[
            "rrf_score"
        ] = fused_info[
            "rrf_score"
        ]

        item[
            "retrieval_signals"
        ] = fused_info[
            "retrieval_signals"
        ]

        candidates.append(
            item
        )

    candidates.sort(
        key=lambda x: (
            -x[
                "rrf_score"
            ],
            x[
                "citation"
            ],
        )
    )

    return candidates[
        :final_top_k
    ]


# ============================================================
# Stage 1:
# Candidate Relation Classification
# ============================================================

CANDIDATE_RELATION_SYSTEM = r"""
You are the Candidate Relation Classifier for a legal rule compiler.

You are NOT a compliance classifier.

You receive:
1. one official FRECA checking-point criterion;
2. a set of candidate excerpts retrieved from the official Rules.

Your task is NOT to build the final CP contract.

Your only task is to classify the relationship between EACH
candidate Rules chunk and THIS CP criterion.

This distinction is critical:

A provision can be legally relevant to the same general topic
without being an independent scoring requirement for this CP.

Use exactly one of these relation labels:

PRIMARY_NORM

    The candidate directly supplies or defines a normative rule
    that grounds what THIS CP criterion scores.

    A PRIMARY_NORM may later support a mandatory proposition in
    the satisfaction logic.

APPLICABILITY

    The candidate supplies a condition governing whether the whole
    CP or its governing legal rule applies.

EXCEPTION_OR_DEEMING

    The candidate supplies an exception, non-applicability rule,
    deeming provision, or alternative legal effect relevant to the
    governing rule.

DEFINITION

    The candidate defines a legal term needed to interpret a
    selected governing provision.

    A definition is context. It does not independently create a
    satisfaction requirement.

CROSS_REFERENCE_DEPENDENCY

    The candidate is required because a selected governing rule
    explicitly depends on or refers to it.

    It is context unless its own normative role is separately
    established.

STRUCTURAL_CONTEXT

    The candidate is necessary structural context such as a
    chapeau or parent provision.

    It does not independently create a scoring requirement.

CP_OPERATIONALIZATION_SUPPORT

    The candidate helps explain how the CP criterion may be
    operationalised or evidenced, but it is NOT by itself an
    independent requirement scored by this CP.

    This category is especially important.

    Do not upgrade an operationally related obligation into
    PRIMARY_NORM merely because both concern the same topic.

IRRELEVANT

    The candidate does not materially contribute to interpreting
    this CP.

UNRESOLVED

    The supplied text is insufficient to determine the relationship.

Important rules:

1. Use only the supplied CP and Rules chunks.
2. Never use case evidence.
3. Never output 1, 0, N/A, PASS, FAIL or compliance conclusions.
4. Never infer a legal provision that is not supplied.
5. Do not classify every retrieved provision as PRIMARY_NORM.
6. Similar topic does not mean same scoring criterion.
7. For every non-IRRELEVANT/non-UNRESOLVED relation, provide:
   - an exact CP quote;
   - an exact policy quote.
8. Quotes must be copied from the supplied text.
9. Do not paraphrase source quotes.
10. If grounding cannot be made exact, use UNRESOLVED.

Return JSON:

{
  "decisions": [
    {
      "candidate_id": "RULE-P45-C2",
      "relation": "PRIMARY_NORM",
      "cp_quote": "exact phrase from CP",
      "policy_quote": "exact phrase from candidate",
      "reason_code": "DIRECT_NORMATIVE_GROUNDING",
      "reason": "brief explanation"
    }
  ]
}

Return one decision for every candidate_id supplied.

Return JSON only.
"""


def make_candidate_prompt(
    cp: dict,
    candidates: list[dict],
) -> str:

    candidate_text = "\n\n".join(
        (
            f"[{candidate['id']}]\n"
            f"CITATION={candidate.get('citation', '')}\n"
            f"TYPE={candidate.get('unit_type', '')}\n"
            f"PAGE={candidate['page']}\n"
            f"OWN_TEXT:\n"
            f"{candidate.get('own_text', candidate['text'])}\n"
            f"CONTEXT_VIEW:\n"
            f"{candidate['text']}"
        )
        for candidate
        in candidates
    )

    return f"""
OFFICIAL CHECKING POINT

CP_ID:
{cp["cp_id"]}

ELEMENT:
{cp["element"]}

SUBELEMENT:
{cp["subelement"]}

CRITERION:
{cp["criterion"]}


RETRIEVED RULES CANDIDATES

{candidate_text}


Classify the relation of EVERY candidate to the CP criterion.

Do not build the contract yet.

Return JSON only.
"""


def validate_candidate_ledger(
    raw: dict,
    cp: dict,
    candidates: list[dict],
) -> list[dict]:

    candidate_map = {
        candidate["id"]:
            candidate
        for candidate
        in candidates
    }

    raw_map = {}

    for decision in raw.get(
        "decisions",
        [],
    ):

        candidate_id = (
            decision.get(
                "candidate_id"
            )
        )

        if (
            candidate_id
            in candidate_map
            and candidate_id
            not in raw_map
        ):
            raw_map[
                candidate_id
            ] = decision

    ledger = []

    for candidate in candidates:

        candidate_id = (
            candidate["id"]
        )

        raw_decision = (
            raw_map.get(
                candidate_id
            )
        )

        # Missing model decision:
        # preserve as unresolved.
        if raw_decision is None:

            ledger.append(
                {
                    "candidate_id":
                        candidate_id,
                    "page":
                        candidate["page"],
                    "rrf_or_bm25_score":
                        candidate.get(
                            "score"
                        ),
                    "relation":
                        "UNRESOLVED",
                    "selected":
                        False,
                    "reason_code":
                        "NO_MODEL_DECISION",
                    "reason":
                        "Classifier returned "
                        "no decision.",
                    "cp_quote":
                        "",
                    "policy_quote":
                        "",
                    "cp_match_mode":
                        None,
                    "policy_match_mode":
                        None,
                    "validation_error":
                        None,
                }
            )

            continue

        relation = (
            raw_decision.get(
                "relation",
                "UNRESOLVED",
            )
        )

        if relation not in RELATIONS:
            relation = "UNRESOLVED"

        cp_quote = str(
            raw_decision.get(
                "cp_quote",
                "",
            )
        ).strip()

        policy_quote = str(
            raw_decision.get(
                "policy_quote",
                "",
            )
        ).strip()

        cp_match = None
        policy_match = None
        validation_error = None

        # Grounding required for every
        # meaningful legal relationship.
        if relation not in {
            "IRRELEVANT",
            "UNRESOLVED",
        }:

            cp_match = quote_match_mode(
                cp_quote,
                cp["criterion"],
            )

            policy_match = (
                quote_match_mode(
                    policy_quote,
                    candidate.get(
                        "own_text",
                        candidate["text"],
                    ),
                )
            )

            if cp_match is None:

                validation_error = (
                    "CP_QUOTE_NOT_GROUNDED"
                )

            elif policy_match is None:

                validation_error = (
                    "POLICY_QUOTE_NOT_GROUNDED"
                )

        # IMPORTANT:
        # semantic grounding failure is NOT
        # repaired by asking the same model
        # to rewrite until accepted.
        if validation_error:

            final_relation = (
                "UNRESOLVED"
            )

            selected = False

        else:

            final_relation = relation

            selected = (
                relation
                in SELECTED_RELATIONS
            )

        ledger.append(
            {
                "candidate_id":
                    candidate_id,
                "page":
                    candidate["page"],
                "rrf_or_bm25_score":
                    candidate.get(
                        "score"
                    ),
                "relation":
                    final_relation,
                "selected":
                    selected,
                "reason_code":
                    str(
                        raw_decision.get(
                            "reason_code",
                            "",
                        )
                    ),
                "reason":
                    str(
                        raw_decision.get(
                            "reason",
                            "",
                        )
                    ),
                "cp_quote":
                    cp_quote,
                "policy_quote":
                    policy_quote,
                "cp_match_mode":
                    cp_match,
                "policy_match_mode":
                    policy_match,
                "validation_error":
                    validation_error,
            }
        )

    return ledger


# ============================================================
# Batched Candidate Relation Classification
#
# Extracted from the full Layer-2 design:
# shortlisted candidates are classified in small independent
# batches, then merged into one CandidateLedger.
# ============================================================

def classify_candidate_batches(
    cp: dict,
    candidates: list[dict],
    batch_size: int = 12,
) -> dict:

    # Full Layer-2 design:
    # SHORTLISTED candidates are classified
    # in stable-ID order.
    candidates = sorted(
        candidates,
        key=lambda x: x["id"],
    )

    all_decisions = []

    total_batches = (
        len(candidates)
        + batch_size
        - 1
    ) // batch_size

    for start in range(
        0,
        len(candidates),
        batch_size,
    ):

        batch = candidates[
            start:start + batch_size
        ]

        batch_no = (
            start // batch_size
            + 1
        )

        print(
            f"    relation batch "
            f"{batch_no}/{total_batches}: "
            f"{len(batch)} candidates"
        )

        print(
            "      "
            + ", ".join(
                item["id"]
                for item in batch
            )
        )

        raw = core.deepseek_json(
            model=
                core.CONTRACT_MODEL,
            system_prompt=
                CANDIDATE_RELATION_SYSTEM,
            user_prompt=
                make_candidate_prompt(
                    cp,
                    batch,
                ),
            thinking=False,
            max_tokens=5000,
        )

        decisions = raw.get(
            "decisions"
        )

        if not isinstance(
            decisions,
            list,
        ):
            raise RuntimeError(
                f"Candidate relation batch "
                f"{batch_no} returned no "
                f"'decisions' list."
            )

        # Do not decide semantic validity here.
        # Grounding/enum validation happens later
        # in validate_candidate_ledger().
        all_decisions.extend(
            decisions
        )

    return {
        "decisions":
            all_decisions
    }



# ============================================================
# LegalBasisLink review
#
# Minimal extraction from full Layer-2 D2.4.
#
# CandidateRelationDecision says what kind of relationship
# the PolicyUnit appears to have.
#
# LegalBasisLink separately asks whether the supplied legal
# provision can actually ground THIS benchmark criterion.
# ============================================================

LEGAL_BASIS_RELATIONS = {
    "DIRECT_TEXTUAL_MATCH",
    "LEGAL_SPECIALIZATION",
    "CP_OPERATIONALIZES_BROADER_RULE",
    "CONTEXT_ONLY",
}

LEGAL_BASIS_TARGET_RELATIONS = {
    "PRIMARY_NORM",
    "APPLICABILITY",
    "EXCEPTION_OR_DEEMING",
    "CP_OPERATIONALIZATION_SUPPORT",
}


LEGAL_BASIS_SYSTEM = r"""
You are the LegalBasisLink reviewer in a closed-source legal
rule compiler.

You are NOT a compliance classifier.

You receive:
1. one official FRECA checking-point criterion;
2. candidate PolicyUnits that have already passed an initial
   CandidateRelationDecision.

Your task is narrower:

For EACH supplied candidate, determine whether the candidate
actually provides legal grounding for THIS specific benchmark
criterion.

The official CP defines WHAT THE BENCHMARK SCORES.
The Rules define THE LEGAL NORM.

A provision may concern the same establishment, export operations,
pests, contamination, cleaning, treatment, screening or other
nearby topics and still be only context for this CP.

Allowed legal_basis_relation values:

DIRECT_TEXTUAL_MATCH

    The operative legal proposition and the CP criterion are
    substantially the same proposition, with direct textual
    grounding.

LEGAL_SPECIALIZATION

    The candidate is a more specific legal rule that directly
    constrains, defines or specializes a material part of the
    supplied CP criterion.

    Do NOT use this merely because the rule is a separate obligation
    in the same section or concerns the same general topic.

CP_OPERATIONALIZES_BROADER_RULE

    The candidate is a broader legal norm for which the CP is a
    more specific benchmark operationalisation.

CONTEXT_ONLY

    The candidate is relevant background, structural material,
    a neighbouring obligation, another regulated object/activity,
    or an operationally related provision, but it cannot by itself
    legally ground the current CP criterion.

Important distinctions:

- Same topic is not enough.
- Same section is not enough.
- Same regulated establishment is not enough.
- A separate obligation is not automatically part of this CP.
- A rule about another regulated object or another conditional
  activity is normally CONTEXT_ONLY unless the supplied text
  itself establishes the legal link.
- Do not infer relationships from outside knowledge.
- Do not use case evidence.
- Do not use other CPs.
- Do not output 1, 0, N/A, PASS or FAIL.

For every RESOLVED decision:
- copy one exact CP quote;
- copy one exact policy quote;
- use the shortest useful exact quote that establishes the mapping.

If the supplied text is insufficient to determine the mapping,
return status=UNRESOLVED and legal_basis_relation=null.

Return JSON:

{
  "decisions": [
    {
      "candidate_id": "rules2021:4-2(4)(c)",
      "status": "RESOLVED",
      "legal_basis_relation": "DIRECT_TEXTUAL_MATCH",
      "cp_quote": "exact CP quote",
      "policy_quote": "exact PolicyUnit quote",
      "reason": "brief explanation"
    }
  ]
}

Return exactly one object for each supplied candidate.
Return JSON only.
"""


def make_legal_basis_prompt(
    cp: dict,
    candidates: list[dict],
    ledger_by_id: dict[str, dict],
) -> str:

    records = []

    for candidate in candidates:

        decision = ledger_by_id[
            candidate["id"]
        ]

        records.append(
            {
                "candidate_id":
                    candidate["id"],
                "citation":
                    candidate.get(
                        "citation",
                        "",
                    ),
                "unit_type":
                    candidate.get(
                        "unit_type",
                        "",
                    ),
                "initial_relation":
                    decision.get(
                        "relation",
                    ),
                "initial_cp_quote":
                    decision.get(
                        "cp_quote",
                        "",
                    ),
                "initial_policy_quote":
                    decision.get(
                        "policy_quote",
                        "",
                    ),
                "own_text":
                    candidate.get(
                        "own_text",
                        "",
                    ),
                "context_view":
                    candidate.get(
                        "text",
                        "",
                    ),
            }
        )

    return f"""
OFFICIAL CHECKING POINT

CP_ID:
{cp["cp_id"]}

SUBELEMENT:
{cp["subelement"]}

CRITERION:
{cp["criterion"]}


CANDIDATES TO REVIEW

{json.dumps(
    records,
    ensure_ascii=False,
    indent=2,
)}


Create a LegalBasisLink review for every candidate.

Return JSON only.
"""


def review_legal_basis(
    cp: dict,
    candidates: list[dict],
    ledger: list[dict],
    batch_size: int = 12,
) -> list[dict]:

    candidate_map = {
        candidate["id"]:
            candidate
        for candidate in candidates
    }

    ledger_by_id = {
        item["candidate_id"]:
            item
        for item in ledger
    }

    targets = []

    for item in ledger:

        # Default:
        # retained candidates are NOT automatically
        # contract-eligible.
        item[
            "contract_eligible"
        ] = False

        item[
            "legal_basis_status"
        ] = None

        item[
            "legal_basis_relation"
        ] = None

        item[
            "legal_basis_reason"
        ] = ""

        item[
            "legal_basis_validation_error"
        ] = None

        if (
            item.get("selected")
            and item.get("relation")
            in LEGAL_BASIS_TARGET_RELATIONS
            and item["candidate_id"]
            in candidate_map
        ):
            targets.append(
                candidate_map[
                    item["candidate_id"]
                ]
            )

    targets.sort(
        key=lambda x: x["id"]
    )

    all_reviews = []

    total_batches = (
        len(targets)
        + batch_size
        - 1
    ) // batch_size

    for start in range(
        0,
        len(targets),
        batch_size,
    ):

        batch = targets[
            start:start + batch_size
        ]

        batch_no = (
            start // batch_size
            + 1
        )

        print(
            f"    legal-basis batch "
            f"{batch_no}/{total_batches}: "
            f"{len(batch)} candidates"
        )

        raw = core.deepseek_json(
            model=
                core.CONTRACT_MODEL,
            system_prompt=
                LEGAL_BASIS_SYSTEM,
            user_prompt=
                make_legal_basis_prompt(
                    cp,
                    batch,
                    ledger_by_id,
                ),
            thinking=False,
            max_tokens=6000,
        )

        decisions = raw.get(
            "decisions"
        )

        if not isinstance(
            decisions,
            list,
        ):
            raise RuntimeError(
                "LegalBasisLink reviewer "
                "returned no decisions list."
            )

        all_reviews.extend(
            decisions
        )

    review_map = {}

    for review in all_reviews:

        candidate_id = review.get(
            "candidate_id"
        )

        if (
            candidate_id
            in candidate_map
            and candidate_id
            not in review_map
        ):
            review_map[
                candidate_id
            ] = review

    for item in ledger:

        candidate_id = (
            item["candidate_id"]
        )

        if (
            not item.get("selected")
            or item.get("relation")
            not in LEGAL_BASIS_TARGET_RELATIONS
        ):
            continue

        candidate = candidate_map.get(
            candidate_id
        )

        review = review_map.get(
            candidate_id
        )

        if (
            candidate is None
            or review is None
        ):

            item[
                "legal_basis_status"
            ] = "UNRESOLVED"

            item[
                "legal_basis_validation_error"
            ] = "NO_REVIEW_DECISION"

            continue

        status = review.get(
            "status"
        )

        relation = review.get(
            "legal_basis_relation"
        )

        cp_quote = str(
            review.get(
                "cp_quote",
                "",
            )
        ).strip()

        policy_quote = str(
            review.get(
                "policy_quote",
                "",
            )
        ).strip()

        item[
            "legal_basis_status"
        ] = status

        item[
            "legal_basis_relation"
        ] = relation

        item[
            "legal_basis_reason"
        ] = str(
            review.get(
                "reason",
                "",
            )
        )

        if status != "RESOLVED":

            item[
                "legal_basis_status"
            ] = "UNRESOLVED"

            continue

        if (
            relation
            not in LEGAL_BASIS_RELATIONS
        ):

            item[
                "legal_basis_status"
            ] = "UNRESOLVED"

            item[
                "legal_basis_validation_error"
            ] = "INVALID_LEGAL_BASIS_RELATION"

            continue

        cp_match = quote_match_mode(
            cp_quote,
            cp["criterion"],
        )

        policy_match = (
            quote_match_mode(
                policy_quote,
                candidate.get(
                    "own_text",
                    candidate["text"],
                ),
            )
        )

        if cp_match is None:

            item[
                "legal_basis_status"
            ] = "UNRESOLVED"

            item[
                "legal_basis_validation_error"
            ] = "CP_QUOTE_NOT_GROUNDED"

            continue

        if policy_match is None:

            item[
                "legal_basis_status"
            ] = "UNRESOLVED"

            item[
                "legal_basis_validation_error"
            ] = "POLICY_QUOTE_NOT_GROUNDED"

            continue

        item[
            "legal_basis_cp_quote"
        ] = cp_quote

        item[
            "legal_basis_policy_quote"
        ] = policy_quote

        item[
            "legal_basis_cp_match_mode"
        ] = cp_match

        item[
            "legal_basis_policy_match_mode"
        ] = policy_match

        # CONTEXT_ONLY remains visible in the ledger,
        # but cannot become a contract legal basis.
        item[
            "contract_eligible"
        ] = (
            relation
            != "CONTEXT_ONLY"
        )

    return ledger


# ============================================================
# Deterministic structural rescue
#
# Minimal extraction from full Layer-2:
#
# after initial relation classification, selected PolicyUnits
# may deterministically rescue:
#
#   - their direct parent/chapeau;
#   - their direct siblings;
#   - their direct children.
#
# Rescued units are NOT automatically accepted as law.
# They must pass the same CandidateRelationClassifier and
# grounding validator as ordinary retrieved candidates.
#
# No CP-specific citation is hard-coded here.
# ============================================================

def structural_rescue_candidates(
    all_units: list[dict],
    existing_candidates: list[dict],
    ledger: list[dict],
) -> list[dict]:

    unit_map = {
        unit["id"]: unit
        for unit in all_units
    }

    existing_ids = {
        unit["id"]
        for unit in existing_candidates
    }

    # Only units that survived the relation/grounding gate
    # are allowed to trigger rescue.
    selected_ids = [
        item["candidate_id"]
        for item in ledger
        if (
            item.get("selected")
            and item.get("relation")
            == "PRIMARY_NORM"
            and item.get(
                "contract_eligible",
                False,
            )
        )
    ]

    rescued = {}

    def add_rescue(
        unit_id: str,
        *,
        source_id: str,
        reason: str,
    ):

        if not unit_id:
            return

        if unit_id in existing_ids:
            return

        if unit_id not in unit_map:
            return

        if unit_id not in rescued:

            item = dict(
                unit_map[unit_id]
            )

            item["score"] = 0.0
            item["rrf_score"] = 0.0

            item[
                "retrieval_signals"
            ] = []

            rescued[
                unit_id
            ] = item

        rescued[
            unit_id
        ][
            "retrieval_signals"
        ].append(
            {
                "channel":
                    "STRUCTURAL_RESCUE",
                "rank":
                    None,
                "raw_score":
                    None,
                "rrf_contribution":
                    0.0,
                "source_candidate_id":
                    source_id,
                "reason":
                    reason,
            }
        )

    for selected_id in selected_ids:

        unit = unit_map.get(
            selected_id
        )

        if not unit:
            continue

        # ----------------------------------------------------
        # Parent / ancestor chapeau
        # ----------------------------------------------------

        parent_id = unit.get(
            "parent_id"
        )

        if parent_id:

            add_rescue(
                parent_id,
                source_id=
                    selected_id,
                reason=
                    "DIRECT_PARENT",
            )

            parent = unit_map.get(
                parent_id
            )

            if parent:

                # --------------------------------------------
                # Direct siblings.
                #
                # This is the minimal sibling-rescue mechanism
                # preserved from the full architecture.
                # --------------------------------------------

                for sibling_id in parent.get(
                    "child_ids",
                    [],
                ):

                    if sibling_id == selected_id:
                        continue

                    add_rescue(
                        sibling_id,
                        source_id=
                            selected_id,
                        reason=
                            "DIRECT_SIBLING",
                    )

        # ----------------------------------------------------
        # Direct children of a selected chapeau/parent.
        # ----------------------------------------------------

        for child_id in unit.get(
            "child_ids",
            [],
        ):

            add_rescue(
                child_id,
                source_id=
                    selected_id,
                reason=
                    "DIRECT_CHILD",
            )

    result = list(
        rescued.values()
    )

    # Stable deterministic ordering.
    result.sort(
        key=lambda x: (
            x.get(
                "citation",
                "",
            ),
            x["id"],
        )
    )

    return result


# ============================================================
# Stage 2:
# Grounded contract compilation
# ============================================================

CONTRACT_SYSTEM_V2 = r"""
You are the second stage of a legal rule compiler.

You are NOT given arbitrary retrieved Rules text.

You receive:

1. one official FRECA CP criterion;
2. a VALIDATED CandidateLedger.

Every legal candidate in the ledger already has:
- a relation type;
- an exact CP span;
- an exact Rules span.

Your task is to compile an executable CP contract using ONLY
those validated relationships.

Each candidate now also contains a LegalBasisLink review.

HARD RULE:

A candidate may appear in basis_candidate_ids ONLY if:

    contract_eligible == true

Candidates with contract_eligible == false may be read as context,
but must never become a legal basis for a contract atom or root.

Do not re-retrieve law.
Do not invent new legal provisions.
Do not output a compliance result.

RELATION SEMANTICS

PRIMARY_NORM

    May ground a mandatory satisfaction proposition.

APPLICABILITY

    May ground an applicability proposition.

EXCEPTION_OR_DEEMING

    May ground an exception or positive non-applicability proposition.

CP_OPERATIONALIZATION_SUPPORT

    May help explain or formulate a proposition already grounded
    by PRIMARY_NORM.

    IMPORTANT:
    CP_OPERATIONALIZATION_SUPPORT can NEVER be the sole legal basis
    for a mandatory satisfaction atom.

DEFINITION
CROSS_REFERENCE_DEPENDENCY
STRUCTURAL_CONTEXT

    May help interpret the governing legal material but must not
    independently create a new scoring requirement.

IRRELEVANT
UNRESOLVED

    Must not be used.

CRITICAL LOGIC RULE

Multiple selected legal candidates do NOT automatically imply ALL.

Do not create ALL merely because several PRIMARY_NORM or supporting
provisions were retrieved.

ALL / ANY must be justified by:
- explicit wording or structure in the official CP; or
- explicit connector/structure in a validated legal candidate.

For every ALL or ANY root with multiple children, provide a
logic_basis entry quoting the source text that justifies that
logical combination.

Each scoring atom must contain:

- atom_id
- proposition
- criterion_quote
- basis_candidate_ids

criterion_quote must be copied from the official CP criterion.

A satisfaction atom must contain at least one PRIMARY_NORM
basis_candidate_id.

It may additionally contain CP_OPERATIONALIZATION_SUPPORT,
DEFINITION or structural/context candidates.

Return JSON:

{
  "cp_id": "CP12",

  "atoms": [
    {
      "atom_id": "A1",
      "proposition": "testable factual proposition",
      "criterion_quote": "exact CP phrase",
      "basis_candidate_ids": [
        "RULE-P45-C2"
      ]
    }
  ],

  "applicability": {
    "op": "CONST",
    "value": true
  },

  "satisfaction": {
    "op": "ATOM",
    "atom_id": "A1"
  },

  "non_applicability": {
    "op": "CONST",
    "value": false
  },

  "logic_basis": [],

  "notes": []
}

Allowed expressions:

{"op":"ATOM","atom_id":"A1"}

{"op":"ALL","children":[...]}

{"op":"ANY","children":[...]}

{"op":"NOT","children":[one_expression]}

{"op":"CONST","value":true}

{"op":"CONST","value":false}

A logic_basis item has this form:

{
  "root": "satisfaction",
  "operator": "ALL",
  "source": "CP",
  "candidate_id": null,
  "quote": "exact source phrase containing the structural basis"
}

or:

{
  "root": "satisfaction",
  "operator": "ALL",
  "source": "RULES",
  "candidate_id": "RULE-P45-C2",
  "quote": "exact source phrase containing the structural basis"
}

Do not manufacture a logical connector.

If the logical relationship cannot be justified from supplied
grounded material, prefer one holistic proposition or state the
problem in notes rather than inventing ALL/ANY.

Return JSON only.
"""


def make_contract_prompt_v2(
    cp: dict,
    ledger: list[dict],
) -> str:

    allowed = [
        item
        for item in ledger
        if item["selected"]
    ]

    return f"""
OFFICIAL CHECKING POINT

CP_ID:
{cp["cp_id"]}

ELEMENT:
{cp["element"]}

SUBELEMENT:
{cp["subelement"]}

CRITERION:
{cp["criterion"]}


VALIDATED CANDIDATE LEDGER

{json.dumps(
    allowed,
    ensure_ascii=False,
    indent=2,
)}


Compile the grounded CP contract.

Return JSON only.
"""


# ============================================================
# Contract validation
# ============================================================

def collect_atom_ids(
    expression: dict,
) -> set[str]:

    op = expression["op"]

    if op == "ATOM":
        return {
            expression["atom_id"]
        }

    result = set()

    for child in expression.get(
        "children",
        [],
    ):
        result |= collect_atom_ids(
            child
        )

    return result


def validate_logic_basis(
    raw_contract: dict,
    cp: dict,
    ledger_map: dict[str, dict],
):

    logic_basis = (
        raw_contract.get(
            "logic_basis",
            [],
        )
    )

    basis_lookup = set()

    for item in logic_basis:

        root = item.get(
            "root"
        )

        operator = item.get(
            "operator"
        )

        source = item.get(
            "source"
        )

        quote = str(
            item.get(
                "quote",
                "",
            )
        )

        if root not in {
            "applicability",
            "satisfaction",
            "non_applicability",
        }:
            continue

        if operator not in {
            "ALL",
            "ANY",
        }:
            continue

        valid = False

        if source == "CP":

            valid = (
                quote_match_mode(
                    quote,
                    cp["criterion"],
                )
                is not None
            )

        elif source == "RULES":

            candidate_id = (
                item.get(
                    "candidate_id"
                )
            )

            candidate = (
                ledger_map.get(
                    candidate_id
                )
            )

            if candidate:

                valid = (
                    quote_match_mode(
                        quote,
                        candidate[
                            "policy_quote"
                        ],
                    )
                    is not None
                )

        if valid:

            basis_lookup.add(
                (
                    root,
                    operator,
                )
            )

    # Minimal structural gate:
    # multi-child ALL/ANY at each root
    # requires a grounded logic basis.
    for root in (
        "applicability",
        "satisfaction",
        "non_applicability",
    ):

        expression = (
            raw_contract[root]
        )

        op = expression.get(
            "op"
        )

        children = expression.get(
            "children",
            [],
        )

        if (
            op in {
                "ALL",
                "ANY",
            }
            and len(children) > 1
        ):

            if (
                root,
                op,
            ) not in basis_lookup:

                raise ValueError(
                    f"{root} uses {op} "
                    "without grounded "
                    "logic_basis."
                )


def validate_and_materialize_contract(
    raw_contract: dict,
    cp: dict,
    ledger: list[dict],
) -> dict:

    if (
        core.canonical_cp_id(
            raw_contract.get(
                "cp_id",
                "",
            )
        )
        != cp[
            "canonical_cp_id"
        ]
    ):
        raise ValueError(
            "Contract CP ID mismatch."
        )

    ledger_map = {
        item["candidate_id"]:
            item
        for item in ledger
    }

    atoms = raw_contract.get(
        "atoms"
    )

    if (
        not isinstance(
            atoms,
            list,
        )
        or not atoms
    ):
        raise ValueError(
            "No contract atoms."
        )

    atom_ids = set()

    materialized_atoms = []

    atom_relation_map = {}

    for atom in atoms:

        atom_id = str(
            atom.get(
                "atom_id",
                "",
            )
        ).strip()

        proposition = str(
            atom.get(
                "proposition",
                "",
            )
        ).strip()

        criterion_quote = str(
            atom.get(
                "criterion_quote",
                "",
            )
        ).strip()

        basis_ids = (
            atom.get(
                "basis_candidate_ids",
                []
            )
        )

        if not atom_id:
            raise ValueError(
                "Atom missing atom_id."
            )

        if atom_id in atom_ids:
            raise ValueError(
                f"Duplicate atom "
                f"{atom_id}"
            )

        atom_ids.add(
            atom_id
        )

        if not proposition:
            raise ValueError(
                f"{atom_id}: "
                "missing proposition"
            )

        if (
            quote_match_mode(
                criterion_quote,
                cp["criterion"],
            )
            is None
        ):
            raise ValueError(
                f"{atom_id}: "
                "criterion_quote "
                "not grounded."
            )

        if (
            not isinstance(
                basis_ids,
                list,
            )
            or not basis_ids
        ):
            raise ValueError(
                f"{atom_id}: "
                "no basis candidates."
            )

        relations = []
        anchors = [
            {
                "source":
                    "CP",
                "quote":
                    criterion_quote,
            }
        ]

        for candidate_id in basis_ids:

            candidate = (
                ledger_map.get(
                    candidate_id
                )
            )

            if candidate is None:
                raise ValueError(
                    f"{atom_id}: "
                    f"unknown basis "
                    f"{candidate_id}"
                )

            if not candidate[
                "selected"
            ]:
                raise ValueError(
                    f"{atom_id}: "
                    f"unselected basis "
                    f"{candidate_id}"
                )

            if not candidate.get(
                "contract_eligible",
                False,
            ):
                raise ValueError(
                    f"{atom_id}: "
                    f"{candidate_id} is not "
                    f"contract-eligible after "
                    f"LegalBasisLink review."
                )

            relation = (
                candidate[
                    "relation"
                ]
            )

            relations.append(
                relation
            )

            policy_quote = (
                candidate[
                    "policy_quote"
                ]
            )

            if not policy_quote:
                raise ValueError(
                    f"{atom_id}: "
                    f"{candidate_id} "
                    "has no grounded quote."
                )

            anchors.append(
                {
                    "source":
                        "RULES",
                    "chunk_id":
                        candidate_id,
                    "quote":
                        policy_quote,
                }
            )

        atom_relation_map[
            atom_id
        ] = relations

        materialized_atoms.append(
            {
                "atom_id":
                    atom_id,
                "proposition":
                    proposition,
                "criterion_quote":
                    criterion_quote,
                "basis_candidate_ids":
                    basis_ids,
                "anchors":
                    anchors,
            }
        )

    # Validate logic syntax using
    # V1's deterministic evaluator schema.
    for root in (
        "applicability",
        "satisfaction",
        "non_applicability",
    ):

        if root not in raw_contract:
            raise ValueError(
                f"Missing root {root}"
            )

        core.validate_expression(
            raw_contract[root],
            atom_ids,
        )

    satisfaction_atoms = (
        collect_atom_ids(
            raw_contract[
                "satisfaction"
            ]
        )
    )

    applicability_atoms = (
        collect_atom_ids(
            raw_contract[
                "applicability"
            ]
        )
    )

    na_atoms = (
        collect_atom_ids(
            raw_contract[
                "non_applicability"
            ]
        )
    )

    # Key extraction from full architecture:
    #
    # Operationalisation support cannot
    # independently create scoring criteria.
    for atom_id in satisfaction_atoms:

        relations = (
            atom_relation_map[
                atom_id
            ]
        )

        if (
            "PRIMARY_NORM"
            not in relations
        ):
            raise ValueError(
                f"{atom_id}: "
                "satisfaction atom lacks "
                "PRIMARY_NORM basis. "
                "Operationalisation/context "
                "alone cannot create a "
                "mandatory scoring atom."
            )

    for atom_id in applicability_atoms:

        relations = (
            atom_relation_map[
                atom_id
            ]
        )

        if (
            "APPLICABILITY"
            not in relations
        ):
            raise ValueError(
                f"{atom_id}: "
                "applicability atom lacks "
                "APPLICABILITY basis."
            )

    for atom_id in na_atoms:

        relations = (
            atom_relation_map[
                atom_id
            ]
        )

        if (
            "EXCEPTION_OR_DEEMING"
            not in relations
        ):
            raise ValueError(
                f"{atom_id}: "
                "non-applicability atom "
                "lacks "
                "EXCEPTION_OR_DEEMING "
                "basis."
            )

    validate_logic_basis(
        raw_contract,
        cp,
        ledger_map,
    )

    contract = dict(
        raw_contract
    )

    contract[
        "atoms"
    ] = materialized_atoms

    return contract


# ============================================================
# Compile V2
# ============================================================

def compile_cp_v2(
    cp_id: str,
    policy_top_k: int,
):

    CONTRACT_DIR_V2.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "\n"
        + "=" * 72
    )

    print(
        "FRECA CORE V2 — "
        "GROUNDED COMPILE"
    )

    print(
        "=" * 72
    )

    cp = core.get_cp(
        cp_id
    )

    print(
        f"\nCP: {cp['cp_id']}"
    )

    print(
        "Criterion:\n"
        f"{cp['criterion']}"
    )

    # --------------------------------------------------------
    # Retrieval
    # --------------------------------------------------------

    print(
        "\n[1/4] Parsing Rules PDF..."
    )

    all_rules = (
        extract_policy_units(
            core.RULES_PATH
        )
    )

    print(
        "PolicyUnits:",
        len(all_rules),
    )

    print(
        "\n[2/4] Multi-channel "
        "PolicyUnit retrieval..."
    )

    candidates = (
        retrieve_policy_candidates(
            cp,
            all_rules,
            final_top_k=
                policy_top_k,
        )
    )

    for candidate in candidates:

        print(
            f"  "
            f"{candidate['rrf_score']:.5f}  "
            f"{candidate['citation']:18s} "
            f"{candidate['unit_type']}"
        )

        signals = sorted(
            candidate.get(
                "retrieval_signals",
                [],
            ),
            key=lambda x:
                x["rank"],
        )

        short_signals = ", ".join(
            (
                f"{x['channel']}"
                f"#{x['rank']}"
            )
            for x in signals
        )

        print(
            f"      {short_signals}"
        )

        print(
            "      ",
            candidate.get(
                "own_text",
                "",
            )
            .replace(
                "\n",
                " ",
            )[:180],
        )

    # --------------------------------------------------------
    # CandidateRelationDecision
    # --------------------------------------------------------

    print(
        "\n[3/4] Candidate relation "
        f"classification with "
        f"{core.CONTRACT_MODEL}..."
    )

    raw_decisions = (
        classify_candidate_batches(
            cp,
            candidates,
            batch_size=12,
        )
    )

    ledger = (
        validate_candidate_ledger(
            raw_decisions,
            cp,
            candidates,
        )
    )

    ledger_path = (
        CONTRACT_DIR_V2
        / (
            f"{cp['cp_id']}"
            "_candidate_ledger.json"
        )
    )

    core.save_json(
        {
            "schema":
                "freca-core-"
                "candidate-ledger-v2",
            "cp":
                cp,
            "model":
                core.CONTRACT_MODEL,
            "candidates":
                candidates,
            "decisions":
                ledger,
        },
        ledger_path,
    )

    print(
        "\nCandidateLedger:"
    )

    relation_counts = {}

    for item in ledger:

        relation = (
            item["relation"]
        )

        relation_counts[
            relation
        ] = (
            relation_counts.get(
                relation,
                0,
            )
            + 1
        )

        marker = (
            "*"
            if item["selected"]
            else " "
        )

        print(
            f"{marker} "
            f"{item['candidate_id']:15s} "
            f"{relation:32s} "
            f"{item['reason_code']}"
        )

        if item[
            "validation_error"
        ]:

            print(
                "    GROUNDING ERROR:",
                item[
                    "validation_error"
                ],
            )

    print(
        "\nRelation counts:"
    )

    for relation, count in sorted(
        relation_counts.items()
    ):

        print(
            f"  {relation}: {count}"
        )

    # --------------------------------------------------------
    # Deterministic structural rescue
    # --------------------------------------------------------

    # --------------------------------------------------------
    # LegalBasisLink gate for initial candidates
    # --------------------------------------------------------

    print(
        "\nLegalBasisLink review:"
    )

    ledger = review_legal_basis(
        cp,
        candidates,
        ledger,
        batch_size=12,
    )

    core.save_json(
        {
            "schema":
                "freca-core-"
                "candidate-ledger-v2",
            "cp":
                cp,
            "model":
                core.CONTRACT_MODEL,
            "candidates":
                candidates,
            "decisions":
                ledger,
        },
        ledger_path,
    )

    print(
        "\nLegalBasisLink decisions:"
    )

    for item in ledger:

        if (
            item.get("selected")
            and item.get("relation")
            in LEGAL_BASIS_TARGET_RELATIONS
        ):

            print(
                f"{'*' if item.get('contract_eligible') else ' '} "
                f"{item['candidate_id']:28s} "
                f"{str(item.get('legal_basis_relation')):35s} "
                f"{item.get('legal_basis_status')}"
            )


    rescued_candidates = (
        structural_rescue_candidates(
            all_rules,
            candidates,
            ledger,
        )
    )

    if rescued_candidates:

        print(
            "\nStructural rescue:"
        )

        for item in rescued_candidates:

            reasons = ", ".join(
                (
                    f"{signal.get('reason')}"
                    f"<-{signal.get('source_candidate_id')}"
                )
                for signal
                in item.get(
                    "retrieval_signals",
                    [],
                )
            )

            print(
                f"  + "
                f"{item['citation']:18s} "
                f"{item['unit_type']:14s} "
                f"{reasons}"
            )

        print(
            "\nClassifying rescued "
            "PolicyUnits..."
        )

        rescued_raw = (
            classify_candidate_batches(
                cp,
                rescued_candidates,
                batch_size=12,
            )
        )

        rescued_ledger = (
            validate_candidate_ledger(
                rescued_raw,
                cp,
                rescued_candidates,
            )
        )

        print(
            "\nLegalBasisLink review "
            "for rescued units..."
        )

        rescued_ledger = (
            review_legal_basis(
                cp,
                rescued_candidates,
                rescued_ledger,
                batch_size=12,
            )
        )

        # Merge candidates and ledgers.
        candidates.extend(
            rescued_candidates
        )

        ledger.extend(
            rescued_ledger
        )

        # Save the updated complete ledger,
        # replacing the pre-rescue version.
        core.save_json(
            {
                "schema":
                    "freca-core-"
                    "candidate-ledger-v2",
                "cp":
                    cp,
                "model":
                    core.CONTRACT_MODEL,
                "candidates":
                    candidates,
                "decisions":
                    ledger,
            },
            ledger_path,
        )

        print(
            "\nRescued CandidateLedger:"
        )

        for item in rescued_ledger:

            marker = (
                "*"
                if item["selected"]
                else " "
            )

            print(
                f"{marker} "
                f"{item['candidate_id']:28s} "
                f"{item['relation']:32s} "
                f"{item['reason_code']}"
            )

    else:

        print(
            "\nStructural rescue: "
            "no new units."
        )


    primary = [
        item
        for item in ledger
        if (
            item["selected"]
            and item["relation"]
            == "PRIMARY_NORM"
        )
    ]

    if not primary:

        raise RuntimeError(
            "COMPILE_REVIEW_REQUIRED: "
            "no grounded PRIMARY_NORM "
            "candidate was identified. "
            "CandidateLedger has been "
            f"saved to {ledger_path}"
        )

    # --------------------------------------------------------
    # Contract compilation from ledger
    # --------------------------------------------------------

    print(
        "\n[4/4] Compiling contract "
        "from grounded CandidateLedger..."
    )

    raw_contract = (
        core.deepseek_json(
            model=
                core.CONTRACT_MODEL,
            system_prompt=
                CONTRACT_SYSTEM_V2,
            user_prompt=
                make_contract_prompt_v2(
                    cp,
                    ledger,
                ),
            thinking=False,
            max_tokens=7000,
        )
    )

    # No semantic "rewrite until validator likes it".
    # A semantic contract error means review required.
    contract = (
        validate_and_materialize_contract(
            raw_contract,
            cp,
            ledger,
        )
    )

    output_path = (
        CONTRACT_DIR_V2
        / f"{cp['cp_id']}.json"
    )

    core.save_json(
        {
            "schema":
                "freca-core-contract-v2",
            "cp":
                cp,
            "model":
                core.CONTRACT_MODEL,
            "retrieved_rules":
                candidates,
            "candidate_ledger":
                ledger,
            "contract":
                contract,
        },
        output_path,
    )

    print(
        "\n"
        + "-" * 72
    )

    print(
        "CONTRACT COMPILED"
    )

    print(
        "-" * 72
    )

    print(
        "\nATOMS:"
    )

    for atom in contract[
        "atoms"
    ]:

        print(
            f"\n{atom['atom_id']}: "
            f"{atom['proposition']}"
        )

        print(
            "  CP:",
            atom[
                "criterion_quote"
            ],
        )

        print(
            "  BASIS:",
            ", ".join(
                atom[
                    "basis_candidate_ids"
                ]
            ),
        )

        relations = []

        ledger_map = {
            item[
                "candidate_id"
            ]:
                item
            for item in ledger
        }

        for candidate_id in atom[
            "basis_candidate_ids"
        ]:

            relations.append(
                ledger_map[
                    candidate_id
                ][
                    "relation"
                ]
            )

        print(
            "  RELATIONS:",
            ", ".join(
                relations
            ),
        )

    print(
        "\nAPPLICABILITY:"
    )

    print(
        json.dumps(
            contract[
                "applicability"
            ],
            indent=2,
            ensure_ascii=False,
        )
    )

    print(
        "\nSATISFACTION:"
    )

    print(
        json.dumps(
            contract[
                "satisfaction"
            ],
            indent=2,
            ensure_ascii=False,
        )
    )

    print(
        "\nNON-APPLICABILITY:"
    )

    print(
        json.dumps(
            contract[
                "non_applicability"
            ],
            indent=2,
            ensure_ascii=False,
        )
    )

    print(
        "\nLOGIC BASIS:"
    )

    print(
        json.dumps(
            contract.get(
                "logic_basis",
                [],
            ),
            indent=2,
            ensure_ascii=False,
        )
    )

    print(
        "\nSaved CandidateLedger:"
    )

    print(
        ledger_path
    )

    print(
        "\nSaved Contract:"
    )

    print(
        output_path
    )


# ============================================================
# Evaluate using existing V1 evidence/evaluator machinery
# ============================================================

def evaluate_v2(
    cp_id: str,
    case_name: str,
    evidence_top_k: int,
):

    cp = core.get_cp(
        cp_id
    )

    contract_path = (
        CONTRACT_DIR_V2
        / f"{cp['cp_id']}.json"
    )

    if not contract_path.exists():

        raise FileNotFoundError(
            "No V2 contract found:\n"
            f"{contract_path}\n\n"
            "Compile it first."
        )

    RESULT_DIR_V2.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Reuse existing evidence parser,
    # alignment model and deterministic
    # evaluator, but isolate V2 results.
    old_result_dir = (
        core.RESULT_DIR
    )

    core.RESULT_DIR = (
        RESULT_DIR_V2
    )

    try:

        core.evaluate_case(
            contract_path,
            case_name,
            evidence_top_k,
        )

    finally:

        core.RESULT_DIR = (
            old_result_dir
        )


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=
            "FRECA Core V2"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    compile_parser = (
        sub.add_parser(
            "compile"
        )
    )

    compile_parser.add_argument(
        "--cp",
        required=True,
    )

    compile_parser.add_argument(
        "--policy-topk",
        type=int,
        default=60,
    )

    evaluate_parser = (
        sub.add_parser(
            "evaluate"
        )
    )

    evaluate_parser.add_argument(
        "--cp",
        required=True,
    )

    evaluate_parser.add_argument(
        "--case",
        required=True,
    )

    evaluate_parser.add_argument(
        "--evidence-topk",
        type=int,
        default=60,
    )

    args = parser.parse_args()

    if args.command == "compile":

        compile_cp_v2(
            args.cp,
            args.policy_topk,
        )

        return

    if args.command == "evaluate":

        evaluate_v2(
            args.cp,
            args.case,
            args.evidence_topk,
        )

        return


if __name__ == "__main__":
    main()
