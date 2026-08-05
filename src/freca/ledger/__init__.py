"""Structured fact ledger + runtime regulatory rubric audit architecture.

This package implements ``docs/STRUCTURED_RUBRIC_AUDIT_PROPOSAL.md`` as a
*parallel* architecture. It adds no behaviour to, and changes no line of, the
legacy pipeline in :mod:`freca.pipeline`. Both stacks share the same parsing
artifacts, the same indexes and the same ``1 / 0 / N/A`` vocabulary, so a run
can be switched with a config file:

* legacy   ``freca --config config.yaml run-audit``
* ledger   ``python -m freca.ledger --config config.ledger.yaml run``

Pipeline shape (proposal §1)::

    9 case documents ──▶ Stage A  fact ledger (facts, contradictions, locators)
    CP text ──▶ Stage B  runtime policy retrieval ──▶ citation-complete rubric
                          │
    ledger + rubric ──▶ Stage C  compact evidence pack
                     ──▶ Stage D  adjudication (1 / 0 / N/A, dual citation)
                     ──▶ Stage E  gates + scorecard ──▶ conditional review

Submodules
----------
``models``        schemas with the proposal's invariants enforced
``taxonomy``      generic material topics (never a CP→answer map)
``leakage``       Track-3 answer-like field guard (§3 red line)
``config``        ``LedgerConfig`` layered on the legacy ``PipelineConfig``
``store``         artifact layout under ``build/ledger/``
``extraction``    Stage A extractors (deterministic / LLM / fallback)
``contradictions`` deterministic conflict detection inside a ledger
``rubric``        Stage B runtime rubric generation and caching
``selection``     Stage C compact evidence pack builder
``adjudicate``    Stage D rubric-anchored verdicts
``scoring``       §6 five independent dimensions (no weighted total)
``gates``         §7 hard gates and review triggers
``review``        §7 conditional independent review
``baseline``      §8 three-class artifact reporting
``pipeline``      end-to-end orchestration
``cli``           ``python -m freca.ledger`` entry point
"""

from __future__ import annotations

from freca.ledger.config import LedgerConfig, LedgerSettings
from freca.ledger.models import (
    ArtifactClass,
    BaselineReport,
    CaseFactLedger,
    CheckpointRubric,
    ContradictionKind,
    CriterionKind,
    CriterionOutcome,
    CriterionStatus,
    DecisionStage,
    EvidenceCoverage,
    EvidencePack,
    EvidenceScorecard,
    EvidenceView,
    FactContradiction,
    FactPolarity,
    FactRecord,
    GateFinding,
    GateReport,
    GateSeverity,
    LedgerDecision,
    PackedFact,
    RubricCriterion,
    SilverEntry,
    TaskOutcome,
)
from freca.ledger.store import LedgerStore, case_key

__all__ = [
    "ArtifactClass",
    "BaselineReport",
    "CaseFactLedger",
    "CheckpointRubric",
    "ContradictionKind",
    "CriterionKind",
    "CriterionOutcome",
    "CriterionStatus",
    "DecisionStage",
    "EvidenceCoverage",
    "EvidencePack",
    "EvidenceScorecard",
    "EvidenceView",
    "FactContradiction",
    "FactPolarity",
    "FactRecord",
    "GateFinding",
    "GateReport",
    "GateSeverity",
    "LedgerConfig",
    "LedgerDecision",
    "LedgerSettings",
    "LedgerStore",
    "PackedFact",
    "RubricCriterion",
    "SilverEntry",
    "TaskOutcome",
    "case_key",
]
