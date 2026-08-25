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
