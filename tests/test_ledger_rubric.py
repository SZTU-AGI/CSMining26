"""Stage B — runtime regulatory rubric (proposal §5, §3).

The rubric is the piece that makes the architecture defensible: it is derived at
runtime from retrieved official clauses, never from a human-authored map of
"checking point → expected answer".

These tests pin the four properties that keep it honest:

* the seed queries contain only the **official CP wording** (§5.1);
* every criterion must cite a chunk that is actually in the retrieval context —
  an ungrounded criterion is dropped, never repaired (§3, §5);
* a missing model degrades to an explicit, marked-down rubric instead of an
  invented one, and the degradation is visible downstream;
* caching is keyed on everything the rubric derives from, so a changed clause
  regenerates rather than silently reusing.
"""

from __future__ import annotations

import pytest

from freca.models import (
    CheckpointDefinition,
    ContentKind,
    EvidenceChunk,
    RetrievalHit,
    SourceLocation,
    SourceType,
)

from freca.ledger.config import RubricConfig
from freca.ledger.models import CriterionKind
from freca.ledger.rubric import (
    RUBRIC_PROMPT_VERSION,
    RubricGenerator,
    build_policy_queries,
    retrieve_policy_context,
    rubric_input_hash,
)
from freca.ledger.store import LedgerStore

from ledger_helpers import StubJsonClient


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _checkpoint(cp_id: str = "CP9") -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id=cp_id,
        element_id=1,
        element_title="Element 1 - Establishment and premises",
        section_title="1.3 Pest control",
        text="Is a documented pest control programme implemented and are treatment records retained?",
        source_file="checkpoints.xlsx",
        cell="B12",
    )


def _policy_chunk(chunk_id: str, content: str, *, sha: str | None = None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        source_id="policy",
        source_file="rules.pdf",
        source_type=SourceType.PDF,
        location=SourceLocation(page=4),
        content=content,
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256=(sha or "a") * 64 if len(sha or "a") == 1 else (sha or "a" * 64),
    )


class FakePolicyIndex:
    """Minimal stand-in for the policy index used by Stage B."""

    def __init__(self, chunks, *, scores=None):
        self._chunks = list(chunks)
        self._scores = scores or {}
        self.queries: list[str] = []

    def search(self, query, *, limit, config=None, reranker=None):
        self.queries.append(query)
        hits = []
        for rank, chunk in enumerate(self._chunks[:limit], start=1):
            hits.append(
                RetrievalHit(
                    chunk=chunk,
                    score=self._scores.get(chunk.chunk_id, 1.0 / rank),
                    rank=rank,
                )
            )
        return hits


def _chunks():
    return [
        _policy_chunk(
            "policy-1",
            "Establishments must implement a documented pest control programme.",
            sha="a",
        ),
        _policy_chunk(
            "policy-2",
            "Treatment records shall be retained for a period of two years.",
            sha="b",
        ),
    ]


def _rubric_payload(citations=("policy-1", "policy-2")) -> dict:
    return {
        "applicability_note": "Applies to all registered establishments handling product.",
        "criteria": [
            {
                "criterion_id": "A1",
                "kind": "applicability",
                "statement": "The establishment is registered and handles product on premises.",
                "policy_citations": [citations[0]],
                "facts_to_verify": ["registration status"],
                "required_evidence_categories": ["registration_document"],
            },
            {
                "criterion_id": "S1",
                "kind": "supporting",
                "statement": "A documented pest control programme is implemented.",
                "policy_citations": list(citations),
                "facts_to_verify": ["pest control programme document"],
                "required_evidence_categories": ["dated_record"],
            },
            {
                "criterion_id": "X1",
                "kind": "contrary",
                "statement": "Treatment records are absent for part of the retention period.",
                "policy_citations": [citations[-1]],
            },
        ],
    }


# --------------------------------------------------------------------------
# §5.1 — seed queries come from the official wording only
# --------------------------------------------------------------------------


def test_policy_queries_are_seeded_only_by_official_checking_point_text():
    checkpoint = _checkpoint()
    queries = build_policy_queries(checkpoint)

    assert len(queries) == 3
    for query in queries:
        assert checkpoint.text in query
        assert checkpoint.cp_id in query
    # Three angles: obligation, scope, exceptions/timing.
    assert "obligation requirement must shall" in queries[0]
    assert "applicability scope definition" in queries[1]
    assert "exception exemption time period retention deadline" in queries[2]


