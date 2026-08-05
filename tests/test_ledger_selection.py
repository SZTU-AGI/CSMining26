"""Stage C — compact evidence pack construction (proposal §5.4, §7).

Selection is *routing*, not judgement: it decides what the adjudicator reads,
never what the adjudicator concludes. The tests below assert exactly that
boundary, plus the three properties the gate depends on:

* every packed fact records **which criterion pulled it in and why**;
* a criterion that pulled in nothing becomes ``uncovered_criteria`` rather than
  disappearing — that is what lets the gate tell "requirement satisfied" apart
  from "we never looked";
* answer-like text is never packed as support (§3), and another
  establishment's paperwork is kept as *contrary* signal only.
"""

from __future__ import annotations

from freca.ledger.config import SelectionConfig
from freca.ledger.models import (
    CaseFactLedger,
    ContradictionKind,
    CriterionKind,
    RubricCriterion,
)
from freca.ledger.selection import (
    CriterionProfile,
    build_evidence_pack,
    citable_fact_ids,
    compact_pack,
    contaminated_only,
    criterion_ids,
    facts_by_ids,
    render_pack,
    score_fact,
    summarize_pack,
)

from ledger_helpers import (
    ANSWER_LIKE_FLAG,
    CONTAMINATION_FLAG,
    make_contradiction,
    make_fact,
    make_rubric,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _ledger(
    facts,
    *,
    case_id: int = 1,
    contradictions=(),
    missing_tracks=(),
    quality_flags=(),
) -> CaseFactLedger:
    return CaseFactLedger(
        case_id=case_id,
        re_number="RE-WA-2021-0041",
        facts=list(facts),
        contradictions=list(contradictions),
        missing_tracks=list(missing_tracks),
        quality_flags=list(quality_flags),
        chunk_count=len(facts),
        extractor="deterministic-segment-v1",
    )


def _pest_fact(fact_id: str = "F-PEST", **kwargs):
    defaults = dict(
        topic="sanitation_pest",
        claim="Pest control treatment carried out on 2021-03-04",
        value="2021-03-04",
        verbatim="Pest control treatment carried out on 2021-03-04 by a licensed operator.",
        categories=("dated_record",),
    )
    defaults.update(kwargs)
    return make_fact(fact_id, **defaults)


def _registration_fact(fact_id: str = "F-REG", **kwargs):
    defaults = dict(
        topic="registration",
        claim="Registered export establishment RE-WA-2021-0041",
        value="RE-WA-2021-0041",
        verbatim="This registered export establishment holds approval RE-WA-2021-0041.",
        categories=("registration_document",),
        track=1,
        source_file="track-1.docx",
    )
    defaults.update(kwargs)
    return make_fact(fact_id, **defaults)


def _noise_fact(fact_id: str = "F-NOISE"):
    return make_fact(
        fact_id,
        topic="unclassified",
        claim="Vehicle parking bays repainted",
        value="repainted",
        verbatim="The visitor vehicle parking bays were repainted in blue.",
        categories=(),
        track=9,
        source_file="track-9.docx",
    )


# --------------------------------------------------------------------------
# Scoring is lexical routing, not judgement
# --------------------------------------------------------------------------


def test_a_fact_with_no_lexical_overlap_scores_zero():
    rubric = make_rubric()
    profile = CriterionProfile.build(rubric.criteria[1])  # pest control criterion
    score, reasons = score_fact(_noise_fact(), profile, SelectionConfig())
    assert score == 0.0
    assert reasons == []


def test_topic_and_category_matches_raise_the_score_with_stated_reasons():
    rubric = make_rubric()
    profile = CriterionProfile.build(rubric.criteria[1])
    config = SelectionConfig()

    matched, reasons = score_fact(_pest_fact(), profile, config)
    bare, bare_reasons = score_fact(
        _pest_fact("F-BARE", topic="unclassified", categories=()), profile, config
    )

    assert matched > bare > 0
    assert any(reason.startswith("lexical:") for reason in reasons)
    assert "topic:sanitation_pest" in reasons
    assert "category:dated_record" in reasons
    assert not any(reason.startswith("topic:") for reason in bare_reasons)


def test_contaminated_material_is_boosted_only_for_contrary_criteria():
    contrary = RubricCriterion(
        criterion_id="X1",
        kind=CriterionKind.CONTRARY,
        statement="Pest control treatment records belong to another establishment.",
        policy_citations=["policy-2"],
        facts_to_verify=["pest control treatment record ownership"],
    )
    supporting = contrary.model_copy(
        update={"criterion_id": "S1", "kind": CriterionKind.SUPPORTING}
    )
    config = SelectionConfig()
    fact = _pest_fact("F-DIRTY", flags=[CONTAMINATION_FLAG])

    contrary_score, contrary_reasons = score_fact(
        fact, CriterionProfile.build(contrary), config
    )
    supporting_score, supporting_reasons = score_fact(
        fact, CriterionProfile.build(supporting), config
    )

    assert contrary_score > supporting_score
    assert "contaminated_supports_contrary" in contrary_reasons
    assert "contaminated_supports_contrary" not in supporting_reasons


def test_selection_records_no_verdict_and_no_criterion_status():
    rubric = make_rubric()
    ledger = _ledger([_registration_fact(), _pest_fact()])
    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    payload = pack.model_dump(mode="json")
    assert "verdict" not in payload
    assert "status" not in payload
    for item in payload["facts"]:
        assert "polarity" not in item
        # §4: the ledger's own polarity stays undecided all the way through.
        assert item["fact"]["polarity"] == "undecided"


# --------------------------------------------------------------------------
# Attributable selection
# --------------------------------------------------------------------------


def test_every_packed_fact_names_the_criteria_that_pulled_it_in():
    rubric = make_rubric()
    ledger = _ledger([_registration_fact(), _pest_fact(), _noise_fact()])

    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    packed = {item.fact.fact_id: item for item in pack.facts}
    assert "F-NOISE" not in packed  # matched nothing, so it is not packed
    assert "C1" in packed["F-REG"].matched_criteria
    assert "C2" in packed["F-PEST"].matched_criteria
    for item in pack.facts:
        assert item.matched_criteria
        assert item.match_reasons
        assert all(
            reason.split(":")[0] in set(criterion_ids(rubric))
            for reason in item.match_reasons
        )


def test_a_criterion_that_matched_nothing_is_reported_not_hidden():
    rubric = make_rubric()
    ledger = _ledger([_pest_fact()])  # nothing about registration

    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    assert pack.uncovered_criteria == ["C1"]
    assert pack.coverage_by_criterion == {"C1": 0, "C2": 1}
    # This is the signal that separates "satisfied" from "never looked".
    assert summarize_pack(pack)["uncovered_criteria"] == ["C1"]


def test_an_empty_ledger_yields_an_empty_pack_with_every_criterion_uncovered():
    rubric = make_rubric()
    pack = build_evidence_pack(ledger=_ledger([]), rubric=rubric)

    assert pack.facts == []
    assert pack.uncovered_criteria == ["C1", "C2"]
    assert pack.ledger_fact_count == 0
    assert contaminated_only(pack) is False  # empty is not "all contaminated"


def test_selection_trace_explains_the_routing_per_criterion():
    rubric = make_rubric()
    ledger = _ledger([_registration_fact(), _pest_fact()])

    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    trace = {entry["criterion_id"]: entry for entry in pack.selection_trace}
    assert set(trace) == {"C1", "C2"}
    assert trace["C1"]["kind"] == CriterionKind.APPLICABILITY.value
    assert trace["C2"]["kind"] == CriterionKind.SUPPORTING.value
    assert trace["C2"]["candidates"] >= 1
    assert trace["C2"]["selected"] == pack.coverage_by_criterion["C2"]


def test_pack_is_deterministic_for_the_same_ledger_and_rubric():
    rubric = make_rubric()
    ledger = _ledger([_registration_fact(), _pest_fact(), _pest_fact("F-PEST2")])

    first = build_evidence_pack(ledger=ledger, rubric=rubric)
    second = build_evidence_pack(ledger=ledger, rubric=rubric)

    assert first.model_dump() == second.model_dump()


# --------------------------------------------------------------------------
# §3 leakage and contamination
# --------------------------------------------------------------------------


def test_answer_like_facts_are_excluded_from_the_pack_by_default():
    rubric = make_rubric()
    leaked = _pest_fact("F-LEAK", flags=[ANSWER_LIKE_FLAG])
    ledger = _ledger([_pest_fact(), leaked])

    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    assert "F-LEAK" not in {item.fact.fact_id for item in pack.facts}
    assert pack.excluded_fact_count == 1
    assert "excluded_facts:1" in pack.integrity_notes


def test_a_retained_answer_like_fact_is_still_never_citable_as_support():
    rubric = make_rubric()
    leaked = _pest_fact("F-LEAK", flags=[ANSWER_LIKE_FLAG])
    ledger = _ledger([_pest_fact(), leaked])

    pack = build_evidence_pack(
        ledger=ledger, rubric=rubric, config=SelectionConfig(include_answer_like=True)
    )

    assert "F-LEAK" in {item.fact.fact_id for item in pack.facts}
    # §3: it may be visible, but it can never back a supporting citation.
    assert citable_fact_ids(pack) == {"F-PEST"}


def test_contaminated_paperwork_is_kept_but_flagged():
    rubric = make_rubric()
    dirty = _pest_fact("F-DIRTY", flags=[CONTAMINATION_FLAG])
    ledger = _ledger([dirty])

    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    assert {item.fact.fact_id for item in pack.facts} == {"F-DIRTY"}
    assert "pack_contains_contaminated_evidence" in pack.integrity_notes
    assert contaminated_only(pack) is True
    # It stays in the pack as contrary signal, but another establishment's
    # paperwork can never *support* this one, so it is not citable for support.
    assert citable_fact_ids(pack) == set()
    assert pack.facts[0].fact.is_contaminated is True


def test_contaminated_material_can_be_excluded_by_configuration():
    rubric = make_rubric()
    ledger = _ledger([_pest_fact(), _pest_fact("F-DIRTY", flags=[CONTAMINATION_FLAG])])

    pack = build_evidence_pack(
        ledger=ledger, rubric=rubric, config=SelectionConfig(include_contaminated=False)
    )

    assert {item.fact.fact_id for item in pack.facts} == {"F-PEST"}
    assert pack.excluded_fact_count == 1
    assert "pack_contains_contaminated_evidence" not in pack.integrity_notes


# --------------------------------------------------------------------------
# Integrity notes and contradictions travel with the pack
# --------------------------------------------------------------------------


def test_ledger_integrity_problems_reach_the_adjudicator_as_notes():
    rubric = make_rubric()
    ledger = _ledger(
        [_pest_fact()],
        missing_tracks=[6, 7],
        quality_flags=["multiple_re_numbers_in_materials"],
        contradictions=[
            make_contradiction(kind=ContradictionKind.IDENTITY_MISMATCH),
            make_contradiction(kind=ContradictionKind.MISSING_RECORD, severity="REVIEW"),
        ],
    )

    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    assert "missing_tracks:6,7" in pack.integrity_notes
    assert "multiple_re_numbers_in_materials" in pack.integrity_notes
    assert len(pack.contradictions) == 2
    # §7: these are evidence problems, never labels.
    assert "verdict" not in pack.model_dump(mode="json")


def test_contradictions_can_be_withheld_only_by_explicit_configuration():
    rubric = make_rubric()
    ledger = _ledger([_pest_fact()], contradictions=[make_contradiction()])

    pack = build_evidence_pack(
        ledger=ledger,
        rubric=rubric,
        config=SelectionConfig(include_all_contradictions=False),
    )
    assert pack.contradictions == []


# --------------------------------------------------------------------------
# Budgeting
# --------------------------------------------------------------------------


def test_a_narrow_criterion_keeps_its_floor_against_a_verbose_one():
    """Coverage floor first, global ranking second."""

    rubric = make_rubric()
    facts = [_pest_fact(f"F-PEST{index}") for index in range(1, 10)]
    facts.append(_registration_fact("F-REG"))
    ledger = _ledger(facts)

    pack = build_evidence_pack(
        ledger=ledger,
        rubric=rubric,
        config=SelectionConfig(max_facts=3, min_facts_per_criterion=1),
    )

    packed = {item.fact.fact_id for item in pack.facts}
    assert len(packed) == 3
    assert "F-REG" in packed  # the narrow applicability criterion kept its slot
    assert pack.uncovered_criteria == []


def test_max_facts_is_respected_and_the_ledger_size_is_still_reported():
    rubric = make_rubric()
    facts = [_pest_fact(f"F-PEST{index}") for index in range(1, 21)]
    ledger = _ledger(facts)

    pack = build_evidence_pack(
        ledger=ledger, rubric=rubric, config=SelectionConfig(max_facts=5)
    )

    assert len(pack.facts) == 5
    assert pack.ledger_fact_count == 20
    assert summarize_pack(pack)["ledger_facts"] == 20


def test_long_verbatim_text_is_truncated_with_a_visible_marker():
    rubric = make_rubric()
    long_fact = _pest_fact(
        "F-LONG", verbatim="Pest control treatment record. " + ("x" * 900)
    )
    ledger = _ledger([long_fact])

    pack = build_evidence_pack(
        ledger=ledger, rubric=rubric, config=SelectionConfig(verbatim_char_limit=120)
    )

    verbatim = pack.facts[0].fact.verbatim
    assert verbatim.endswith("…[truncated]")
    assert len(verbatim) <= 120 + len(" …[truncated]")


def test_compact_pack_shrinks_the_review_context_without_changing_the_record():
    rubric = make_rubric()
    facts = [_pest_fact(f"F-PEST{index}") for index in range(1, 8)]
    pack = build_evidence_pack(ledger=_ledger(facts), rubric=rubric)

    tight = compact_pack(pack, max_facts=2, verbatim_char_limit=40)

    assert len(tight.facts) == 2
    assert tight.case_id == pack.case_id and tight.cp_id == pack.cp_id
    assert tight.rubric_version == pack.rubric_version
    # Bookkeeping is preserved so the gate still sees the true ledger size.
    assert tight.ledger_fact_count == pack.ledger_fact_count
    assert tight.uncovered_criteria == pack.uncovered_criteria
    assert all(len(item.fact.verbatim) <= 40 + len(" …[truncated]") for item in tight.facts)
    assert len(pack.facts) == 7  # the original is untouched


# --------------------------------------------------------------------------
# Rendering and lookup
# --------------------------------------------------------------------------


def test_rendered_pack_carries_rubric_policy_facts_and_coverage():
    rubric = make_rubric()
    ledger = _ledger(
        [_registration_fact(), _pest_fact("F-DIRTY", flags=[CONTAMINATION_FLAG])],
        missing_tracks=[6],
        contradictions=[make_contradiction()],
    )
    pack = build_evidence_pack(ledger=ledger, rubric=rubric)

    rendered = render_pack(pack, rubric=rubric)

    assert "REGULATORY RUBRIC" in rendered
    assert "POLICY TEXT" in rendered
    assert "CASE FACT PACK" in rendered
    assert "LEDGER CONTRADICTIONS" in rendered
    assert "EVIDENCE COVERAGE" in rendered
    assert "policy-1" in rendered and "policy-2" in rendered
    assert "F-REG" in rendered and "F-DIRTY" in rendered
    # The adjudicator is told, in the prompt itself, what is contaminated.
    assert "⚠CONTAMINATED" in rendered
    assert "track-1.docx" in rendered  # source locator is visible for citation
    assert "missing_tracks:6" in rendered


def test_rendered_pack_says_so_when_nothing_matched():
    rubric = make_rubric()
    rendered = render_pack(build_evidence_pack(ledger=_ledger([]), rubric=rubric), rubric=rubric)
    assert "(no ledger fact matched any rubric criterion)" in rendered
    assert "criteria with no matching case fact: C1, C2" in rendered


def test_facts_by_ids_ignores_ids_that_are_not_in_the_pack():
    rubric = make_rubric()
    pack = build_evidence_pack(ledger=_ledger([_pest_fact()]), rubric=rubric)

    found = facts_by_ids(pack, ["F-PEST", "F-INVENTED"])
    assert [fact.fact_id for fact in found] == ["F-PEST"]
