"""Stage B — runtime regulatory rubric per checking point (proposal §5).

For each CP the pipeline retrieves the relevant clauses of the official rules
using the CP's own text as the query seed, then asks a model to turn *those
clauses* into an explicit, citation-complete rubric.

Why this is not answer hardcoding (§3)
--------------------------------------
The red line is a human-authored map from a checking point to its expected
answer. This module never contains one:

* the only checking-point text used is the official CP wording, read at runtime
  from ``build/parsed/checkpoints.json``;
* every criterion must cite at least one retrieved policy chunk, and
  :class:`~freca.ledger.models.CheckpointRubric` refuses to validate a citation
  that is absent from the retrieval context;
* nothing in this file references a specific CP id or a specific verdict;
* the prompt forbids writing thresholds or conditions that the retrieved text
  does not state.

Caching
-------
A rubric is a pure function of (CP text, retrieved policy chunk ids and their
content, generator identity, prompt version). That tuple is hashed into
``input_hash``; a cached rubric with a matching hash is reused, anything else is
regenerated. Retrieval context is persisted next to the rubric so a reviewer can
verify that each citation really says what the criterion claims.

A rubric is per-CP, not per-case: it describes what the regulation requires, and
requirements do not vary by case. Case-specific applicability is decided in
Stage D against the fact ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from freca.config import RetrievalConfig
from freca.llm import JsonChatClient
from freca.models import CheckpointDefinition, EvidenceChunk

from freca.ledger.config import RubricConfig, RubricSource
from freca.ledger.criteria import CURATED_CHUNK_PREFIX, CriteriaTable, curated_chunk
from freca.ledger.models import CheckpointRubric, CriterionKind, RubricCriterion
from freca.ledger.store import LedgerStore
from freca.ledger.taxonomy import EVIDENCE_CATEGORIES
from freca.state import build_cache_key

RUBRIC_PROMPT_VERSION = "rubric-v1"
RUBRIC_CURATED_PROMPT_VERSION = "rubric-curated-v1"

_RUBRIC_SYSTEM = """You convert retrieved official regulation text into an explicit audit rubric
for ONE checking point. You do not audit any case and you never see case material.

Hard rules:
1. Derive every criterion from the supplied POLICY CHUNKS only. If the chunks do not state a
   requirement, do not invent one.
2. Every criterion MUST list at least one `policy_citations` entry, and each entry MUST be a
   chunk_id present in the supplied policy chunks.
3. Do not state a verdict, a score, a weighting, or a pass mark. You describe what the
   regulation requires and what would contradict it; you do not decide anything.
4. Do not copy numeric thresholds, retention periods, or timing rules unless the supplied text
   states them. Quote the regulation's own wording in `statement` where possible.
5. Provide at least one criterion of kind "applicability" (when this checking point applies,
   and the regulatory basis for it not applying) and at least one of kind "supporting".
   Add "contrary" criteria for what would demonstrate a breach, and "exception_timing"
   criteria when the regulation states exemptions, transition periods or deadlines.
6. `facts_to_verify` lists the concrete factual questions an auditor must answer from case
   material. `required_evidence_categories` names the form the proof must take.

Return only an object matching the supplied JSON schema."""


_CURATED_SYSTEM = """You convert the team's curated scoring standard and retrieved official
regulation text into an explicit audit rubric for ONE checking point. You do not audit any
case and you never see case material.

Hard rules:
1. The chunk whose chunk_id starts with "curated:" is the AUTHORITATIVE curated scoring
   standard for this checking point (red line plus the full scoring criteria). Derive every
   criterion primarily from that chunk; the remaining chunks are underlying regulation
   clauses you may cite to enrich or sharpen criteria. If neither the curated standard nor
   the clauses state a requirement, do not invent one.
