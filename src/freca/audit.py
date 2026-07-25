from __future__ import annotations

import json

from freca.llm import JsonChatClient
from freca.models import AuditDecision, CheckpointDefinition, RetrievalBundle, RetrievalHit


_AUDIT_SYSTEM = """You are auditing one official checking point for one farm case.
Determine applicability first, derive the regulatory requirement only from the supplied policy chunks, then assess supporting and contrary case evidence.
Use N/A only when policy applicability makes the checking point genuinely not applicable. Missing evidence, parser failure, retrieval failure, or uncertainty is not N/A.
Only cite chunk_id values present in the supplied context. Do not infer a rule from answer-like wording in farm evidence.

Some case evidence is marked "exclude_from_compliance_evidence". Those chunks are foreign-farm
contamination: the document carries another establishment's RE number or name, so it does NOT prove
the registered establishment under audit complies. You must:
  (a) treat contaminated evidence as contrary evidence by default;
  (b) NOT use any contaminated chunk as the sole supporting evidence for a compliant verdict;
  (c) if the only available evidence for the checking point is contaminated, return verdict "0"
      and add "signature_foreign_evidence_only" to review_flags;
  (d) if contaminated evidence contains the only registration scope, commodity, establishment name,
      or premises detail used by the checking point, return verdict "0".

Return only an object matching the supplied JSON schema with a concise, auditable reasoning summary."""


def _format_hits(title: str, hits: list[RetrievalHit]) -> str:
    lines = [title]
    for hit in hits:
        chunk = hit.chunk
        location = chunk.location.model_dump(exclude_none=True)
        notice = (
            "  ⚠ CONTAMINATED_EVIDENCE — not the registered establishment; do not cite as supporting."
            if "exclude_from_compliance_evidence" in chunk.flags
            else ""
        )
        lines.append(
            json.dumps(
                {
                    "chunk_id": chunk.chunk_id,
                    "source_file": chunk.source_file,
                    "case_id": chunk.case_id,
                    "track": chunk.track,
                    "location": location,
                    "flags": chunk.flags,
                    "contamination_notice": notice,
                    "content": chunk.content,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def build_audit_messages(
    checkpoint: CheckpointDefinition,
    retrieval: RetrievalBundle,
) -> tuple[str, str]:
    user = "\n\n".join(
        [
            "OFFICIAL CHECKING POINT\n"
            + json.dumps(checkpoint.model_dump(), ensure_ascii=False),
            _format_hits("POLICY CHUNKS", retrieval.policy_hits),
            _format_hits("CASE EVIDENCE CHUNKS", retrieval.evidence_hits),
            "RETRIEVAL STATUS\n"
            + json.dumps(
                {
                    "complete": retrieval.complete,
                    "stop_reason": retrieval.stop_reason,
                    "rounds": len(retrieval.rounds),
                }
            ),
        ]
    )
    return _AUDIT_SYSTEM, user


def audit_checkpoint(
    client: JsonChatClient,
    checkpoint: CheckpointDefinition,
    retrieval: RetrievalBundle,
) -> AuditDecision:
    if checkpoint.cp_id != retrieval.cp_id:
        raise ValueError("checkpoint and retrieval CP do not match")
    system, user = build_audit_messages(checkpoint, retrieval)
    payload = client.complete_json(
        system=system,
        user=user,
        schema=AuditDecision.model_json_schema(),
    )
    decision = AuditDecision.model_validate(payload)
    if decision.case_id != retrieval.case_id or decision.cp_id != retrieval.cp_id:
        raise ValueError("model returned the wrong case_id or cp_id")
    return decision
