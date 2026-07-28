from __future__ import annotations

import json

from freca.experiments.models import ExecutionUnit, MaterialSnapshot, PromptEnvelope
from freca.state import build_cache_key


SYSTEM_PROMPT = """You are an audit assistant. Assess every supplied official checking point
independently using only the official policy, current case evidence, and attached official images.
Do not infer or add checkpoint-specific rules beyond the supplied original materials. Preserve
uncertainty and contradictions. Cite only supplied chunk or image identifiers. Return JSON only."""

VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cp_id", "verdict", "reason", "citation_ids", "uncertainty"],
                "properties": {
                    "cp_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["1", "0", "N/A"]},
                    "reason": {"type": "string"},
                    "citation_ids": {"type": "array", "items": {"type": "string"}},
                    "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}


def build_prompt(*, unit: ExecutionUnit, material: MaterialSnapshot) -> PromptEnvelope:
    checkpoint_by_id = {checkpoint.cp_id: checkpoint for checkpoint in material.checkpoints}
    if set(unit.checkpoint_ids) - checkpoint_by_id.keys():
        raise ValueError("execution unit checkpoints are absent from its material snapshot")
    payload = {
        "case_id": unit.case_id,
        "method": unit.method,
        "official_checkpoints": [
            checkpoint_by_id[cp_id].model_dump(mode="json") for cp_id in unit.checkpoint_ids
        ],
        "official_material_chunks": [chunk.model_dump(mode="json") for chunk in material.chunks],
        "official_image_paths": list(material.image_paths),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return PromptEnvelope(
        system=SYSTEM_PROMPT,
        text=text,
        image_paths=material.image_paths,
        input_sha256=material.input_sha256,
        prompt_sha256=build_cache_key(SYSTEM_PROMPT, text, material.image_paths),
    )
