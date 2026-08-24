from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable

from freca.config import (
    FusionMode,
    PipelineConfig,
    RecallMode,
    RerankerMode,
    RetrievalAgentMode,
    RetrievalConfig,
    SelectorMode,
)
from freca.index import HybridIndex
from freca.llm import OpenAICompatibleEmbeddingProvider
from freca.methods import MethodRunLayout, gold_tasks
from freca.models import AuditTask, CheckpointDefinition, EvidenceChunk, PipelineRunSummary, RetrievalBundle
from freca.pipeline import (
    BlockedTaskError,
    _build_reranker,
    _build_retrieval_agent,
    _cached_model_client,
    process_retrieved_audit_task,
    run_pending_tasks,
)
from freca.retrieval import retrieve_for_checkpoint
from freca.state import TaskStore, atomic_write_json, read_json


ABLATION_VARIANT_NAMES = (
    "bm25_only",
    "vector_only",
    "weighted_hybrid",
    "rrf_reranker_no_mmr",
    "full_retrieval",
)

ABLATION_VARIANT_DESCRIPTIONS = {
    "bm25_only": "BM25 recall, no fusion/reranker/MMR or repair",
    "vector_only": "vector recall, no fusion/reranker/MMR or repair",
    "weighted_hybrid": "weighted BM25/vector fusion without reranker/MMR or repair",
    "rrf_reranker_no_mmr": "RRF plus reranker, relevance top-k, no repair",
    "full_retrieval": "the configured full production retrieval snapshot",
}


