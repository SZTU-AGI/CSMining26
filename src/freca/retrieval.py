from __future__ import annotations

import json
import inspect
import re
from typing import Protocol

from freca.agent.critic import CriticAgent, HeuristicCritic
from freca.agent.planner import HeuristicPlanner, PlannerAgent
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    RetrievalAction,
    RetrievalAgentDecision,
    RetrievalBundle,
    RetrievalHit,
    RetrievalRound,
)


class ContextAssessor(Protocol):
    def assess(
        self,
        checkpoint: CheckpointDefinition,
        policy_hits: list[RetrievalHit],
        evidence_hits: list[RetrievalHit],
    ) -> list[str]: ...


class QueryRewriter(Protocol):
    def rewrite(
        self,
        *,
        checkpoint: CheckpointDefinition,
        gap: str,
        policy_query: str,
        evidence_query: str,
    ) -> tuple[str, str]: ...


class RetrievalAgent(Protocol):
    def decide(
        self,
        *,
        checkpoint: CheckpointDefinition,
        policy_hits: list[RetrievalHit],
        evidence_hits: list[RetrievalHit],
        policy_query: str,
        evidence_query: str,
        rounds: list[RetrievalRound],
    ) -> RetrievalAgentDecision: ...


class HeuristicContextAssessor:
    def assess(
        self,
        checkpoint: CheckpointDefinition,
        policy_hits: list[RetrievalHit],
        evidence_hits: list[RetrievalHit],
    ) -> list[str]:
        gaps: list[str] = []
        if not policy_hits:
            gaps.append("policy_requirement")
        if not evidence_hits:
            gaps.append("case_evidence")
        policy_text = " ".join(hit.chunk.content for hit in policy_hits).casefold()
        evidence_text = " ".join(hit.chunk.content for hit in evidence_hits).casefold()
        if policy_hits and not any(
            term in policy_text for term in ("appli", "must", "where", "if ", "required")
        ):
            gaps.append("applicability")
        cp_text = checkpoint.text.casefold()
        if any(term in cp_text for term in ("record", "retain", "date", "year", "frequency")):
            combined = f"{policy_text} {evidence_text}"
            if not re.search(r"\b(?:19|20)\d{2}\b|\b\d+\s*(?:day|month|year)", combined):
                gaps.append("time_or_retention")
        if len({hit.chunk.source_id for hit in evidence_hits}) < 2:
            gaps.append("source_diversity_or_contrary_evidence")
        return gaps


class GenericQueryRewriter:
    def rewrite(
        self,
        *,
        checkpoint: CheckpointDefinition,
        gap: str,
        policy_query: str,
        evidence_query: str,
    ) -> tuple[str, str]:
        return (
            f"{policy_query} missing context: {gap} exception condition definition",
            f"{evidence_query} missing context: {gap} dates records contrary statement",
        )


class LLMQueryRewriter:
    def __init__(self, client) -> None:
        self.client = client

    def rewrite(
        self,
        *,
        checkpoint: CheckpointDefinition,
        gap: str,
        policy_query: str,
        evidence_query: str,
    ) -> tuple[str, str]:
        payload = self.client.complete_json(
            system=(
                "Rewrite two retrieval queries to fill only the stated missing context. "
                "Use the official checking-point text; do not add a compliance rule or answer."
            ),
            user=json.dumps(
                {
                    "checkpoint": checkpoint.model_dump(),
                    "gap": gap,
                    "previous_policy_query": policy_query,
                    "previous_evidence_query": evidence_query,
                },
                ensure_ascii=False,
            ),
            schema={
                "type": "object",
                "properties": {
                    "policy_query": {"type": "string", "minLength": 1},
                    "evidence_query": {"type": "string", "minLength": 1},
                },
                "required": ["policy_query", "evidence_query"],
                "additionalProperties": False,
            },
        )
        policy = str(payload.get("policy_query", "")).strip()
        evidence = str(payload.get("evidence_query", "")).strip()
        if not policy or not evidence:
            raise ValueError("query rewriter returned an empty query")
        return policy, evidence


