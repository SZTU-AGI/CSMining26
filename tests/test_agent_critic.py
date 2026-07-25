from __future__ import annotations

from freca.agent.critic import HeuristicCritic, LLMCritic
from freca.llm import ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    RetrievalHit,
    SourceLocation,
    SourceType,
)


def _checkpoint(element_id: int = 1, cp_id: str = "CP1") -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=element_id,
        element_title=f"Element-{element_id}",
        section_title=f"{element_id}.1 Test",
        text="Test checkpoint",
        source_file="cp.xlsx",
        cell="A3",
    )


def _chunk(
    chunk_id: str,
    content: str,
    *,
    source_id: str = "src-A",
    track: int | None = 1,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=1,
        re_number="RE-X",
        track=track,
        source_id=source_id,
        source_file="t1.docx",
        source_type=SourceType.DOCX,
        location=SourceLocation(paragraph_index=0),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="d" * 64,
    )


def _hit(chunk_id: str, content: str, *, score: float, source_id: str = "src-A") -> RetrievalHit:
    return RetrievalHit(
        chunk=_chunk(chunk_id, content, source_id=source_id),
        score=score,
        rank=1,
    )


def test_heuristic_critic_flags_contrary_tokens() -> None:
    critic = HeuristicCritic()
    hits = [
        _hit("e1", "All requirements met.", score=1.0),
        _hit("e2", "Missing pest control record.", score=0.9),
    ]
    decision = critic.critique(checkpoint=_checkpoint(), hits=hits)
    assert "e2" in decision.flag_chunk_ids
    assert "e1" not in decision.flag_chunk_ids


def test_heuristic_critic_flags_answer_leak_tokens() -> None:
    critic = HeuristicCritic()
    hits = [
        _hit("e1", "Audit scenario: pass", score=1.0),
    ]
    decision = critic.critique(checkpoint=_checkpoint(), hits=hits)
    assert "e1" in decision.flag_chunk_ids


def test_heuristic_critic_dedupes_repeating_sources() -> None:
    critic = HeuristicCritic()
    hits = [
        _hit("a", "alpha", score=1.0, source_id="src-A"),
        _hit("b", "beta", score=0.9, source_id="src-A"),
        _hit("c", "gamma", score=0.8, source_id="src-A"),  # 第三个副本 → 降权
        _hit("d", "delta", score=0.7, source_id="src-B"),
    ]
    decision = critic.critique(checkpoint=_checkpoint(), hits=hits)
    assert "c" in decision.weighted_down_chunk_ids
    assert "a" not in decision.weighted_down_chunk_ids
    assert "b" not in decision.weighted_down_chunk_ids


def test_heuristic_critic_flags_missing_scope_for_element1() -> None:
    critic = HeuristicCritic()
    hits = [_hit("e1", "Some random content.", score=1.0)]
    decision = critic.critique(checkpoint=_checkpoint(element_id=1), hits=hits)
    assert "registered_scope_anchor" in decision.missing_dimensions


def test_heuristic_critic_no_scope_missing_for_non_element1() -> None:
    critic = HeuristicCritic()
    hits = [_hit("e1", "Some random content.", score=1.0)]
    decision = critic.critique(checkpoint=_checkpoint(element_id=2), hits=hits)
    assert "registered_scope_anchor" not in decision.missing_dimensions


def test_llm_critic_caps_drop_at_30_percent() -> None:
    payload = {
        "drop_chunk_ids": ["a", "b", "c", "d"],   # 4/4 = 100% 想 drop
        "flag_chunk_ids": [],
        "missing_dimensions": [],
        "suggested_query_focus": None,
        "rationale": "all should be dropped",
    }
    critic = LLMCritic(ReplayJsonClient([payload]))
    hits = [_hit("a", "x", score=1), _hit("b", "y", score=0.9), _hit("c", "z", score=0.8), _hit("d", "w", score=0.7)]
    decision = critic.critique(checkpoint=_checkpoint(), hits=hits)
    # max_drop = round(4 * 0.30) = 1
    assert len(decision.drop_chunk_ids) == 1


def test_llm_critic_handles_empty_hits() -> None:
    critic = LLMCritic(ReplayJsonClient([]))
    decision = critic.critique(checkpoint=_checkpoint(), hits=[])
    assert decision.drop_chunk_ids == []