"""§8 — honest artifact classification.

The proposal refuses to call anything a "baseline". Outputs are split into
three classes, and the subtle rule is §8's last paragraph: *multi-method
agreement is only admissible when the methods have different evidence views.*

These tests pin the three properties that keep the classification honest:

* evidence-integrity QA reports material problems and **never** a business
  ``1`` / ``0`` label;
* the silver consistency set counts **distinct evidence views**, not methods,
  so running one adjudicator twice admits nothing;
* the production candidate is a full, traceable output that never claims to be
  ground truth.
"""

from __future__ import annotations

from freca.models import (
    Applicability,
    AuditDecision,
    Verdict,
)
from freca.state import atomic_write_json

from freca.ledger.baseline import (
    DISCLAIMERS,
    LEDGER_METHOD,
    LEGACY_METHOD,
    MethodRun,
    build_baseline_report,
    build_integrity_qa,
    build_production_candidate,
    build_silver_consistency,
    method_from_legacy_finals,
    method_from_outcomes,
)
from freca.ledger.config import BaselineConfig
from freca.ledger.models import (
    ArtifactClass,
    CaseFactLedger,
    ContradictionKind,
    EvidenceView,
    TaskOutcome,
)
from freca.ledger.review import ACCEPT_PRIMARY, ACCEPT_REVIEW_ON_CONFLICT

from ledger_helpers import (
    ANSWER_LIKE_FLAG,
    CONTAMINATION_FLAG,
    make_contradiction,
    make_decision,
    make_fact,
    make_gate_report,
    make_pack,
    make_rubric,
)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _outcome(
    *,
    case_id: int = 1,
    cp_id: str = "CP1",
    verdict: Verdict = Verdict.COMPLIANT,
    gate_passed: bool = True,
    policy_citations=None,
    supporting=None,
    applicability: Applicability = Applicability.APPLICABLE,
    applicability_reasoning: str = "",
    reviewed: bool = False,
    review_verdict: Verdict | None = None,
    resolution: str = ACCEPT_PRIMARY,
    review_priority: float = 0.0,
) -> TaskOutcome:
    rubric = make_rubric(cp_id=cp_id)
    pack = make_pack(
        rubric=rubric, facts=[make_fact("F1", case_id=case_id)], case_id=case_id
    )
    decision = make_decision(
        rubric=rubric,
        pack=pack,
        verdict=verdict,
        applicability=applicability,
        applicability_reasoning=applicability_reasoning,
        policy_citations=policy_citations,
        supporting=supporting,
        contrary=["F1"] if verdict == Verdict.NON_COMPLIANT else (),
        case_id=case_id,
    )
    gate = make_gate_report(
        passed=gate_passed,
        errors=0 if gate_passed else 1,
        case_id=case_id,
        cp_id=cp_id,
        review_priority=review_priority,
    )
    review = None
    review_gate = None
    if reviewed:
        review = make_decision(
            rubric=rubric,
            pack=pack,
            verdict=review_verdict or verdict,
            case_id=case_id,
        )
        review_gate = make_gate_report(case_id=case_id, cp_id=cp_id)
    return TaskOutcome(
        case_id=case_id,
        cp_id=cp_id,
        primary=decision,
        primary_gate=gate,
        review=review,
        review_gate=review_gate,
        final=decision,
        reviewed=reviewed,
        resolution=resolution,
        pack_summary={"facts": len(pack.facts)},
    )


def _ledger(
    *,
    case_id: int = 1,
    facts=None,
    contradictions=(),
    missing_tracks=(),
    quality_flags=(),
) -> CaseFactLedger:
    return CaseFactLedger(
        case_id=case_id,
        re_number="RE-WA-2021-0041",
        facts=list(facts if facts is not None else [make_fact("F1", case_id=case_id)]),
        contradictions=list(contradictions),
        missing_tracks=list(missing_tracks),
        quality_flags=list(quality_flags),
        chunk_count=4,
        extractor="deterministic-segment-v1",
    )


def _view(name: str, *, model="m", context="c", scope="s") -> EvidenceView:
    return EvidenceView(
        method=name,
        model_signature=model,
        context_construction=context,
        retrieval_scope=scope,
    )


