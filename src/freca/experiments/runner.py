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
from freca.llm import JsonChatClient
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
    prompt = build_prompt(unit=unit, material=material)
    if prompt.image_paths:
        multimodal_complete = getattr(client, "complete_json_with_images", None)
        if not callable(multimodal_complete):
            raise TypeError("client does not support structured multimodal requests")
        raw = multimodal_complete(
            system=prompt.system,
            user=prompt.text,
            image_paths=[Path(image_path) for image_path in prompt.image_paths],
            schema=VERDICT_SCHEMA,
        )
    else:
        raw = client.complete_json(
            system=prompt.system,
            user=prompt.text,
            schema=VERDICT_SCHEMA,
        )
    result = validate_response(unit=unit, material=material, raw=raw, prompt_sha256=prompt.prompt_sha256)
    atomic_write_json(artifact_dir / "request.json", prompt.model_dump(mode="json"))
    atomic_write_json(artifact_dir / "response.json", raw)
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
    unknown = sorted(
        {citation for verdict in verdicts for citation in verdict.citation_ids} - known_citation_ids
    )
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
