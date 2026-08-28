from __future__ import annotations

from pathlib import Path

from freca.ledger.config import LedgerConfig, ReviewMode


ROOT = Path(__file__).parents[1]


def test_review_profile_only_changes_review_mode() -> None:
    config = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.review-always.yaml")

    assert config.ledger.review.mode == ReviewMode.ALWAYS
    assert config.ledger.selection.max_facts == 28
    assert config.ledger.selection.include_contaminated is True


def test_evidence_profile_expands_clean_evidence_only() -> None:
    config = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.evidence-expanded.yaml")

    assert config.ledger.selection.max_facts == 42
    assert config.ledger.selection.include_contaminated is False
    assert config.ledger.review.mode == ReviewMode.ON_TRIGGER


def test_evidence_scope_profile_enables_only_scope_aware_adjudication() -> None:
    config = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.evidence-scope.yaml")

    assert config.ledger.adjudication.scope_aware_evidence is True
    assert config.ledger.critic.enabled is False


def test_conflict_critic_profile_enables_only_the_conflict_critic() -> None:
    config = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.conflict-critic.yaml")

    assert config.ledger.critic.enabled is True
    assert config.ledger.adjudication.scope_aware_evidence is False


def test_curated_na_gate_profile_changes_only_the_rubric_source() -> None:
    from freca.ledger.config import RubricSource

    config = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.curated-na-gate.yaml")
    baseline = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.na-gate.yaml")

    assert config.ledger.rubric.source is RubricSource.CURATED
    assert config.ledger.rubric.criteria_xlsx == (ROOT / "FRECA_41CP_评分标准_最终合并版_材料并入.xlsx").resolve()
    assert config.ledger.rubric.criteria_xlsx.exists()
    assert config.ledger.critic.enabled is False
    assert config.ledger.review.mode == baseline.ledger.review.mode
    assert config.ledger.extraction == baseline.ledger.extraction
    assert config.ledger.adjudication == baseline.ledger.adjudication


def test_curated_conflict_critic_profile_changes_only_the_rubric_source() -> None:
    from freca.ledger.config import RubricSource

    config = LedgerConfig.from_yaml(
        ROOT / "config.ledger.minimax.curated-conflict-critic.yaml"
    )
    baseline = LedgerConfig.from_yaml(ROOT / "config.ledger.minimax.conflict-critic.yaml")

    assert config.ledger.rubric.source is RubricSource.CURATED
    assert config.ledger.rubric.criteria_xlsx == (ROOT / "FRECA_41CP_评分标准_最终合并版_材料并入.xlsx").resolve()
    assert config.ledger.critic.enabled is True
    assert config.ledger.extraction == baseline.ledger.extraction
    assert config.ledger.adjudication == baseline.ledger.adjudication
    assert config.ledger.review == baseline.ledger.review