def _method(
    name: str,
    *,
    view: EvidenceView,
    verdicts: dict[tuple[int, str], Verdict],
    complete: bool | dict[tuple[int, str], bool] = True,
) -> MethodRun:
    if isinstance(complete, bool):
        citation_complete = {key: complete for key in verdicts}
    else:
        citation_complete = dict(complete)
    return MethodRun(
        method=name,
        view=view,
        verdicts=dict(verdicts),
        citation_complete=citation_complete,
    )


# --------------------------------------------------------------------------
# Method adapters
# --------------------------------------------------------------------------


def test_ledger_outcomes_become_one_method_with_a_declared_view():
    outcomes = [_outcome(case_id=1), _outcome(case_id=2, cp_id="CP3")]
    run = method_from_outcomes(outcomes)

    assert run.method == LEDGER_METHOD
    assert run.keys() == {(1, "CP1"), (2, "CP3")}
    assert run.verdicts[(1, "CP1")] == Verdict.COMPLIANT
    # §8: a method must declare what evidence view produced its votes.
    assert run.view.view_signature() == (
        "ledger-adjudicator|rubric+fact-pack|policy-index+case-fact-ledger"
    )
    assert all(run.citation_complete.values())


def test_citation_completeness_requires_both_citation_kinds_and_a_clean_gate():
    # No policy citation → not citation-complete.
    run = method_from_outcomes([_outcome(policy_citations=[])])
    assert run.citation_complete[(1, "CP1")] is False

    # No case citation → not citation-complete.
    run = method_from_outcomes([_outcome(supporting=[])])
    assert run.citation_complete[(1, "CP1")] is False

    # Citations present but the gate failed → not citation-complete.
    run = method_from_outcomes([_outcome(gate_passed=False)])
    assert run.citation_complete[(1, "CP1")] is False


def test_not_applicable_is_citation_complete_on_its_own_terms():
    """§7: ``N/A`` is justified by policy plus applicability reasoning."""

    outcome = _outcome(
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning="The clause is limited to slaughter establishments.",
        supporting=[],
    )
    run = method_from_outcomes([outcome])
    assert run.citation_complete[(1, "CP1")] is True

    unexplained = _outcome(
        verdict=Verdict.NOT_APPLICABLE,
        applicability=Applicability.NOT_APPLICABLE,
        applicability_reasoning="",
        supporting=[],
    )
    run = method_from_outcomes([unexplained])
    assert run.citation_complete[(1, "CP1")] is False


def test_legacy_finals_are_readable_as_a_second_independent_view(tmp_path):
    build_dir = tmp_path / "build"
    payload = AuditDecision(
        case_id=1,
        cp_id="CP1",
        applicability=Applicability.APPLICABLE,
        regulatory_requirement="Pest control must be performed and recorded.",
        policy_citations=["policy-1"],
        supporting_evidence=["case-001-t5-p3"],
        contrary_evidence=[],
        contradictions=[],
        verdict=Verdict.COMPLIANT,
        reasoning_summary="Records located.",
        confidence=0.8,
        retrieval_complete=True,
    )
    atomic_write_json(
        build_dir / "final" / "case-001" / "CP1.json", payload.model_dump(mode="json")
    )
    (build_dir / "final" / "case-001" / "CP2.json").write_text(
        "{not json", encoding="utf-8"
    )

    run = method_from_legacy_finals(build_dir)

    assert run.method == LEGACY_METHOD
    assert run.verdicts == {(1, "CP1"): Verdict.COMPLIANT}
    assert run.citation_complete[(1, "CP1")] is True
    # A malformed legacy file is skipped, never guessed at.
    assert (1, "CP2") not in run.verdicts
    # §8 independence: a different context construction and retrieval scope.
    ledger_view = method_from_outcomes([]).view
    assert run.view.view_signature() != ledger_view.view_signature()


def test_missing_legacy_build_directory_is_an_empty_method_not_an_error(tmp_path):
    run = method_from_legacy_finals(tmp_path / "nonexistent")
    assert run.verdicts == {}
    assert run.method == LEGACY_METHOD


# --------------------------------------------------------------------------
# Class 1 — evidence integrity QA
# --------------------------------------------------------------------------


