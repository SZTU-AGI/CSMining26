"""Stage E — decision gates and review triggers (proposal §7).

Two distinct jobs live here, and keeping them apart is the point:

**Gates** check whether a decision satisfies the *contract*:

* ``1`` / ``0`` carry at least one policy citation **and** one case citation;
* ``N/A`` explains regulatory applicability — "not retrieved" or "materials
  incomplete" is not an ``N/A``;
* every citation belongs to the current ``case_id`` and can be traced back to
  an original file;
* answer-like scenario text never backs a supporting citation (§3).

A gate ``ERROR`` means the decision is not deliverable as-is. It does **not**
rewrite the verdict.

**Review triggers** are the §7 list of situations that call for an independent
second pass: rubric without regulatory basis, missing key supporting/contrary
facts, same-topic contradictions in the ledger, reasoning inconsistent with the
label, low confidence or invalid citations.

Critically — and this is the §7 red line — subject conflicts, missing records
and contradictions land in ``quality_flags`` / findings. They never silently
become a business label.
"""

from __future__ import annotations

from freca.models import Applicability, Verdict

from freca.ledger.config import AdjudicationConfig
from freca.ledger.models import (
    CaseFactLedger,
    CheckpointRubric,
    ContradictionKind,
    CriterionStatus,
    EvidenceCoverage,
    EvidencePack,
    GateFinding,
    GateReport,
    GateSeverity,
    LedgerDecision,
)
from freca.ledger.scoring import build_scorecard, review_priority

VERBATIM_MISSING_FLAG = "verbatim_not_found_in_source"

# Codes that always force an independent review when raised.
_TRIGGER_ONLY = "trigger"


class _Collector:
    def __init__(self) -> None:
        self.findings: list[GateFinding] = []
        self.triggers: list[str] = []

    def error(self, code: str, message: str, *, trigger: bool = True) -> None:
        self.findings.append(
            GateFinding(code=code, severity=GateSeverity.ERROR, message=message)
        )
        if trigger:
            self.add_trigger(code)

    def warn(self, code: str, message: str, *, trigger: bool = False) -> None:
        self.findings.append(
            GateFinding(code=code, severity=GateSeverity.WARNING, message=message)
        )
        if trigger:
            self.add_trigger(code)

    def add_trigger(self, code: str) -> None:
        if code not in self.triggers:
            self.triggers.append(code)


# --------------------------------------------------------------------------
# Contract gates
# --------------------------------------------------------------------------


def _gate_execution(decision: LedgerDecision, sink: _Collector) -> None:
    if "adjudication_blocked" in decision.quality_flags:
        sink.error(
            "ADJUDICATION_BLOCKED",
            "the adjudicator did not run; this record is a placeholder, not a verdict",
        )


def _gate_dual_citation(
    decision: LedgerDecision,
    config: AdjudicationConfig,
    sink: _Collector,
) -> None:
    if decision.verdict == Verdict.NOT_APPLICABLE:
        return
    if not config.require_dual_citation:
        return
    if not decision.policy_citations:
        sink.error(
            "MISSING_POLICY_CITATION",
            f"verdict {decision.verdict.value} carries no regulatory citation",
        )
    if not decision.cited_fact_ids:
        sink.error(
            "MISSING_CASE_CITATION",
            f"verdict {decision.verdict.value} carries no citation from case "
            f"{decision.case_id}",
        )


def _gate_not_applicable(decision: LedgerDecision, sink: _Collector) -> None:
    if decision.verdict != Verdict.NOT_APPLICABLE:
        if decision.applicability == Applicability.NOT_APPLICABLE:
            sink.error(
                "APPLICABILITY_INCOHERENT",
                "NOT_APPLICABLE applicability paired with a substantive verdict",
            )
        return
    if decision.applicability != Applicability.NOT_APPLICABLE:
        sink.error(
            "NA_WITHOUT_NOT_APPLICABLE",
            "N/A verdict without NOT_APPLICABLE applicability",
        )
    if not decision.policy_citations:
        sink.error(
            "NA_WITHOUT_POLICY_BASIS",
            "N/A must cite the clause that makes the requirement inapplicable",
        )
    if not decision.applicability_reasoning.strip():
        sink.error(
            "NA_WITHOUT_APPLICABILITY_REASONING",
            "N/A must explain regulatory applicability, not evidence shortage",
        )
    for flag in decision.quality_flags:
        if flag.startswith("na_withdrawn_"):
            sink.warn("NA_WITHDRAWN", f"an N/A claim was withdrawn upstream ({flag})")