def test_retrieval_merges_queries_and_keeps_the_best_score_per_chunk():
    chunks = _chunks()
    index = FakePolicyIndex(chunks, scores={"policy-1": 0.4, "policy-2": 0.9})

    retrieved, queries, trace = retrieve_policy_context(
        checkpoint=_checkpoint(), policy_index=index, limit=5
    )

    assert [chunk.chunk_id for chunk in retrieved] == ["policy-2", "policy-1"]
    assert len(queries) == 3
    # The trace records every query and its hits so a reviewer can replay it.
    assert len(trace) == 3
    assert trace[0]["hits"][0]["chunk_id"] in {"policy-1", "policy-2"}
    assert index.queries == queries


# --------------------------------------------------------------------------
# §3 / §5 — citation completeness
# --------------------------------------------------------------------------


def test_generated_rubric_cites_only_retrieved_clauses():
    index = FakePolicyIndex(_chunks())
    generator = RubricGenerator(
        client=StubJsonClient([_rubric_payload()]), model_name="test-model"
    )

    rubric, from_cache = generator.generate(checkpoint=_checkpoint(), policy_index=index)

    assert from_cache is False
    assert rubric.rubric_version == RUBRIC_PROMPT_VERSION
    assert set(rubric.policy_chunk_ids) == {"policy-1", "policy-2"}
    for criterion in rubric.criteria:
        assert criterion.policy_citations
        assert set(criterion.policy_citations) <= set(rubric.policy_chunk_ids)
    kinds = {criterion.kind for criterion in rubric.criteria}
    assert CriterionKind.APPLICABILITY in kinds
    assert CriterionKind.SUPPORTING in kinds
    # Snippets travel with the rubric so citations stay checkable later.
    assert set(rubric.policy_snippets) == set(rubric.policy_chunk_ids)


def test_a_criterion_citing_an_unretrieved_chunk_is_dropped_not_repaired():
    payload = _rubric_payload()
    payload["criteria"].append(
        {
            "criterion_id": "GHOST",
            "kind": "supporting",
            "statement": "Invented requirement with an invented citation.",
            "policy_citations": ["policy-999"],
        }
    )
    generator = RubricGenerator(
        client=StubJsonClient([payload]), model_name="test-model"
    )

    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    ids = {criterion.criterion_id for criterion in rubric.criteria}
    assert "GHOST" not in ids
    assert ids == {"A1", "S1", "X1"}


def test_a_response_with_no_groundable_criterion_degrades_instead_of_passing():
    payload = {
        "applicability_note": "",
        "criteria": [
            {
                "criterion_id": "G1",
                "kind": "supporting",
                "statement": "Ungrounded requirement.",
                "policy_citations": ["policy-999"],
            }
        ],
    }
    generator = RubricGenerator(client=StubJsonClient([payload]), model_name="m")

    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    assert rubric.generator["degraded"].startswith("ValueError")
    assert rubric.rubric_version.endswith(":degraded")


def test_missing_applicability_or_supporting_kinds_are_completed_from_policy():
    payload = {
        "applicability_note": "note",
        "criteria": [
            {
                "criterion_id": "X1",
                "kind": "contrary",
                "statement": "Records absent for part of the retention period.",
                "policy_citations": ["policy-2"],
            }
        ],
    }
    generator = RubricGenerator(client=StubJsonClient([payload]), model_name="m")

    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    kinds = {criterion.kind for criterion in rubric.criteria}
    assert CriterionKind.APPLICABILITY in kinds
    assert CriterionKind.SUPPORTING in kinds
    # The completions are still citation-backed, not free text.
    for criterion in rubric.criteria:
        assert set(criterion.policy_citations) <= set(rubric.policy_chunk_ids)
    # The supporting completion restates the *official* wording, nothing else.
    supporting = next(
        c for c in rubric.criteria if c.criterion_id == "CP9-supporting"
    )
    assert _checkpoint().text in supporting.statement


