from freca.ledger.critic import choice_from_payload
from freca.ledger.adjudicate import Adjudicator
from freca.ledger.config import AdjudicationConfig, CriticConfig
from freca.ledger.critic import ConflictCritic
from freca.ledger.gates import evaluate_gates
from freca.ledger.review import ACCEPT_CRITIC_PRIMARY, ReviewCoordinator

from ledger_helpers import StubJsonClient, make_decision, make_fact, make_pack, make_rubric


def test_critic_accepts_only_existing_decisions() -> None:
    assert choice_from_payload({"choice": "primary", "reasoning": "citation supports it"}) == "primary"
    assert choice_from_payload({"choice": "review", "reasoning": "scope is correct"}) == "review"


def test_invalid_critic_choice_is_rejected() -> None:
    assert choice_from_payload({"choice": "new_verdict", "reasoning": "invented"}) is None
    assert choice_from_payload({"choice": "primary"}) is None


def test_critic_can_only_choose_one_of_the_two_clean_decisions() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("F1"), make_fact("F2")])
    primary = make_decision(rubric=rubric, pack=pack, confidence=0.30)
    primary_gate = evaluate_gates(decision=primary, pack=pack, rubric=rubric)
    review_payload = {
        "applicability": "APPLICABLE", "verdict": "0",
        "criterion_outcomes": [
            {"criterion_id": "C1", "status": "satisfied", "fact_ids": ["F1"]},
            {"criterion_id": "C2", "status": "violated", "fact_ids": ["F1"]},
        ],
        "policy_citations": ["policy-2"], "supporting_fact_ids": [],
        "contrary_fact_ids": ["F1"], "evidence_coverage": "complete",
        "applicability_reasoning": "", "reasoning_summary": "Record fails the interval.",
        "confidence": 0.81,
    }
    critic_client = StubJsonClient([{"choice": "primary", "reasoning": "Primary uses the correct scope."}])
    coordinator = ReviewCoordinator(
        adjudicator=Adjudicator(client=StubJsonClient([review_payload])),
        adjudication_config=AdjudicationConfig(),
        critic=ConflictCritic(client=critic_client, config=CriticConfig(enabled=True)),
    )

    outcome = coordinator.resolve(
        rubric=rubric, pack=pack, primary=primary, primary_gate=primary_gate
    )

    assert outcome.resolution == ACCEPT_CRITIC_PRIMARY
    assert outcome.final is not primary
    assert outcome.final.verdict == primary.verdict
    assert outcome.critic["choice"] == "primary"
    assert len(critic_client.calls) == 1