def _gate_citation_integrity(
    decision: LedgerDecision,
    pack: EvidencePack,
    rubric: CheckpointRubric,
    sink: _Collector,
) -> None:
    index = pack.fact_index()
    allowed_policy = set(rubric.policy_chunk_ids)

    unknown_policy = [
        citation for citation in decision.policy_citations if citation not in allowed_policy
    ]
    if unknown_policy:
        sink.error(
            "POLICY_CITATION_OUT_OF_RUBRIC",
            "policy citations outside the rubric's retrieval context: "
            + ", ".join(sorted(unknown_policy)[:5]),
        )

    unresolved = [fact_id for fact_id in decision.cited_fact_ids if fact_id not in index]
    if unresolved:
        sink.error(
            "CITATION_UNRESOLVED",
            "cited facts are absent from the evidence pack: "
            + ", ".join(sorted(unresolved)[:5]),
        )

    foreign_case: list[str] = []
    untraceable: list[str] = []
    unverified: list[str] = []
    for fact_id in decision.cited_fact_ids:
        fact = index.get(fact_id)
        if fact is None:
            continue
        if fact.case_id != decision.case_id:
            foreign_case.append(fact_id)
        if not fact.source_file or not fact.chunk_id:
            untraceable.append(fact_id)
        if VERBATIM_MISSING_FLAG in fact.quality_flags:
            unverified.append(fact_id)

    if foreign_case:
        sink.error(
            "CITATION_FOREIGN_CASE",
            f"citations belong to another case: {', '.join(sorted(foreign_case)[:5])}",
        )
    if untraceable:
        sink.error(
            "CITATION_NOT_TRACEABLE",
            "cited facts cannot be traced back to an original file: "
            + ", ".join(sorted(untraceable)[:5]),
        )
    if unverified:
        sink.warn(
            "CITATION_VERBATIM_UNVERIFIED",
            "cited facts whose quote was not found in the source chunk: "
            + ", ".join(sorted(unverified)[:5]),
            trigger=True,
        )

    answer_like = [
        fact_id
        for fact_id in decision.supporting_fact_ids
        if (fact := index.get(fact_id)) is not None and fact.is_answer_like
    ]
    if answer_like:
        sink.error(
            "ANSWER_LIKE_SUPPORT",
            "answer-like scenario text used as supporting evidence: "
            + ", ".join(sorted(answer_like)[:5]),
        )

    contaminated = [
        fact_id
        for fact_id in decision.supporting_fact_ids
        if (fact := index.get(fact_id)) is not None and fact.is_contaminated
    ]
    if contaminated and decision.verdict == Verdict.COMPLIANT:
        sink.warn(
            "COMPLIANT_ON_FOREIGN_PAPERWORK",
            "compliance supported by another establishment's records: "
            + ", ".join(sorted(contaminated)[:5]),
            trigger=True,
        )


# --------------------------------------------------------------------------
# §7 review triggers
# --------------------------------------------------------------------------


def _trigger_rubric(rubric: CheckpointRubric, sink: _Collector) -> None:
    if not rubric.policy_chunk_ids:
        sink.error("RUBRIC_MISSING_POLICY_BASIS", "rubric has no regulatory basis")
        return
    degraded = rubric.generator.get("degraded")
    if degraded:
        sink.warn(
            "RUBRIC_DEGRADED",
            f"rubric was generated in degraded mode ({degraded})",
            trigger=True,
        )
    ungrounded = [
        criterion.criterion_id
        for criterion in rubric.criteria
        if not criterion.policy_citations
    ]
    if ungrounded:
        sink.error(
            "RUBRIC_CRITERION_UNGROUNDED",
            "criteria without policy citations: " + ", ".join(ungrounded[:5]),
        )


def _trigger_evidence(
    decision: LedgerDecision,
    pack: EvidencePack,
    ledger: CaseFactLedger | None,
    sink: _Collector,
) -> None:
    if not pack.facts:
        sink.warn(
            "EMPTY_EVIDENCE_PACK",
            "no ledger fact matched this checking point",
            trigger=True,
        )
    if pack.uncovered_criteria:
        sink.warn(
            "UNCOVERED_CRITERIA",
            "criteria with no matching case fact: "
            + ", ".join(pack.uncovered_criteria[:5]),
            trigger=True,
        )
    if decision.evidence_coverage == EvidenceCoverage.INSUFFICIENT:
        sink.warn(
            "EVIDENCE_INSUFFICIENT",
            "adjudicator reported insufficient evidence coverage",
            trigger=True,
        )

    not_evidenced = [
        item.criterion_id
        for item in decision.criterion_outcomes
        if item.status == CriterionStatus.NOT_EVIDENCED
    ]
    if not_evidenced:
        sink.warn(
            "KEY_FACTS_MISSING",
            "criteria left unevidenced: " + ", ".join(not_evidenced[:5]),
            trigger=True,
        )

    if ledger is not None and ledger.missing_tracks:
        # §7: missing records are a quality flag, never an automatic label.
        sink.warn(
            "MISSING_RECORDS",
            "case materials are missing tracks: "
            + ", ".join(str(track) for track in ledger.missing_tracks),
            trigger=True,
        )