def test_rubric_generation_refuses_to_run_without_policy_context():
    generator = RubricGenerator(client=StubJsonClient([_rubric_payload()]))
    with pytest.raises(ValueError, match="no policy context retrieved"):
        generator.generate(checkpoint=_checkpoint(), policy_index=FakePolicyIndex([]))


# --------------------------------------------------------------------------
# Degradation is explicit, never silent
# --------------------------------------------------------------------------


def test_no_model_client_produces_a_marked_degraded_rubric():
    generator = RubricGenerator()  # offline

    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    assert rubric.generator["mode"] == "degraded"
    assert rubric.generator["degraded"] == "no model client configured"
    assert rubric.rubric_version == f"{RUBRIC_PROMPT_VERSION}:degraded"
    assert "degraded rubric" in rubric.applicability_note
    kinds = {criterion.kind for criterion in rubric.criteria}
    assert kinds == {
        CriterionKind.APPLICABILITY,
        CriterionKind.SUPPORTING,
        CriterionKind.CONTRARY,
    }
    # Even degraded, every criterion cites the retrieved clauses.
    for criterion in rubric.criteria:
        assert set(criterion.policy_citations) == set(rubric.policy_chunk_ids)
    # It restates the official wording and adds no interpretation of its own.
    supporting = next(
        c for c in rubric.criteria if c.kind == CriterionKind.SUPPORTING
    )
    assert _checkpoint().text in supporting.statement


def test_a_failing_model_call_degrades_with_the_reason_recorded():
    generator = RubricGenerator(
        client=StubJsonClient([RuntimeError("upstream 500")]), model_name="m"
    )

    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    assert rubric.generator["degraded"] == "RuntimeError: upstream 500"
    assert rubric.rubric_version.endswith(":degraded")


def test_degradation_is_visible_to_the_scorecard():
    """§6: a degraded rubric caps regulatory_coverage; it is not free."""

    from freca.ledger.scoring import build_scorecard

    from ledger_helpers import make_decision, make_fact, make_pack

    generator = RubricGenerator()
    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )
    pack = make_pack(rubric=rubric, facts=[make_fact("F1")])
    decision = make_decision(rubric=rubric, pack=pack)

    scorecard = build_scorecard(decision=decision, pack=pack, rubric=rubric)
    assert scorecard.regulatory_coverage <= 0.5


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def test_input_hash_covers_clause_content_generator_and_checking_point():
    checkpoint = _checkpoint()
    chunks = _chunks()
    generator = {"prompt_version": RUBRIC_PROMPT_VERSION, "model": "m", "mode": "llm"}

    base = rubric_input_hash(
        checkpoint=checkpoint, policy_chunks=chunks, generator=generator
    )
    # Same inputs in a different order hash the same.
    assert base == rubric_input_hash(
        checkpoint=checkpoint, policy_chunks=list(reversed(chunks)), generator=generator
    )
    # A changed clause invalidates the cache.
    edited = [chunks[0], _policy_chunk("policy-2", "Retained for five years.", sha="b")]
    assert base != rubric_input_hash(
        checkpoint=checkpoint, policy_chunks=edited, generator=generator
    )
    # A changed model or prompt version invalidates the cache.
    assert base != rubric_input_hash(
        checkpoint=checkpoint,
        policy_chunks=chunks,
        generator={**generator, "model": "other"},
    )
    # A changed checking point invalidates the cache.
    assert base != rubric_input_hash(
        checkpoint=checkpoint.model_copy(update={"text": "different wording"}),
        policy_chunks=chunks,
        generator=generator,
    )


