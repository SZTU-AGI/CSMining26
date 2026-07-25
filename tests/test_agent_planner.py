from __future__ import annotations

import pytest

from freca.agent.planner import HeuristicPlanner, LLMPlanner
from freca.llm import ReplayJsonClient
from freca.models import (
    CheckpointDefinition,
    ContentKind,
    PlannerPlan,
)


def _checkpoint(element_id: int, cp_id: str = "CP1") -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=element_id,
        element_title=f"Element-{element_id}",
        section_title=f"{element_id}.1 Test",
        text="Test checkpoint",
        source_file="cp.xlsx",
        cell="A3",
    )


def test_heuristic_planner_maps_elements_to_tracks() -> None:
    planner = HeuristicPlanner()
    # Element-1 → Track 1
    p1 = planner.plan(checkpoint=_checkpoint(1, "CP1"), case_id=1, available_tracks=[1, 3, 5])
    assert 1 in p1.target_tracks
    # Element-3 → Track 2, 3, 4, 6
    p3 = planner.plan(checkpoint=_checkpoint(3, "CP17"), case_id=1, available_tracks=[2, 3, 4, 6])
    assert set(p3.target_tracks) == {2, 3, 4, 6}
    # Element-4 → Track 8, 9
    p4 = planner.plan(checkpoint=_checkpoint(4, "CP29"), case_id=1, available_tracks=[8, 9])
    assert set(p4.target_tracks) == {8, 9}


def test_heuristic_planner_filters_by_available_tracks() -> None:
    planner = HeuristicPlanner()
    # case 只有 Track 5 → Element-2 应当只产出 [5]
    p = planner.plan(checkpoint=_checkpoint(2, "CP8"), case_id=1, available_tracks=[5])
    assert p.target_tracks == [5]


def test_llm_planner_returns_structured_plan() -> None:
    payload = {
        "target_tracks": [2, 3],
        "target_content_kinds": ["paragraph", "table"],
        "rationale": "Pest control evidence is on T2/T3.",
        "confidence": 0.7,
    }
    client = ReplayJsonClient([payload])
    planner = LLMPlanner(client)
    plan = planner.plan(checkpoint=_checkpoint(3, "CP17"), case_id=1, available_tracks=[2, 3, 4])
    assert isinstance(plan, PlannerPlan)
    assert plan.target_tracks == [2, 3]
    assert plan.target_content_kinds == [ContentKind.PARAGRAPH, ContentKind.TABLE]
    assert plan.confidence == 0.7


def test_llm_planner_drops_unavailable_tracks() -> None:
    payload = {
        "target_tracks": [2, 9],   # 9 不在 available
        "target_content_kinds": [],
        "rationale": "Spurious tracks filtered out.",
        "confidence": 0.5,
    }
    planner = LLMPlanner(ReplayJsonClient([payload]))
    plan = planner.plan(checkpoint=_checkpoint(3, "CP17"), case_id=1, available_tracks=[2, 3])
    assert plan.target_tracks == [2]   # 9 被过滤掉


def test_llm_planner_invalid_kind_does_not_crash() -> None:
    payload = {
        "target_tracks": [],
        "target_content_kinds": ["garbage_kind"],  # 不在 enum
        "rationale": "unknown kind, gracefully dropped.",
        "confidence": 0.3,
    }
    planner = LLMPlanner(ReplayJsonClient([payload]))
    plan = planner.plan(checkpoint=_checkpoint(1, "CP1"), case_id=1, available_tracks=[])
    assert plan.target_content_kinds == []


def test_planner_for_unknown_element_returns_empty() -> None:
    planner = HeuristicPlanner()
    # 模拟 Element-1 但 case 仅有 Track 5,过滤后应返回空 tracks
    plan = planner.plan(checkpoint=_checkpoint(1, "CP1"), case_id=1, available_tracks=[5])
    assert plan.target_tracks == []
    assert plan.confidence == 0.85