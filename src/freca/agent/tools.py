"""Agent 工具函数(预注入 prompt 模式).

本项目 LLM 端点 (:class:`freca.llm.OpenAICompatibleJsonClient`) 不支持
function calling;这些工具由 Planner / Critic 在构造 prompt 时**预先调用**,
把结果以 JSON 形式塞进 user message。

供 LLMPlanner / LLMCritic 使用;Heuristic 实现不调。
"""
from __future__ import annotations

from freca.index import HybridIndex
from freca.models import EvidenceChunk


def list_case_chunks(
    case_index: HybridIndex,
    *,
    case_id: int,
    tracks: list[int] | None = None,
) -> list[str]:
    """返回 ``case_id`` 的 chunk_id 列表(可选 track 过滤).

    Args:
        case_index: 必须为 ``scope="case"`` 索引.
        case_id:    1-100.
        tracks:     可选 track 过滤 (1-9).

    Returns:
        chunk_id 列表(稳定顺序).
    """
    if case_index.scope != "case":
        raise ValueError("list_case_chunks requires a case-scoped HybridIndex")
    chunks = [c for c in case_index.chunks if c.case_id == case_id]
    if tracks:
        allowed = set(tracks)
        chunks = [c for c in chunks if c.track in allowed]
    return sorted(c.chunk_id for c in chunks)


def get_chunk_content(
    chunk_id: str,
    *,
    case_index: HybridIndex | None,
    policy_index: HybridIndex | None,
) -> dict | None:
    """按 ``chunk_id`` 取出完整 chunk(优先 case 索引,fallback policy).

    Returns:
        序列化的 ``EvidenceChunk`` dict,或 None (找不到).
    """
    if case_index is not None and case_index.scope == "case":
        for chunk in case_index.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk.model_dump(mode="json")
    if policy_index is not None and policy_index.scope == "policy":
        for chunk in policy_index.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk.model_dump(mode="json")
    return None


def list_tracks_for_case(case_index: HybridIndex, *, case_id: int) -> list[int]:
    """返回该 case 实际存在的 track 编号(去重升序).

    Args:
        case_index: 必须为 ``scope="case"`` 索引.

    Returns:
        Track 列表,如 ``[1, 3, 6]``.空 list 表示该 case 没有 case 侧 chunk.
    """
    if case_index.scope != "case":
        raise ValueError("list_tracks_for_case requires a case-scoped HybridIndex")
    tracks = {
        c.track for c in case_index.chunks if c.case_id == case_id and c.track is not None
    }
    return sorted(tracks)


def summarize_index_coverage(
    *,
    case_index: HybridIndex,
    case_id: int,
    policy_index: HybridIndex | None = None,
) -> dict:
    """返回 case 索引 + (可选) policy 索引的覆盖概况,供 Planner 决策."""
    case_chunks = [c for c in case_index.chunks if c.case_id == case_id]
    case_tracks = sorted({c.track for c in case_chunks if c.track is not None})
    policy_chunks = list(policy_index.chunks) if policy_index is not None else []
    return {
        "case_id": case_id,
        "case_chunk_count": len(case_chunks),
        "case_tracks": case_tracks,
        "policy_chunk_count": len(policy_chunks),
    }