def _trigger_contradictions(pack: EvidencePack, sink: _Collector) -> None:
    for contradiction in pack.contradictions:
        if contradiction.kind == ContradictionKind.IDENTITY_MISMATCH:
            sink.warn(
                "IDENTITY_MISMATCH",
                f"{contradiction.contradiction_id}: {contradiction.detail}",
                trigger=True,
            )
        elif contradiction.kind == ContradictionKind.SAME_TOPIC_CONFLICT:
            sink.warn(
                "SAME_TOPIC_CONFLICT",
                f"{contradiction.contradiction_id}: {contradiction.detail}",
                trigger=True,
            )
        elif contradiction.kind == ContradictionKind.CROSS_DOCUMENT_VALUE:
            sink.warn(
                "CROSS_DOCUMENT_VALUE_CONFLICT",
                f"{contradiction.contradiction_id}: {contradiction.detail}",
                trigger=True,
            )


def _trigger_coherence(decision: LedgerDecision, sink: _Collector) -> None:
    statuses = {item.status for item in decision.criterion_outcomes}

    if decision.verdict == Verdict.COMPLIANT:
        if CriterionStatus.VIOLATED in statuses:
            sink.warn(
                "VERDICT_REASONING_INCONSISTENT",
                "verdict is compliant while at least one criterion is violated",
                trigger=True,
            )
        if decision.contrary_fact_ids:
            sink.warn(
                "COMPLIANT_WITH_CONTRARY_FACTS",
                "verdict is compliant while contrary facts were cited",
                trigger=True,
            )
        if not decision.supporting_fact_ids:
            sink.warn(
                "COMPLIANT_WITHOUT_SUPPORT",
                "verdict is compliant without any supporting fact",
                trigger=True,
            )
    elif decision.verdict == Verdict.NON_COMPLIANT:
        if CriterionStatus.VIOLATED not in statuses and not decision.contrary_fact_ids:
            sink.warn(
                "NON_COMPLIANT_WITHOUT_CONTRARY",
                "verdict is non-compliant but no criterion is violated and no "
                "contrary fact was cited",
                trigger=True,
            )


def _trigger_confidence(
    decision: LedgerDecision,
    config: AdjudicationConfig,
    sink: _Collector,
) -> None:
    if decision.confidence < config.confidence_threshold:
        sink.warn(
            "LOW_CONFIDENCE",
            f"confidence {decision.confidence:.2f} below threshold "
            f"{config.confidence_threshold:.2f}",
            trigger=True,
        )
    repairs = [
        flag
        for flag in decision.quality_flags
        if flag.startswith(("dropped_", "applicability_", "coverage_downgraded", "verdict_unparsed"))
    ]
    if repairs:
        sink.warn(
            "NORMALIZATION_REPAIRS",
            "the raw response required repairs: " + ", ".join(sorted(set(repairs))[:5]),
            trigger=True,
        )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def evaluate_gates(
    *,
    decision: LedgerDecision,
    pack: EvidencePack,
    rubric: CheckpointRubric,
    ledger: CaseFactLedger | None = None,
    config: AdjudicationConfig | None = None,
) -> GateReport:
    """Run every §7 gate and review trigger for one decision."""

    config = config or AdjudicationConfig()
    sink = _Collector()

    _gate_execution(decision, sink)
    _gate_dual_citation(decision, config, sink)
    _gate_not_applicable(decision, sink)
    _gate_citation_integrity(decision, pack, rubric, sink)

    _trigger_rubric(rubric, sink)
    _trigger_evidence(decision, pack, ledger, sink)
    _trigger_contradictions(pack, sink)
    _trigger_coherence(decision, sink)
    _trigger_confidence(decision, config, sink)

    scorecard = build_scorecard(
        decision=decision,
        pack=pack,
        rubric=rubric,
        ledger=ledger,
    )
    errors = [
        finding for finding in sink.findings if finding.severity == GateSeverity.ERROR
    ]
    passed = not errors

    return GateReport(
        case_id=decision.case_id,
        cp_id=decision.cp_id,
        passed=passed,
        findings=sink.findings,
        review_triggers=sink.triggers,
        scorecard=scorecard,
        review_priority=review_priority(
            scorecard=scorecard,
            decision=decision,
            error_count=len(errors),
            trigger_count=len(sink.triggers),
            confidence_threshold=config.confidence_threshold,
        ),
    )


def gate_flags(report: GateReport) -> list[str]:
    """Gate findings rendered as quality flags for the final decision.

    §7: these describe evidence problems. They are attached to the record and
    never used to flip ``1`` / ``0`` / ``N/A``.
    """

    return sorted({f"gate:{finding.code.lower()}" for finding in report.findings})


def summarize_gate(report: GateReport) -> dict[str, object]:
    return {
        "passed": report.passed,
        "errors": [finding.code for finding in report.errors],
        "warnings": [
            finding.code
            for finding in report.findings
            if finding.severity == GateSeverity.WARNING
        ],
        "review_triggers": list(report.review_triggers),
        "review_priority": round(report.review_priority, 4),
        "scorecard": {
            name: round(value, 4)
            for name, value in report.scorecard.as_dimensions().items()
        },
    }


__all__ = [
    "evaluate_gates",
    "gate_flags",
    "summarize_gate",
]
