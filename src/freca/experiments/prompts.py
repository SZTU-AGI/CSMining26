from __future__ import annotations

import json

from freca.experiments.models import ExecutionUnit, MaterialSnapshot, PromptEnvelope
from freca.state import build_cache_key


SYSTEM_PROMPT = """You are an audit assistant. Assess every supplied official checking point
independently using only the official policy, current case evidence, and attached official images.
Do not infer or add checkpoint-specific rules beyond the supplied original materials. Preserve
uncertainty and contradictions. Cite only supplied chunk or image identifiers.

Reply with a single JSON object that matches this exact shape and nothing else —
no prose, no markdown, no code fence, no commentary before or after:

{
  "verdicts": [
    {
      "cp_id": "<cp_id from official_checkpoints>",
      "verdict": "1" | "0" | "N/A",
      "reason": "<short justification citing only supplied materials>",
      "citation_ids": ["<chunk_id or image_path>", ...],
      "uncertainty": <float between 0 and 1>
    }
  ]
}

Rules:
- One entry in verdicts for every cp_id supplied under official_checkpoints.
- verdict values are exactly "1", "0", or "N/A" (1 = documented/pass, 0 = missing/fail, N/A = not applicable).
- citation_ids must be copied VERBATIM from the allowed_citation_ids list supplied in the input. Do not type,
  reconstruct, or invent any identifier - copy it character-for-character from that list. An invented or
  mistyped identifier fails the audit.
- uncertainty reflects how strongly the supplied evidence supports your verdict (0 = certain, 1 = pure guess).
- Do not echo or paraphrase the input. Do not add extra keys.
"""

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


# ─────────────────────────────────────────────────────────────────────────────
# Stage-audit (method C) per-stage prompts and JSON schemas.
#
# Each stage is a separate, narrow call with a tight schema; the model is not
# asked to do everything in one pass. The system message is identical across
# stages so the model's persona is stable, but the user payload narrows per
# stage.
# ─────────────────────────────────────────────────────────────────────────────

STAGE_SYSTEM_PROMPT = """You are an audit assistant. You assess one official checking point
using only the official policy and the current case evidence. Do not infer or
add checkpoint-specific rules beyond the supplied original materials. Preserve
uncertainty and contradictions. Cite only supplied chunk identifiers.

Reply with a single JSON object — fill in the example shape below with real
values drawn from the materials. Do NOT echo the shape, schema, or any
placeholder text back; produce concrete answers only:

<example>

Rules:
- Copy any identifier you cite verbatim from the allowed_citation_ids list. Do
  not invent, reconstruct, or paraphrase chunk ids.
- Do not echo or paraphrase the rest of the input. Do not add extra keys.
"""


# Literal example objects (not schema definitions) shown to the model so it fills
# in values rather than echoing the shape back. ``complete_json`` against the
# MiniMax /anthropic endpoint does not enforce a response_format schema, so the
# system prompt must convey the contract in prose + example.
_STAGE_EXAMPLES: dict[int, str] = {
    1: (
        '{"applicability": "APPLICABLE", "reason": "<one sentence citing the policy scope that makes this CP apply>", '
        '"policy_citations": ["<chunk_id from allowed_citation_ids>"], "uncertainty": 0.1}'
    ),
    2: (
        '{"escalate": true, "evidence_citations": ["<chunk_id that proves the CP actually applies>"], '
        '"reason": "<one sentence>", "uncertainty": 0.2}'
    ),
    3: (
        '{"verdict": "1", "reason": "<one sentence citing the evidence>", '
        '"citation_ids": ["<chunk_id>"], "contradictions": ["<optional contrary chunk_id>"], "uncertainty": 0.1}'
    ),
}


APPLICABILITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["applicability", "reason", "policy_citations"],
    "properties": {
        "applicability": {"type": "string", "enum": ["APPLICABLE", "NOT_APPLICABLE"]},
        "reason": {"type": "string"},
        "policy_citations": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


CONTRARY_SEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["escalate", "evidence_citations", "reason"],
    "properties": {
        "escalate": {"type": "boolean"},
        "evidence_citations": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


JUDGMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason", "citation_ids", "uncertainty"],
    "properties": {
        "verdict": {"type": "string", "enum": ["1", "0"]},
        "reason": {"type": "string"},
        "citation_ids": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Agent-audit (method D) per-module prompts.
#
# These prompts are used by the post-stage-audit pass in
# :mod:`freca.experiments.agent_audit`. Each module has a tight JSON schema so
# the call site never has to handle free-form prose.
# ─────────────────────────────────────────────────────────────────────────────

CRITIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason", "citation_ids", "uncertainty"],
    "properties": {
        "verdict": {"type": "string", "enum": ["1", "0"]},
        "reason": {"type": "string"},
        "citation_ids": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


VERIFIER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "verdict", "reason", "citation_ids", "uncertainty"],
    "properties": {
        "status": {"type": "string", "enum": ["PASS", "FAIL", "UNCERTAIN"]},
        "verdict": {"type": "string", "enum": ["1", "0", "N/A"]},
        "reason": {"type": "string"},
        "citation_ids": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


ARBITRATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "agreement", "resolution", "reason"],
    "properties": {
        "verdict": {"type": "string", "enum": ["1", "0", "N/A"]},
        "agreement": {"type": "boolean"},
        "resolution": {"type": "string"},
        "reason": {"type": "string"},
    },
}


_AGENT_MODULE_INSTRUCTIONS: dict[str, str] = {
    "critic": (
        "You are a critic reviewing a single checking-point decision that has "
        "both supporting and contrary evidence. Decide whether the current "
        "verdict is still the right call given the contradictions listed, or "
        "whether it should flip to the opposite compliance result. Do NOT "
        "introduce new evidence beyond what is supplied. Output JSON."
    ),
    "verifier": (
        "You are a verifier doing a second-look at a single checking-point "
        "verdict. The verdict may have low confidence or missing citations. "
        "Return status=PASS if the current verdict stands, status=FAIL with a "
        "corrected verdict if it does not, or status=UNCERTAIN if you cannot "
        "improve on it. Output JSON."
    ),
    "arbitration": (
        "You are an arbitrator resolving a disagreement between two CP "
        "decisions on the same case that reference the same fact but reach "
        "opposite conclusions. Pick the verdict that is more consistent with "
        "the supplied evidence and explain why. Output JSON."
    ),
}


_AGENT_MODULE_EXAMPLES: dict[str, str] = {
    "critic": (
        '{"verdict": "1", "reason": "<one sentence citing the evidence>", '
        '"citation_ids": ["<chunk_id>"], "uncertainty": 0.2}'
    ),
    "verifier": (
        '{"status": "PASS", "verdict": "1", "reason": "<one sentence>", '
        '"citation_ids": ["<chunk_id>"], "uncertainty": 0.2}'
    ),
    "arbitration": (
        '{"verdict": "1", "agreement": false, "resolution": "ACCEPT_MAJORITY", "reason": "<one sentence>"}'
    ),
}


def build_agent_prompt(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    module: str,
    payload: dict[str, object],
) -> PromptEnvelope:
    """Build the prompt for a critic / verifier / arbitration call.

    ``module`` selects which system prompt and schema to use. ``payload`` is
    merged into the user JSON verbatim so the module sees the prior decision's
    verdict, reason, citations, and (when relevant) the contradictions or
    verifier reason.
    """
    if module not in _AGENT_MODULE_INSTRUCTIONS:
        raise ValueError(f"unknown agent module {module!r}; expected one of {sorted(_AGENT_MODULE_INSTRUCTIONS)}")
    system = STAGE_SYSTEM_PROMPT.replace("<example>", _AGENT_MODULE_EXAMPLES[module])
    checkpoint_by_id = {checkpoint.cp_id: checkpoint for checkpoint in material.checkpoints}
    base: dict[str, object] = {
        "case_id": unit.case_id,
        "module": module,
        "module_instruction": _AGENT_MODULE_INSTRUCTIONS[module],
        "official_checkpoint": checkpoint_by_id[unit.checkpoint_ids[0]].model_dump(mode="json"),
        "official_material_chunks": [chunk.model_dump(mode="json") for chunk in material.chunks],
        "allowed_citation_ids": list(material.chunk_ids),
    }
    base.update(payload)
    text = json.dumps(base, ensure_ascii=False, sort_keys=True)
    return PromptEnvelope(
        system=system,
        text=text,
        image_paths=(),
        input_sha256=material.input_sha256,
        prompt_sha256=build_cache_key(system, text, ()),
    )


_STAGE_INSTRUCTIONS: dict[int, str] = {
    1: (
        "STAGE 1 — APPLICABILITY. Decide whether the supplied checking point applies to "
        "this case at all. If yes, set applicability=APPLICABLE. If the case has no "
        "facts that touch this CP (e.g. an establishment that does not store chemicals "
        "is not subject to a CP about chemical storage), set applicability=NOT_APPLICABLE "
        "and cite the policy section that defines the CP's scope. Do not decide 1/0 here."
    ),
    2: (
        "STAGE 2 — CONTRARY SEARCH. The previous stage concluded NOT_APPLICABLE. "
        "Search the full supplied material set (all 9 tracks, all chunks) for any "
        "evidence that this CP actually applies — e.g. a chemical-storage record, an "
        "infestation entry, a pest-control line, a foreign-farm declaration. If you "
        "find one, set escalate=true and list the evidence_citations. If none, set "
        "escalate=false and explain in one sentence why the CP does not apply."
    ),
    3: (
        "STAGE 3 — JUDGMENT. The CP is APPLICABLE. Decide 1 (compliant / documented) "
        "or 0 (missing / non-compliant) based on the supplied evidence. List every "
        "chunk_id you relied on in citation_ids and note any contradictions in the "
        "evidence in the contradictions array."
    ),
}


def build_stage_prompt(
    *,
    unit: ExecutionUnit,
    material: MaterialSnapshot,
    stage: int,
    extra: dict[str, object] | None = None,
) -> PromptEnvelope:
    """Build the per-stage prompt for STAGE_AUDIT.

    ``stage`` must be 1, 2, or 3 (stage 4 is a local consolidation, no LLM call).
    The system prompt embeds the per-stage literal example so the model fills in
    values rather than echoing a schema definition back.
    """
    if stage not in _STAGE_INSTRUCTIONS:
        raise ValueError(f"stage must be 1, 2, or 3; got {stage}")
    system = STAGE_SYSTEM_PROMPT.replace("<example>", _STAGE_EXAMPLES[stage])

    checkpoint_by_id = {checkpoint.cp_id: checkpoint for checkpoint in material.checkpoints}
    checkpoint = checkpoint_by_id[unit.checkpoint_ids[0]]
    payload: dict[str, object] = {
        "case_id": unit.case_id,
        "stage": stage,
        "stage_instruction": _STAGE_INSTRUCTIONS[stage],
        "official_checkpoint": checkpoint.model_dump(mode="json"),
        "official_material_chunks": [chunk.model_dump(mode="json") for chunk in material.chunks],
        "allowed_citation_ids": list(material.chunk_ids),
    }
    if extra:
        payload["prior_stage"] = extra
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return PromptEnvelope(
        system=system,
        text=text,
        image_paths=(),
        input_sha256=material.input_sha256,
        prompt_sha256=build_cache_key(system, text, ()),
    )


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
        "allowed_citation_ids": list(material.chunk_ids) + list(material.image_paths),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return PromptEnvelope(
        system=SYSTEM_PROMPT,
        text=text,
        image_paths=material.image_paths,
        input_sha256=material.input_sha256,
        prompt_sha256=build_cache_key(SYSTEM_PROMPT, text, material.image_paths),
    )
