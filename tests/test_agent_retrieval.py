from __future__ import annotations

from dataclasses import dataclass

from freca.index import HybridIndex
from freca.models import ContentKind, RetrievalAction, RetrievalAgentDecision
from freca.retrieval import retrieve_for_checkpoint

from test_retrieval import SequenceIndex, _checkpoint, _chunk


@dataclass
class SequenceAgent:
    decisions: list[RetrievalAgentDecision]
    calls: int = 0

    def decide(self, **kwargs) -> RetrievalAgentDecision:
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return decision


def _retrieve_decision(
    policy_query: str = "more policy",
    evidence_query: str = "more evidence",
    *,
    tracks: list[int] | None = None,
) -> RetrievalAgentDecision:
    return RetrievalAgentDecision(
        action=RetrievalAction.RETRIEVE,
        complete=False,
        gaps=["missing context"],
        policy_query=policy_query,
        evidence_query=evidence_query,
        target_tracks=tracks or [],
        target_content_kinds=[],
        reason="one bounded repair is needed",
    )


def _stop_decision() -> RetrievalAgentDecision:
    return RetrievalAgentDecision(
        action=RetrievalAction.STOP,
        complete=True,
        gaps=[],
        reason="context is sufficient",
    )


def test_agent_can_stop_only_after_policy_and_case_evidence_exist() -> None:
    policy = _chunk("p1", "the requirement applies", case_id=None, source_id="policy")
    evidence = _chunk("e1", "dated farm record 2025", case_id=1, source_id="track3")

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=SequenceIndex([[policy]]),
        case_index=SequenceIndex([[evidence]]),
        agent=SequenceAgent([_stop_decision()]),
    )

    assert result.complete is True
    assert result.stop_reason == "complete"
    assert result.rounds[0].agent_decision.action == RetrievalAction.STOP


def test_mechanical_gate_rejects_early_stop_without_required_context() -> None:
    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=SequenceIndex([[], []]),
        case_index=SequenceIndex([[], []]),
        agent=SequenceAgent([_stop_decision(), _stop_decision()]),
        max_repairs=1,
    )

    assert result.complete is False
    assert result.stop_reason == "no_new_chunks"
    assert "agent_stop_rejected_missing_context" in result.rounds[0].gate_flags
    assert len(result.rounds) == 2


def test_repeated_repair_query_is_stopped_deterministically() -> None:
    policy = _chunk("p1", "requirement", case_id=None, source_id="policy")
    evidence = _chunk("e1", "record", case_id=1, source_id="track3")
    agent = SequenceAgent([_retrieve_decision()])

    # Force the requested repair to repeat the exact initial pair.
    from freca.retrieval import build_initial_queries

    initial_policy, initial_evidence = build_initial_queries(_checkpoint())
    agent.decisions = [_retrieve_decision(initial_policy, initial_evidence)]
    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=SequenceIndex([[policy]]),
        case_index=SequenceIndex([[evidence]]),
        agent=agent,
    )

    assert result.complete is False
    assert result.stop_reason == "repeated_query"
    assert len(result.rounds) == 1


def test_agent_track_target_is_applied_only_to_next_case_retrieval_round() -> None:
    policy = _chunk("p1", "requirement", case_id=None, source_id="policy")
    track3 = _chunk("e3", "generic record", case_id=1, source_id="track3")
    track3.track = 3
    track6 = _chunk("e6", "inspection date 2025", case_id=1, source_id="track6")
    track6.track = 6
    case_index = HybridIndex([track3, track6], scope="case")
    agent = SequenceAgent([_retrieve_decision(tracks=[6]), _stop_decision()])

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=HybridIndex([policy], scope="policy"),
        case_index=case_index,
        agent=agent,
        max_repairs=1,
        evidence_limit=1,
    )

    assert result.rounds[0].agent_decision.target_tracks == [6]
    assert result.rounds[1].target_tracks == [6]
    assert result.rounds[1].added_evidence_chunk_ids == ["e6"]
    assert result.rounds[1].target_content_kinds == []


def test_agent_decision_retrieve_requires_nonempty_queries() -> None:
    try:
        RetrievalAgentDecision(
            action=RetrievalAction.RETRIEVE,
            complete=False,
            gaps=["gap"],
            reason="bad payload",
        )
    except ValueError as exc:
        assert "queries" in str(exc)
    else:
        raise AssertionError("invalid retrieval decision was accepted")


def test_planner_plan_is_attached_to_first_round() -> None:
    """HeuristicPlanner 默认应给 Element-3 → Track 2/3/4/6,首轮 round 携带 planner_plan。"""
    policy = _chunk("p1", "the requirement applies", case_id=None, source_id="policy")
    evidence = _chunk("e1", "pest control record 2025", case_id=1, source_id="t3")
    evidence.track = 3
    case_index = HybridIndex([evidence], scope="case")
    policy_index = HybridIndex([policy], scope="policy")

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=policy_index,
        case_index=case_index,
        agent=SequenceAgent([_stop_decision()]),
    )

    assert result.rounds
    plan = result.rounds[0].planner_plan
    assert plan is not None
    assert plan.target_tracks  # Element-3 必有非空 target


def test_critic_weighted_down_removes_low_score_duplicates() -> None:
    """同一 source 出现 ≥3 次时,HeuristicCritic 把低分副本加入 weighted_down_chunk_ids,
    retrieval 层应把这些 chunk 从 hits 移除。"""
    policy = _chunk("p1", "the requirement applies", case_id=None, source_id="policy")
    a = _chunk("a", "alpha content", case_id=1, source_id="repeating-src")
    a.track = 3
    b = _chunk("b", "beta content", case_id=1, source_id="repeating-src")
    b.track = 3
    c = _chunk("c", "gamma content", case_id=1, source_id="repeating-src")
    c.track = 3
    d = _chunk("d", "delta content", case_id=1, source_id="other-src")
    d.track = 3
    # 用 HybridIndex 直接搜,BM25 会按相关性返回多个同名 source 副本.
    case_index = HybridIndex([a, b, c, d], scope="case")
    policy_index = HybridIndex([policy], scope="policy")

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=policy_index,
        case_index=case_index,
        agent=SequenceAgent([_stop_decision()]),
        evidence_limit=4,
    )
    all_chunk_ids = {hit.chunk.chunk_id for hit in result.evidence_hits}
    # c 是低分副本,weighted_down 后应不在 hits 中
    assert "c" not in all_chunk_ids or "c" in result.rounds[0].dropped_chunk_ids


def test_critic_attaches_decision_to_round() -> None:
    """每轮 round 都应带 critic_decision 字段."""
    policy = _chunk("p1", "the requirement applies", case_id=None, source_id="policy")
    evidence = _chunk("e1", "Missing pest record.", case_id=1, source_id="t3")
    case_index = HybridIndex([evidence], scope="case")
    policy_index = HybridIndex([policy], scope="policy")

    result = retrieve_for_checkpoint(
        checkpoint=_checkpoint(),
        case_id=1,
        policy_index=policy_index,
        case_index=case_index,
        agent=SequenceAgent([_stop_decision()]),
    )

    assert result.rounds[0].critic_decision is not None
    assert "e1" in result.rounds[0].critic_decision.flag_chunk_ids  # "missing" 是反证 token