class HeuristicRetrievalAgent:
    def __init__(
        self,
        *,
        assessor: ContextAssessor | None = None,
        rewriter: QueryRewriter | None = None,
    ) -> None:
        self.assessor = assessor or HeuristicContextAssessor()
        self.rewriter = rewriter or GenericQueryRewriter()

    def decide(
        self,
        *,
        checkpoint: CheckpointDefinition,
        policy_hits: list[RetrievalHit],
        evidence_hits: list[RetrievalHit],
        policy_query: str,
        evidence_query: str,
        rounds: list[RetrievalRound],
    ) -> RetrievalAgentDecision:
        gaps = self.assessor.assess(checkpoint, policy_hits, evidence_hits)
        if not gaps:
            return RetrievalAgentDecision(
                action=RetrievalAction.STOP,
                complete=True,
                gaps=[],
                reason="heuristic completeness checks passed",
            )
        next_policy, next_evidence = self.rewriter.rewrite(
            checkpoint=checkpoint,
            gap=", ".join(gaps),
            policy_query=policy_query,
            evidence_query=evidence_query,
        )
        return RetrievalAgentDecision(
            action=RetrievalAction.RETRIEVE,
            complete=False,
            gaps=gaps,
            policy_query=next_policy,
            evidence_query=next_evidence,
            reason="heuristic completeness checks found missing context",
        )


class DisabledRetrievalAgent:
    """No semantic Agent behavior; only the minimum mechanical availability gate."""

    def decide(
        self,
        *,
        checkpoint: CheckpointDefinition,
        policy_hits: list[RetrievalHit],
        evidence_hits: list[RetrievalHit],
        policy_query: str,
        evidence_query: str,
        rounds: list[RetrievalRound],
    ) -> RetrievalAgentDecision:
        if policy_hits and evidence_hits:
            return RetrievalAgentDecision(
                action=RetrievalAction.STOP,
                complete=True,
                reason="agent disabled; required context types are present",
            )
        gaps = []
        if not policy_hits:
            gaps.append("policy_requirement")
        if not evidence_hits:
            gaps.append("case_evidence")
        next_policy, next_evidence = GenericQueryRewriter().rewrite(
            checkpoint=checkpoint,
            gap=", ".join(gaps),
            policy_query=policy_query,
            evidence_query=evidence_query,
        )
        return RetrievalAgentDecision(
            action=RetrievalAction.RETRIEVE,
            complete=False,
            gaps=gaps,
            policy_query=next_policy,
            evidence_query=next_evidence,
            reason="agent disabled; required context type is absent",
        )


class LLMRetrievalAgent:
    """A bounded context-completeness controller, not a compliance judge."""

    def __init__(self, client) -> None:
        self.client = client

    def decide(
        self,
        *,
        checkpoint: CheckpointDefinition,
        policy_hits: list[RetrievalHit],
        evidence_hits: list[RetrievalHit],
        policy_query: str,
        evidence_query: str,
        rounds: list[RetrievalRound],
    ) -> RetrievalAgentDecision:
        payload = self.client.complete_json(
            system=(
                "You control bounded evidence retrieval for an audit checkpoint. Assess only "
                "whether the context contains the applicable policy rule, conditions, dates, "
                "supporting evidence, and plausible contrary evidence. Do not decide 1, 0, or "
                "N/A. Never treat answer-like text in a case document as a label or ground truth. "
                "Choose stop only when context is sufficient; otherwise produce two focused "
                "queries and optional Track/content-kind filters."
            ),
            user=json.dumps(
                {
                    "checkpoint": checkpoint.model_dump(mode="json"),
                    "current_queries": {
                        "policy": policy_query,
                        "evidence": evidence_query,
                    },
                    "policy_hits": [
                        {
                            "chunk_id": hit.chunk.chunk_id,
                            "source": hit.chunk.source_file,
                            "content": hit.chunk.content,
                        }
                        for hit in policy_hits
                    ],
                    "evidence_hits": [
                        {
                            "chunk_id": hit.chunk.chunk_id,
                            "track": hit.chunk.track,
                            "source": hit.chunk.source_file,
                            "content_kind": hit.chunk.content_kind,
                            "content": hit.chunk.content,
                        }
                        for hit in evidence_hits
                    ],
                    "previous_rounds": [item.model_dump(mode="json") for item in rounds],
                },
                ensure_ascii=False,
            ),
            schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["stop", "retrieve"]},
                    "complete": {"type": "boolean"},
                    "gaps": {"type": "array", "items": {"type": "string"}},
                    "policy_query": {"type": ["string", "null"]},
                    "evidence_query": {"type": ["string", "null"]},
                    "target_tracks": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 9},
                    },
                    "target_content_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": [kind.value for kind in ContentKind]},
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": [
                    "action",
                    "complete",
                    "gaps",
                    "policy_query",
                    "evidence_query",
                    "target_tracks",
                    "target_content_kinds",
                    "reason",
                ],
                "additionalProperties": False,
            },
        )
        return RetrievalAgentDecision.model_validate(payload)