def test_integrity_qa_reports_material_problems_and_never_a_business_label():
    ledgers = [
        _ledger(
            case_id=1,
            facts=[
                make_fact("F1"),
                make_fact("F2", flags=[CONTAMINATION_FLAG]),
                make_fact("F3", flags=[ANSWER_LIKE_FLAG]),
            ],
            contradictions=[
                make_contradiction(kind=ContradictionKind.IDENTITY_MISMATCH),
                make_contradiction(
                    kind=ContradictionKind.MISSING_RECORD, severity="REVIEW"
                ),
            ],
            missing_tracks=[6, 7],
        ),
        _ledger(case_id=2, facts=[]),
    ]
    outcomes = [_outcome(case_id=1), _outcome(case_id=2, gate_passed=False)]

    qa = build_integrity_qa(ledgers=ledgers, outcomes=outcomes)

    assert qa["artifact_class"] == ArtifactClass.EVIDENCE_INTEGRITY_QA.value
    assert qa["cases_examined"] == 2 and qa["tasks_examined"] == 2
    assert qa["total_facts"] == 3
    assert qa["empty_ledgers"] == [2]
    assert qa["cases_missing_tracks"] == {1: [6, 7]}
    assert qa["cases_with_identity_mismatch"] == [1]
    assert qa["contradictions_by_kind"]["identity_mismatch"] == 1
    assert qa["contradictions_by_kind"]["missing_record"] == 1
    assert qa["contaminated_facts"] == 1
    assert qa["answer_like_facts_retained"] == 1
    assert qa["tasks_failing_gates"] == 1
    assert qa["gate_findings"]["ERROR_0"] == 1

    # §8 red line: this class carries no verdict of any kind.
    serialized = repr(qa)
    assert "verdict" not in qa
    assert "1" not in {qa.get("verdict")} and "0" not in {qa.get("verdict")}
    assert qa["forbidden_use"] == "直接充当业务 1/0 标签"
    assert "compliant" not in serialized.lower()


def test_integrity_qa_on_a_clean_run_is_quiet():
    qa = build_integrity_qa(ledgers=[_ledger()], outcomes=[_outcome()])
    assert qa["empty_ledgers"] == []
    assert qa["cases_missing_tracks"] == {}
    assert qa["contradictions_by_kind"] == {}
    assert qa["tasks_failing_gates"] == 0
    assert qa["gate_findings"] == {}


# --------------------------------------------------------------------------
# Class 2 — silver consistency (§8 distinct evidence views)
# --------------------------------------------------------------------------


def test_one_method_alone_cannot_form_a_consistency_set():
    only = _method(
        "ledger", view=_view("ledger"), verdicts={(1, "CP1"): Verdict.COMPLIANT}
    )
    entries, summary = build_silver_consistency([only])

    assert entries == []
    assert summary["admitted"] == 0
    assert summary["rejected"]["too_few_agreeing_methods"] == 1
    assert summary["distinct_views_available"] == 1
    assert "只有一个证据视图可用" in summary["note"]


def test_two_methods_sharing_an_evidence_view_are_not_independent_voters():
    """§8: same model + same context construction ⇒ one voter, not two."""

    shared = _view("a", model="gpt-x", context="full-context", scope="all")
    twin = _view("b", model="gpt-x", context="full-context", scope="all")
    assert shared.view_signature() == twin.view_signature()

    methods = [
        _method("a", view=shared, verdicts={(1, "CP1"): Verdict.COMPLIANT}),
        _method("b", view=twin, verdicts={(1, "CP1"): Verdict.COMPLIANT}),
    ]
    entries, summary = build_silver_consistency(methods)

    assert entries == []
    assert summary["rejected"]["shared_evidence_view"] == 1
    assert summary["distinct_views_available"] == 1


