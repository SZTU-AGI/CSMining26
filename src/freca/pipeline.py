from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from freca.audit import audit_checkpoint
from freca.config import PipelineConfig, RerankerMode, RetrievalAgentMode
from freca.cp import build_policy_source, load_checkpoints
from freca.index import HybridIndex
from freca.integrity import assess_evidence_integrity
from freca.index.rerankers import CrossEncoderApiReranker, LLMListwiseReranker
from freca.llm import (
    CachedJsonClient,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleJsonClient,
    OpenAICompatibleVisionDescriber,
)
from freca.manifest import build_manifest
from freca.models import (
    ArbitrationResult,
    AuditDecision,
    AuditTask,
    CheckpointDefinition,
    CaseManifest,
    EvidenceChunk,
    EscalationTier,
    PipelineRunSummary,
    SourceType,
    TaskStatus,
)
from freca.parsing import parse_docx, parse_pdf, parse_xlsx
from freca.parsing.mineru import build_mineru_client
from freca.signatures import (
    SignatureTruthLoader,
    annotate_chunks,
    contamination_summary_from_truth,
)
from freca.quality import validate_citations, verify_decision
from freca.quality import (
    arbitrate_checkpoint,
    find_consistency_issues,
    should_arbitrate,
)
from freca.retrieval import (
    DisabledRetrievalAgent,
    HeuristicRetrievalAgent,
    LLMQueryRewriter,
    LLMRetrievalAgent,
    retrieve_for_checkpoint,
)
from freca.agent.critic import CriticAgent, HeuristicCritic, LLMCritic
from freca.agent.escalation import escalated_arbitrate
from freca.agent.memory import CaseMemory, FailureModeMemory
from freca.agent.planner import HeuristicPlanner, LLMPlanner, PlannerAgent
from freca.state import TaskStore, atomic_write_json, read_json
from freca.submission import assemble_submission


class BlockedTaskError(RuntimeError):
    pass


def _cached_model_client(config, build_dir: Path, *, name: str):
    return CachedJsonClient(
        OpenAICompatibleJsonClient(config),
        cache_dir=build_dir / "cache" / "models" / name,
        ledger_path=build_dir / "logs" / "model-calls.jsonl",
        client_name=name,
        model_metadata={
            "base_url": config.base_url,
            "model": config.model,
            "response_format": config.response_format.value,
        },
    )


def _build_reranker(config: PipelineConfig):
    mode = config.retrieval.reranker_mode
    if mode in {RerankerMode.NONE, RerankerMode.LEXICAL}:
        return None
    endpoint = config.models.reranker
    if endpoint is None:
        raise BlockedTaskError(f"{mode.value} requires models.reranker configuration")
    if mode == RerankerMode.CROSS_ENCODER_API:
        return CrossEncoderApiReranker(endpoint)
    return LLMListwiseReranker(
        _cached_model_client(endpoint, config.paths.build_dir, name="reranker")
    )


def _build_retrieval_agent(config: PipelineConfig):
    mode = config.retrieval.agent_mode
    if mode == RetrievalAgentMode.DISABLED:
        return DisabledRetrievalAgent()
    if mode == RetrievalAgentMode.LLM:
        endpoint = config.models.retrieval_agent
        if endpoint is None:
            raise BlockedTaskError("llm retrieval agent requires models.retrieval_agent")
        return LLMRetrievalAgent(
            _cached_model_client(
                endpoint,
                config.paths.build_dir,
                name="retrieval_agent",
            )
        )
    rewriter = None
    endpoint = config.models.query_rewriter
    if mode == RetrievalAgentMode.HEURISTIC and endpoint is not None and os.environ.get(
        endpoint.api_key_env
    ):
        rewriter = LLMQueryRewriter(
            _cached_model_client(endpoint, config.paths.build_dir, name="query_rewriter")
        )
    return HeuristicRetrievalAgent(rewriter=rewriter)


