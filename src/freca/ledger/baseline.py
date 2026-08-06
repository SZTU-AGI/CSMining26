"""§8 — honest artifact classification.

Hand-labelling all 4,100 items is not realistic, so the proposal refuses to
call anything a "baseline" and instead splits outputs into three classes with
explicit permitted and forbidden uses:

===========================  =================================  ==============================
class                        may be used for                    may **not** be used for
===========================  =================================  ==============================
``evidence_integrity_qa``    finding missing records, subject   standing in for a business
                             conflicts, contamination, parse    ``1`` / ``0`` label
                             failures
``silver_consistency``       stability, regression tests,       claiming official accuracy or
                             sampling for review                ground truth
``production_candidate``     submission candidate, per-item     replacing the official gold
                             traceability                       standard
===========================  =================================  ==============================

The subtle part is §8's last paragraph: *"multi-method agreement" is only
admissible when the methods have different evidence views. Methods sharing one
model and one full-context construction are not independent voters.*

:class:`MethodRun` therefore forces every contributing method to declare its
:class:`~freca.ledger.models.EvidenceView`, and
:func:`build_silver_consistency` counts **distinct view signatures**, not
methods. Running the same adjudicator twice — or counting the primary decision
and its own review pass — yields ``distinct_view_count == 1`` and is rejected.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from freca.models import AuditDecision, Verdict
from freca.state import read_json

from freca.ledger.config import BaselineConfig
from freca.ledger.models import (
    ArtifactClass,
    BaselineReport,
    CaseFactLedger,
    ContradictionKind,
    EvidenceView,
    SilverEntry,
    TaskOutcome,
)

LEDGER_METHOD = "ledger-rubric-adjudication"
LEGACY_METHOD = "legacy-retrieval-audit"

DISCLAIMERS = [
    "证据完整性 QA 只反映材料与解析质量，不得直接充当业务 1/0 标签。",
    "Silver 一致性集只表示不同证据视图下的稳定一致，不声称官方准确率或真值。",
    "生产候选结果是本方案的全量输出，可逐项追溯，但不取代官方金标。",
    "共享同一模型与同一上下文构造的方法不计为独立投票者（§8）。",
]


@dataclass
class MethodRun:
    """One method's verdicts plus the evidence view that produced them."""

    method: str
    view: EvidenceView
    verdicts: dict[tuple[int, str], Verdict] = field(default_factory=dict)
    citation_complete: dict[tuple[int, str], bool] = field(default_factory=dict)

    def keys(self) -> set[tuple[int, str]]:
        return set(self.verdicts)


# --------------------------------------------------------------------------
# Method adapters
# --------------------------------------------------------------------------


def _ledger_citation_complete(outcome: TaskOutcome) -> bool:
    decision = outcome.final
    if not decision.policy_citations:
        return False
    if decision.verdict == Verdict.NOT_APPLICABLE:
        return bool(decision.applicability_reasoning.strip())
    return bool(decision.cited_fact_ids) and outcome.primary_gate.passed


def method_from_outcomes(
    outcomes: Iterable[TaskOutcome],
    *,
    method: str = LEDGER_METHOD,
    view: EvidenceView | None = None,
) -> MethodRun:
    """Wrap this architecture's outcomes as one voting method."""

    run = MethodRun(
        method=method,
        view=view
        or EvidenceView(
            method=method,
            model_signature="ledger-adjudicator",
            context_construction="rubric+fact-pack",
            retrieval_scope="policy-index+case-fact-ledger",
        ),
    )
    for outcome in outcomes:
        key = (outcome.case_id, outcome.cp_id)
        run.verdicts[key] = outcome.final.verdict
        run.citation_complete[key] = _ledger_citation_complete(outcome)
    return run


def method_from_legacy_finals(
    build_dir: Path,
    *,
    method: str = LEGACY_METHOD,
) -> MethodRun:
    """Read the legacy pipeline's ``build/final`` decisions as a second view.

    This is the one genuinely independent comparison available today: the
    legacy architecture retrieves case chunks per checking point, while the
    ledger architecture adjudicates against a pre-extracted fact ledger. The
    context construction and retrieval scope differ, so §8's independence
    requirement is satisfiable.
    """

    run = MethodRun(
        method=method,
        view=EvidenceView(
            method=method,
            model_signature="legacy-audit-verifier-arbitrator",
            context_construction="per-cp-retrieval-window",
            retrieval_scope="policy-index+case-chunk-index",
        ),
    )
    final_dir = Path(build_dir) / "final"
    if not final_dir.exists():
        return run
    for path in sorted(final_dir.glob("*/CP*.json")):
        try:
            decision = AuditDecision.model_validate(read_json(path))
        except Exception:  # noqa: BLE001 - a malformed legacy file is simply skipped
            continue
        key = (decision.case_id, decision.cp_id)
        run.verdicts[key] = decision.verdict
        complete = bool(decision.policy_citations) and (
            bool(decision.supporting_evidence or decision.contrary_evidence)
            or decision.verdict == Verdict.NOT_APPLICABLE
        )
        run.citation_complete[key] = complete
    return run


