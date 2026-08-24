from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from freca.config import PipelineConfig
from freca.llm import JsonChatClient
from freca.methods import MethodRunLayout, gold_tasks
from freca.models import AuditDecision, AuditTask, CheckpointDefinition, EvidenceChunk, PipelineRunSummary
from freca.pipeline import BlockedTaskError, _cached_model_client, run_pending_tasks
from freca.state import TaskStore, atomic_write_json, read_json


DIRECT_JUDGE_METHODS = ("automatic_retrieval_judge", "checkpoint_full_judge")

_SYSTEM = """You are auditing one official checking point for one farm case.
Use only the supplied official policy and evidence chunks. Determine applicability before
compliance. A procedure proves capability, not continuous execution; for an execution
requirement, require a current-subject record. Facility facts may be reused only at the
facility level; product routes and batch facts require the same product subject. Official
records outrank company assertions. A missing proof of an applicable condition supports
verdict 0, not N/A. Use N/A only with explicit non-applicability evidence. Cite only exact
chunk_id values supplied. Return one AuditDecision JSON object matching the schema."""


@dataclass(frozen=True)
class DirectEnvelope:
    system: str
    text: str
    policy_ids: frozenset[str]
    evidence_ids: frozenset[str]


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _bm25_like_select(
    chunks: Iterable[EvidenceChunk], query: str, *, limit: int
) -> list[EvidenceChunk]:
    items = list(chunks)
    if len(items) <= limit:
        return items
    query_tokens = _tokens(query)
    return sorted(
        items,
        key=lambda chunk: (-len(query_tokens & _tokens(chunk.content)), chunk.chunk_id),
    )[:limit]


def build_direct_envelope(
    *,
    method: str,
    case_id: int,
    checkpoint: CheckpointDefinition,
    policy_chunks: Iterable[EvidenceChunk],
    case_chunks: Iterable[EvidenceChunk],
) -> DirectEnvelope:
    if method not in DIRECT_JUDGE_METHODS:
        raise ValueError(f"unknown direct method: {method}")
    policy = [chunk for chunk in policy_chunks if chunk.case_id is None]
    evidence = [chunk for chunk in case_chunks if chunk.case_id == case_id]
    if method == "automatic_retrieval_judge":
        policy = _bm25_like_select(policy, checkpoint.text, limit=12)
        evidence = _bm25_like_select(evidence, checkpoint.text, limit=12)
    if not policy or not evidence:
        raise ValueError("direct judge requires policy and current-case evidence")
    payload = {
        "case_id": case_id,
        "method": method,
        "official_checkpoint": checkpoint.model_dump(mode="json"),
        "policy_chunks": [chunk.model_dump(mode="json") for chunk in policy],
        "case_evidence_chunks": [chunk.model_dump(mode="json") for chunk in evidence],
        "allowed_citation_ids": [*(chunk.chunk_id for chunk in policy), *(chunk.chunk_id for chunk in evidence)],
    }
    return DirectEnvelope(
        system=_SYSTEM,
        text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        policy_ids=frozenset(chunk.chunk_id for chunk in policy),
        evidence_ids=frozenset(chunk.chunk_id for chunk in evidence),
    )


def decision_from_direct_payload(
    payload: dict,
    *,
    allowed_policy_ids: set[str] | frozenset[str],
    allowed_evidence_ids: set[str] | frozenset[str],
) -> AuditDecision:
    decision = AuditDecision.model_validate(payload)
    invalid_policy = set(decision.policy_citations) - set(allowed_policy_ids)
    invalid_evidence = (
        set(decision.supporting_evidence) | set(decision.contrary_evidence)
    ) - set(allowed_evidence_ids)
    if invalid_policy or invalid_evidence:
        raise ValueError("direct judge returned an unknown citation")
    return decision


def judge_direct_envelope(
    client: JsonChatClient,
    *,
    case_id: int,
    checkpoint: CheckpointDefinition,
    envelope: DirectEnvelope,
) -> AuditDecision:
    payload = client.complete_json(
        system=envelope.system,
        user=envelope.text,
        schema=AuditDecision.model_json_schema(),
    )
    decision = decision_from_direct_payload(
        payload,
        allowed_policy_ids=envelope.policy_ids,
        allowed_evidence_ids=envelope.evidence_ids,
    )
    if decision.case_id != case_id or decision.cp_id != checkpoint.cp_id:
        raise ValueError("direct judge returned the wrong case_id or cp_id")
    return decision


def _load_chunks(path: Path) -> list[EvidenceChunk]:
    return [EvidenceChunk.model_validate(item) for item in read_json(path)]


def run_direct_judge_experiment(
    config: PipelineConfig,
    *,
    run_id: str,
    method: str,
    gold_path: Path,
    max_workers: int = 1,
) -> PipelineRunSummary:
    if method not in DIRECT_JUDGE_METHODS:
        raise ValueError(f"unknown direct method: {method}")
    layout = MethodRunLayout(config.paths.build_dir, run_id)
    gold = gold_tasks(gold_path)
    checkpoints = {
        item.cp_id: item
        for item in (
            CheckpointDefinition.model_validate(raw)
            for raw in read_json(config.paths.build_dir / "parsed" / "checkpoints.json")
        )
    }
    policy_chunks = _load_chunks(config.paths.build_dir / "parsed" / "policy.json")
    case_chunks = {
        case_id: [
            chunk
            for path in sorted((config.paths.build_dir / "parsed" / "cases" / f"{case_id:03d}").glob("*.json"))
            if not path.name.endswith(".error.json")
            for chunk in _load_chunks(path)
        ]
        for case_id in {item.case_id for item in gold}
    }
    store = TaskStore(layout.root / "state" / "tasks.json")
    expected = [
        AuditTask(
            task_id=f"{run_id}:case-{item.case_id:03d}:{item.cp_id}",
            run_id=run_id,
            case_id=item.case_id,
            cp_id=item.cp_id,
        )
        for item in gold
    ]
    initialized = store.initialize(expected)
    if {(task.case_id, task.cp_id) for task in initialized} != {
        (item.case_id, item.cp_id) for item in gold
    }:
        raise ValueError("method task state does not match confirmed Gold tasks")
    atomic_write_json(
        layout.root / "method.json",
        {"run_id": run_id, "method": method, "gold_path": str(gold_path), "gold_count": len(gold)},
    )
    client = _cached_model_client(config.models.audit, layout.root, name="audit")

    def worker(task: AuditTask) -> str:
        if not os.environ.get(config.models.audit.api_key_env):
            raise BlockedTaskError(
                "required model credential environment variable is unset: "
                f"{config.models.audit.api_key_env}"
            )
        envelope = build_direct_envelope(
            method=method,
            case_id=task.case_id,
            checkpoint=checkpoints[task.cp_id],
            policy_chunks=policy_chunks,
            case_chunks=case_chunks[task.case_id],
        )
        atomic_write_json(
            layout.root / "requests" / f"{task.case_id:03d}" / f"{task.cp_id}.json",
            {"system": envelope.system, "user": envelope.text},
        )
        decision = judge_direct_envelope(
            client,
            case_id=task.case_id,
            checkpoint=checkpoints[task.cp_id],
            envelope=envelope,
        )
        output = layout.final_path(task.case_id, task.cp_id)
        atomic_write_json(output, decision.model_dump(mode="json"))
        return str(output.resolve())

    return run_pending_tasks(store, worker, max_workers=max_workers)