def _build_planner(config: PipelineConfig) -> PlannerAgent:
    """按 config.models.planner 是否配置决定 Heuristic vs LLM."""
    endpoint = config.models.planner
    if endpoint is not None and os.environ.get(endpoint.api_key_env):
        return LLMPlanner(
            _cached_model_client(endpoint, config.paths.build_dir, name="planner")
        )
    return HeuristicPlanner()


def _build_critic(config: PipelineConfig) -> CriticAgent:
    """按 config.models.critic 是否配置决定 Heuristic vs LLM."""
    endpoint = config.models.critic
    if endpoint is not None and os.environ.get(endpoint.api_key_env):
        return LLMCritic(
            _cached_model_client(endpoint, config.paths.build_dir, name="critic")
        )
    return HeuristicCritic()


def _build_tiebreaker_client(config: PipelineConfig):
    """返回缓存后的 tiebreaker client;未配置时返回 None。"""
    endpoint = config.models.tiebreaker
    if endpoint is None or not os.environ.get(endpoint.api_key_env):
        return None
    return _cached_model_client(endpoint, config.paths.build_dir, name="tiebreaker")


def _load_signature_truth(config: PipelineConfig):
    """从 ``paths.signature_truth_xlsx`` 读取污染 ground truth;不存在返回 ``None``。"""
    truth_path = getattr(config.paths, "signature_truth_xlsx", None)
    if truth_path is None or not truth_path.exists():
        return None
    return SignatureTruthLoader().load(truth_path)


def write_manifest(config: PipelineConfig) -> dict:
    truth = _load_signature_truth(config)
    manifest = build_manifest(config.paths.cases_root, signature_truth=truth)
    path = config.paths.build_dir / "manifests" / "cases.json"
    atomic_write_json(path, manifest.model_dump(mode="json"))
    return {
        "path": str(path),
        "cases": len(manifest.cases),
        "sources": manifest.source_count,
        "signature_truth_loaded": truth is not None,
        "signature_summary": contamination_summary_from_truth(truth),
    }