def test_an_unchanged_rubric_is_served_from_cache_without_calling_the_model(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    index = FakePolicyIndex(_chunks())
    client = StubJsonClient([_rubric_payload()])  # exactly one payload available
    generator = RubricGenerator(client=client, store=store, model_name="test-model")

    first, from_cache = generator.generate(checkpoint=_checkpoint(), policy_index=index)
    assert from_cache is False
    assert len(client.calls) == 1
    assert store.rubric_path("CP9").exists()
    assert store.rubric_retrieval_path("CP9").exists()

    second, from_cache = generator.generate(checkpoint=_checkpoint(), policy_index=index)
    assert from_cache is True
    assert len(client.calls) == 1  # the stub would raise on a second call
    assert second.input_hash == first.input_hash
    assert second.model_dump() == first.model_dump()


def test_changed_policy_text_regenerates_instead_of_reusing_the_cache(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    generator = RubricGenerator(
        client=StubJsonClient([_rubric_payload(), _rubric_payload()]),
        store=store,
        model_name="test-model",
    )

    first, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    amended = [
        _chunks()[0],
        _policy_chunk("policy-2", "Records shall be retained for five years.", sha="b"),
    ]
    second, from_cache = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(amended)
    )

    assert from_cache is False
    assert second.input_hash != first.input_hash


def test_cache_can_be_disabled(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    client = StubJsonClient([_rubric_payload(), _rubric_payload()])
    generator = RubricGenerator(
        client=client,
        store=store,
        model_name="m",
        config=RubricConfig(cache_enabled=False),
    )

    generator.generate(checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks()))
    _, from_cache = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    assert from_cache is False
    assert len(client.calls) == 2


def test_retrieval_context_is_persisted_for_citation_review(tmp_path):
    from freca.state import read_json

    store = LedgerStore(tmp_path / "ledger")
    generator = RubricGenerator(
        client=StubJsonClient([_rubric_payload()]), store=store, model_name="m"
    )
    generator.generate(checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks()))

    payload = read_json(store.rubric_retrieval_path("CP9"))
    assert payload["cp_id"] == "CP9"
    assert len(payload["queries"]) == 3
    stored = {chunk["chunk_id"]: chunk["content"] for chunk in payload["policy_chunks"]}
    # The full clause text is kept, so a reviewer can check what a citation says.
    assert "documented pest control programme" in stored["policy-1"]
    assert "retained for a period of two years" in stored["policy-2"]


def test_the_module_contains_no_checkpoint_specific_answer_map():
    """§3 red line: no human-authored CP → verdict mapping anywhere in Stage B."""

    import re
    from pathlib import Path

    import freca.ledger.rubric as rubric_module

    source = Path(rubric_module.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # No literal CP id (CP1..CP41) is referenced in code or prompts.
    assert not re.search(r"\bCP\d+\b", code)
    # No verdict literal is produced here.
    assert '"1"' not in code and "'1'" not in code


# -- curated rubric source config -------------------------------------------


def test_curated_source_requires_criteria_xlsx() -> None:
    from pydantic import ValidationError

    from freca.ledger.config import RubricSource

    with pytest.raises(ValidationError):
        RubricConfig(source=RubricSource.CURATED)


def test_policy_source_rejects_criteria_xlsx() -> None:
    from pathlib import Path

    from pydantic import ValidationError

    from freca.ledger.config import RubricSource

    with pytest.raises(ValidationError):
        RubricConfig(
            source=RubricSource.POLICY,
            criteria_xlsx=Path("criteria.xlsx"),
        )
    with pytest.raises(ValidationError):
        RubricConfig(source=RubricSource.CURATED, criteria_xlsx=None)


def test_from_yaml_resolves_criteria_xlsx_against_config_dir(tmp_path) -> None:
    from pathlib import Path

    from freca.ledger.config import LedgerConfig

    root = Path(__file__).parents[1]
    source = (root / "config.ledger.minimax.na-gate.yaml").read_text(encoding="utf-8")
    source = source.replace(
        "  rubric: {max_workers: 1}",
        "  rubric: {max_workers: 1, source: curated, criteria_xlsx: my-criteria.xlsx}",
    )
    config_path = tmp_path / "config.test.curated.yaml"
    config_path.write_text(source, encoding="utf-8")

    config = LedgerConfig.from_yaml(config_path)

    assert config.ledger.rubric.criteria_xlsx == (tmp_path / "my-criteria.xlsx").resolve()


# -- curated rubric source ---------------------------------------------------


def _criteria_table() -> "CriteriaTable":
    from freca.ledger.criteria import CriteriaEntry, CriteriaTable

    return CriteriaTable(
        entries={
            "CP9": CriteriaEntry(
                redline="红线命题（含门槛）",
                criteria_text="评分标准正文 TAIL_MARKER_" + "字" * 2000,
                row_index=10,
            )
        },
        sha256="c" * 64,
        source_name="criteria.xlsx",
        sheet_name="CP评分标准",
    )


def _curated_generator(payload: dict) -> RubricGenerator:
    from pathlib import Path

    from freca.ledger.config import RubricSource

    return RubricGenerator(
        config=RubricConfig(
            source=RubricSource.CURATED, criteria_xlsx=Path("criteria.xlsx")
        ),
        client=StubJsonClient([payload]),
        model_name="stub",
        criteria=_criteria_table(),
    )


def test_curated_mode_prepends_untruncated_pseudo_chunk() -> None:
    from freca.ledger.rubric import RUBRIC_CURATED_PROMPT_VERSION

    generator = _curated_generator(
        _rubric_payload(citations=("curated:CP9", "policy-1"))
    )

    rubric, _ = generator.generate(
        checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks())
    )

    assert "curated:CP9" in rubric.policy_chunk_ids
    assert "TAIL_MARKER_" in rubric.policy_snippets["curated:CP9"]
    assert len(rubric.policy_snippets["curated:CP9"]) > 1800
    user = generator.client.calls[0]["user"]
    assert "TAIL_MARKER_" in user
    assert "CURATED SCORING STANDARD chunk_id=curated:CP9" in user
    assert rubric.rubric_version == RUBRIC_CURATED_PROMPT_VERSION
    assert rubric.generator["prompt_version"] == RUBRIC_CURATED_PROMPT_VERSION


