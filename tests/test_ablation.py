from __future__ import annotations

from pathlib import Path

import pytest

from freca.ablation import (
    ABLATION_VARIANT_NAMES,
    ablation_artifact_path,
    build_variant_config,
    compute_retrieval_metrics,
    run_retrieval_judge_experiment,
    validate_relevance_labels,
)
from freca.config import (
    RecallMode,
    RetrievalAgentMode,
    RetrievalConfig,
    SelectorMode,
)
from freca.models import RetrievalBundle, RetrievalHit, RetrievalRound

from test_retrieval import _chunk


def _bundle() -> RetrievalBundle:
    policy = _chunk("p1", "policy", case_id=None, source_id="policy")
    evidence1 = _chunk("e1", "record", case_id=1, source_id="track3")
    evidence2 = _chunk("e2", "map", case_id=1, source_id="track6")
    evidence2.track = 6
    return RetrievalBundle(
        case_id=1,
        cp_id="CP1",
        policy_hits=[RetrievalHit(chunk=policy, score=1, rank=1)],
        evidence_hits=[
            RetrievalHit(chunk=evidence1, score=0.9, rank=1),
            RetrievalHit(chunk=evidence2, score=0.8, rank=2),
        ],
        rounds=[
            RetrievalRound(
                round_number=0,
                policy_query="p",
                evidence_query="e",
                added_policy_chunk_ids=["p1"],
                added_evidence_chunk_ids=["e1", "e2"],
                gaps=[],
            )
        ],
        complete=True,
        stop_reason="complete",
    )


def test_registry_contains_the_five_documented_variants() -> None:
    assert ABLATION_VARIANT_NAMES == (
        "bm25_only",
        "vector_only",
        "weighted_hybrid",
        "rrf_reranker_no_mmr",
        "full_retrieval",
    )


def test_variants_change_the_executed_retrieval_and_agent_stages() -> None:
    base = RetrievalConfig()
    bm25 = build_variant_config("bm25_only", base)
    full = build_variant_config("full_retrieval", base)

    assert bm25.recall_mode == RecallMode.BM25
    assert bm25.selector_mode == SelectorMode.TOP_K
    assert bm25.agent_mode == RetrievalAgentMode.DISABLED
    assert bm25.max_repairs == 0
    assert full.selector_mode == SelectorMode.SOURCE_AWARE_MMR
    assert full.agent_mode == base.agent_mode
    assert full.max_repairs == base.max_repairs


def test_retrieval_config_rejects_unbounded_agent_repairs() -> None:
    with pytest.raises(ValueError):
        RetrievalConfig(max_repairs=3)


def test_retrieval_metrics_include_recall_mrr_diversity_and_isolation() -> None:
    metrics = compute_retrieval_metrics(_bundle(), relevant_chunk_ids={"e2", "missing"})

    assert metrics["recall_at_k"] == 0.5
    assert metrics["mrr"] == pytest.approx(1 / 3)
    assert metrics["unique_sources"] == 3
    assert metrics["unique_tracks"] == 2
    assert metrics["cross_case_hits"] == 0
    assert metrics["repair_rounds"] == 0


def test_artifact_paths_are_isolated_and_reject_path_traversal(tmp_path: Path) -> None:
    path = ablation_artifact_path(tmp_path, "exp-01", "bm25_only", 1, "CP1")
    assert path == tmp_path / "ablation" / "exp-01" / "bm25_only" / "001" / "CP1.json"
    with pytest.raises(ValueError, match="experiment_id"):
        ablation_artifact_path(tmp_path, "../escape", "bm25_only", 1, "CP1")


def test_label_validation_rejects_unknown_and_cross_case_chunks() -> None:
    case1 = _chunk("e1", "one", case_id=1, source_id="s1")
    case2 = _chunk("e2", "two", case_id=2, source_id="s2")
    known = {item.chunk_id: item for item in [case1, case2]}

    with pytest.raises(ValueError, match="unknown chunk"):
        validate_relevance_labels({"001:CP1": ["missing"]}, known)
    with pytest.raises(ValueError, match="cross-case"):
        validate_relevance_labels({"001:CP1": ["e2"]}, known)


def test_retrieval_judge_requires_one_isolated_variant() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        run_retrieval_judge_experiment(
            None,
            run_id="combined-gold-v1",
            variant_names=["bm25_only", "full_retrieval"],
            gold_path=Path("gold/consensus-v1.json"),
        )
