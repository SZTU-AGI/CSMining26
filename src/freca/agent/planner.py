"""Tier-1 Planner Agent.

职责: 决策先查哪些 Track / content kind,产出 ``PlannerPlan``。
明确不做合规裁决 (``1/0/N/A``)。

* :class:`HeuristicPlanner` - 零 LLM 成本,按 CP Element 分桶映射到 Track
* :class:`LLMPlanner`       - 调 LLM 产出更细粒度 target_tracks + rationale
"""
from __future__ import annotations

import json
from typing import Protocol

from freca.llm import JsonChatClient
from freca.models import CheckpointDefinition, ContentKind, PlannerPlan


class PlannerAgent(Protocol):
    def plan(
        self,
        *,
        checkpoint: CheckpointDefinition,
        case_id: int,
        available_tracks: list[int],
    ) -> PlannerPlan: ...


# Element → 必查 Track 映射表.
# 数据来自 FRECA 法规结构 + 41 CP 分布:
#   CP1-CP7     (Element-1 经营业务范围) → Track 1 Registration
#   CP8-CP16    (Element-2 建筑/设施)   → Track 5 SitePlan, Track 6 Hygiene
#   CP17-CP28   (Element-3 控制体系)    → Track 2 HACCP, Track 3 Pest, Track 6 Hygiene, Track 4 Management
#   CP29-CP41   (Element-4 追溯/检疫)   → Track 8 Phytosanitary, Track 9 Traceability
# Element 划分由 ``checkingpoints_all_elements_onesheet_翻译.xlsx`` 提供.
_ELEMENT_TRACK_MAP: dict[int, list[int]] = {
    1: [1],                          # 经营业务范围
    2: [5, 6],                       # 建筑/设施
    3: [2, 3, 4, 6],                 # 控制体系
    4: [8, 9],                       # 追溯/检疫
}

# Element → 优先 content_kind 映射 (MMR 与 selector 会再 rerank).
_ELEMENT_KIND_MAP: dict[int, list[ContentKind]] = {
    1: [ContentKind.PARAGRAPH, ContentKind.TABLE],
    2: [ContentKind.PARAGRAPH, ContentKind.TABLE, ContentKind.IMAGE],
    3: [ContentKind.TABLE, ContentKind.PARAGRAPH],
    4: [ContentKind.TABLE, ContentKind.IMAGE_DESCRIPTION, ContentKind.PARAGRAPH],
}


class HeuristicPlanner:
    """基于 Element→Track 静态映射的零成本规划器."""

    def plan(
        self,
        *,
        checkpoint: CheckpointDefinition,
        case_id: int,
        available_tracks: list[int],
    ) -> PlannerPlan:
        wanted = _ELEMENT_TRACK_MAP.get(checkpoint.element_id, [])
        kinds = _ELEMENT_KIND_MAP.get(checkpoint.element_id, [])
        # 与该 case 实际存在的 track 求交集,避免检索层空集
        if available_tracks:
            wanted = [track for track in wanted if track in available_tracks]
        return PlannerPlan(
            target_tracks=wanted,
            target_content_kinds=kinds,
            rationale=(
                f"HeuristicPlanner: CP{checkpoint.cp_id} belongs to Element-"
                f"{checkpoint.element_id}; defaulted tracks="
                f"{_ELEMENT_TRACK_MAP.get(checkpoint.element_id, [])}, "
                f"kinds={[kind.value for kind in kinds]}."
            ),
            confidence=0.85,
        )


_PLANNER_SYSTEM = """You are a retrieval planner for an agricultural compliance audit.
Decide only which tracks (1-9) and content kinds to query first for the given checking point.
Do NOT decide compliance verdicts (1, 0, or N/A). Output JSON matching the schema."""


class LLMPlanner:
    """LLM 驱动的规划器,产更细粒度的 target_tracks / rationale."""

    def __init__(self, client: JsonChatClient) -> None:
        self.client = client

    def plan(
        self,
        *,
        checkpoint: CheckpointDefinition,
        case_id: int,
        available_tracks: list[int],
    ) -> PlannerPlan:
        schema = {
            "type": "object",
            "properties": {
                "target_tracks": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 9},
                },
                "target_content_kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [kind.value for kind in ContentKind]},
                },
                "rationale": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "target_tracks",
                "target_content_kinds",
                "rationale",
                "confidence",
            ],
            "additionalProperties": False,
        }
        user = json.dumps(
            {
                "checkpoint": checkpoint.model_dump(),
                "case_id": case_id,
                "available_tracks": available_tracks,
            },
            ensure_ascii=False,
        )
        payload = self.client.complete_json(
            system=_PLANNER_SYSTEM, user=user, schema=schema
        )
        try:
            kinds = [ContentKind(value) for value in payload.get("target_content_kinds", [])]
        except ValueError:
            kinds = []
        # 安全过滤: target_tracks 必须在 available_tracks 内
        tracks = [t for t in payload.get("target_tracks", []) if t in available_tracks]
        return PlannerPlan(
            target_tracks=tracks,
            target_content_kinds=kinds,
            rationale=str(payload.get("rationale", "")).strip(),
            confidence=float(payload.get("confidence", 0.5)),
        )