# --------------------------------------------------------------------------
# Class 1 — evidence integrity QA
# --------------------------------------------------------------------------


def build_integrity_qa(
    *,
    ledgers: Sequence[CaseFactLedger],
    outcomes: Sequence[TaskOutcome],
) -> dict[str, Any]:
    """Gate 0 + fact ledger findings. Never a business label."""

    contradiction_kinds: Counter[str] = Counter()
    cases_with_identity_mismatch: set[int] = set()
    for ledger in ledgers:
        for contradiction in ledger.contradictions:
            contradiction_kinds[contradiction.kind.value] += 1
            if contradiction.kind == ContradictionKind.IDENTITY_MISMATCH:
                cases_with_identity_mismatch.add(ledger.case_id)

    missing_tracks = {
        ledger.case_id: list(ledger.missing_tracks)
        for ledger in ledgers
        if ledger.missing_tracks
    }
    empty_ledgers = [ledger.case_id for ledger in ledgers if not ledger.facts]
    contaminated_facts = sum(
        1 for ledger in ledgers for fact in ledger.facts if fact.is_contaminated
    )
    answer_like_facts = sum(
        1 for ledger in ledgers for fact in ledger.facts if fact.is_answer_like
    )

    gate_codes: Counter[str] = Counter()
    failed_gates = 0
    for outcome in outcomes:
        if not outcome.primary_gate.passed:
            failed_gates += 1
        for finding in outcome.primary_gate.findings:
            gate_codes[finding.code] += 1

    return {
        "artifact_class": ArtifactClass.EVIDENCE_INTEGRITY_QA.value,
        "cases_examined": len(ledgers),
        "tasks_examined": len(outcomes),
        "total_facts": sum(len(ledger.facts) for ledger in ledgers),
        "empty_ledgers": empty_ledgers,
        "cases_missing_tracks": missing_tracks,
        "contradictions_by_kind": dict(contradiction_kinds),
        "cases_with_identity_mismatch": sorted(cases_with_identity_mismatch),
        "contaminated_facts": contaminated_facts,
        "answer_like_facts_retained": answer_like_facts,
        "tasks_failing_gates": failed_gates,
        "gate_findings": dict(gate_codes.most_common()),
        "permitted_use": "发现缺件、冲突、污染、解析问题",
        "forbidden_use": "直接充当业务 1/0 标签",
    }


# --------------------------------------------------------------------------
# Class 2 — silver consistency set
# --------------------------------------------------------------------------


def build_silver_consistency(
    methods: Sequence[MethodRun],
    *,
    config: BaselineConfig | None = None,
) -> tuple[list[SilverEntry], dict[str, Any]]:
    """Admit an item only when *independent evidence views* agree completely."""

    config = config or BaselineConfig()
    entries: list[SilverEntry] = []
    rejected: Counter[str] = Counter()

    view_signatures = {method.method: method.view.view_signature() for method in methods}
    distinct_available = len(set(view_signatures.values()))

    keys: set[tuple[int, str]] = set()
    for method in methods:
        keys |= method.keys()

    for key in sorted(keys):
        votes: dict[Verdict, list[MethodRun]] = defaultdict(list)
        for method in methods:
            verdict = method.verdicts.get(key)
            if verdict is None:
                continue
            if not method.citation_complete.get(key, False):
                continue
            votes[verdict].append(method)

        if not votes:
            rejected["no_citation_complete_vote"] += 1
            continue
        if len(votes) > 1:
            rejected["methods_disagree"] += 1
            continue

        verdict, agreeing = next(iter(votes.items()))
        signatures = {method.view.view_signature() for method in agreeing}
        if len(agreeing) < config.min_agreeing_methods:
            rejected["too_few_agreeing_methods"] += 1
            continue
        if config.require_distinct_views and len(signatures) < config.min_distinct_views:
            rejected["shared_evidence_view"] += 1
            continue

        entries.append(
            SilverEntry(
                case_id=key[0],
                cp_id=key[1],
                verdict=verdict,
                agreeing_methods=sorted(method.method for method in agreeing),
                distinct_view_count=len(signatures),
                citation_complete=True,
            )
        )

    summary = {
        "artifact_class": ArtifactClass.SILVER_CONSISTENCY.value,
        "methods": [
            {
                "method": method.method,
                "items": len(method.verdicts),
                "view_signature": method.view.view_signature(),
            }
            for method in methods
        ],
        "distinct_views_available": distinct_available,
        "candidate_items": len(keys),
        "admitted": len(entries),
        "rejected": dict(rejected),
        "verdict_distribution": dict(
            Counter(entry.verdict.value for entry in entries)
        ),
        "permitted_use": "稳定性、回归测试、选择复核样本",
        "forbidden_use": "声称官方准确率或真值",
    }
    if config.require_distinct_views and distinct_available < config.min_distinct_views:
        summary["note"] = (
            "只有一个证据视图可用，按 §8 不能构成一致性集；"
            "请接入具备不同检索范围/上下文构造的第二种方法。"
        )
    return entries, summary


