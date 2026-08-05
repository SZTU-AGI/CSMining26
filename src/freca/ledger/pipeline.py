"""Orchestration for the ledger architecture (Stage A → E).

This module is the switchable counterpart of :mod:`freca.pipeline`. It imports
that module's durable-task machinery (``run_pending_tasks``,
``create_audit_tasks``, ``BlockedTaskError``) instead of re-implementing it,
and writes every artifact under ``build/ledger/`` so both architectures can run
against the same ``build/`` tree and be compared file by file.

Execution order::

    stage A  build_fact_ledgers   one pass per case  → build/ledger/facts
    stage B  build_rubrics        one pass per CP    → build/ledger/rubrics
    stage C  build_evidence_pack  per case×CP        → build/ledger/packs
    stage D  Adjudicator          per case×CP        → primary decision
    stage E  gates + review       per case×CP        → build/ledger/outcomes

Stages A and B are the reason the architecture is cheaper than it looks: the
case materials are read once per case rather than once per case×CP, and the
rubric is derived once per checking point rather than once per task.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from freca.config import RerankerMode
from freca.index import HybridIndex
from freca.index.rerankers import CrossEncoderApiReranker, LLMListwiseReranker
from freca.llm import (
    CachedJsonClient,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleJsonClient,
)
from freca.models import (
    AuditDecision,
    AuditTask,
    CaseManifest,
    CheckpointDefinition,
    PipelineRunSummary,
    Verdict,
)
from freca.pipeline import BlockedTaskError, create_audit_tasks, run_pending_tasks
from freca.state import read_json
from freca.submission import assemble_submission

from freca.ledger.adjudicate import Adjudicator
from freca.ledger.baseline import (
    build_baseline_report,
    method_from_legacy_finals,
)
from freca.ledger.config import LedgerConfig, ReviewMode
from freca.ledger.extraction import (
    build_case_ledger,
    build_extractor,
    discover_case_ids,
    load_case_chunks,
)
from freca.ledger.gates import evaluate_gates
from freca.ledger.models import (
    BaselineReport,
    CaseFactLedger,
    CheckpointRubric,
    LedgerDecision,
    TaskOutcome,
)
from freca.ledger.review import ReviewCoordinator, accept_without_review
from freca.ledger.rubric import RubricGenerator
from freca.ledger.selection import build_evidence_pack
from freca.ledger.store import LedgerStore

ALL_CP_IDS = tuple(f"CP{index}" for index in range(1, 42))


# --------------------------------------------------------------------------
# Shared resources
# --------------------------------------------------------------------------


def make_store(config: LedgerConfig) -> LedgerStore:
    return LedgerStore(config.ledger_dir)


def stage_client(
    config: LedgerConfig,
    stage: str,
    store: LedgerStore,
) -> CachedJsonClient | None:
    """Build a cached JSON client for one ledger stage, or ``None``.

    Returning ``None`` is a first-class outcome, not an error: Stage A falls
    back to the deterministic extractor, Stage B degrades to a citation-only
    rubric, and Stage D records ``adjudication_blocked`` instead of guessing.
    """

    endpoint = config.endpoint(stage)
    if endpoint is None:
        return None
    if not os.environ.get(endpoint.api_key_env):
        return None
    return CachedJsonClient(
        OpenAICompatibleJsonClient(endpoint),
        cache_dir=store.cache_dir / stage,
        ledger_path=store.ledger_log_path,
        client_name=f"ledger-{stage}",
        model_metadata={
            "base_url": endpoint.base_url,
            "model": endpoint.model,
            "response_format": endpoint.response_format.value,
            "stage": stage,
        },
    )


def _embedding_provider(config: LedgerConfig):
    endpoint = config.pipeline.models.embedding
    if endpoint is None or not os.environ.get(endpoint.api_key_env):
        return None
    return OpenAICompatibleEmbeddingProvider(endpoint)


def load_policy_index(config: LedgerConfig) -> HybridIndex:
    path = config.build_dir / "indexes" / "policy.json"
    if not path.exists():
        raise FileNotFoundError(
            f"policy index is missing: {path}. Run the shared `freca index` step first."
        )
    return HybridIndex.load(path, embedding_provider=_embedding_provider(config))


def load_checkpoint_map(config: LedgerConfig) -> dict[str, CheckpointDefinition]:
    path = config.build_dir / "parsed" / "checkpoints.json"
    if not path.exists():
        raise FileNotFoundError(f"parsed checkpoints are missing: {path}")
    return {
        item.cp_id: item
        for item in (CheckpointDefinition.model_validate(raw) for raw in read_json(path))
    }


def _reranker(config: LedgerConfig, store: LedgerStore):
    mode = config.pipeline.retrieval.reranker_mode
    if mode in {RerankerMode.NONE, RerankerMode.LEXICAL}:
        return None
    endpoint = config.pipeline.models.reranker
    if endpoint is None or not os.environ.get(endpoint.api_key_env):
        return None
    if mode == RerankerMode.CROSS_ENCODER_API:
        return CrossEncoderApiReranker(endpoint)
    client = CachedJsonClient(
        OpenAICompatibleJsonClient(endpoint),
        cache_dir=store.cache_dir / "reranker",
        ledger_path=store.ledger_log_path,
        client_name="ledger-reranker",
        model_metadata={"base_url": endpoint.base_url, "model": endpoint.model},
    )
    return LLMListwiseReranker(client)


# --------------------------------------------------------------------------
# Stage A — fact ledgers
# --------------------------------------------------------------------------


def build_fact_ledgers(
    config: LedgerConfig,
    *,
    case_ids: Iterable[int] | None = None,
    force: bool = False,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """One materials pass per case (§4)."""

    store = make_store(config)
    settings = config.ledger.extraction
    client = stage_client(config, "extractor", store)
    extractor = build_extractor(settings, client=client)

    targets = list(case_ids) if case_ids is not None else discover_case_ids(config.build_dir)
    if not targets:
        raise FileNotFoundError(
            f"no parsed cases found under {config.build_dir / 'parsed' / 'cases'}"
        )

    results: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}

    def one(case_id: int) -> None:
        try:
            if not force and store.has_ledger(case_id):
                existing = store.read_ledger(case_id)
                results[case_id] = {
                    "facts": len(existing.facts),
                    "contradictions": len(existing.contradictions),
                    "cached": True,
                }
                return
            chunks = load_case_chunks(config.build_dir, case_id)
            ledger, trace = build_case_ledger(
                case_id=case_id,
                chunks=chunks,
                extractor=extractor,
                config=settings,
            )
            store.write_ledger(ledger)
            store.write_ledger_trace(case_id, trace)
            results[case_id] = {
                "facts": len(ledger.facts),
                "contradictions": len(ledger.contradictions),
                "missing_tracks": ledger.missing_tracks,
                "quality_flags": ledger.quality_flags,
                "cached": False,
            }
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the pass
            errors[case_id] = f"{type(exc).__name__}: {exc}"

    workers = max_workers or settings.max_workers
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(one, case_id) for case_id in targets]
        for future in as_completed(futures):
            future.result()

    return {
        "stage": "A",
        "extractor": getattr(extractor, "name", type(extractor).__name__),
        "model_client": "configured" if client is not None else "deterministic-only",
        "cases": len(targets),
        "succeeded": len(results),
        "failed": len(errors),
        "total_facts": sum(item.get("facts", 0) for item in results.values()),
        "cases_with_contradictions": sum(
            1 for item in results.values() if item.get("contradictions")
        ),
        "errors": errors,
    }


# --------------------------------------------------------------------------
# Stage B — rubrics
# --------------------------------------------------------------------------


def build_rubrics(
    config: LedgerConfig,
    *,
    cp_ids: Iterable[str] | None = None,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """One citation-complete rubric per checking point (§5)."""

    store = make_store(config)
    checkpoints = load_checkpoint_map(config)
    policy_index = load_policy_index(config)
    client = stage_client(config, "rubric", store)
    endpoint = config.endpoint("rubric")

    generator = RubricGenerator(
        config=config.ledger.rubric,
        client=client,
        store=store,
        retrieval_config=config.pipeline.retrieval,
        reranker=_reranker(config, store),
        model_name=getattr(endpoint, "model", "unconfigured"),
    )

    targets = list(cp_ids) if cp_ids is not None else list(ALL_CP_IDS)
    built: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    degraded: list[str] = []

    def one(cp_id: str) -> None:
        checkpoint = checkpoints.get(cp_id)
        if checkpoint is None:
            errors[cp_id] = "unknown checking point"
            return
        try:
            rubric, cached = generator.generate(
                checkpoint=checkpoint,
                policy_index=policy_index,
            )
        except Exception as exc:  # noqa: BLE001
            errors[cp_id] = f"{type(exc).__name__}: {exc}"
            return
        if rubric.generator.get("degraded"):
            degraded.append(cp_id)
        built[cp_id] = {
            "criteria": len(rubric.criteria),
            "policy_chunks": len(rubric.policy_chunk_ids),
            "cached": cached,
            "version": rubric.rubric_version,
        }

    workers = max_workers or config.ledger.rubric.max_workers
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(one, cp_id) for cp_id in targets]
        for future in as_completed(futures):
            future.result()

    return {
        "stage": "B",
        "model_client": "configured" if client is not None else "degraded-only",
        "requested": len(targets),
        "built": len(built),
        "cached": sum(1 for item in built.values() if item["cached"]),
        "degraded": sorted(degraded),
        "failed": len(errors),
        "errors": errors,
    }


# --------------------------------------------------------------------------
# Stages C–E — per case×CP
# --------------------------------------------------------------------------


def process_ledger_task(
    *,
    case_id: int,
    cp_id: str,
    ledger: CaseFactLedger,
    rubric: CheckpointRubric,
    config: LedgerConfig,
    adjudicator: Adjudicator,
    coordinator: ReviewCoordinator | None,
    store: LedgerStore,
) -> Path:
    """Run Stage C → E for one task and persist the outcome."""

    pack = build_evidence_pack(
        ledger=ledger,
        rubric=rubric,
        config=config.ledger.selection,
    )
    store.write_pack(pack)

    primary = adjudicator.adjudicate(rubric=rubric, pack=pack)
    primary_gate = evaluate_gates(
        decision=primary,
        pack=pack,
        rubric=rubric,
        ledger=ledger,
        config=config.ledger.adjudication,
    )

    if coordinator is not None and coordinator.should_review(primary_gate):
        outcome = coordinator.resolve(
            rubric=rubric,
            pack=pack,
            primary=primary,
            primary_gate=primary_gate,
            ledger=ledger,
        )
    else:
        outcome = accept_without_review(
            rubric=rubric,
            pack=pack,
            primary=primary,
            primary_gate=primary_gate,
        )

    return store.write_outcome(outcome)


def run_ledger_tasks(
    config: LedgerConfig,
    *,
    run_id: str,
    case_ids: Iterable[int] = range(1, 101),
    cp_ids: Iterable[str] = ALL_CP_IDS,
    max_workers: int = 4,
) -> PipelineRunSummary:
    """Run Stage C→E over the durable task store."""

    store = make_store(config)
    case_list = list(case_ids)
    cp_list = list(cp_ids)

    ledgers: dict[int, CaseFactLedger] = {}
    for case_id in case_list:
        if not store.has_ledger(case_id):
            raise FileNotFoundError(
                f"fact ledger for case {case_id} is missing; run stage A first"
            )
        ledgers[case_id] = store.read_ledger(case_id)

    rubrics: dict[str, CheckpointRubric] = {}
    for cp_id in cp_list:
        if not store.has_rubric(cp_id):
            raise FileNotFoundError(
                f"rubric for {cp_id} is missing; run stage B first"
            )
        rubrics[cp_id] = store.read_rubric(cp_id)

    adjudicator = Adjudicator(
        client=stage_client(config, "adjudicator", store),
        config=config.ledger.adjudication,
    )
    coordinator: ReviewCoordinator | None = None
    if config.ledger.review.mode != ReviewMode.DISABLED:
        coordinator = ReviewCoordinator(
            adjudicator=Adjudicator(
                client=stage_client(config, "reviewer", store),
                config=config.ledger.adjudication,
            ),
            config=config.ledger.review,
            adjudication_config=config.ledger.adjudication,
        )

    task_store = store.task_store(run_id)
    create_audit_tasks(task_store, run_id=run_id, case_ids=case_list, cp_ids=cp_list)

    def worker(task: AuditTask) -> str:
        if adjudicator.client is None:
            endpoint = config.endpoint("adjudicator")
            hint = (
                f"set {endpoint.api_key_env}"
                if endpoint is not None
                else "configure ledger.models.adjudicator or models.audit"
            )
            raise BlockedTaskError(f"adjudication model is unavailable; {hint}")
        return str(
            process_ledger_task(
                case_id=task.case_id,
                cp_id=task.cp_id,
                ledger=ledgers[task.case_id],
                rubric=rubrics[task.cp_id],
                config=config,
                adjudicator=adjudicator,
                coordinator=coordinator,
                store=store,
            ).resolve()
        )

    return run_pending_tasks(task_store, worker, max_workers=max_workers)


# --------------------------------------------------------------------------
# §8 report and submission
# --------------------------------------------------------------------------


def run_baseline(
    config: LedgerConfig,
    *,
    run_id: str,
    include_legacy: bool = True,
) -> BaselineReport:
    """Classify this run's artifacts into the three §8 classes."""

    store = make_store(config)
    ledgers = list(store.iter_ledgers())
    outcomes = list(store.iter_outcomes())

    extra = []
    if include_legacy:
        legacy = method_from_legacy_finals(config.build_dir)
        if legacy.verdicts:
            extra.append(legacy)

    report = build_baseline_report(
        run_id=run_id,
        ledgers=ledgers,
        outcomes=outcomes,
        extra_methods=extra,
        config=config.ledger.baseline,
    )
    store.write_baseline(run_id, report.model_dump(mode="json"))
    return report


