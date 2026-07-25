"""升级仲裁 (Escalation).

调用链::

    verifier PASS  →  跳过仲裁
    verifier FAIL  →  arbitrate_checkpoint (盲式)  →  一致  →  ACCEPT
                                           ↓
                                          分歧
                                           ↓
                       tiebreaker (若配置)  →  多数票  →  ACCEPT
                                           ↓
                                         仍分歧
                                           ↓
                                      REVIEW_DISAGREEMENT

约束:

* tiebreaker_client 未配置时静默降级到盲式(等价旧行为),不抛错.
* 多数票: first/second/third 中 ≥2 一致 verdict 即可接受;否则 REVIEW_DISAGREEMENT.
* ``resolution`` 取自 :class:`freca.models.ArbitrationResult`,新值 ``THREE_WAY_TIE``
  用于"三模型均不一致";``ACCEPT_MAJORITY`` 用于"多数票通过".
"""
from __future__ import annotations

from freca.audit import audit_checkpoint
from freca.llm import JsonChatClient
from freca.models import (
    ArbitrationResult,
    AuditDecision,
    CheckpointDefinition,
    RetrievalBundle,
    Verdict,
)
from freca.quality import arbitrate_checkpoint


_RESOLUTION_ACCEPT_MAJORITY = "ACCEPT_MAJORITY"
_RESOLUTION_THREE_WAY_TIE = "THREE_WAY_TIE"
_RESOLUTION_REVIEW = "REVIEW_DISAGREEMENT"
_RESOLUTION_ACCEPT_AGREEMENT = "ACCEPT_AGREEMENT"


def escalated_arbitrate(
    *,
    blind_client: JsonChatClient,
    tiebreaker_client: JsonChatClient | None,
    checkpoint: CheckpointDefinition,
    retrieval: RetrievalBundle,
    first_decision: AuditDecision,
) -> ArbitrationResult:
    """三级升级仲裁.

    Args:
        blind_client:      第二模型客户端(必填,等价旧路径).
        tiebreaker_client: 第三模型客户端(可选;None 时降级盲式).
        checkpoint:        当前 CP 定义.
        retrieval:         检索合集.
        first_decision:    第一模型裁决.

    Returns:
        ``ArbitrationResult``,``resolution`` 视升级结果取 4 个值之一.
    """
    blind = arbitrate_checkpoint(
        blind_client, checkpoint, retrieval, first_decision
    )
    if blind.agreement:
        return blind

    # 仍分歧
    if tiebreaker_client is None:
        # 降级到旧路径
        return blind

    third = audit_checkpoint(tiebreaker_client, checkpoint, retrieval)
    majority = _majority_verdict([first_decision.verdict, blind.second_decision.verdict, third.verdict])
    if majority is None:
        return ArbitrationResult(
            case_id=first_decision.case_id,
            cp_id=first_decision.cp_id,
            first_verdict=first_decision.verdict,
            second_decision=third,
            agreement=False,
            resolution=_RESOLUTION_THREE_WAY_TIE,
        )

    # 接受多数票,但 second_decision 字段保留 third 的决策结构(便于复盘)
    return ArbitrationResult(
        case_id=first_decision.case_id,
        cp_id=first_decision.cp_id,
        first_verdict=first_decision.verdict,
        second_decision=third,
        agreement=(majority == first_decision.verdict),
        resolution=_RESOLUTION_ACCEPT_MAJORITY,
    )


def _majority_verdict(verdicts: list[Verdict]) -> Verdict | None:
    """多数票: 三个 verdict 中至少两个相同 → 返回该值;否则 None."""
    if len(verdicts) < 3:
        return None
    counts: dict[Verdict, int] = {}
    for verdict in verdicts:
        counts[verdict] = counts.get(verdict, 0) + 1
    winner = max(counts.items(), key=lambda item: item[1])
    if winner[1] >= 2:
        return winner[0]
    return None


__all__ = ["escalated_arbitrate"]