2. Every criterion MUST list at least one `policy_citations` entry, and each entry MUST be a
   chunk_id present in the supplied chunks (the curated chunk's id included).
3. Do not state a verdict, a score, a weighting, or a pass mark. You describe what the
   standard requires and what would contradict it; you do not decide anything.
4. Do not copy numeric thresholds, retention periods, or timing rules unless the supplied text
   states them. Quote the curated standard's or the regulation's own wording in `statement`
   where possible.
5. Provide at least one criterion of kind "applicability" (when this checking point applies,
   and the regulatory basis for it not applying) and at least one of kind "supporting".
   Add "contrary" criteria for what would demonstrate a breach, and "exception_timing"
   criteria when the standard states exemptions, transition periods or deadlines.
6. `facts_to_verify` lists the concrete factual questions an auditor must answer from case
   material. `required_evidence_categories` names the form the proof must take.

Return only an object matching the supplied JSON schema."""


_RUBRIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["applicability_note", "criteria"],
    "properties": {
        "applicability_note": {"type": "string"},
        "criteria": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion_id", "kind", "statement", "policy_citations"],
                "properties": {
                    "criterion_id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [kind.value for kind in CriterionKind],
                    },
                    "statement": {"type": "string"},
                    "policy_citations": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "facts_to_verify": {"type": "array", "items": {"type": "string"}},
                    "required_evidence_categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(EVIDENCE_CATEGORIES)},
                    },
                },
            },
        },
    },
}


def build_policy_queries(checkpoint: CheckpointDefinition) -> list[str]:
    """Seed queries built only from the official CP wording (§5.1)."""

    official = (
        f"{checkpoint.cp_id} {checkpoint.element_title} {checkpoint.section_title}: "
        f"{checkpoint.text}"
    )
    return [
        f"{official} obligation requirement must shall",
        f"{official} applicability scope definition interpretation",
        f"{official} exception exemption time period retention deadline",
    ]


def retrieve_policy_context(
    *,
    checkpoint: CheckpointDefinition,
    policy_index,
    limit: int,
    retrieval_config: RetrievalConfig | None = None,
    reranker=None,
) -> tuple[list[EvidenceChunk], list[str], list[dict[str, Any]]]:
    """Retrieve the policy clauses that a rubric for ``checkpoint`` may cite."""

    queries = build_policy_queries(checkpoint)
    ranked: dict[str, tuple[float, EvidenceChunk]] = {}
    trace: list[dict[str, Any]] = []
    for query in queries:
        hits = policy_index.search(
            query,
            limit=limit,
            config=retrieval_config,
            reranker=reranker,
        )
        trace.append(
            {
                "query": query,
                "hits": [
                    {"chunk_id": hit.chunk.chunk_id, "score": hit.score} for hit in hits
                ],
            }
        )
        for hit in hits:
            chunk_id = hit.chunk.chunk_id
            best = ranked.get(chunk_id)
            if best is None or hit.score > best[0]:
                ranked[chunk_id] = (hit.score, hit.chunk)
    ordered = sorted(ranked.items(), key=lambda item: (-item[1][0], item[0]))[:limit]
    chunks = [chunk for _, (_, chunk) in ordered]
    return chunks, queries, trace


def rubric_input_hash(
    *,
    checkpoint: CheckpointDefinition,
    policy_chunks: Sequence[EvidenceChunk],
    generator: dict[str, str],
    prompt_version: str = RUBRIC_PROMPT_VERSION,
) -> str:
    """Hash everything a rubric derives from, so caching stays reproducible."""

    return build_cache_key(
        {
            "prompt_version": prompt_version,
            "cp_id": checkpoint.cp_id,
            "cp_text": checkpoint.text,
            "element_title": checkpoint.element_title,
            "section_title": checkpoint.section_title,
        },
        [
            {"chunk_id": chunk.chunk_id, "sha256": chunk.source_sha256, "content": chunk.content}
            for chunk in sorted(policy_chunks, key=lambda item: item.chunk_id)
        ],
        generator,
    )


def _render_policy(chunks: Sequence[EvidenceChunk], *, char_limit: int) -> str:
    blocks = []
    for chunk in chunks:
        location = chunk.location.model_dump(exclude_none=True)
        content = chunk.content or ""
        # The curated scoring standard is exempt from the snippet limit: it is
        # the team's authoritative criterion text, not raw PDF noise.
        if chunk.chunk_id.startswith(CURATED_CHUNK_PREFIX):
            label = "CURATED SCORING STANDARD"
        else:
            label = "POLICY"
            content = content[:char_limit]
        blocks.append(
            f"{label} chunk_id={chunk.chunk_id} source_file={chunk.source_file} "
            f"location={location}\n{content}"
        )
    return "\n\n".join(blocks)


def _snippets(chunks: Sequence[EvidenceChunk], *, char_limit: int) -> dict[str, str]:
    snippets: dict[str, str] = {}
    for chunk in chunks:
        content = chunk.content or ""
        if not chunk.chunk_id.startswith(CURATED_CHUNK_PREFIX):
            content = content[:char_limit]
        snippets[chunk.chunk_id] = content
    return snippets


def _fallback_rubric(
    *,
    checkpoint: CheckpointDefinition,
    policy_chunks: Sequence[EvidenceChunk],
    queries: Sequence[str],
    generator: dict[str, str],
    input_hash: str,
    config: RubricConfig,
    reason: str,
    prompt_version: str = RUBRIC_PROMPT_VERSION,
) -> CheckpointRubric:
    """A minimal, honest rubric used when no model is available.

    It states the CP's own wording as the requirement and cites the retrieved
    clauses, so downstream gates still see real citations. It deliberately adds
    no interpretation — the scorecard's ``regulatory_coverage`` stays low and
    the run report marks the rubric as degraded.
    """

    citations = [chunk.chunk_id for chunk in policy_chunks]
    criteria = [
        RubricCriterion(
            criterion_id=f"{checkpoint.cp_id}-applicability",
            kind=CriterionKind.APPLICABILITY,
            statement=(
                "Determine from the cited clauses whether this checking point applies to the "
                "establishment under audit; record the clause that makes it apply or not apply."
            ),
            policy_citations=citations,
            facts_to_verify=[
                "registered scope, commodity and premises of the establishment",
                "whether the activity described by the checking point occurs at this establishment",
            ],
        ),
        RubricCriterion(
            criterion_id=f"{checkpoint.cp_id}-supporting",
            kind=CriterionKind.SUPPORTING,
            statement=(
                "Official checking point wording, to be matched against case facts: "
                + checkpoint.text
            ),
            policy_citations=citations,
            facts_to_verify=[checkpoint.text],
        ),
        RubricCriterion(
            criterion_id=f"{checkpoint.cp_id}-contrary",
            kind=CriterionKind.CONTRARY,
            statement=(
                "Any case fact that directly contradicts the cited clauses, or a required "
                "record that the materials show to be absent, incomplete or out of date."
            ),
            policy_citations=citations,
        ),
    ]
    return CheckpointRubric(
        cp_id=checkpoint.cp_id,
        element_id=checkpoint.element_id,
        element_title=checkpoint.element_title,
        checkpoint_text=checkpoint.text,
        applicability_note=f"degraded rubric ({reason}); applicability must be decided from cited clauses",
        criteria=criteria,
        policy_chunk_ids=citations,
        policy_snippets=_snippets(policy_chunks, char_limit=config.snippet_char_limit),
        retrieval_queries=list(queries),
        generator={**generator, "degraded": reason},
        rubric_version=f"{prompt_version}:degraded",
        input_hash=input_hash,
    )


@dataclass
class RubricGenerator:
    """Generate, validate and cache one rubric per checking point."""

    config: RubricConfig = field(default_factory=RubricConfig)
    client: JsonChatClient | None = None
    store: LedgerStore | None = None
    retrieval_config: RetrievalConfig | None = None
    reranker: Any = None
    model_name: str = "unconfigured"
    criteria: CriteriaTable | None = None

    @property
    def prompt_version(self) -> str:
        if self.config.source is RubricSource.CURATED:
            return RUBRIC_CURATED_PROMPT_VERSION
        return RUBRIC_PROMPT_VERSION

    @property
    def generator_identity(self) -> dict[str, str]:
        return {
            "prompt_version": self.prompt_version,
            "model": self.model_name,
            "mode": "llm" if self.client is not None else "degraded",
        }

    def generate(
        self,
        *,
        checkpoint: CheckpointDefinition,
        policy_index,
    ) -> tuple[CheckpointRubric, bool]:
        """Return ``(rubric, from_cache)`` for one checking point."""

        policy_chunks, queries, trace = retrieve_policy_context(
            checkpoint=checkpoint,
            policy_index=policy_index,
            limit=self.config.policy_limit,
            retrieval_config=self.retrieval_config,
            reranker=self.reranker,
        )
        if not policy_chunks:
            raise ValueError(
                f"no policy context retrieved for {checkpoint.cp_id}; "
                "a rubric cannot cite the regulation"
            )
        if self.config.source is RubricSource.CURATED:
            if self.criteria is None:
                raise ValueError("rubric.source=curated requires a loaded criteria table")
            policy_chunks = [
                curated_chunk(
                    self.criteria.entry(checkpoint.cp_id),
                    cp_id=checkpoint.cp_id,
                    table=self.criteria,
                )
            ] + list(policy_chunks)
        generator = self.generator_identity
        input_hash = rubric_input_hash(
            checkpoint=checkpoint,
            policy_chunks=policy_chunks,
            generator=generator,
            prompt_version=self.prompt_version,
        )

        if self.store is not None and self.config.cache_enabled:
            cached = self.store.load_cached_rubric(checkpoint.cp_id, input_hash=input_hash)
            if cached is not None:
                return cached, True

        rubric = self._build(
            checkpoint=checkpoint,
            policy_chunks=policy_chunks,
            queries=queries,
            generator=generator,
            input_hash=input_hash,
        )

        if self.store is not None:
            self.store.write_rubric(rubric)
            self.store.write_rubric_retrieval(
                checkpoint.cp_id,
                {
                    "cp_id": checkpoint.cp_id,
                    "input_hash": input_hash,
                    "queries": list(queries),
                    "trace": trace,
                    "policy_chunks": [
                        {
                            "chunk_id": chunk.chunk_id,
                            "source_file": chunk.source_file,
                            "location": chunk.location.model_dump(exclude_none=True),
                            "content": chunk.content,
                        }
                        for chunk in policy_chunks
                    ],
                },
            )
        return rubric, False

    def _build(
        self,
        *,
        checkpoint: CheckpointDefinition,
        policy_chunks: Sequence[EvidenceChunk],
        queries: Sequence[str],
        generator: dict[str, str],
        input_hash: str,
    ) -> CheckpointRubric:
        if self.client is None:
            return _fallback_rubric(
                checkpoint=checkpoint,
                policy_chunks=policy_chunks,
                queries=queries,
                generator=generator,
                input_hash=input_hash,
                config=self.config,
                reason="no model client configured",
                prompt_version=self.prompt_version,
            )

        available = {chunk.chunk_id for chunk in policy_chunks}
        user = "\n\n".join(
            (
                "OFFICIAL CHECKING POINT\n"
                + "\n".join(
                    (
                        f"cp_id: {checkpoint.cp_id}",
                        f"element: {checkpoint.element_title}",
                        f"section: {checkpoint.section_title}",
                        f"text: {checkpoint.text}",
                    )
                ),
                _render_policy(policy_chunks, char_limit=self.config.snippet_char_limit),
                f"Produce at most {self.config.max_criteria} criteria. "
                "Valid policy_citations values: " + ", ".join(sorted(available)),
            )
        )
        try:
            payload = self.client.complete_json(
                system=(
                    _CURATED_SYSTEM
                    if self.config.source is RubricSource.CURATED
                    else _RUBRIC_SYSTEM
                ),
                user=user,
                schema=_RUBRIC_SCHEMA,
            )
            criteria = self._parse_criteria(payload, available=available, checkpoint=checkpoint)
            rubric = CheckpointRubric(
                cp_id=checkpoint.cp_id,
                element_id=checkpoint.element_id,
                element_title=checkpoint.element_title,
                checkpoint_text=checkpoint.text,
                applicability_note=str(payload.get("applicability_note", "")).strip(),
                criteria=criteria,
                policy_chunk_ids=sorted(available),
                policy_snippets=_snippets(
                    policy_chunks, char_limit=self.config.snippet_char_limit
                ),
                retrieval_queries=list(queries),
                generator=generator,
                rubric_version=self.prompt_version,
                input_hash=input_hash,
            )
        except Exception as exc:  # noqa: BLE001 - degrade instead of failing the run
            return _fallback_rubric(
                checkpoint=checkpoint,
                policy_chunks=policy_chunks,
                queries=queries,
                generator=generator,
                input_hash=input_hash,
                config=self.config,
                reason=f"{type(exc).__name__}: {exc}",
                prompt_version=self.prompt_version,
            )
        return rubric

    def _parse_criteria(
        self,
        payload: dict[str, Any],
        *,
        available: set[str],
        checkpoint: CheckpointDefinition,
    ) -> list[RubricCriterion]:
        raw = payload.get("criteria")
        if not isinstance(raw, list) or not raw:
            raise ValueError("rubric response contains no criteria")
        criteria: list[RubricCriterion] = []
        used_ids: set[str] = set()
        for index, item in enumerate(raw[: self.config.max_criteria]):
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement", "")).strip()
            if not statement:
                continue
            citations = [
                str(citation)
                for citation in (item.get("policy_citations") or [])
                if str(citation) in available
            ]
            if not citations:
                # A criterion without a resolvable citation is exactly the
                # ungrounded rule §3 forbids. Drop it rather than repair it.
                continue
            try:
                kind = CriterionKind(str(item.get("kind", "")).strip().casefold())
            except ValueError:
                kind = CriterionKind.SUPPORTING
            criterion_id = str(item.get("criterion_id", "")).strip() or (
                f"{checkpoint.cp_id}-{kind.value}-{index}"
            )
            while criterion_id in used_ids:
                criterion_id = f"{criterion_id}-{index}"
            used_ids.add(criterion_id)
            criteria.append(
                RubricCriterion(
                    criterion_id=criterion_id,
                    kind=kind,
                    statement=statement,
                    policy_citations=sorted(set(citations)),
                    facts_to_verify=[
                        str(value).strip()
                        for value in (item.get("facts_to_verify") or [])
                        if str(value).strip()
                    ],
                    required_evidence_categories=[
                        str(value)
                        for value in (item.get("required_evidence_categories") or [])
                        if str(value) in EVIDENCE_CATEGORIES
                    ],
                )
            )
        if not criteria:
            raise ValueError("no rubric criterion survived citation validation")
        kinds = {criterion.kind for criterion in criteria}
        if CriterionKind.APPLICABILITY not in kinds:
            criteria.insert(
                0,
                RubricCriterion(
                    criterion_id=f"{checkpoint.cp_id}-applicability",
                    kind=CriterionKind.APPLICABILITY,
                    statement=(
                        "Decide from the cited clauses whether this checking point applies to "
                        "the establishment under audit and cite the clause relied upon."
                    ),
                    policy_citations=sorted(available),
                ),
            )
        if CriterionKind.SUPPORTING not in kinds:
            criteria.append(
                RubricCriterion(
                    criterion_id=f"{checkpoint.cp_id}-supporting",
                    kind=CriterionKind.SUPPORTING,
                    statement=(
                        "Official checking point wording, to be matched against case facts: "
                        + checkpoint.text
                    ),
                    policy_citations=sorted(available),
                )
            )
        return criteria


__all__ = [
    "RUBRIC_PROMPT_VERSION",
    "RubricGenerator",
    "build_policy_queries",
    "retrieve_policy_context",
    "rubric_input_hash",
]