def test_agreement_across_distinct_views_is_admitted():
    methods = [
        _method(
            "ledger",
            view=_view("ledger", model="m1", context="rubric+facts", scope="ledger"),
            verdicts={(1, "CP1"): Verdict.COMPLIANT, (2, "CP1"): Verdict.NON_COMPLIANT},
        ),
        _method(
            "legacy",
            view=_view("legacy", model="m2", context="retrieval-window", scope="chunks"),
            verdicts={(1, "CP1"): Verdict.COMPLIANT, (2, "CP1"): Verdict.NON_COMPLIANT},
        ),
    ]
    entries, summary = build_silver_consistency(methods)

    assert [(entry.case_id, entry.verdict) for entry in entries] == [
        (1, Verdict.COMPLIANT),
        (2, Verdict.NON_COMPLIANT),
    ]
    assert all(entry.distinct_view_count == 2 for entry in entries)
    assert all(entry.citation_complete for entry in entries)
    assert all(entry.agreeing_methods == ["ledger", "legacy"] for entry in entries)
    assert summary["admitted"] == 2
    assert summary["distinct_views_available"] == 2
    assert summary["verdict_distribution"] == {"1": 1, "0": 1}
    assert summary["forbidden_use"] == "声称官方准确率或真值"


def test_disagreement_is_excluded_rather_than_voted_on():
    methods = [
        _method(
            "ledger",
            view=_view("ledger", model="m1"),
            verdicts={(1, "CP1"): Verdict.COMPLIANT},
        ),
        _method(
            "legacy",
            view=_view("legacy", model="m2"),
            verdicts={(1, "CP1"): Verdict.NON_COMPLIANT},
        ),
    ]
    entries, summary = build_silver_consistency(methods)

    assert entries == []
    assert summary["rejected"]["methods_disagree"] == 1
    # No majority rule, no tie-break: a contested item simply is not silver.
    assert summary["admitted"] == 0


def test_a_vote_without_complete_citations_does_not_count():
    key = (1, "CP1")
    methods = [
        _method(
            "ledger",
            view=_view("ledger", model="m1"),
            verdicts={key: Verdict.COMPLIANT},
            complete={key: False},
        ),
        _method(
            "legacy",
            view=_view("legacy", model="m2"),
            verdicts={key: Verdict.COMPLIANT},
            complete={key: False},
        ),
    ]
    entries, summary = build_silver_consistency(methods)

    assert entries == []
    assert summary["rejected"]["no_citation_complete_vote"] == 1


def test_a_single_citation_complete_vote_is_not_agreement():
    key = (1, "CP1")
    methods = [
        _method(
            "ledger",
            view=_view("ledger", model="m1"),
            verdicts={key: Verdict.COMPLIANT},
            complete={key: True},
        ),
        _method(
            "legacy",
            view=_view("legacy", model="m2"),
            verdicts={key: Verdict.COMPLIANT},
            complete={key: False},
        ),
    ]
    entries, summary = build_silver_consistency(methods)

    assert entries == []
    assert summary["rejected"]["too_few_agreeing_methods"] == 1


def test_distinct_view_requirement_can_be_relaxed_only_by_explicit_configuration():
    shared = _view("a", model="same", context="same", scope="same")
    methods = [
        _method("a", view=shared, verdicts={(1, "CP1"): Verdict.COMPLIANT}),
        _method(
            "b",
            view=_view("b", model="same", context="same", scope="same"),
            verdicts={(1, "CP1"): Verdict.COMPLIANT},
        ),
    ]

    entries, _ = build_silver_consistency(methods)
    assert entries == []  # default configuration honours §8

    entries, summary = build_silver_consistency(
        methods, config=BaselineConfig(require_distinct_views=False)
    )
    assert len(entries) == 1
    # Even when relaxed, the record still says only one view was behind it.
    assert entries[0].distinct_view_count == 1
    assert "note" not in summary


def test_items_seen_by_only_one_method_are_not_admitted():
    methods = [
        _method(
            "ledger",
            view=_view("ledger", model="m1"),
            verdicts={(1, "CP1"): Verdict.COMPLIANT, (9, "CP4"): Verdict.COMPLIANT},
        ),
        _method(
            "legacy",
            view=_view("legacy", model="m2"),
            verdicts={(1, "CP1"): Verdict.COMPLIANT},
        ),
    ]
    entries, summary = build_silver_consistency(methods)

    assert [(entry.case_id, entry.cp_id) for entry in entries] == [(1, "CP1")]
    assert summary["candidate_items"] == 2
    assert summary["rejected"]["too_few_agreeing_methods"] == 1


# --------------------------------------------------------------------------
# Class 3 — production candidate
# --------------------------------------------------------------------------


