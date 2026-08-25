"""A constrained third-pass judge for primary/review disagreements."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from freca.llm import JsonChatClient
from freca.ledger.config import CriticConfig
from freca.ledger.models import CheckpointRubric, EvidencePack, GateReport, LedgerDecision
from freca.ledger.selection import compact_pack, render_pack

_SYSTEM = """You adjudicate a disagreement between two existing audit decisions.
Compare their cited facts and regulatory reasoning against the supplied compact rubric and evidence.
Prefer the decision whose facts match the current establishment and whose evidence has the correct
scope: design/facility claims need design/facility evidence, while execution claims need actual records.
You MUST choose exactly one existing decision. Do not create a new verdict, fact or citation."""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["choice", "reasoning"],
    "properties": {
        "choice": {"type": "string", "enum": ["primary", "review"]},
        "reasoning": {"type": "string", "minLength": 1},
    },
}


def choice_from_payload(payload: dict) -> str | None:
    choice = payload.get("choice")
    reasoning = str(payload.get("reasoning", "")).strip()
    return choice if choice in {"primary", "review"} and reasoning else None


def _summary(decision: LedgerDecision, gate: GateReport) -> dict:
    return {
        "verdict": decision.verdict.value,
        "applicability": decision.applicability.value,
        "reasoning": decision.reasoning_summary,
        "policy_citations": decision.policy_citations,
        "supporting_fact_ids": decision.supporting_fact_ids,
        "contrary_fact_ids": decision.contrary_fact_ids,
        "gate_errors": [finding.code for finding in gate.errors],
        "review_triggers": gate.review_triggers,
    }


@dataclass
class ConflictCritic:
    client: JsonChatClient | None = None
    config: CriticConfig = field(default_factory=CriticConfig)

    def choose(
        self,
        *,
        rubric: CheckpointRubric,
        pack: EvidencePack,
        primary: LedgerDecision,
        primary_gate: GateReport,
        review: LedgerDecision,
        review_gate: GateReport,
    ) -> tuple[str | None, str]:
        if self.client is None:
            return None, "critic unavailable"
        compact = compact_pack(
            pack,
            max_facts=self.config.max_facts,
            verbatim_char_limit=self.config.snippet_char_limit,
        )
        user = "\n\n".join(
            (
                render_pack(compact, rubric=rubric),
                "PRIMARY:\n" + json.dumps(_summary(primary, primary_gate), ensure_ascii=False),
                "REVIEW:\n" + json.dumps(_summary(review, review_gate), ensure_ascii=False),
            )
        )
        try:
            payload = self.client.complete_json(system=_SYSTEM, user=user, schema=_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            return None, f"critic blocked: {type(exc).__name__}: {exc}"
        choice = choice_from_payload(payload)
        return choice, str(payload.get("reasoning", "")).strip()[:1200]


__all__ = ["ConflictCritic", "choice_from_payload"]