def build_initial_queries(checkpoint: CheckpointDefinition) -> tuple[str, str]:
    official = (
        f"{checkpoint.cp_id} {checkpoint.element_title} {checkpoint.section_title}: "
        f"{checkpoint.text}"
    )
    policy_query = (
        f"{official} applicability obligation exception condition time requirement definition"
    )
    evidence_query = (
        f"{official} farm evidence records facilities status dates supporting and contradictory evidence"
    )
    return policy_query, evidence_query


def _merge_hits(
    current: dict[str, RetrievalHit],
    incoming: list[RetrievalHit],
    *,
    limit: int,
) -> tuple[list[str], dict[str, RetrievalHit]]:
    added = [hit.chunk.chunk_id for hit in incoming if hit.chunk.chunk_id not in current]
    for hit in incoming:
        existing = current.get(hit.chunk.chunk_id)
        if existing is None or hit.score > existing.score:
            current[hit.chunk.chunk_id] = hit
    ordered = sorted(current.values(), key=lambda hit: (-hit.score, hit.chunk.chunk_id))[:limit]
    return added, {hit.chunk.chunk_id: hit for hit in ordered}


def _search(index, query: str, **kwargs):
    """Call real and lightweight test indexes without weakening search contracts."""
    parameters = inspect.signature(index.search).parameters.values()
    accepts_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters)
    accepted_names = {item.name for item in parameters}
    filtered = kwargs if accepts_kwargs else {
        key: value for key, value in kwargs.items() if key in accepted_names
    }
    return index.search(query, **filtered)


