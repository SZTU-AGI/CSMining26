"""Stage A — deterministic fact extraction and contradiction detection (§4).

The deterministic extractor is the offline path: no model client, byte-stable
output. That makes it both the production fallback and the test fixture used
here, so these assertions exercise the same code that runs in a degraded run.
"""

from __future__ import annotations

import pytest

from freca.ledger.config import ExtractionConfig, ExtractorMode
from freca.ledger.contradictions import detect_contradictions
from freca.ledger.extraction import (
    DeterministicFactExtractor,
    build_case_ledger,
    build_extractor,
)
from freca.ledger.models import ContradictionKind, FactPolarity

from ledger_helpers import make_chunk

REGISTRATION = (
    "Establishment name: Graincore Export Services Pty Ltd\n"
    "\n"
    "Registered establishment number: RE-WA-2021-0041\n"
    "\n"
    "Registration valid until: 2026-06-30\n"
)

PEST_CONTROL = (
    "Pest control treatment performed on 2021-03-04 by licensed operator.\n"
    "\n"
    "Bait station inspection interval: 14 days\n"
)

ANSWER_LIKE = (
    "Audit scenario: the establishment fails to keep pest control records.\n"
    "\n"
    "NOTE: NON-COMPLIANT - the inspector should mark this checking point.\n"
)


def _extract(chunks, config: ExtractionConfig | None = None):
    settings = config or ExtractionConfig(mode=ExtractorMode.DETERMINISTIC)
    ledger, trace = build_case_ledger(
        case_id=1,
        chunks=chunks,
        extractor=DeterministicFactExtractor(config=settings),
        config=settings,
    )
    return ledger, trace


def test_deterministic_extraction_produces_traceable_undecided_facts() -> None:
    chunks = [
        make_chunk(content=REGISTRATION, track=1),
        make_chunk(content=PEST_CONTROL, track=5),
    ]
    ledger, _trace = _extract(chunks)

    assert ledger.facts, "deterministic extraction produced no facts"
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    for fact in ledger.facts:
        assert fact.polarity is FactPolarity.UNDECIDED
        assert fact.case_id == 1
        assert fact.chunk_id in chunk_ids
        assert fact.source_file
        assert fact.verbatim
        assert fact.locator().startswith("track-")

    assert ledger.extractor == "deterministic-segment-v1"
    assert ledger.chunk_count == 2
    assert ledger.re_number == "RE-WA-2021-0041"


def test_deterministic_extraction_is_reproducible() -> None:
    chunks = [make_chunk(content=REGISTRATION, track=1)]
    first, _ = _extract(chunks)
    second, _ = _extract(chunks)

    assert [fact.fact_id for fact in first.facts] == [
        fact.fact_id for fact in second.facts
    ]
    assert first.input_hash == second.input_hash


def test_answer_like_scenario_text_is_dropped_from_the_ledger() -> None:
    chunks = [
        make_chunk(content=PEST_CONTROL, track=5),
        make_chunk(content=ANSWER_LIKE, track=3),
    ]
    ledger, _trace = _extract(chunks)

    assert "answer_like_facts_dropped" in ledger.quality_flags
    assert all(not fact.is_answer_like for fact in ledger.facts)
    assert all("Audit scenario" not in fact.verbatim for fact in ledger.facts)
    assert all("NON-COMPLIANT" not in fact.verbatim.upper() for fact in ledger.facts)


def test_retained_answer_like_facts_stay_flagged_and_uncitable() -> None:
    settings = ExtractionConfig(
        mode=ExtractorMode.DETERMINISTIC, drop_answer_like_facts=False
    )
    ledger, _trace = _extract([make_chunk(content=ANSWER_LIKE, track=3)], settings)

    flagged = [fact for fact in ledger.facts if fact.is_answer_like]
    assert flagged, "answer-like text should still be detected when retained"
    assert all(fact.citable_for_support is False for fact in flagged)
    assert "answer_like_facts_dropped" not in ledger.quality_flags


def test_missing_tracks_become_quality_flags_not_verdicts() -> None:
    ledger, _trace = _extract([make_chunk(content=PEST_CONTROL, track=5)])

    assert ledger.missing_tracks == [1, 2, 3, 4, 6, 7, 8, 9]
    assert "missing_tracks" in ledger.quality_flags
    kinds = {item.kind for item in ledger.contradictions}
    assert ContradictionKind.MISSING_RECORD in kinds
    missing = next(
        item
        for item in ledger.contradictions
        if item.kind == ContradictionKind.MISSING_RECORD
    )
    assert missing.severity == "REVIEW"


def test_two_re_numbers_raise_a_blocking_identity_mismatch() -> None:
    other = (
        "Registered establishment number: RE-NSW-2019-0441\n"
        "\n"
        "Consignment released under the above registration.\n"
    )
    chunks = [
        make_chunk(content=REGISTRATION, track=1),
        make_chunk(content=other, track=7, source_file="track-7.docx"),
    ]
    ledger, _trace = _extract(chunks)

    identity = [
        item
        for item in ledger.contradictions
        if item.kind == ContradictionKind.IDENTITY_MISMATCH
    ]
    assert identity, "two RE numbers must be reported as an identity mismatch"
    assert identity[0].severity == "BLOCKER"
    assert "RE-NSW-2019-0441" in identity[0].detail
    assert "RE-WA-2021-0041" in identity[0].detail
    assert "multiple_re_numbers_in_materials" in ledger.quality_flags


def test_same_label_with_different_values_across_documents_is_flagged() -> None:
    facts_source_a = "Storage temperature reading: 18 degrees celsius on 2021-03-04\n"
    facts_source_b = "Storage temperature reading: 25 degrees celsius on 2021-03-04\n"
    chunks = [
        make_chunk(content=facts_source_a, track=4, source_file="track-4.docx"),
        make_chunk(content=facts_source_b, track=6, source_file="track-6.docx"),
    ]
    ledger, _trace = _extract(chunks)

    cross_doc = [
        item
        for item in ledger.contradictions
        if item.kind == ContradictionKind.CROSS_DOCUMENT_VALUE
    ]
    assert cross_doc, "conflicting values across documents must be detected"
    assert cross_doc[0].severity == "REVIEW"
    assert len(cross_doc[0].fact_ids) >= 2


def test_contradiction_detection_is_order_stable() -> None:
    chunks = [
        make_chunk(content=REGISTRATION, track=1),
        make_chunk(
            content="Registered establishment number: RE-NSW-2019-0441\n",
            track=7,
            source_file="track-7.docx",
        ),
    ]
    ledger, _trace = _extract(chunks)
    repeated = detect_contradictions(
        case_id=1, facts=list(reversed(ledger.facts)), chunks=chunks
    )
    assert [item.contradiction_id for item in ledger.contradictions] == [
        item.contradiction_id for item in repeated
    ]


def test_build_extractor_falls_back_to_deterministic_without_a_client() -> None:
    extractor = build_extractor(
        ExtractionConfig(mode=ExtractorMode.LLM_WITH_FALLBACK), client=None
    )
    assert extractor.name == "deterministic-segment-v1"


def test_llm_only_mode_refuses_to_run_without_a_client() -> None:
    with pytest.raises(ValueError, match="requires a configured model client"):
        build_extractor(ExtractionConfig(mode=ExtractorMode.LLM), client=None)
