
"""Install alignment != proof separation for FRECA Core.

Expected current state:
- FactCandidate bridge installed.
- evidence_reasoning_v2.py still asks the alignment model for proof_role.
- evaluate_minimal_proof_gate consumes DIRECT_SUPPORT/EXPLICIT_VIOLATION.

This patch:
1) removes proof-role judgment from the alignment prompt;
2) validates only fact<->requirement relation + typed compatibility;
3) sets accepted_for_proof=False at alignment stage;
4) converts the current proof gate into an alignment-only diagnostic reducer.

It does NOT implement ArgumentGraph or ProofStandard yet.
"""

from __future__ import annotations

import ast
from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")

if not TARGET.exists():
    raise SystemExit(
        "Missing evidence_reasoning_v2.py; run from ~/freca/core_v1"
    )

source = TARGET.read_text(encoding="utf-8")

required_markers = (
    "Also classify proof_role:",
    "DIRECT_SUPPORT",
    "EXPLICIT_VIOLATION",
    "evaluate_minimal_proof_gate",
    "fact_candidate_id",
)

missing = [m for m in required_markers if m not in source]
if missing:
    raise SystemExit(
        "Unexpected current evidence_reasoning_v2.py. Missing markers: "
        + ", ".join(missing)
    )


def replace_function(src: str, name: str, replacement: str) -> str:
    tree = ast.parse(src)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected one top-level function {name}, found {len(matches)}"
        )
    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [
        replacement.rstrip() + "\n\n"
    ]
    return "".join(lines)


def replace_assignment(src: str, name: str, replacement_expr: str) -> str:
    tree = ast.parse(src)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == name
                    for t in node.targets
                )
            )
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
            )
        )
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected one top-level assignment {name}, found {len(matches)}"
        )
    node = matches[0]
    lines = src.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [
        f"{name} = {replacement_expr}\n"
    ]
    return "".join(lines)


NEW_SYSTEM = r"""
You are a closed-source fact-to-requirement relation classifier.

You receive ONE frozen EvidenceRequirement and ONE grounded FactCandidate.
Use only the supplied requirement, official bindings, and fact.

Your task is ONLY to classify whether the observable fact bears on the
requirement.

Do NOT decide:
- overall CP compliance;
- applicability;
- evidence sufficiency;
- proof standard;
- whether one fact is enough to establish or defeat the requirement;
- DIRECT_SUPPORT;
- CORROBORATION_ONLY;
- EXPLICIT_VIOLATION;
- a final 1/0/N/A value.

Do NOT infer facts not stated in the supplied FactCandidate.
Do NOT use outside knowledge or information from other cases.
Do NOT override the supplied deterministic identity/admissibility metadata.

Classify relation:

SUPPORT
    The observable fact positively bears on the supplied requirement.

ATTACK
    The observable fact negatively bears on, contradicts, or materially
    undermines the supplied requirement.

IRRELEVANT
    The observable fact does not materially bear on the requirement.

AMBIGUOUS
    The direction depends on an unstated assumption or unclear entity, scope,
    time, or semantics.

Important:
- Relation is not proof sufficiency.
- An ATTACK relation does not mean the requirement is violated.
- A SUPPORT relation does not mean the requirement is satisfied.
- Preserve contradictory facts rather than choosing a global conclusion.

Return JSON only:

{
  "alignments": [
    {
      "requirement_id": "ER1",
      "evidence_id": "...",
      "relation": "SUPPORT|ATTACK|IRRELEVANT|AMBIGUOUS",
      "exact_quote": "exact substring from supplied FactCandidate",
      "reason_code": "POSITIVE_BEARING|NEGATIVE_BEARING|IRRELEVANT|SCOPE_DEPENDENT|AMBIGUOUS_SEMANTICS",
      "reason": "brief explanation of the fact-to-requirement relation only"
    }
  ]
}
""".strip()


NEW_VALIDATE = r