def _validate_experiment_id(experiment_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", experiment_id):
        raise ValueError("experiment_id must be a safe 1-80 character identifier")


def build_variant_config(name: str, base: RetrievalConfig) -> RetrievalConfig:
    if name not in ABLATION_VARIANT_NAMES:
        raise ValueError(f"unknown ablation variant: {name}")
    reranker = (
        base.reranker_mode
        if base.reranker_mode != RerankerMode.NONE
        else RerankerMode.LEXICAL
    )
    common_full = {
        "recall_mode": RecallMode.HYBRID,
        "fusion_mode": FusionMode.RRF,
        "reranker_mode": reranker,
        "selector_mode": SelectorMode.SOURCE_AWARE_MMR,
    }
    overrides: dict[str, Any]
    if name == "bm25_only":
        overrides = {
            "recall_mode": RecallMode.BM25,
            "fusion_mode": FusionMode.NONE,
            "reranker_mode": RerankerMode.NONE,
            "selector_mode": SelectorMode.TOP_K,
            "agent_mode": RetrievalAgentMode.DISABLED,
            "max_repairs": 0,
        }
    elif name == "vector_only":
        overrides = {
            "recall_mode": RecallMode.VECTOR,
            "fusion_mode": FusionMode.NONE,
            "reranker_mode": RerankerMode.NONE,
            "selector_mode": SelectorMode.TOP_K,
            "agent_mode": RetrievalAgentMode.DISABLED,
            "max_repairs": 0,
        }
    elif name == "weighted_hybrid":
        overrides = {
            "recall_mode": RecallMode.HYBRID,
            "fusion_mode": FusionMode.WEIGHTED,
            "reranker_mode": RerankerMode.NONE,
            "selector_mode": SelectorMode.TOP_K,
            "agent_mode": RetrievalAgentMode.DISABLED,
            "max_repairs": 0,
        }
    elif name == "rrf_reranker_no_mmr":
        overrides = {
            "recall_mode": RecallMode.HYBRID,
            "fusion_mode": FusionMode.RRF,
            "reranker_mode": reranker,
            "selector_mode": SelectorMode.TOP_K,
            "agent_mode": RetrievalAgentMode.DISABLED,
            "max_repairs": 0,
        }
    else:  # full_retrieval
        overrides = common_full
    return base.model_copy(update=overrides)


def compute_retrieval_metrics(
    bundle: RetrievalBundle,
    *,
    relevant_chunk_ids: set[str] | None = None,
) -> dict[str, int | float | str | None]:
    hits = [*bundle.policy_hits, *bundle.evidence_hits]
    selected_ids = [hit.chunk.chunk_id for hit in hits]
    sources = {hit.chunk.source_id for hit in hits}
    tracks = {hit.chunk.track for hit in bundle.evidence_hits if hit.chunk.track is not None}
    cross_case = sum(
        hit.chunk.case_id != bundle.case_id for hit in bundle.evidence_hits
    )
    recall: float | None = None
    mrr: float | None = None
    if relevant_chunk_ids is not None:
        recall = (
            len(set(selected_ids) & relevant_chunk_ids) / len(relevant_chunk_ids)
            if relevant_chunk_ids
            else 1.0
        )
        first = next(
            (rank for rank, chunk_id in enumerate(selected_ids, start=1) if chunk_id in relevant_chunk_ids),
            None,
        )
        mrr = 1.0 / first if first is not None else 0.0
    return {
        "hit_count": len(hits),
        "policy_hit_count": len(bundle.policy_hits),
        "evidence_hit_count": len(bundle.evidence_hits),
        "unique_sources": len(sources),
        "unique_tracks": len(tracks),
        "cross_case_hits": cross_case,
        "rounds": len(bundle.rounds),
        "repair_rounds": max(0, len(bundle.rounds) - 1),
        "added_chunks": sum(
            len(item.added_policy_chunk_ids) + len(item.added_evidence_chunk_ids)
            for item in bundle.rounds
        ),
        "complete": int(bundle.complete),
        "stop_reason": bundle.stop_reason,
        "recall_at_k": recall,
        "mrr": mrr,
    }


def ablation_artifact_path(
    build_dir: Path,
    experiment_id: str,
    variant: str,
    case_id: int,
    cp_id: str,
) -> Path:
    _validate_experiment_id(experiment_id)
    if variant not in ABLATION_VARIANT_NAMES:
        raise ValueError(f"unknown ablation variant: {variant}")
    if not 1 <= case_id <= 100:
        raise ValueError("case_id must be between 1 and 100")
    if not re.fullmatch(r"CP(?:[1-9]|[1-3][0-9]|4[01])", cp_id):
        raise ValueError(f"invalid cp_id: {cp_id}")
    return build_dir / "ablation" / experiment_id / variant / f"{case_id:03d}" / f"{cp_id}.json"


def validate_relevance_labels(
    labels: dict[str, list[str]],
    chunks_by_id: dict[str, EvidenceChunk],
) -> None:
    for task_key, chunk_ids in labels.items():
        match = re.fullmatch(r"(\d{3}):(CP(?:[1-9]|[1-3][0-9]|4[01]))", task_key)
        if match is None:
            raise ValueError(f"invalid label task key: {task_key}")
        case_id = int(match.group(1))
        for chunk_id in chunk_ids:
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                raise ValueError(f"unknown chunk in relevance labels: {chunk_id}")
            if chunk.case_id is not None and chunk.case_id != case_id:
                raise ValueError(
                    f"cross-case relevance label for {task_key}: {chunk_id} belongs to {chunk.case_id}"
                )


def _load_checkpoints(path: Path) -> dict[str, CheckpointDefinition]:
    return {
        item.cp_id: item
        for item in (CheckpointDefinition.model_validate(raw) for raw in read_json(path))
    }


def run_ablation_experiment(
    config: PipelineConfig,
    *,
    experiment_id: str,
    variants: Iterable[str],
    case_ids: Iterable[int],
    cp_ids: Iterable[str],
    relevance_labels_path: Path | None = None,
) -> dict[str, Any]:
    variant_names = list(dict.fromkeys(variants))
    case_ids = list(dict.fromkeys(case_ids))
    cp_ids = list(dict.fromkeys(cp_ids))
    _validate_experiment_id(experiment_id)
    for name in variant_names:
        build_variant_config(name, config.retrieval)
    embedding_provider = None
    endpoint = config.models.embedding
    if endpoint is not None and os.environ.get(endpoint.api_key_env):
        embedding_provider = OpenAICompatibleEmbeddingProvider(endpoint)
    policy_index = HybridIndex.load(
        config.paths.build_dir / "indexes" / "policy.json",
        embedding_provider=embedding_provider,
    )
    case_index = HybridIndex.load(
        config.paths.build_dir / "indexes" / "cases.json",
        embedding_provider=embedding_provider,
    )
    checkpoints = _load_checkpoints(config.paths.build_dir / "parsed" / "checkpoints.json")
    labels: dict[str, list[str]] = {}
    if relevance_labels_path is not None:
        labels = json.loads(relevance_labels_path.read_text(encoding="utf-8"))
        chunks = {chunk.chunk_id: chunk for chunk in [*policy_index.chunks, *case_index.chunks]}
        validate_relevance_labels(labels, chunks)
    completed = 0
    failed = 0
    for variant in variant_names:
        retrieval = build_variant_config(variant, config.retrieval)
        effective = config.model_copy(update={"retrieval": retrieval})
        try:
            reranker = _build_reranker(effective)
            agent = _build_retrieval_agent(effective)
        except (ValueError, RuntimeError) as exc:
            reranker = agent = None
            component_error = str(exc)
        else:
            component_error = None
        for case_id in case_ids:
            for cp_id in cp_ids:
                output = ablation_artifact_path(
                    config.paths.build_dir, experiment_id, variant, case_id, cp_id
                )
                try:
                    if component_error:
                        raise RuntimeError(component_error)
                    bundle = retrieve_for_checkpoint(
                        checkpoint=checkpoints[cp_id],
                        case_id=case_id,
                        policy_index=policy_index,
                        case_index=case_index,
                        agent=agent,
                        max_repairs=retrieval.max_repairs,
                        retrieval_config=retrieval,
                        reranker=reranker,
                    )
                    task_key = f"{case_id:03d}:{cp_id}"
                    metrics = compute_retrieval_metrics(
                        bundle,
                        relevant_chunk_ids=set(labels[task_key]) if task_key in labels else None,
                    )
                    payload = {
                        "status": "COMPLETED",
                        "experiment_id": experiment_id,
                        "variant": variant,
                        "retrieval_config": retrieval.model_dump(mode="json"),
                        "bundle": bundle.model_dump(mode="json"),
                        "metrics": metrics,
                    }
                    completed += 1
                except Exception as exc:  # each task is an independently auditable experiment unit
                    payload = {
                        "status": "FAILED",
                        "experiment_id": experiment_id,
                        "variant": variant,
                        "case_id": case_id,
                        "cp_id": cp_id,
                        "retrieval_config": retrieval.model_dump(mode="json"),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    failed += 1
                atomic_write_json(output, payload)
    summary = write_ablation_report(config.paths.build_dir, experiment_id)
    summary.update({"completed": completed, "failed": failed})
    atomic_write_json(
        config.paths.build_dir / "ablation" / experiment_id / "summary.json", summary
    )
    return summary


def run_retrieval_judge_experiment(
    config: PipelineConfig,
    *,
    run_id: str,
    variant_names: Iterable[str],
    gold_path: Path,
    max_workers: int = 1,
) -> PipelineRunSummary:
    """Run one retrieval variant through the production judge on confirmed Gold tasks."""
    variants = list(dict.fromkeys(variant_names))
    if len(variants) != 1:
        raise ValueError("retrieval judge requires exactly one isolated variant")
    variant = variants[0]
    retrieval = build_variant_config(variant, config.retrieval)
    layout = MethodRunLayout(config.paths.build_dir, run_id)
    effective_paths = config.paths.model_copy(update={"build_dir": layout.root})
    effective = config.model_copy(
        update={"paths": effective_paths, "retrieval": retrieval}
    )
    embedding_provider = None
    endpoint = config.models.embedding
    if endpoint is not None and os.environ.get(endpoint.api_key_env):
        embedding_provider = OpenAICompatibleEmbeddingProvider(endpoint)
    policy_index = HybridIndex.load(
        config.paths.build_dir / "indexes" / "policy.json",
        embedding_provider=embedding_provider,
    )
    case_index = HybridIndex.load(
        config.paths.build_dir / "indexes" / "cases.json",
        embedding_provider=embedding_provider,
    )
    checkpoints = _load_checkpoints(config.paths.build_dir / "parsed" / "checkpoints.json")
    gold = gold_tasks(gold_path)
    store = TaskStore(layout.root / "state" / "tasks.json")
    expected_tasks = [
        AuditTask(
            task_id=f"{run_id}:case-{item.case_id:03d}:{item.cp_id}",
            run_id=run_id,
            case_id=item.case_id,
            cp_id=item.cp_id,
        )
        for item in gold
    ]
    initialized = store.initialize(expected_tasks)
    if {(task.case_id, task.cp_id) for task in initialized} != {
        (item.case_id, item.cp_id) for item in gold
    }:
        raise ValueError("method task state does not match confirmed Gold tasks")
    atomic_write_json(
        layout.root / "method.json",
        {
            "run_id": run_id,
            "method": f"{variant}_judge",
            "gold_path": str(gold_path),
            "gold_count": len(gold),
            "retrieval_config": retrieval.model_dump(mode="json"),
        },
    )
    audit_client = _cached_model_client(effective.models.audit, layout.root, name="audit")
    verifier_client = (
        _cached_model_client(effective.models.verifier, layout.root, name="verifier")
        if effective.models.verifier is not None
        else None
    )
    arbitrator_client = (
        _cached_model_client(effective.models.arbitrator, layout.root, name="arbitrator")
        if effective.models.arbitrator is not None
        else None
    )
    reranker = _build_reranker(effective)
    retrieval_agent = _build_retrieval_agent(effective)

    def worker(task: AuditTask) -> str:
        if not os.environ.get(effective.models.audit.api_key_env):
            raise BlockedTaskError(
                "required model credential environment variable is unset: "
                f"{effective.models.audit.api_key_env}"
            )
        if verifier_client is None:
            raise BlockedTaskError("verifier model endpoint is not configured")
        if not os.environ.get(effective.models.verifier.api_key_env):
            raise BlockedTaskError(
                "required verifier credential environment variable is unset: "
                f"{effective.models.verifier.api_key_env}"
            )
        bundle = retrieve_for_checkpoint(
            checkpoint=checkpoints[task.cp_id],
            case_id=task.case_id,
            policy_index=policy_index,
            case_index=case_index,
            agent=retrieval_agent,
            max_repairs=(
                0
                if retrieval.agent_mode == RetrievalAgentMode.DISABLED
                else retrieval.max_repairs
            ),
            retrieval_config=retrieval,
            reranker=reranker,
        )
        arbitrator_for_task = (
            arbitrator_client
            if arbitrator_client is not None
            and os.environ.get(effective.models.arbitrator.api_key_env)
            else None
        )
        return str(
            process_retrieved_audit_task(
                task=task,
                checkpoint=checkpoints[task.cp_id],
                retrieval=bundle,
                audit_client=audit_client,
                verifier_client=verifier_client,
                arbitrator_client=arbitrator_for_task,
                output_build_dir=layout.root,
                arbitration_tier=effective.arbitration.tier,
            ).resolve()
        )

    return run_pending_tasks(store, worker, max_workers=max_workers)


def write_ablation_report(build_dir: Path, experiment_id: str) -> dict[str, Any]:
    _validate_experiment_id(experiment_id)
    root = build_dir / "ablation" / experiment_id
    if not root.exists():
        raise FileNotFoundError(root)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failures: dict[str, int] = defaultdict(int)
    for path in root.glob("*/*/CP*.json"):
        payload = read_json(path)
        variant = str(payload["variant"])
        if payload.get("status") == "COMPLETED":
            grouped[variant].append(payload["metrics"])
        else:
            failures[variant] += 1
    variants: dict[str, Any] = {}
    for name in sorted(set(grouped) | set(failures)):
        rows = grouped[name]
        numeric_keys = sorted(
            {
                key
                for row in rows
                for key, value in row.items()
                if isinstance(value, (int, float)) and value is not None
            }
        )
        variants[name] = {
            "completed": len(rows),
            "failed": failures[name],
            "mean_metrics": {
                key: fmean(float(row[key]) for row in rows if row.get(key) is not None)
                for key in numeric_keys
            },
            "stop_reasons": {
                reason: sum(row.get("stop_reason") == reason for row in rows)
                for reason in sorted({str(row.get("stop_reason")) for row in rows})
            },
        }
    report = {
        "experiment_id": experiment_id,
        "variant_count": len(variants),
        "variants": variants,
    }
    atomic_write_json(root / "summary.json", report)
    return report