def test_production_candidate_describes_the_full_output_without_claiming_truth():
    outcomes = [
        _outcome(case_id=1, review_priority=0.2),
        _outcome(case_id=2, verdict=Verdict.NON_COMPLIANT, review_priority=0.6),
        _outcome(
            case_id=3,
            gate_passed=False,
            reviewed=True,
            review_verdict=Verdict.NON_COMPLIANT,
            resolution=ACCEPT_REVIEW_ON_CONFLICT,
            review_priority=0.8,
        ),
    ]
    production = build_production_candidate(outcomes)

    assert production["artifact_class"] == ArtifactClass.PRODUCTION_CANDIDATE.value
    assert production["items"] == 3
    assert production["verdict_distribution"] == {"1": 2, "0": 1}
    assert production["reviewed_items"] == 1
    assert production["review_disagreements"] == 1
    assert production["resolutions"][ACCEPT_REVIEW_ON_CONFLICT] == 1
    assert production["items_with_gate_errors"] == 1
    assert production["gate_error_examples"] == ["003:CP1"]
    assert production["mean_review_priority"] == round((0.2 + 0.6 + 0.8) / 3, 4)
    assert production["forbidden_use"] == "取代官方金标"


def test_production_candidate_on_an_empty_run_does_not_divide_by_zero():
    production = build_production_candidate([])
    assert production["items"] == 0
    assert production["mean_review_priority"] == 0.0
    assert production["verdict_distribution"] == {}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def test_baseline_report_bundles_the_three_classes_with_disclaimers():
    ledgers = [_ledger(case_id=1), _ledger(case_id=2)]
    outcomes = [_outcome(case_id=1), _outcome(case_id=2)]

    report = build_baseline_report(run_id="r1", ledgers=ledgers, outcomes=outcomes)

    assert report.run_id == "r1"
    assert report.integrity_qa["artifact_class"] == ArtifactClass.EVIDENCE_INTEGRITY_QA.value
    assert report.silver["artifact_class"] == ArtifactClass.SILVER_CONSISTENCY.value
    assert report.production["artifact_class"] == ArtifactClass.PRODUCTION_CANDIDATE.value
    assert report.disclaimers == DISCLAIMERS
    assert len(report.disclaimers) == 4

    # Only the ledger method is present, so §8 forbids a consistency set.
    assert report.silver["admitted"] == 0
    assert report.silver["entries"] == []
    assert "只有一个证据视图可用" in report.silver["note"]
    assert report.silver["methods"][0]["method"] == LEDGER_METHOD


def test_baseline_report_admits_silver_once_a_second_view_is_supplied():
    ledgers = [_ledger(case_id=1)]
    outcomes = [_outcome(case_id=1)]
    legacy = _method(
        LEGACY_METHOD,
        view=_view(LEGACY_METHOD, model="legacy", context="window", scope="chunks"),
        verdicts={(1, "CP1"): Verdict.COMPLIANT},
    )

    report = build_baseline_report(
        run_id="r2", ledgers=ledgers, outcomes=outcomes, extra_methods=[legacy]
    )

    assert report.silver["admitted"] == 1
    entry = report.silver["entries"][0]
    assert entry["case_id"] == 1 and entry["cp_id"] == "CP1"
    assert entry["distinct_view_count"] == 2
    assert sorted(entry["agreeing_methods"]) == sorted([LEDGER_METHOD, LEGACY_METHOD])
    assert "note" not in report.silver


def test_the_report_serializes_cleanly_for_the_run_directory():
    report = build_baseline_report(
        run_id="r3", ledgers=[_ledger()], outcomes=[_outcome()]
    )
    payload = report.model_dump(mode="json")

    assert set(payload) == {
        "run_id",
        "integrity_qa",
        "silver",
        "production",
        "disclaimers",
    }
    # §8 disclaimers travel with the artifacts, they are not documentation-only.
    assert any("不得直接充当业务 1/0 标签" in text for text in payload["disclaimers"])
    assert any("不声称官方准确率或真值" in text for text in payload["disclaimers"])
    assert any("不取代官方金标" in text for text in payload["disclaimers"])
    assert any("独立投票者" in text for text in payload["disclaimers"])