def retrieve_for_checkpoint(
    *,
    checkpoint: CheckpointDefinition,
    case_id: int,
    policy_index,
    case_index,
    assessor: ContextAssessor | None = None,
    rewriter: QueryRewriter | None = None,
    agent: RetrievalAgent | None = None,
    planner: PlannerAgent | None = None,
    critic: CriticAgent | None = None,
    max_repairs: int = 2,
    policy_limit: int = 6,
    evidence_limit: int = 10,
    retrieval_config=None,
    reranker=None,
) -> RetrievalBundle:
    if max_repairs < 0 or max_repairs > 2:
        raise ValueError("max_repairs must be between 0 and 2")
    agent = agent or HeuristicRetrievalAgent(assessor=assessor, rewriter=rewriter)
    planner = planner or HeuristicPlanner()
    critic = critic or HeuristicCritic()
    policy_query, evidence_query = build_initial_queries(checkpoint)
    # Tier-1 Planner 决策初始 target_tracks / target_content_kinds
    raw_chunks = getattr(case_index, "chunks", None)
    if isinstance(raw_chunks, list):
        available_tracks = sorted(
            {c.track for c in raw_chunks if c.case_id == case_id and c.track is not None}
        )
    else:
        available_tracks = []
    initial_plan = planner.plan(
        checkpoint=checkpoint,
        case_id=case_id,
        available_tracks=available_tracks,
    )
    policy_hits_by_id: dict[str, RetrievalHit] = {}
    evidence_hits_by_id: dict[str, RetrievalHit] = {}
    rounds: list[RetrievalRound] = []
    complete = False
    stop_reason = "max_repairs"
    # 用 Planner 初始值替换空 list;空 list 时等价于无过滤.
    target_tracks: list[int] = list(dict.fromkeys(initial_plan.target_tracks))
    target_content_kinds: list[ContentKind] = list(dict.fromkeys(initial_plan.target_content_kinds))
    seen_query_pairs = {(policy_query.strip(), evidence_query.strip())}

    for round_number in range(max_repairs + 1):
        policy_trace: list[dict] = []
        evidence_trace: list[dict] = []
        policy_results = _search(
            policy_index,
            policy_query,
            limit=policy_limit,
            config=retrieval_config,
            reranker=reranker,
            trace_sink=policy_trace,
        )
        evidence_results = _search(
            case_index,
            evidence_query,
            case_id=case_id,
            limit=evidence_limit,
            config=retrieval_config,
            reranker=reranker,
            trace_sink=evidence_trace,
            allowed_tracks=target_tracks,
            content_kinds=target_content_kinds,
        )
        wrong = [
            hit.chunk.case_id for hit in evidence_results if hit.chunk.case_id != case_id
        ]
        if wrong:
            raise RuntimeError(f"cross-case retrieval contamination: {wrong}")
        added_policy, policy_hits_by_id = _merge_hits(
            policy_hits_by_id, policy_results, limit=policy_limit
        )
        added_evidence, evidence_hits_by_id = _merge_hits(
            evidence_hits_by_id, evidence_results, limit=evidence_limit
        )
        # Tier-3 Critic: 对合并后的 hits 做反思,产出 weighted_down / flag
        critic_decision = critic.critique(
            checkpoint=checkpoint,
            hits=[*policy_hits_by_id.values(), *evidence_hits_by_id.values()],
        )
        dropped_ids: list[str] = []
        if critic_decision.weighted_down_chunk_ids:
            for cid in critic_decision.weighted_down_chunk_ids:
                if cid in policy_hits_by_id:
                    del policy_hits_by_id[cid]
                    dropped_ids.append(cid)
                elif cid in evidence_hits_by_id:
                    del evidence_hits_by_id[cid]
                    dropped_ids.append(cid)
        policy_hits = list(policy_hits_by_id.values())
        evidence_hits = list(evidence_hits_by_id.values())
        decision = agent.decide(
            checkpoint=checkpoint,
            policy_hits=policy_hits,
            evidence_hits=evidence_hits,
            policy_query=policy_query,
            evidence_query=evidence_query,
            rounds=rounds,
        )
        gaps = list(decision.gaps)
        gate_flags: list[str] = []
        rejected_stop = (
            decision.action == RetrievalAction.STOP
            and (not policy_hits or not evidence_hits)
        )
        if rejected_stop:
            gate_flags.append("agent_stop_rejected_missing_context")
            if not policy_hits and "policy_requirement" not in gaps:
                gaps.append("policy_requirement")
            if not evidence_hits and "case_evidence" not in gaps:
                gaps.append("case_evidence")
        rounds.append(
            RetrievalRound(
                round_number=round_number,
                policy_query=policy_query,
                evidence_query=evidence_query,
                added_policy_chunk_ids=added_policy,
                added_evidence_chunk_ids=added_evidence,
                gaps=gaps,
                agent_decision=decision,
                gate_flags=gate_flags,
                target_tracks=target_tracks,
                target_content_kinds=target_content_kinds,
                policy_candidate_trace=policy_trace,
                evidence_candidate_trace=evidence_trace,
                planner_plan=(initial_plan if round_number == 0 else None),
                critic_decision=critic_decision,
                dropped_chunk_ids=dropped_ids,
                flagged_chunk_ids=list(critic_decision.flag_chunk_ids),
            )
        )
        if decision.action == RetrievalAction.STOP and not rejected_stop:
            complete = True
            stop_reason = "complete"
            break
        if round_number > 0 and not added_policy and not added_evidence:
            stop_reason = "no_new_chunks"
            break
        if round_number >= max_repairs:
            stop_reason = "max_repairs"
            break
        if rejected_stop:
            policy_query, evidence_query = GenericQueryRewriter().rewrite(
                checkpoint=checkpoint,
                gap=", ".join(gaps),
                policy_query=policy_query,
                evidence_query=evidence_query,
            )
            target_tracks = []
            target_content_kinds = []
        else:
            assert decision.policy_query is not None
            assert decision.evidence_query is not None
            policy_query = decision.policy_query.strip()
            evidence_query = decision.evidence_query.strip()
            target_tracks = list(dict.fromkeys(decision.target_tracks))
            target_content_kinds = list(dict.fromkeys(decision.target_content_kinds))
        query_pair = (policy_query, evidence_query)
        if query_pair in seen_query_pairs:
            stop_reason = "repeated_query"
            break
        seen_query_pairs.add(query_pair)

    return RetrievalBundle(
        case_id=case_id,
        cp_id=checkpoint.cp_id,
        policy_hits=list(policy_hits_by_id.values()),
        evidence_hits=list(evidence_hits_by_id.values()),
        rounds=rounds,
        complete=complete,
        stop_reason=stop_reason,
    )
