"""Stage A — one structured fact-extraction pass per case (proposal §4).

Each case is read **once**. The output is a ledger of facts, not checking-point
verdicts. Three properties are enforced mechanically rather than by convention:

Traceability
    Every fact carries ``source_file``/``source_id``/``chunk_id`` plus a refined
    ``SourceLocation`` (worksheet row range for spreadsheets, paragraph index
    for documents), and a ``verbatim`` string that must be recoverable from the
    cited chunk. A quote the extractor cannot find is flagged
    ``verbatim_not_found_in_source`` and — under the default configuration —
    dropped, so a hallucinated quotation can never reach an adjudicator.

No pre-judging
    ``FactRecord.polarity`` only accepts ``undecided``; the model prompt says so
    and :class:`~freca.ledger.models.FactPolarity` rejects the alternatives at
    parse time. Whether a fact supports or contradicts a requirement is decided
    later, against a rubric derived from the regulation.

Leakage containment
    Track 3 scenario metadata (``Audit scenario: ...``, ``NOTE: NON-COMPLIANT``)
    describes the intended outcome of the exercise, not the farm. Facts derived
    from such text are kept for auditability but flagged ``answer_like_field``
    and excluded from the evidence pack by default (§3).

Extractors
----------
``DeterministicFactExtractor``
    Segment-level extraction with no model calls. Reproducible, offline, and
    used as the fallback path plus the test fixture.
``LLMFactExtractor``
    Model-driven extraction, verbatim-validated against the source chunk.
``FallbackFactExtractor``
    LLM first, deterministic for any batch that fails.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from freca.llm import JsonChatClient
from freca.models import EvidenceChunk, SourceLocation
from freca.state import build_cache_key, read_json

from freca.ledger.config import ExtractionConfig, ExtractorMode
from freca.ledger.contradictions import detect_contradictions
from freca.ledger.leakage import ANSWER_LIKE_FLAG, leakage_flags
from freca.ledger.models import CaseFactLedger, FactPolarity, FactRecord
from freca.ledger.taxonomy import (
    TOPIC_DESCRIPTIONS,
    TOPICS,
    classify_topic,
    detect_evidence_categories,
    normalize_evidence_categories,
    normalize_topic,
)

CONTAMINATION_FLAG = "exclude_from_compliance_evidence"
VERBATIM_MISSING_FLAG = "verbatim_not_found_in_source"

_CELL_REF = re.compile(r"^([A-Z]{1,3})(\d+)=")
_BLANK_CELL = "<BLANK>"
_DATE = re.compile(
    r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b"
    r"|\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b"
)
_MEASURE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:lux|lx|mm|cm|m2|m²|kg|g|ml|l|%|ppm|°c|hours?|days?|months?|years?)\b",
    re.IGNORECASE,
)
_RE_NUMBER = re.compile(r"\bRE-[A-Z]{2,3}-\d{4}-\d{4}\b")
_LABEL_VALUE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z /&'()\.-]{2,48}?)\s*[:=]\s*(?P<value>.+)$")
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip()


def _short_hash(*parts: Any) -> str:
    digest = hashlib.sha256(
        "\u241f".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return digest[:10]


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """A citable slice of one chunk: raw text plus a refined locator."""

    raw: str
    display: str
    location: SourceLocation
    score: float


def _row_cells(line: str) -> list[tuple[str, str, str]]:
    """Split a spreadsheet row rendered as ``A1=x | B1=y`` into cells."""

    cells: list[tuple[str, str, str]] = []
    for part in line.split("|"):
        candidate = part.strip()
        match = _CELL_REF.match(candidate)
        if match is None:
            continue
        value = candidate[match.end() :].strip()
        cells.append((match.group(1), match.group(2), value))
    return cells


def _row_location(base: SourceLocation, cells: Sequence[tuple[str, str, str]]) -> SourceLocation:
    if not cells:
        return base
    first = f"{cells[0][0]}{cells[0][1]}"
    last = f"{cells[-1][0]}{cells[-1][1]}"
    cell_range = first if first == last else f"{first}:{last}"
    return base.model_copy(update={"cell_range": cell_range})


def _informativeness(text: str) -> float:
    """Rank a segment so truncation keeps the evidentially useful lines."""

    score = 0.0
    if _DATE.search(text):
        score += 2.5
    if _MEASURE.search(text):
        score += 2.5
    if _RE_NUMBER.search(text):
        score += 2.0
    if _LABEL_VALUE.match(text.strip()):
        score += 1.0
    topic = classify_topic(text)
    if topic != "unclassified":
        score += 1.0
    digits = sum(character.isdigit() for character in text)
    score += min(digits / 12.0, 1.5)
    score += min(len(text) / 400.0, 1.0)
    return round(score, 4)


def segment_chunk(chunk: EvidenceChunk, config: ExtractionConfig) -> list[Segment]:
    """Split one chunk into citable segments, highest information first."""

    content = chunk.content or ""
    segments: list[Segment] = []
    lines = content.splitlines()
    spreadsheet_like = sum(1 for line in lines if _CELL_REF.match(line.strip())) >= 2

    if spreadsheet_like:
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            cells = _row_cells(stripped)
            values = [value for _, _, value in cells if value and value != _BLANK_CELL]
            display = " | ".join(values)
            if len(_normalize(display)) < config.min_segment_chars:
                continue
            segments.append(
                Segment(
                    raw=stripped[: config.segment_char_limit],
                    display=display[: config.segment_char_limit],
                    location=_row_location(chunk.location, cells),
                    score=_informativeness(display),
                )
            )
    else:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
        if not blocks:
            blocks = [line.strip() for line in lines if line.strip()]
        for offset, block in enumerate(blocks):
            display = _normalize(block)
            if len(display) < config.min_segment_chars:
                continue
            location = chunk.location
            if chunk.location.paragraph_index is not None:
                location = chunk.location.model_copy(
                    update={"paragraph_index": chunk.location.paragraph_index + offset}
                )
            segments.append(
                Segment(
                    raw=block[: config.segment_char_limit],
                    display=display[: config.segment_char_limit],
                    location=location,
                    score=_informativeness(display),
                )
            )

    segments.sort(key=lambda item: (-item.score, item.display))
    return segments


# --------------------------------------------------------------------------
# Fact construction
# --------------------------------------------------------------------------


def _chunk_quality_flags(chunk: EvidenceChunk) -> list[str]:
    flags: list[str] = []
    for flag in chunk.flags:
        if flag not in flags:
            flags.append(flag)
    return flags


def _derive_claim(display: str) -> tuple[str, str]:
    """Split ``Label: value`` into a claim and a value when possible."""

    text = _normalize(display)
    match = _LABEL_VALUE.match(text)
    if match is None:
        return text, ""
    label = _normalize(match.group("label"))
    value = _normalize(match.group("value"))
    return f"{label}: {value}" if value else label, value


def build_fact(
    *,
    case_id: int,
    chunk: EvidenceChunk,
    display: str,
    verbatim: str,
    location: SourceLocation,
    topic: str | None,
    evidence_categories: Sequence[str] | None,
    sequence: int,
    batch: str | None,
    extra_flags: Sequence[str] = (),
) -> FactRecord:
    """Assemble one :class:`FactRecord` with all provenance and flags attached."""

    claim, value = _derive_claim(display)
    resolved_topic = normalize_topic(topic) if topic else classify_topic(display)
    categories = normalize_evidence_categories(list(evidence_categories or []))
    if not categories:
        categories = detect_evidence_categories(display)

    flags = _chunk_quality_flags(chunk)
    for flag in leakage_flags(display) + leakage_flags(verbatim):
        if flag not in flags:
            flags.append(flag)
    for flag in extra_flags:
        if flag not in flags:
            flags.append(flag)

    fact_id = "-".join(
        (
            f"case-{case_id:03d}",
            f"t{chunk.track}" if chunk.track else "t0",
            resolved_topic,
            _short_hash(chunk.chunk_id, verbatim, sequence),
        )
    )
    return FactRecord(
        fact_id=fact_id,
        case_id=case_id,
        topic=resolved_topic,
        claim=claim or _normalize(display)[:400],
        value=value,
        polarity=FactPolarity.UNDECIDED,
        source_file=chunk.source_file,
        source_id=chunk.source_id,
        chunk_id=chunk.chunk_id,
        track=chunk.track,
        location=location,
        verbatim=verbatim,
        evidence_categories=categories,
        quality_flags=sorted(set(flags)),
        extraction_batch=batch,
    )


# --------------------------------------------------------------------------
# Extractor protocol and implementations
# --------------------------------------------------------------------------


class FactExtractor(Protocol):
    name: str

    def extract(
        self,
        *,
        case_id: int,
        chunks: Sequence[EvidenceChunk],
    ) -> tuple[list[FactRecord], list[dict[str, Any]]]: ...


@dataclass
class DeterministicFactExtractor:
    """Segment-level extraction with no model call.

    Reproducible by construction: identical parsed input yields byte-identical
    ledgers. Used standalone (``extraction.mode: deterministic``), as the
    fallback for failed LLM batches, and as the offline test fixture.
    """

    config: ExtractionConfig = field(default_factory=ExtractionConfig)
    name: str = "deterministic-segment-v1"

    def extract(
        self,
        *,
        case_id: int,
        chunks: Sequence[EvidenceChunk],
    ) -> tuple[list[FactRecord], list[dict[str, Any]]]:
        facts: list[FactRecord] = []
        trace: list[dict[str, Any]] = []
        seen: set[str] = set()
        budget = self.config.max_facts_per_case
        truncated_chunks = 0

        for chunk in chunks:
            segments = segment_chunk(chunk, self.config)
            keep = segments[: self.config.max_facts_per_chunk]
            if len(segments) > len(keep):
                truncated_chunks += 1
            produced = 0
            for sequence, segment in enumerate(keep):
                if len(facts) >= budget:
                    break
                fact = build_fact(
                    case_id=case_id,
                    chunk=chunk,
                    display=segment.display,
                    verbatim=segment.raw,
                    location=segment.location,
                    topic=None,
                    evidence_categories=None,
                    sequence=sequence,
                    batch=None,
                )
                if fact.fact_id in seen:
                    continue
                seen.add(fact.fact_id)
                facts.append(fact)
                produced += 1
            trace.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "segments": len(segments),
                    "kept": produced,
                    "extractor": self.name,
                }
            )
            if len(facts) >= budget:
                break

        summary = {
            "extractor": self.name,
            "chunks": len(chunks),
            "facts": len(facts),
            "chunks_truncated": truncated_chunks,
            "case_budget_reached": len(facts) >= budget,
        }
        return facts, [summary, *trace]


_EXTRACTION_SYSTEM = """You build a factual ledger for one farm case. You are NOT auditing.