# --------------------------------------------------------------------------
# Class 3 — production candidate
# --------------------------------------------------------------------------


def build_production_candidate(
    outcomes: Sequence[TaskOutcome],
    *,
    config: BaselineConfig | None = None,
) -> dict[str, Any]:
    config = config or BaselineConfig()
    threshold = config.production_priority_threshold

    # 漏洞1 门禁:评分从不阻断 verdict(scoring.py 明令不得阈值化),所以一个引用
    # 完整但判定逻辑错的 verdict 能直接溜进生产候选。这里补一道事后闸门——
    # review_priority 达到阈值且未经独立复核的项计入 held_back,不进入可提交子集;
    # 已复核(review 已确认或推翻 primary)的项放行。items 仍记全量以保留可追溯性。
    held_back: list[str] = []
    submittable = 0
    for outcome in outcomes:
        if outcome.primary_gate.review_priority >= threshold and not outcome.reviewed:
            held_back.append(f"{outcome.case_id:03d}:{outcome.cp_id}")
        else:
            submittable += 1

    verdicts = Counter(outcome.final.verdict.value for outcome in outcomes)
    reviewed = sum(1 for outcome in outcomes if outcome.reviewed)
    resolutions = Counter(outcome.resolution for outcome in outcomes)
    disagreements = sum(
        1
        for outcome in outcomes
        if outcome.review is not None and outcome.review.verdict != outcome.primary.verdict
    )
    gate_failed = [
        f"{outcome.case_id:03d}:{outcome.cp_id}"
        for outcome in outcomes
        if not outcome.primary_gate.passed
    ]
    return {
        "artifact_class": ArtifactClass.PRODUCTION_CANDIDATE.value,
        "items": len(outcomes),
        "submittable_items": submittable,
        "held_back_items": len(held_back),
        "held_back_reason": "high_review_priority_without_review",
        "held_back_examples": held_back[:20],
        "production_priority_threshold": threshold,
        "verdict_distribution": dict(verdicts),
        "reviewed_items": reviewed,
        "review_disagreements": disagreements,
        "resolutions": dict(resolutions),
        "items_with_gate_errors": len(gate_failed),
        "gate_error_examples": gate_failed[:20],
        "mean_review_priority": (
            round(
                sum(outcome.primary_gate.review_priority for outcome in outcomes)
                / len(outcomes),
                4,
            )
            if outcomes
            else 0.0
        ),
        "permitted_use": "提交候选、逐项追溯(已扣除高优先未复核项)",
        "forbidden_use": "取代官方金标",
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def build_baseline_report(
    *,
    run_id: str,
    ledgers: Sequence[CaseFactLedger],
    outcomes: Sequence[TaskOutcome],
    extra_methods: Sequence[MethodRun] = (),
    config: BaselineConfig | None = None,
) -> BaselineReport:
    """Assemble the three §8 artifact classes for one run."""

    config = config or BaselineConfig()
    methods = [method_from_outcomes(outcomes), *extra_methods]
    entries, silver = build_silver_consistency(methods, config=config)
    silver = {**silver, "entries": [entry.model_dump(mode="json") for entry in entries]}
    return BaselineReport(
        run_id=run_id,
        integrity_qa=build_integrity_qa(ledgers=ledgers, outcomes=outcomes),
        silver=silver,
        production=build_production_candidate(outcomes, config=config),
        disclaimers=list(DISCLAIMERS),
    )


__all__ = [
    "DISCLAIMERS",
    "LEDGER_METHOD",
    "LEGACY_METHOD",
    "MethodRun",
    "build_baseline_report",
    "build_integrity_qa",
    "build_production_candidate",
    "build_silver_consistency",
    "method_from_legacy_finals",
    "method_from_outcomes",
]