def test_curated_mode_keeps_retrieval_queries_unchanged() -> None:
    policy_generator = RubricGenerator(
        config=RubricConfig(),
        client=StubJsonClient([_rubric_payload()]),
        model_name="stub",
    )
    curated_index = FakePolicyIndex(_chunks())
    policy_index = FakePolicyIndex(_chunks())

    _curated_generator(_rubric_payload(citations=("curated:CP9", "policy-1"))).generate(
        checkpoint=_checkpoint(), policy_index=curated_index
    )
    policy_generator.generate(checkpoint=_checkpoint(), policy_index=policy_index)

    assert curated_index.queries == policy_index.queries


def test_curated_and_policy_arm_hashes_differ() -> None:
    from freca.ledger.criteria import curated_chunk
    from freca.ledger.rubric import RUBRIC_CURATED_PROMPT_VERSION

    checkpoint = _checkpoint()
    policy_chunks = _chunks()
    generator_policy = {
        "prompt_version": RUBRIC_PROMPT_VERSION,
        "model": "m",
        "mode": "llm",
    }
    generator_curated = {
        "prompt_version": RUBRIC_CURATED_PROMPT_VERSION,
        "model": "m",
        "mode": "llm",
    }

    policy_hash = rubric_input_hash(
        checkpoint=checkpoint, policy_chunks=policy_chunks, generator=generator_policy
    )
    curated_hash = rubric_input_hash(
        checkpoint=checkpoint,
        policy_chunks=[
            curated_chunk(_criteria_table().entry("CP9"), cp_id="CP9", table=_criteria_table())
        ]
        + policy_chunks,
        generator=generator_curated,
        prompt_version=RUBRIC_CURATED_PROMPT_VERSION,
    )
    default_hash = rubric_input_hash(
        checkpoint=checkpoint, policy_chunks=policy_chunks, generator=generator_policy
    )

    assert curated_hash != policy_hash
    assert default_hash == policy_hash


def test_curated_missing_cp_row_raises_key_error() -> None:
    from freca.ledger.criteria import CriteriaTable

    table = CriteriaTable(
        entries={},
        sha256="c" * 64,
        source_name="criteria.xlsx",
        sheet_name="CP评分标准",
    )
    generator = RubricGenerator(
        config=RubricConfig(
            source=_rubric_source_curated(), criteria_xlsx=__import__("pathlib").Path("x.xlsx")
        ),
        client=StubJsonClient([]),
        model_name="stub",
        criteria=table,
    )

    with pytest.raises(KeyError):
        generator.generate(checkpoint=_checkpoint(), policy_index=FakePolicyIndex(_chunks()))


def _rubric_source_curated():
    from freca.ledger.config import RubricSource

    return RubricSource.CURATED