Rules:
1. Record only what the supplied material states. Never infer, never generalise.
2. Never judge compliance. Do not write "compliant", "non-compliant", "meets", "fails",
   "satisfies" or any verdict. polarity is always "undecided".
3. Every fact MUST quote `verbatim` as an exact substring copied from the `content` of the
   chunk you cite. Do not paraphrase inside `verbatim`.
4. Cite only `chunk_id` values present in this batch.
5. Prefer facts carrying dates, measurements, statuses, retention periods, signatures,
   identities (establishment name, RE number, address), record presence/absence, and
   cross-document values that could later conflict.
6. Some material contains scenario-authoring metadata that announces the intended outcome of
   the exercise (for example "Audit scenario: ...", "NOTE: NON-COMPLIANT"). That text is not
   farm evidence. You may record it, but state it plainly as the document's own annotation;
   never restate it as a finding about the establishment.

Return only an object matching the supplied JSON schema."""


_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["chunk_id", "topic", "claim", "verbatim"],
                "properties": {
                    "chunk_id": {"type": "string"},
                    "topic": {"type": "string", "enum": list(TOPICS)},
                    "claim": {"type": "string"},
                    "value": {"type": "string"},
                    "verbatim": {"type": "string"},
                    "evidence_categories": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


def _batch_chunks(
    chunks: Sequence[EvidenceChunk],
    config: ExtractionConfig,
) -> list[list[EvidenceChunk]]:
    batches: list[list[EvidenceChunk]] = []
    current: list[EvidenceChunk] = []
    used = 0
    for chunk in chunks:
        size = len(chunk.content or "")
        too_many = len(current) >= config.max_chunks_per_batch
        too_large = current and used + size > config.batch_char_budget
        if too_many or too_large:
            batches.append(current)
            current, used = [], 0
        current.append(chunk)
        used += size
    if current:
        batches.append(current)
    return batches


def _render_batch(chunks: Sequence[EvidenceChunk]) -> str:
    blocks = []
    for chunk in chunks:
        header = {
            "chunk_id": chunk.chunk_id,
            "source_file": chunk.source_file,
            "track": chunk.track,
            "content_kind": chunk.content_kind.value,
            "location": chunk.location.model_dump(exclude_none=True),
            "flags": chunk.flags,
        }
        blocks.append(
            "CHUNK "
            + "; ".join(f"{key}={value}" for key, value in header.items())
            + "\n"
            + (chunk.content or "")
        )
    return "\n\n".join(blocks)


@dataclass
class LLMFactExtractor:
    """Model-driven extraction with mandatory verbatim verification."""

    client: JsonChatClient
    config: ExtractionConfig = field(default_factory=ExtractionConfig)
    name: str = "llm-fact-extractor-v1"

    def extract(
        self,
        *,
        case_id: int,
        chunks: Sequence[EvidenceChunk],
    ) -> tuple[list[FactRecord], list[dict[str, Any]]]:
        facts: list[FactRecord] = []
        trace: list[dict[str, Any]] = []
        seen: set[str] = set()

        for index, batch in enumerate(_batch_chunks(chunks, self.config)):
            batch_id = f"case-{case_id:03d}-b{index:03d}"
            by_id = {chunk.chunk_id: chunk for chunk in batch}
            user = "\n\n".join(
                (
                    f"CASE {case_id}",
                    "TOPIC VOCABULARY\n"
                    + "\n".join(
                        f"- {topic}: {TOPIC_DESCRIPTIONS[topic]}" for topic in TOPICS
                    ),
                    f"Return at most {self.config.max_facts_per_batch} facts.",
                    _render_batch(batch),
                )
            )
            payload = self.client.complete_json(
                system=_EXTRACTION_SYSTEM,
                user=user,
                schema=_EXTRACTION_SCHEMA,
            )
            raw_facts = payload.get("facts")
            if not isinstance(raw_facts, list):
                raise ValueError(f"extractor returned no fact list for {batch_id}")

            accepted = rejected_chunk = rejected_verbatim = 0
            for sequence, item in enumerate(raw_facts[: self.config.max_facts_per_batch]):
                if not isinstance(item, dict):
                    continue
                chunk = by_id.get(str(item.get("chunk_id", "")))
                if chunk is None:
                    rejected_chunk += 1
                    continue
                verbatim = str(item.get("verbatim", "")).strip()
                claim = _normalize(str(item.get("claim", "")))
                if not claim:
                    continue
                matched = _verbatim_matches(verbatim, chunk.content or "")
                extra_flags: list[str] = []
                if not matched:
                    if self.config.require_verbatim_match:
                        rejected_verbatim += 1
                        continue
                    extra_flags.append(VERBATIM_MISSING_FLAG)
                if len(verbatim) < self.config.verbatim_min_length:
                    extra_flags.append("verbatim_too_short")
                value = _normalize(str(item.get("value", "")))
                display = f"{claim}: {value}" if value and value not in claim else claim
                fact = build_fact(
                    case_id=case_id,
                    chunk=chunk,
                    display=display,
                    verbatim=verbatim or display,
                    location=chunk.location,
                    topic=item.get("topic"),
                    evidence_categories=item.get("evidence_categories"),
                    sequence=sequence,
                    batch=batch_id,
                    extra_flags=extra_flags,
                )
                if fact.fact_id in seen:
                    continue
                seen.add(fact.fact_id)
                facts.append(fact)
                accepted += 1

            trace.append(
                {
                    "batch": batch_id,
                    "chunks": [chunk.chunk_id for chunk in batch],
                    "returned": len(raw_facts),
                    "accepted": accepted,
                    "rejected_unknown_chunk": rejected_chunk,
                    "rejected_verbatim": rejected_verbatim,
                    "extractor": self.name,
                }
            )

        summary = {
            "extractor": self.name,
            "chunks": len(chunks),
            "facts": len(facts),
            "batches": len(trace),
        }
        return facts, [summary, *trace]


def _verbatim_matches(verbatim: str, content: str) -> bool:
    if not verbatim:
        return False
    if verbatim in content:
        return True
    return _normalize(verbatim).casefold() in _normalize(content).casefold()


@dataclass
class FallbackFactExtractor:
    """Try the LLM extractor; fall back to deterministic segmentation on error."""

    primary: FactExtractor
    fallback: FactExtractor
    name: str = "llm-with-deterministic-fallback-v1"

    def extract(
        self,
        *,
        case_id: int,
        chunks: Sequence[EvidenceChunk],
    ) -> tuple[list[FactRecord], list[dict[str, Any]]]:
        try:
            facts, trace = self.primary.extract(case_id=case_id, chunks=chunks)
            if facts:
                return facts, [{"path": "primary", "extractor": self.primary.name}, *trace]
            reason = "primary extractor returned no facts"
        except Exception as exc:  # noqa: BLE001 - fallback must be total
            reason = f"{type(exc).__name__}: {exc}"
        facts, trace = self.fallback.extract(case_id=case_id, chunks=chunks)
        return facts, [
            {"path": "fallback", "reason": reason, "extractor": self.fallback.name},
            *trace,
        ]


def build_extractor(
    config: ExtractionConfig,
    *,
    client: JsonChatClient | None = None,
) -> FactExtractor:
    """Instantiate the extractor named by ``config.mode``."""

    deterministic = DeterministicFactExtractor(config=config)
    if config.mode == ExtractorMode.DETERMINISTIC:
        return deterministic
    if client is None:
        if config.mode == ExtractorMode.LLM:
            raise ValueError("extraction.mode=llm requires a configured model client")
        return deterministic
    llm = LLMFactExtractor(client=client, config=config)
    if config.mode == ExtractorMode.LLM:
        return llm
    return FallbackFactExtractor(primary=llm, fallback=deterministic)


# --------------------------------------------------------------------------
# Case loading and ledger assembly
# --------------------------------------------------------------------------


def load_case_chunks(build_dir: Path, case_id: int) -> list[EvidenceChunk]:
    """Read the already-parsed chunks of one case from ``build/parsed/cases``."""

    case_dir = Path(build_dir) / "parsed" / "cases" / f"{case_id:03d}"
    if not case_dir.exists():
        raise FileNotFoundError(f"parsed case directory is missing: {case_dir}")
    chunks: list[EvidenceChunk] = []
    for path in sorted(case_dir.glob("track-*.json")):
        if path.name.endswith(".error.json"):
            continue
        chunks.extend(EvidenceChunk.model_validate(item) for item in read_json(path))
    return chunks


def discover_case_ids(build_dir: Path) -> list[int]:
    root = Path(build_dir) / "parsed" / "cases"
    if not root.exists():
        return []
    ids = []
    for path in sorted(root.iterdir()):
        if path.is_dir() and path.name.isdigit():
            ids.append(int(path.name))
    return ids


def _missing_tracks(chunks: Iterable[EvidenceChunk]) -> list[int]:
    present = {chunk.track for chunk in chunks if chunk.track is not None}
    return sorted(track for track in range(1, 10) if track not in present)


def build_case_ledger(
    *,
    case_id: int,
    chunks: Sequence[EvidenceChunk],
    extractor: FactExtractor,
    config: ExtractionConfig,
) -> tuple[CaseFactLedger, list[dict[str, Any]]]:
    """Run Stage A for one case and assemble its ledger.

    ``drop_answer_like_facts`` removes scenario-authoring facts from the ledger
    entirely; when disabled they are retained but stay flagged, so the selector
    and the gate can still refuse to build a verdict on them.
    """

    facts, trace = extractor.extract(case_id=case_id, chunks=chunks)

    dropped_answer_like = 0
    if config.drop_answer_like_facts:
        kept = []
        for fact in facts:
            if ANSWER_LIKE_FLAG in fact.quality_flags:
                dropped_answer_like += 1
                continue
            kept.append(fact)
        facts = kept

    re_numbers = Counter(
        match
        for fact in facts
        for match in _RE_NUMBER.findall(f"{fact.verbatim}\n{fact.claim}")
    )
    re_number = re_numbers.most_common(1)[0][0] if re_numbers else ""

    contradictions = detect_contradictions(case_id=case_id, facts=facts, chunks=chunks)

    ledger_flags: list[str] = []
    if dropped_answer_like:
        ledger_flags.append("answer_like_facts_dropped")
    if any(CONTAMINATION_FLAG in fact.quality_flags for fact in facts):
        ledger_flags.append("contains_contaminated_evidence")
    if len(re_numbers) > 1:
        ledger_flags.append("multiple_re_numbers_in_materials")
    missing = _missing_tracks(chunks)
    if missing:
        ledger_flags.append("missing_tracks")

    ledger = CaseFactLedger(
        case_id=case_id,
        re_number=re_number,
        facts=facts,
        contradictions=contradictions,
        topic_coverage=dict(Counter(fact.topic for fact in facts)),
        track_coverage={
            str(track): count
            for track, count in sorted(
                Counter(fact.track for fact in facts if fact.track is not None).items()
            )
        },
        missing_tracks=missing,
        quality_flags=sorted(set(ledger_flags)),
        source_ids=sorted({chunk.source_id for chunk in chunks}),
        chunk_count=len(chunks),
        extractor=getattr(extractor, "name", type(extractor).__name__),
        input_hash=build_cache_key(
            sorted(chunk.chunk_id for chunk in chunks),
            {
                "mode": config.mode.value,
                "max_facts_per_chunk": config.max_facts_per_chunk,
                "max_facts_per_case": config.max_facts_per_case,
                "drop_answer_like_facts": config.drop_answer_like_facts,
            },
        ),
    )
    trace = [
        {
            "case_id": case_id,
            "dropped_answer_like": dropped_answer_like,
            "contradictions": len(contradictions),
            "re_number_candidates": dict(re_numbers),
        },
        *trace,
    ]
    return ledger, trace


__all__ = [
    "CONTAMINATION_FLAG",
    "VERBATIM_MISSING_FLAG",
    "DeterministicFactExtractor",
    "FactExtractor",
    "FallbackFactExtractor",
    "LLMFactExtractor",
    "Segment",
    "build_case_ledger",
    "build_extractor",
    "build_fact",
    "discover_case_ids",
    "load_case_chunks",
    "segment_chunk",
]