def ingest_sources(
    config: PipelineConfig,
    *,
    case_ids: set[int] | None = None,
    mineru_executable: str | None = None,
    disable_mineru: bool = False,
) -> dict:
    truth = _load_signature_truth(config)
    manifest = build_manifest(config.paths.cases_root, signature_truth=truth)
    write_manifest(config)
    build_dir = config.paths.build_dir
    checkpoints = load_checkpoints(config.paths.checkpoints_xlsx)
    atomic_write_json(
        build_dir / "parsed" / "checkpoints.json",
        [checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
    )
    policy_source = build_policy_source(config.paths.policy_pdf)
    mineru_client = None if disable_mineru else build_mineru_client(config.mineru)
    policy_chunks = parse_pdf(
        policy_source,
        build_dir / "parsed" / "mineru",
        mineru_executable=None if disable_mineru else mineru_executable,
        mineru_client=mineru_client,
    )
    atomic_write_json(
        build_dir / "parsed" / "policy.json",
        [chunk.model_dump(mode="json") for chunk in policy_chunks],
    )

    selected = [
        case for case in manifest.cases if case_ids is None or case.case_id in case_ids
    ]
    failures: list[dict[str, str | int]] = []
    chunk_count = len(policy_chunks)
    chunk_flag_counts: Counter[str] = Counter()
    contaminated_chunk_counts: Counter[str] = Counter()
    vision_describer = None
    if config.models.vision is not None and os.environ.get(config.models.vision.api_key_env):
        vision_describer = OpenAICompatibleVisionDescriber(config.models.vision)
    for case in selected:
        case_dir = build_dir / "parsed" / "cases" / f"{case.case_id:03d}"
        image_dir = build_dir / "parsed" / "images" / f"{case.case_id:03d}"
        for source in case.sources:
            output = case_dir / f"track-{source.track}.json"
            try:
                if source.source_type == SourceType.DOCX:
                    chunks = parse_docx(
                        source,
                        image_dir,
                        vision_describer=vision_describer,
                    )
                elif source.source_type == SourceType.XLSX:
                    chunks = parse_xlsx(source)
                else:
                    raise ValueError(f"unsupported case source type: {source.source_type}")
            except Exception as exc:
                failures.append(
                    {
                        "case_id": case.case_id,
                        "source_id": source.source_id,
                        "error": str(exc),
                    }
                )
                atomic_write_json(
                    case_dir / f"track-{source.track}.error.json",
                    {
                        "case_id": case.case_id,
                        "source_id": source.source_id,
                        "source_path": str(source.path),
                        "error": str(exc),
                    },
                )
                continue
            if case.contaminated_tracks and source.track in case.contaminated_tracks:
                chunks = annotate_chunks(chunks, case)
            contaminated_chunk_counts[case.case_id] += sum(
                1
                for chunk in chunks
                if "exclude_from_compliance_evidence" in chunk.flags
            )
            atomic_write_json(
                output,
                [chunk.model_dump(mode="json") for chunk in chunks],
            )
            chunk_flag_counts.update(flag for chunk in chunks for flag in chunk.flags)
            chunk_count += len(chunks)
    case_flags: dict[str, list[int]] = {}
    for case in selected:
        for flag in case.flags:
            case_flags.setdefault(flag, []).append(case.case_id)
    report = {
        "selected_cases": [case.case_id for case in selected],
        "source_count": sum(len(case.sources) for case in selected),
        "policy_chunks": len(policy_chunks),
        "total_chunks": chunk_count,
        "failures": failures,
        "mineru_used": all("mineru_generated" in chunk.flags for chunk in policy_chunks),
        "vision_descriptions_enabled": vision_describer is not None,
        "signature_summary": contamination_summary_from_truth(truth),
        "contaminated_chunk_counts": dict(
            sorted(contaminated_chunk_counts.items())
        ),
        "data_quality": {
            "policy": "flag_and_continue",
            "case_flags": case_flags,
            "chunk_flag_counts": dict(sorted(chunk_flag_counts.items())),
        },
    }
    atomic_write_json(build_dir / "parsed" / "ingest-report.json", report)
    return report


def _load_chunk_file(path: Path) -> list[EvidenceChunk]:
    return [EvidenceChunk.model_validate(item) for item in read_json(path)]


def run_evidence_integrity_gate(build_dir: Path) -> dict:
    """Write deterministic source/identity findings before any LLM audit runs."""

    manifest_path = build_dir / "manifests" / "cases.json"
    manifest = CaseManifest.model_validate(read_json(manifest_path))
    chunks: list[EvidenceChunk] = []
    for case in manifest.cases:
        case_dir = build_dir / "parsed" / "cases" / f"{case.case_id:03d}"
        for source in case.sources:
            chunk_path = case_dir / f"track-{source.track}.json"
            if chunk_path.exists():
                chunks.extend(_load_chunk_file(chunk_path))
    report = assess_evidence_integrity(cases=manifest.cases, chunks=chunks)
    output_path = build_dir / "integrity" / "evidence-integrity.json"
    payload = report.to_dict()
    atomic_write_json(output_path, payload)
    return {"path": str(output_path), **payload}


def build_hybrid_indexes(config: PipelineConfig) -> dict:
    build_dir = config.paths.build_dir
    policy_path = build_dir / "parsed" / "policy.json"
    if not policy_path.exists():
        raise FileNotFoundError("policy chunks are missing; run ingest first")
    policy_chunks = _load_chunk_file(policy_path)
    case_chunks: list[EvidenceChunk] = []
    for path in sorted((build_dir / "parsed" / "cases").glob("*/*.json")):
        if path.name.endswith(".error.json"):
            continue
        case_chunks.extend(_load_chunk_file(path))
    if not case_chunks:
        raise ValueError("case chunks are missing; run ingest first")
    embedding_provider = None
    embedding_mode = "local_hashing_fallback"
    if config.models.embedding is not None and os.environ.get(
        config.models.embedding.api_key_env
    ):
        embedding_provider = OpenAICompatibleEmbeddingProvider(config.models.embedding)
        embedding_mode = embedding_provider.name
    policy_index = HybridIndex(
        policy_chunks,
        scope="policy",
        embedding_provider=embedding_provider,
    )
    case_index = HybridIndex(
        case_chunks,
        scope="case",
        embedding_provider=embedding_provider,
    )
    policy_output = build_dir / "indexes" / "policy.json"
    case_output = build_dir / "indexes" / "cases.json"
    policy_index.save(policy_output)
    case_index.save(case_output)
    report = {
        "policy_chunks": len(policy_chunks),
        "case_chunks": len(case_chunks),
        "embedding_provider": embedding_mode,
    }
    atomic_write_json(build_dir / "indexes" / "index-report.json", report)
    return report


def _load_checkpoints(path: Path) -> dict[str, CheckpointDefinition]:
    return {
        item.cp_id: item
        for item in (
            CheckpointDefinition.model_validate(raw) for raw in read_json(path)
        )
    }


def retrieve_task_context(
    config: PipelineConfig,
    *,
    case_id: int,
    cp_id: str,
):
    build_dir = config.paths.build_dir
    embedding_provider = None
    if config.models.embedding is not None and os.environ.get(
        config.models.embedding.api_key_env
    ):
        embedding_provider = OpenAICompatibleEmbeddingProvider(config.models.embedding)
    policy_index = HybridIndex.load(
        build_dir / "indexes" / "policy.json",
        embedding_provider=embedding_provider,
    )
    case_index = HybridIndex.load(
        build_dir / "indexes" / "cases.json",
        embedding_provider=embedding_provider,
    )
    checkpoints = _load_checkpoints(build_dir / "parsed" / "checkpoints.json")
    if cp_id not in checkpoints:
        raise ValueError(f"unknown checkpoint: {cp_id}")
    reranker = _build_reranker(config)
    retrieval_agent = _build_retrieval_agent(config)
    bundle = retrieve_for_checkpoint(
        checkpoint=checkpoints[cp_id],
        case_id=case_id,
        policy_index=policy_index,
        case_index=case_index,
        agent=retrieval_agent,
        max_repairs=(
            0
            if config.retrieval.agent_mode == RetrievalAgentMode.DISABLED
            else config.retrieval.max_repairs
        ),
        retrieval_config=config.retrieval,
        reranker=reranker,
    )
    output = build_dir / "retrieval-smoke" / f"{case_id:03d}" / f"{cp_id}.json"
    atomic_write_json(output, bundle.model_dump(mode="json"))
    return bundle


def process_audit_task(
    *,
    task: AuditTask,
    checkpoint: CheckpointDefinition,
    policy_index: HybridIndex,
    case_index: HybridIndex,
    audit_client,
    verifier_client,
    arbitrator_client,
    build_dir: Path,
    query_rewriter=None,
    retrieval_agent=None,
    planner: PlannerAgent | None = None,
    critic: CriticAgent | None = None,
    tiebreaker_client=None,
    arbitration_tier: EscalationTier = EscalationTier.BLIND,
    retrieval_config=None,
    reranker=None,
    max_repairs: int = 2,
) -> Path:
    retrieval = retrieve_for_checkpoint(
        checkpoint=checkpoint,
        case_id=task.case_id,
        policy_index=policy_index,
        case_index=case_index,
        rewriter=query_rewriter,
        agent=retrieval_agent,
        planner=planner,
        critic=critic,
        retrieval_config=retrieval_config,
        reranker=reranker,
        max_repairs=max_repairs,
    )
    retrieval_path = build_dir / "retrieval" / f"{task.case_id:03d}" / f"{task.cp_id}.json"
    atomic_write_json(retrieval_path, retrieval.model_dump(mode="json"))
    first = audit_checkpoint(audit_client, checkpoint, retrieval)
    first_validation = validate_citations(first, retrieval)
    decision_dir = build_dir / "decisions" / f"{task.case_id:03d}"
    atomic_write_json(
        decision_dir / f"{task.cp_id}.json",
        {
            "decision": first.model_dump(mode="json"),
            "citation_validation": first_validation.model_dump(mode="json"),
        },
    )
    if verifier_client is None:
        raise BlockedTaskError("verifier model endpoint is not configured")
    first_verification = verify_decision(
        verifier_client,
        checkpoint,
        retrieval,
        first,
    )
    verification_dir = build_dir / "verification" / f"{task.case_id:03d}"
    atomic_write_json(
        verification_dir / f"{task.cp_id}.json",
        first_verification.model_dump(mode="json"),
    )
    final = first
    if should_arbitrate(
        first,
        first_validation,
        first_verification,
        consistency_findings=[],
    ):
        if arbitrator_client is None:
            raise BlockedTaskError("risk-triggered task requires an arbitrator model endpoint")
        arbitration: ArbitrationResult
        if arbitration_tier == EscalationTier.ESCALATED:
            arbitration = escalated_arbitrate(
                blind_client=arbitrator_client,
                tiebreaker_client=tiebreaker_client,
                checkpoint=checkpoint,
                retrieval=retrieval,
                first_decision=first,
            )
        else:
            arbitration = arbitrate_checkpoint(
                arbitrator_client,
                checkpoint,
                retrieval,
                first,
            )
        second = arbitration.second_decision
        second_validation = validate_citations(second, retrieval)
        second_verification = verify_decision(
            verifier_client,
            checkpoint,
            retrieval,
            second,
        )
        arbitration_path = (
            build_dir / "arbitration" / f"{task.case_id:03d}" / f"{task.cp_id}.json"
        )
        atomic_write_json(
            arbitration_path,
            {
                "arbitration": arbitration.model_dump(mode="json"),
                "second_citation_validation": second_validation.model_dump(mode="json"),
                "second_verification": second_verification.model_dump(mode="json"),
            },
        )
        # 升级仲裁的 ACCEPT_MAJORITY / THREE_WAY_TIE 也算"agreement"语义
        accepted_resolution = arbitration.resolution in {"ACCEPT_AGREEMENT", "ACCEPT_MAJORITY"}
        if not accepted_resolution:
            raise BlockedTaskError(
                f"arbitration did not converge: {arbitration.resolution}"
            )
        if not second_validation.passed:
            raise BlockedTaskError("arbitrated decision failed citation validation")
        if second_verification.status.value != "PASS":
            raise BlockedTaskError(
                f"arbitrated decision verifier status is {second_verification.status.value}"
            )
        final = second
    elif not first_validation.passed:
        raise BlockedTaskError("mechanical citation validation failed")
    elif first_verification.status.value != "PASS":
        raise BlockedTaskError(f"verifier status is {first_verification.status.value}")

    final_path = build_dir / "final" / f"{task.case_id:03d}" / f"{task.cp_id}.json"
    atomic_write_json(final_path, final.model_dump(mode="json"))
    # 写 CaseMemory: 跨 CP 累积 shared_facts
    case_memory = CaseMemory(
        build_dir / "memory" / "cases" / f"{task.case_id:03d}.json"
    )
    case_memory.update(final)
    return final_path


def _gap_signature_from_bundle(bundle) -> str:
    """从最后一次 round 的 gaps 与 gate_flags 拼出 failure mode signature."""
    if not bundle.rounds:
        return "no_rounds"
    last_round = bundle.rounds[-1]
    gap_part = "+".join(sorted(last_round.gaps)) if last_round.gaps else "none"
    flag_part = "+".join(sorted(last_round.gate_flags)) if last_round.gate_flags else "none"
    return f"{gap_part}|{flag_part}"


def record_failure_mode(
    *,
    build_dir: Path,
    case_id: int,
    cp_id: str,
    gap_signature: str,
    summary: str,
) -> None:
    """写到 ``build/memory/failure_modes.jsonl``。外部错误处理可调用。"""
    memory = FailureModeMemory(build_dir / "memory" / "failure_modes.jsonl")
    memory.record(
        case_id=case_id,
        cp_id=cp_id,
        gap_signature=gap_signature,
        last_round_summary=summary,
    )


def run_consistency_gate(build_dir: Path, *, run_id: str) -> dict:
    decisions_by_case: dict[int, list] = {}
    store = TaskStore(build_dir / "state" / f"{run_id}-tasks.json")
    tasks = store.all()
    decision_paths: list[Path] = []
    for task in tasks:
        if task.status != TaskStatus.COMPLETED:
            continue
        path = (
            Path(task.artifact_path)
            if task.artifact_path
            else build_dir / "final" / f"{task.case_id:03d}" / f"{task.cp_id}.json"
        )
        if path.exists():
            decision_paths.append(path)
    for path in sorted(set(decision_paths)):
        decision = AuditDecision.model_validate(read_json(path))
        decisions_by_case.setdefault(decision.case_id, []).append(decision)
    findings = []
    for decisions in decisions_by_case.values():
        findings.extend(find_consistency_issues(decisions))
    report_path = build_dir / "consistency" / f"{run_id}.json"
    report = {
        "run_id": run_id,
        "case_count": len(decisions_by_case),
        "finding_count": len(findings),
        "findings": [finding.model_dump(mode="json") for finding in findings],
    }
    atomic_write_json(report_path, report)
    if findings:
        affected = {
            (finding.case_id, cp_id)
            for finding in findings
            for cp_id in finding.cp_ids
        }
        for task in store.all():
            if (task.case_id, task.cp_id) in affected and task.status == TaskStatus.COMPLETED:
                store.update(
                    task.task_id,
                    status=TaskStatus.BLOCKED,
                    error="Element consistency conflict requires review",
                )
    return report


def assemble_run_submission(
    config: PipelineConfig,
    *,
    run_id: str,
    allow_unconfirmed_identifiers: bool = False,
    output_path: Path | None = None,
):
    build_dir = config.paths.build_dir
    store = TaskStore(build_dir / "state" / f"{run_id}-tasks.json")
    tasks = store.all()
    if len(tasks) != 4100:
        raise ValueError(f"expected 4100 audit tasks for assembly, got {len(tasks)}")
    unresolved = sum(task.status != TaskStatus.COMPLETED for task in tasks)
    consistency_path = build_dir / "consistency" / f"{run_id}.json"
    if not consistency_path.exists():
        raise ValueError("consistency gate has not been run")
    consistency = read_json(consistency_path)
    if consistency["finding_count"]:
        raise ValueError(
            f"consistency gate has {consistency['finding_count']} unresolved findings"
        )
    manifest = CaseManifest.model_validate(
        read_json(build_dir / "manifests" / "cases.json")
    )
    decisions = [
        AuditDecision.model_validate(read_json(path))
        for path in sorted((build_dir / "final").glob("*/CP*.json"))
    ]
    output = output_path or (build_dir / "submission.xlsx")
    return assemble_submission(
        decisions,
        manifest,
        config.paths.submission_template,
        output,
        unresolved_tasks=unresolved,
        allow_unconfirmed_identifiers=allow_unconfirmed_identifiers,
    )


def run_audit_tasks(
    config: PipelineConfig,
    *,
    run_id: str,
    case_ids: Iterable[int] = range(1, 101),
    cp_ids: Iterable[str] = tuple(f"CP{index}" for index in range(1, 42)),
    max_workers: int = 4,
) -> PipelineRunSummary:
    build_dir = config.paths.build_dir
    embedding_provider = None
    if config.models.embedding is not None and os.environ.get(
        config.models.embedding.api_key_env
    ):
        embedding_provider = OpenAICompatibleEmbeddingProvider(config.models.embedding)
    policy_index = HybridIndex.load(
        build_dir / "indexes" / "policy.json",
        embedding_provider=embedding_provider,
    )
    case_index = HybridIndex.load(
        build_dir / "indexes" / "cases.json",
        embedding_provider=embedding_provider,
    )
    checkpoints = _load_checkpoints(build_dir / "parsed" / "checkpoints.json")
    store = TaskStore(build_dir / "state" / f"{run_id}-tasks.json")
    create_audit_tasks(
        store,
        run_id=run_id,
        case_ids=list(case_ids),
        cp_ids=list(cp_ids),
    )
    audit_client = _cached_model_client(config.models.audit, build_dir, name="audit")
    verifier_client = (
        _cached_model_client(config.models.verifier, build_dir, name="verifier")
        if config.models.verifier is not None
        else None
    )
    arbitrator_client = (
        _cached_model_client(config.models.arbitrator, build_dir, name="arbitrator")
        if config.models.arbitrator is not None
        else None
    )
    reranker = _build_reranker(config)
    retrieval_agent = _build_retrieval_agent(config)
    planner = _build_planner(config)
    critic = _build_critic(config)
    tiebreaker_client = _build_tiebreaker_client(config)
    arbitration_tier = config.arbitration.tier

    def worker(task: AuditTask) -> str:
        if not os.environ.get(config.models.audit.api_key_env):
            raise BlockedTaskError(
                f"required model credential environment variable is unset: "
                f"{config.models.audit.api_key_env}"
            )
        if verifier_client is None:
            raise BlockedTaskError("verifier model endpoint is not configured")
        if not os.environ.get(config.models.verifier.api_key_env):
            raise BlockedTaskError(
                f"required verifier credential environment variable is unset: "
                f"{config.models.verifier.api_key_env}"
            )
        if arbitrator_client is not None and not os.environ.get(
            config.models.arbitrator.api_key_env
        ):
            arbitrator_client_for_task = None
        else:
            arbitrator_client_for_task = arbitrator_client
        return str(
            process_audit_task(
                task=task,
                checkpoint=checkpoints[task.cp_id],
                policy_index=policy_index,
                case_index=case_index,
                audit_client=audit_client,
                verifier_client=verifier_client,
                arbitrator_client=arbitrator_client_for_task,
                build_dir=build_dir,
                retrieval_agent=retrieval_agent,
                planner=planner,
                critic=critic,
                tiebreaker_client=tiebreaker_client,
                arbitration_tier=arbitration_tier,
                retrieval_config=config.retrieval,
                reranker=reranker,
                max_repairs=(
                    0
                    if config.retrieval.agent_mode == RetrievalAgentMode.DISABLED
                    else config.retrieval.max_repairs
                ),
            ).resolve()
        )

    return run_pending_tasks(store, worker, max_workers=max_workers)


def create_audit_tasks(
    store: TaskStore,
    *,
    run_id: str,
    case_ids: Iterable[int] = range(1, 101),
    cp_ids: Iterable[str] = tuple(f"CP{index}" for index in range(1, 42)),
) -> list[AuditTask]:
    tasks = [
        AuditTask(
            task_id=f"{run_id}:case-{case_id:03d}:{cp_id}",
            run_id=run_id,
            case_id=case_id,
            cp_id=cp_id,
        )
        for case_id in case_ids
        for cp_id in cp_ids
    ]
    initialized = store.initialize(tasks)
    if any(task.run_id != run_id for task in initialized):
        raise ValueError("task store belongs to a different run_id")
    return initialized


def _summary(store: TaskStore) -> PipelineRunSummary:
    tasks = store.all()
    counts = Counter(task.status for task in tasks)
    return PipelineRunSummary(
        total=len(tasks),
        pending=counts[TaskStatus.PENDING],
        running=counts[TaskStatus.RUNNING],
        completed=counts[TaskStatus.COMPLETED],
        blocked=counts[TaskStatus.BLOCKED],
        failed=counts[TaskStatus.FAILED],
    )


def run_pending_tasks(
    store: TaskStore,
    worker: Callable[[AuditTask], str],
    *,
    max_workers: int = 4,
) -> PipelineRunSummary:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    pending = store.pending()

    def run_one(task: AuditTask) -> None:
        current = store.update(
            task.task_id,
            status=TaskStatus.RUNNING,
            attempts=task.attempts + 1,
            error=None,
        )
        try:
            artifact = worker(current)
        except BlockedTaskError as exc:
            store.update(task.task_id, status=TaskStatus.BLOCKED, error=str(exc))
        except Exception as exc:  # Task isolation is intentional; the error is persisted.
            store.update(task.task_id, status=TaskStatus.FAILED, error=str(exc))
        else:
            store.update(
                task.task_id,
                status=TaskStatus.COMPLETED,
                artifact_path=artifact,
                error=None,
            )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_one, task) for task in pending]
        for future in as_completed(futures):
            future.result()
    return _summary(store)