def to_audit_decision(
    decision: LedgerDecision,
    *,
    rubric: CheckpointRubric | None = None,
    pack_locators: dict[str, str] | None = None,
) -> AuditDecision:
    """Project a ledger decision onto the legacy submission schema.

    The projection is lossless in the only way that matters for delivery: the
    business label, the applicability and the citations survive. The rubric
    criteria and the fact ledger stay in ``build/ledger/outcomes`` for audit.
    """

    locators = pack_locators or {}
    requirement = ""
    if rubric is not None:
        requirement = rubric.checkpoint_text or "; ".join(
            criterion.statement for criterion in rubric.criteria[:3]
        )
    return AuditDecision(
        case_id=decision.case_id,
        cp_id=decision.cp_id,
        applicability=decision.applicability,
        regulatory_requirement=requirement[:2000],
        policy_citations=list(decision.policy_citations),
        supporting_evidence=[
            locators.get(fact_id, fact_id) for fact_id in decision.supporting_fact_ids
        ],
        contrary_evidence=[
            locators.get(fact_id, fact_id) for fact_id in decision.contrary_fact_ids
        ],
        contradictions=list(decision.contradiction_ids),
        verdict=decision.verdict,
        reasoning_summary=decision.reasoning_summary or decision.applicability_reasoning,
        confidence=decision.confidence,
        retrieval_complete=decision.evidence_coverage.value != "insufficient",
        review_flags=list(decision.quality_flags),
    )


