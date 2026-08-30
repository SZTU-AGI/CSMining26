#!/usr/bin/env python3
"""FRECA experimental tree-search routing harness v1.

Pure search logic. Zero API by itself.

This module does NOT:
- read case evidence;
- mutate proof state;
- emit 1/0/N/A;
- invent repair actions.

It searches only over ActionGate-produced actions. In experiment v1, an action
must also be executable by the current repair_round_executor_v1.

Arms:
  D: current deterministic ActionGate order (baseline)
  T: Tree-of-Thoughts beam search over legal action sequences
  M: MCTS/UCB1 over the same action sequences

Search depth is a planner horizon. Only the first selected action is committed;
after real execution and Layer-7 rerun, the runner replans from the new state.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from typing import Callable, Any


LEGAL_ACTION_CATALOG = {
    "ALIGN_NEXT_CANDIDATE_BATCH",
    "REQUERY_NEW_FACET",
    "RESOLVE_TIME",
    "ASSESS_INFORMATION_RELIABILITY",
    "VALIDATE_CITATION",
    "CHECK_EXCEPTION",
    "CHECK_REBUTTAL",
}

EXECUTABLE_ACTION_TYPES_V1 = {
    "ALIGN_NEXT_CANDIDATE_BATCH",
    "ASSESS_INFORMATION_RELIABILITY",
}


def action_signature(action: dict) -> str:
    value = action.get("action_signature")
    if not value:
        raise ValueError(
            f"Action {action.get('action_id')} has no action_signature"
        )
    return str(value)


def action_sort_key(action: dict) -> tuple[str, str]:
    return (
        action_signature(action),
        str(action.get("action_id") or ""),
    )


def validate_action_from_gate(action: dict) -> list[str]:
    """Return experiment hard-gate violations for one candidate action."""
    violations: list[str] = []

    action_type = str(action.get("action_type") or "")

    if action_type not in LEGAL_ACTION_CATALOG:
        violations.append("CATALOG_EXTERNAL_ACTION")

    if not action.get("action_id"):
        violations.append("MISSING_ACTION_ID")

    if not action.get("action_signature"):
        violations.append("MISSING_ACTION_SIGNATURE")

    targets = list(action.get("target_artifact_ids") or [])
    if not targets:
        violations.append("TARGETLESS_ACTION")

    if action.get("proof_state_modified") is True:
        violations.append("DIRECT_PROOF_STATE_MUTATION")

    if action.get("final_label") is not None:
        violations.append("ACTION_EMITTED_FINAL_LABEL")

    status = str(action.get("execution_status") or "")
    if status and status != "PLANNED_NOT_EXECUTED":
        violations.append(
            f"NON_PLANNED_ACTION_STATUS:{status}"
        )

    return violations


def legal_actions(
    repair_plan: dict,
    *,
    executable_only: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Filter ActionGate output without creating new actions."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen_signatures: set[str] = set()

    for index, action in enumerate(
        repair_plan.get("actions", []),
        start=1,
    ):
        violations = validate_action_from_gate(action)

        action_type = str(action.get("action_type") or "")
        if (
            executable_only
            and action_type not in EXECUTABLE_ACTION_TYPES_V1
        ):
            violations.append(
                "UNEXECUTABLE_IN_EXPERIMENT_V1"
            )

        sig = str(action.get("action_signature") or "")
        if sig and sig in seen_signatures:
            violations.append("REPEATED_ACTION_SIGNATURE")
        elif sig:
            seen_signatures.add(sig)

        row = {
            "source_action_index": index,
            "action": action,
            "violations": sorted(set(violations)),
        }

        if violations:
            rejected.append(row)
        else:
            accepted.append(action)

    return accepted, rejected


def sequence_key(sequence: tuple[dict, ...]) -> tuple[str, ...]:
    return tuple(action_signature(a) for a in sequence)


def enumerate_sequences(
    actions: list[dict],
    *,
    max_depth: int,
) -> list[tuple[dict, ...]]:
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")

    out: list[tuple[dict, ...]] = []

    def walk(prefix: tuple[dict, ...]) -> None:
        if prefix:
            out.append(prefix)

        if len(prefix) >= max_depth:
            return

        used = {action_signature(a) for a in prefix}

        for action in actions:
            sig = action_signature(action)
            if sig in used:
                continue
            walk(prefix + (action,))

    walk(tuple())
    return out


def deterministic_select(
    actions: list[dict],
    *,
    horizon: int = 2,
) -> dict:
    if not actions:
        raise ValueError("No executable actions")

    sequence = tuple(actions[:max(1, horizon)])

    return {
        "arm": "D",
        "selected_sequence": list(sequence),
        "selected_first_action": sequence[0],
        "planner_value": None,
        "search_diagnostics": {
            "policy": "ACTION_GATE_STABLE_ORDER",
            "candidate_action_count": len(actions),
            "horizon": horizon,
        },
    }


def tot_select(
    actions: list[dict],
    *,
    scorer: Callable[[tuple[dict, ...]], float],
    depth: int = 2,
    beam_width: int = 3,
) -> dict:
    if not actions:
        raise ValueError("No executable actions")
    if depth < 1:
        raise ValueError("depth must be >= 1")
    if beam_width < 1:
        raise ValueError("beam_width must be >= 1")

    beam: list[tuple[dict, ...]] = [tuple()]
    scored_nodes: list[dict] = []

    for level in range(1, depth + 1):
        expanded: list[tuple[dict, ...]] = []

        for prefix in beam:
            used = {action_signature(a) for a in prefix}

            for action in actions:
                if action_signature(action) in used:
                    continue
                expanded.append(prefix + (action,))

        if not expanded:
            break

        scored = []
        for seq in expanded:
            score = float(scorer(seq))
            scored.append((score, sequence_key(seq), seq))
            scored_nodes.append({
                "depth": len(seq),
                "sequence_signatures": list(sequence_key(seq)),
                "score": score,
            })

        scored.sort(
            key=lambda row: (
                -row[0],
                row[1],
            )
        )

        beam = [
            row[2]
            for row in scored[:beam_width]
        ]

    if not beam:
        raise RuntimeError("ToT produced no beam")

    final_scored = [
        (
            float(scorer(seq)),
            sequence_key(seq),
            seq,
        )
        for seq in beam
    ]
    final_scored.sort(
        key=lambda row: (
            -row[0],
            row[1],
        )
    )

    best_score, _, best = final_scored[0]

    return {
        "arm": "T",
        "selected_sequence": list(best),
        "selected_first_action": best[0],
        "planner_value": best_score,
        "search_diagnostics": {
            "policy": "TOT_BEAM_SEARCH",
            "depth": depth,
            "beam_width": beam_width,
            "candidate_action_count": len(actions),
            "scored_nodes": scored_nodes,
        },
    }


@dataclasses.dataclass
class _MCTSNode:
    sequence: tuple[dict, ...]
    parent: "_MCTSNode | None" = None
    children: list["_MCTSNode"] = dataclasses.field(
        default_factory=list
    )
    visits: int = 0
    total_value: float = 0.0
    expanded_signatures: set[str] = dataclasses.field(
        default_factory=set
    )

    @property
    def mean_value(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.total_value / self.visits


def _available_actions_for_node(
    node: _MCTSNode,
    actions: list[dict],
) -> list[dict]:
    used = {action_signature(a) for a in node.sequence}
    return [
        action
        for action in actions
        if (
            action_signature(action) not in used
            and action_signature(action)
            not in node.expanded_signatures
        )
    ]


def _ucb1(
    child: _MCTSNode,
    *,
    parent_visits: int,
    c: float,
) -> float:
    if child.visits == 0:
        return float("inf")

    exploration = c * math.sqrt(
        math.log(max(1, parent_visits))
        / child.visits
    )
    return child.mean_value + exploration


def mcts_select(
    actions: list[dict],
    *,
    scorer: Callable[[tuple[dict, ...]], float],
    depth: int = 2,
    simulations: int = 12,
    ucb_c: float = math.sqrt(2.0),
) -> dict:
    if not actions:
        raise ValueError("No executable actions")
    if depth < 1:
        raise ValueError("depth must be >= 1")
    if simulations < 1:
        raise ValueError("simulations must be >= 1")

    root = _MCTSNode(sequence=tuple())
    simulation_log: list[dict] = []

    for sim in range(1, simulations + 1):
        node = root
        path = [root]

        # Selection.
        while (
            node.children
            and len(node.sequence) < depth
            and not _available_actions_for_node(node, actions)
        ):
            ranked = sorted(
                node.children,
                key=lambda child: (
                    -_ucb1(
                        child,
                        parent_visits=max(1, node.visits),
                        c=ucb_c,
                    ),
                    sequence_key(child.sequence),
                ),
            )
            node = ranked[0]
            path.append(node)

        # Expansion.
        if len(node.sequence) < depth:
            available = sorted(
                _available_actions_for_node(node, actions),
                key=action_sort_key,
            )
            if available:
                action = available[0]
                sig = action_signature(action)
                node.expanded_signatures.add(sig)

                child = _MCTSNode(
                    sequence=node.sequence + (action,),
                    parent=node,
                )
                node.children.append(child)
                node = child
                path.append(node)

        # Evaluation. Planner value is 0..4.
        if node.sequence:
            raw_score = float(scorer(node.sequence))
            value = max(0.0, min(4.0, raw_score)) / 4.0
        else:
            raw_score = 0.0
            value = 0.0

        # Backpropagation.
        for visited in path:
            visited.visits += 1
            visited.total_value += value

        simulation_log.append({
            "simulation": sim,
            "leaf_sequence_signatures":
                list(sequence_key(node.sequence)),
            "planner_score_0_4": raw_score,
            "normalized_value": value,
        })

    if not root.children:
        raise RuntimeError("MCTS produced no root children")

    ranked_root = sorted(
        root.children,
        key=lambda child: (
            -child.visits,
            -child.mean_value,
            sequence_key(child.sequence),
        ),
    )

    first = ranked_root[0]

    # Principal variation: greedily follow most visited/valuable child.
    principal = list(first.sequence)
    cursor = first

    while cursor.children and len(principal) < depth:
        ranked = sorted(
            cursor.children,
            key=lambda child: (
                -child.visits,
                -child.mean_value,
                sequence_key(child.sequence),
            ),
        )
        cursor = ranked[0]
        principal = list(cursor.sequence)

    planner_value = (
        float(scorer(tuple(principal)))
        if principal
        else 0.0
    )

    return {
        "arm": "M",
        "selected_sequence": principal,
        "selected_first_action": principal[0],
        "planner_value": planner_value,
        "search_diagnostics": {
            "policy": "MCTS_UCB1",
            "depth": depth,
            "simulations": simulations,
            "ucb_c": ucb_c,
            "candidate_action_count": len(actions),
            "root_children": [
                {
                    "sequence_signatures":
                        list(sequence_key(child.sequence)),
                    "visits": child.visits,
                    "mean_value": child.mean_value,
                }
                for child in ranked_root
            ],
            "simulation_log": simulation_log,
        },
    }


def run_self_tests() -> None:
    def fake_action(i: int, kind: str) -> dict:
        return {
            "action_id": f"a{i}",
            "action_type": kind,
            "action_signature": f"sig-{i}",
            "target_artifact_ids": [f"e{i}"],
            "execution_status": "PLANNED_NOT_EXECUTED",
        }

    plan = {
        "actions": [
            fake_action(1, "ALIGN_NEXT_CANDIDATE_BATCH"),
            fake_action(2, "ASSESS_INFORMATION_RELIABILITY"),
            fake_action(3, "RESOLVE_TIME"),
        ]
    }

    executable, rejected = legal_actions(plan)

    assert [a["action_id"] for a in executable] == ["a1", "a2"]
    assert any(
        "UNEXECUTABLE_IN_EXPERIMENT_V1"
        in row["violations"]
        for row in rejected
    )

    def scorer(seq: tuple[dict, ...]) -> float:
        ids = tuple(a["action_id"] for a in seq)
        if ids == ("a2", "a1"):
            return 4.0
        if ids and ids[0] == "a2":
            return 3.0
        return 1.0

    d = deterministic_select(executable)
    assert d["selected_first_action"]["action_id"] == "a1"

    t = tot_select(
        executable,
        scorer=scorer,
        depth=2,
        beam_width=3,
    )
    assert t["selected_first_action"]["action_id"] == "a2"

    m = mcts_select(
        executable,
        scorer=scorer,
        depth=2,
        simulations=12,
    )
    assert m["selected_first_action"]["action_id"] in {"a1", "a2"}
    assert m["selected_first_action"]["action_id"] == "a2"

    seqs = enumerate_sequences(executable, max_depth=2)
    assert len(seqs) == 4

    print("tree_search_harness_v1 self-tests: PASS")
    print("  ActionGate actions only")
    print("  unimplemented executor actions rejected explicitly")
    print("  deterministic baseline preserves ActionGate order")
    print("  ToT beam search selects planner-preferred branch")
    print("  MCTS/UCB1 selects planner-preferred root branch")
    print("  no API / no proof mutation / no final label")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_tests()


if __name__ == "__main__":
    main()
