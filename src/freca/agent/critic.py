"""Tier-3 Self-Critique Agent.

职责: 对已召回合集做反思,产出 ``CriticDecision``。
默认只 flag,不对 hits 做删除;``weighted_down_chunk_ids`` 是对重复 source 的低分副本**降权**
(由 retrieval 层在合并时跳过),语义上仍保留证据可见性。

* :class:`HeuristicCritic` - 四条规则: dedupe / flag-contrary / flag-answer-leak / boost-scope
* :class:`LLMCritic`       - 调 LLM 评估合集,schema 强约束 ``drop_chunk_ids`` ≤ 当前 hits 的 30%
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Protocol

from freca.llm import JsonChatClient
from freca.models import (
    CheckpointDefinition,
    CriticDecision,
    EvidenceChunk,
    RetrievalHit,
)


class CriticAgent(Protocol):
    def critique(
        self,
        *,
        checkpoint: CheckpointDefinition,
        hits: list[RetrievalHit],
    ) -> CriticDecision: ...


# 反证关键词 — 内容含这些但决策未标 contrary 时,记 flag.
_CONTRARY_TOKENS = (
    "non-compliant",
    "non compliant",
    "not compliant",
    "fail",
    "absent",
    "missing",
    "expired",
    "suspended",
    "revoked",
    "rejected",
    "deficiency",
)

# 近答案字段关键词 — Track 3 中"Full compliant"/"Audit scenario"等.
# 一旦在 case 证据里出现,需 flag 提示 LLM 不可作 label.
_ANSWER_LEAK_TOKENS = (
    "fully compliant",
    "audit scenario",
    "non-conformance",
    "audit outcome: pass",
    "audit outcome: fail",
    "verdict: compliant",
    "verdict: non-compliant",
    "compliant: yes",
    "compliant: no",
)

# Element-1 注册业务范围关键词 — 用于 boost-scope 规则.
_SCOPE_TOKENS = (
    "registered operations",
    "scope of registration",
    "registered commodity",
    "registered establishment",
    "registered activity",
)


def _chunk_text(hit: RetrievalHit) -> str:
    return hit.chunk.content or ""


def _count_sources(hits: list[RetrievalHit]) -> Counter[str]:
    return Counter(hit.chunk.source_id for hit in hits)


class HeuristicCritic:
    """零 LLM 成本规则驱动审视器.

    规则:

    1. dedupe        - 同一 ``source_id`` ≥3 次出现 → 低分副本降权
    2. flag-contrary - 含反证关键词但未标 contrary → 记 flag
    3. flag-answer-leak - 含近答案字段 → 记 flag (不可作 label)
    4. boost-scope   - Element-1 且含注册范围关键词 → 记 boost
    """

    DEDUPE_THRESHOLD = 3
    DEDUPE_KEEP_TOP = 2

    def critique(
        self,
        *,
        checkpoint: CheckpointDefinition,
        hits: list[RetrievalHit],
    ) -> CriticDecision:
        if not hits:
            return CriticDecision(
                rationale="HeuristicCritic: empty hits; nothing to flag.",
            )

        flagged: list[str] = []
        weighted_down: list[str] = []
        missing: list[str] = []

        # Rule 1: dedupe by source_id
        sources = _count_sources(hits)
        for source_id, count in sources.items():
            if count < self.DEDUPE_THRESHOLD:
                continue
            same_source_hits = sorted(
                (hit for hit in hits if hit.chunk.source_id == source_id),
                key=lambda hit: (-hit.score, hit.chunk.chunk_id),
            )
            for hit in same_source_hits[self.DEDUPE_KEEP_TOP :]:
                if hit.chunk.chunk_id not in weighted_down:
                    weighted_down.append(hit.chunk.chunk_id)

        # Rule 2 & 3: token-based flags (casefolded substring match)
        for hit in hits:
            text = _chunk_text(hit).casefold()
            if not text:
                continue
            if any(token in text for token in _CONTRARY_TOKENS):
                if hit.chunk.chunk_id not in flagged:
                    flagged.append(hit.chunk.chunk_id)
                continue
            if any(token in text for token in _ANSWER_LEAK_TOKENS):
                if hit.chunk.chunk_id not in flagged:
                    flagged.append(hit.chunk.chunk_id)

        # Rule 4: boost scope (Element-1)
        if checkpoint.element_id == 1:
            scope_present = any(
                token in _chunk_text(hit).casefold() for hit in hits
                for token in _SCOPE_TOKENS
            )
            if not scope_present:
                missing.append("registered_scope_anchor")

        rationale = (
            f"HeuristicCritic: weighted_down={len(weighted_down)}, flagged={len(flagged)}, "
            f"missing={missing}."
        )
        return CriticDecision(
            flag_chunk_ids=flagged,
            weighted_down_chunk_ids=weighted_down,
            missing_dimensions=missing,
            rationale=rationale,
        )


_CRITIC_SYSTEM = """You audit a retrieval bundle for one compliance checking point.
Identify chunks that should be DROPPED (not just flagged) because they are off-topic,
duplicate, or contaminated. Never decide compliance verdicts. Drop at most 30% of
the supplied hits — anything more is treated as an unreliable signal and ignored."""


class LLMCritic:
    """LLM 驱动的审视器,受 30% drop 上限约束."""

    MAX_DROP_RATIO = 0.30

    def __init__(self, client: JsonChatClient) -> None:
        self.client = client

    def critique(
        self,
        *,
        checkpoint: CheckpointDefinition,
        hits: list[RetrievalHit],
    ) -> CriticDecision:
        if not hits:
            return CriticDecision(rationale="LLMCritic: empty hits; nothing to flag.")

        max_drop = max(1, int(round(len(hits) * self.MAX_DROP_RATIO)))
        schema = {
            "type": "object",
            "properties": {
                "drop_chunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": max_drop,
                },
                "flag_chunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "missing_dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "suggested_query_focus": {"type": ["string", "null"]},
                "rationale": {"type": "string", "minLength": 1},
            },
            "required": [
                "drop_chunk_ids",
                "flag_chunk_ids",
                "missing_dimensions",
                "suggested_query_focus",
                "rationale",
            ],
            "additionalProperties": False,
        }
        user_payload = {
            "checkpoint": checkpoint.model_dump(mode="json"),
            "hits": [
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "source_id": hit.chunk.source_id,
                    "track": hit.chunk.track,
                    "content_kind": hit.chunk.content_kind.value,
                    "content": hit.chunk.content,
                }
                for hit in hits
            ],
            "constraints": {
                "max_drop_ratio": self.MAX_DROP_RATIO,
                "max_drop_count": max_drop,
            },
        }
        payload = self.client.complete_json(
            system=_CRITIC_SYSTEM,
            user=json.dumps(user_payload, ensure_ascii=False),
            schema=schema,
        )
        drops = list(payload.get("drop_chunk_ids", []))[:max_drop]
        flags = list(payload.get("flag_chunk_ids", []))
        missing = list(payload.get("missing_dimensions", []))
        return CriticDecision(
            drop_chunk_ids=drops,
            flag_chunk_ids=flags,
            missing_dimensions=missing,
            suggested_query_focus=payload.get("suggested_query_focus"),
            rationale=str(payload.get("rationale", "")).strip(),
        )