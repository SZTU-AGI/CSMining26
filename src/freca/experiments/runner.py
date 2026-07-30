from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from freca.experiments.models import (
    ExecutionResult,
    ExecutionUnit,
    ExperimentVerdict,
    MaterialSnapshot,
)
from freca.experiments.prompts import VERDICT_SCHEMA, build_prompt
from freca.llm import JsonChatClient, ModelResponseError
from freca.state import atomic_write_json


def run_execution(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    client: JsonChatClient,
    artifact_dir: Path,
) -> ExecutionResult:
    """Execute one planned unit and persist request, raw response, and validation result."""
    if unit.case_id != material.case_id:
        raise ValueError("execution unit and material snapshot case_id do not match")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(unit=unit, material=material)
    atomic_write_json(artifact_dir / "request.json", prompt.model_dump(mode="json"))
    try:
        # M3 has a 1M context window and a large output ceiling (M2 was 128k
        # incl. CoT). Each verdict's reason can run a few hundred tokens, so we
        # give a generous per-CP budget plus a fixed floor; max_tokens is only
        # an upper bound, so oversizing costs nothing when the model emits less.
        anthropic_max_tokens = len(unit.checkpoint_ids) * 1500 + 4096
        if prompt.image_paths:
            multimodal_complete = getattr(client, "complete_json_with_images", None)
            if not callable(multimodal_complete):
                raise TypeError("client does not support structured multimodal requests")
            raw = multimodal_complete(
                system=prompt.system,
                user=prompt.text,
                image_paths=[Path(image_path) for image_path in prompt.image_paths],
                schema=VERDICT_SCHEMA,
                max_tokens=anthropic_max_tokens,
            )
        else:
            raw = client.complete_json(
                system=prompt.system,
                user=prompt.text,
                schema=VERDICT_SCHEMA,
                max_tokens=anthropic_max_tokens,
            )
    except ModelResponseError as exc:
        atomic_write_json(artifact_dir / "response.json", {"error": str(exc)})
        usage = getattr(client, "last_usage", None)
        if usage:
            atomic_write_json(artifact_dir / "usage.json", dict(usage))
        return ExecutionResult(
            unit=unit,
            valid=False,
            errors=(str(exc),),
            input_sha256=material.input_sha256,
            prompt_sha256=prompt.prompt_sha256,
        )
    atomic_write_json(artifact_dir / "response.json", raw)
    usage = getattr(client, "last_usage", None)
    if usage:
        atomic_write_json(artifact_dir / "usage.json", dict(usage))
    result = validate_response(unit=unit, material=material, raw=raw, prompt_sha256=prompt.prompt_sha256)
    atomic_write_json(artifact_dir / "result.json", result.model_dump(mode="json"))
    return result


def validate_response(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    raw: dict[str, Any],
    prompt_sha256: str,
) -> ExecutionResult:
    raw_verdicts = raw.get("verdicts")
    if not isinstance(raw_verdicts, list):
        return _invalid_result(unit, material, prompt_sha256, "verdicts must be an array")

    verdicts: list[ExperimentVerdict] = []
    errors: list[str] = []
    for index, value in enumerate(raw_verdicts):
        try:
            verdicts.append(ExperimentVerdict.model_validate(value))
        except ValidationError as error:
            errors.append(f"invalid verdict at index {index}: {error.errors()[0]['msg']}")

    expected_ids = set(unit.checkpoint_ids)
    actual_ids = {verdict.cp_id for verdict in verdicts}
    if actual_ids != expected_ids:
        errors.append("returned checkpoint_ids do not match the execution unit")
    if len(actual_ids) != len(verdicts):
        errors.append("duplicate checkpoint verdicts")
    known_citation_ids = set(material.chunk_ids) | set(material.image_paths)
    repaired_verdicts = _repair_citation_ids(verdicts, known_citation_ids)
    verdicts = repaired_verdicts.verdicts
    unknown = sorted(repaired_verdicts.unmatched)
    if unknown:
        errors.append(f"unknown citation_ids: {', '.join(unknown)}")

    return ExecutionResult(
        unit=unit,
        valid=not errors,
        errors=tuple(errors),
        verdicts=tuple(verdicts),
        input_sha256=material.input_sha256,
        prompt_sha256=prompt_sha256,
    )


def _invalid_result(
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    prompt_sha256: str,
    error: str,
) -> ExecutionResult:
    return ExecutionResult(
        unit=unit,
        valid=False,
        errors=(error,),
        input_sha256=material.input_sha256,
        prompt_sha256=prompt_sha256,
    )


class _RepairResult:
    """Outcome of citation-id prefix matching: repaired verdicts + unmatched ids."""

    __slots__ = ("verdicts", "unmatched")

    def __init__(self, verdicts: list[ExperimentVerdict], unmatched: set[str]) -> None:
        self.verdicts = verdicts
        self.unmatched = unmatched


def _repair_citation_ids(
    verdicts: list[ExperimentVerdict],
    known_citation_ids: set[str],
) -> _RepairResult:
    """Repair citation_ids the model invented by reconstructing the hash suffix.

    The model often keeps the semantic prefix of a chunk_id correct
    (``case-001-t5_paragraph-0024``) but invents the trailing content hash
    (``_05acc7b4ab`` instead of ``_d8a344119e``). For each unknown citation_id we
    strip the final ``_<hash>`` segment and look for a real id with the same
    prefix. If exactly one real id matches, we accept the repair; if zero or
    several match we leave the id as unknown so the unit fails validation.
    """
    if not verdicts:
        return _RepairResult(verdicts, set())

    # Pre-compute prefix -> real ids for O(1) lookup. The prefix is the id with
    # the trailing ``_<10-hex>`` (or any final underscore-separated segment) removed.
    prefix_index: dict[str, list[str]] = {}
    for real_id in known_citation_ids:
        prefix = real_id.rsplit("_", 1)[0] if "_" in real_id else real_id
        prefix_index.setdefault(prefix, []).append(real_id)

    unmatched: set[str] = set()
    repaired: list[ExperimentVerdict] = []
    for verdict in verdicts:
        if not any(citation not in known_citation_ids for citation in verdict.citation_ids):
            repaired.append(verdict)
            continue
        new_citations: list[str] = []
        for citation in verdict.citation_ids:
            if citation in known_citation_ids:
                new_citations.append(citation)
                continue
            prefix = citation.rsplit("_", 1)[0] if "_" in citation else citation
            candidates = prefix_index.get(prefix, [])
            if len(candidates) == 1:
                new_citations.append(candidates[0])
            else:
                unmatched.add(citation)
                new_citations.append(citation)
        repaired.append(
            verdict.model_copy(update={"citation_ids": tuple(new_citations)})
        )
    return _RepairResult(repaired, unmatched)