def assemble_ledger_submission(
    config: LedgerConfig,
    *,
    run_id: str,
    output_path: Path | None = None,
    allow_unconfirmed_identifiers: bool = False,
):
    """Write a submission workbook from ``build/ledger/final``."""

    store = make_store(config)
    task_store = store.task_store(run_id)
    unresolved = sum(
        1 for task in task_store.all() if task.status.value not in {"COMPLETED"}
    )

    outcomes = list(store.iter_outcomes())
    if not outcomes:
        raise FileNotFoundError("no ledger outcomes found; run the audit stage first")

    rubrics: dict[str, CheckpointRubric] = {}
    decisions: list[AuditDecision] = []
    for outcome in outcomes:
        if outcome.cp_id not in rubrics and store.has_rubric(outcome.cp_id):
            rubrics[outcome.cp_id] = store.read_rubric(outcome.cp_id)
        try:
            pack = store.read_pack(outcome.case_id, outcome.cp_id)
            locators = {
                item.fact.fact_id: item.fact.locator() for item in pack.facts
            }
        except Exception:  # noqa: BLE001 - the pack is an audit aid, not a requirement
            locators = {}
        decisions.append(
            to_audit_decision(
                outcome.final,
                rubric=rubrics.get(outcome.cp_id),
                pack_locators=locators,
            )
        )

    manifest = CaseManifest.model_validate(
        read_json(config.build_dir / "manifests" / "cases.json")
    )
    output = output_path or (config.ledger_dir / "submission.xlsx")
    return assemble_submission(
        decisions,
        manifest,
        config.pipeline.paths.submission_template,
        output,
        unresolved_tasks=unresolved,
        allow_unconfirmed_identifiers=allow_unconfirmed_identifiers,
    )


# --------------------------------------------------------------------------
# End-to-end workflow
# --------------------------------------------------------------------------


def run_ledger_workflow(
    config: LedgerConfig,
    *,
    run_id: str,
    case_ids: Sequence[int] | None = None,
    cp_ids: Sequence[str] | None = None,
    max_workers: int = 4,
    force_facts: bool = False,
    assemble: bool = False,
    allow_unconfirmed_identifiers: bool = False,
) -> dict[str, Any]:
    """Stage A → E, then the §8 classification, in one call."""

    store = make_store(config)
    cases = list(case_ids) if case_ids is not None else discover_case_ids(config.build_dir)
    cps = list(cp_ids) if cp_ids is not None else list(ALL_CP_IDS)

    report: dict[str, Any] = {
        "run_id": run_id,
        "config": config.describe(),
        "cases": len(cases),
        "checking_points": len(cps),
    }

    report["stage_a"] = build_fact_ledgers(config, case_ids=cases, force=force_facts)
    if report["stage_a"]["succeeded"] == 0:
        report["status"] = "BLOCKED"
        report["reason"] = "no fact ledger could be built"
        store.write_run_report(run_id, report)
        return report

    report["stage_b"] = build_rubrics(config, cp_ids=cps)
    if report["stage_b"]["built"] == 0:
        report["status"] = "BLOCKED"
        report["reason"] = "no rubric could be built"
        store.write_run_report(run_id, report)
        return report

    ready_cases = [case_id for case_id in cases if store.has_ledger(case_id)]
    ready_cps = [cp_id for cp_id in cps if store.has_rubric(cp_id)]

    summary = run_ledger_tasks(
        config,
        run_id=run_id,
        case_ids=ready_cases,
        cp_ids=ready_cps,
        max_workers=max_workers,
    )
    report["stage_cde"] = summary.model_dump()

    baseline = run_baseline(config, run_id=run_id)
    report["baseline"] = {
        "integrity_qa": baseline.integrity_qa,
        "silver": {
            key: value for key, value in baseline.silver.items() if key != "entries"
        },
        "production": baseline.production,
        "disclaimers": baseline.disclaimers,
    }

    report["status"] = (
        "COMPLETED" if summary.blocked == 0 and summary.failed == 0 else "INCOMPLETE"
    )

    if assemble and report["status"] == "COMPLETED":
        submission = assemble_ledger_submission(
            config,
            run_id=run_id,
            allow_unconfirmed_identifiers=allow_unconfirmed_identifiers,
        )
        report["submission"] = submission.model_dump(mode="json")

    store.write_run_report(run_id, report)
    return report


def verdict_distribution(outcomes: Sequence[TaskOutcome]) -> dict[str, int]:
    counts = {verdict.value: 0 for verdict in Verdict}
    for outcome in outcomes:
        counts[outcome.final.verdict.value] += 1
    return counts


__all__ = [
    "ALL_CP_IDS",
    "assemble_ledger_submission",
    "build_fact_ledgers",
    "build_rubrics",
    "load_checkpoint_map",
    "load_policy_index",
    "make_store",
    "process_ledger_task",
    "run_baseline",
    "run_ledger_tasks",
    "run_ledger_workflow",
    "stage_client",
    "to_audit_decision",
    "verdict_distribution",
]
