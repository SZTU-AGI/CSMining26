from __future__ import annotations

import json

from freca.audit import audit_checkpoint
from freca.llm import JsonChatClient
from freca.models import (
    ArbitrationResult,
    AuditDecision,
    CheckpointDefinition,
    CitationValidationResult,
    ConsistencyFinding,
    ContentKind,
    RetrievalBundle,
    VerificationResult,
    VerificationStatus,
    Verdict,
)


def validate_citations(
    decision: AuditDecision,
    retrieval: RetrievalBundle,
) -> CitationValidationResult:
    errors: list[str] = []
    policy = {hit.chunk.chunk_id: hit.chunk for hit in retrieval.policy_hits}
    evidence = {hit.chunk.chunk_id: hit.chunk for hit in retrieval.evidence_hits}
    for citation in decision.policy_citations:
        if citation not in policy:
            errors.append(f"policy citation does not exist in retrieval context: {citation}")
    evidence_citations = decision.supporting_evidence + decision.contrary_evidence
    for citation in evidence_citations:
        chunk = evidence.get(citation)
        if chunk is None:
            errors.append(f"case evidence citation does not exist in retrieval context: {citation}")
            continue
        if chunk.case_id != decision.case_id:
            errors.append(
                f"case evidence citation belongs to case {chunk.case_id}, not {decision.case_id}: "
                f"{citation}"
            )
        if chunk.content_kind == ContentKind.IMAGE_DESCRIPTION and not chunk.derived_from:
            errors.append(f"image description is not linked to an original image: {citation}")
        if (
            "exclude_from_compliance_evidence" in chunk.flags
            and citation in decision.supporting_evidence
        ):
            errors.append(
                f"case evidence citation is contaminated and cannot support compliance: {citation}"
            )
    if not decision.policy_citations:
        errors.append("decision has no policy citation")
    if not evidence_citations:
        errors.append("decision has no case evidence citation")
    if decision.verdict == Verdict.COMPLIANT and not decision.supporting_evidence:
        errors.append("compliant decision has no supporting evidence")
    if decision.case_id != retrieval.case_id or decision.cp_id != retrieval.cp_id:
        errors.append("decision identity does not match retrieval identity")
    checked = decision.policy_citations + evidence_citations
    return CitationValidationResult(
        case_id=decision.case_id,
        cp_id=decision.cp_id,
        passed=not errors,
        errors=errors,
        checked_citations=checked,
    )


_VERIFY_SYSTEM = """Independently verify one audit decision against only the supplied official checking point, policy chunks, and case evidence chunks.
Check that every stated regulatory requirement is supported by policy, every factual claim is supported by its cited case chunk, obvious contrary evidence is not ignored, and N/A is supported by applicability.
Return PASS, FAIL, or UNCERTAIN with concise issues. Do not rewrite the verdict."""


def verify_decision(
    client: JsonChatClient,
    checkpoint: CheckpointDefinition,
    retrieval: RetrievalBundle,
    decision: AuditDecision,
) -> VerificationResult:
    context = {
        "checkpoint": checkpoint.model_dump(),
        "decision": decision.model_dump(mode="json"),
        "policy_chunks": [
            {"chunk_id": hit.chunk.chunk_id, "content": hit.chunk.content}
            for hit in retrieval.policy_hits
        ],
        "evidence_chunks": [
            {
                "chunk_id": hit.chunk.chunk_id,
                "case_id": hit.chunk.case_id,
                "content": hit.chunk.content,
            }
            for hit in retrieval.evidence_hits
        ],
    }
    payload = client.complete_json(
        system=_VERIFY_SYSTEM,
        user=json.dumps(context, ensure_ascii=False),
        schema=VerificationResult.model_json_schema(),
    )
    result = VerificationResult.model_validate(payload)
    if result.case_id != decision.case_id or result.cp_id != decision.cp_id:
        raise ValueError("verifier returned the wrong case_id or cp_id")
    return result


def find_consistency_issues(decisions: list[AuditDecision]) -> list[ConsistencyFinding]:
    if not decisions:
        return []
    case_ids = {decision.case_id for decision in decisions}
    if len(case_ids) != 1:
        raise ValueError("consistency checks must be scoped to one case")
    facts: dict[str, dict[str, str]] = {}
    for decision in decisions:
        for key, value in decision.shared_facts.items():
            facts.setdefault(key, {})[decision.cp_id] = value
    findings = []
    for key, values in sorted(facts.items()):
        normalized = {value.strip().casefold() for value in values.values()}
        if len(normalized) > 1:
            findings.append(
                ConsistencyFinding(
                    case_id=decisions[0].case_id,
                    fact_key=key,
                    cp_ids=sorted(values),
                    values=values,
                )
            )
    return findings


def find_signature_consistency_issues(
    decisions: list[AuditDecision],
    case,
) -> list[ConsistencyFinding]:
    """挑跨 Track 业务字段冲突(同 case 内 Track 自相矛盾)。

    触发:

    * audit 决策 ``shared_facts`` 含不同值;
    * manifest ``expected_establishment_name`` 与某 decision 的 ``_establishment_name`` 不一致。

    不硬改 verdict,只产一致性告警让仲裁复判。
    """
    findings: list[ConsistencyFinding] = []
    if not decisions:
        return findings
    fact_buckets: dict[str, dict[str, str]] = {}
    for decision in decisions:
        for key, value in decision.shared_facts.items():
            fact_buckets.setdefault(key, {})[decision.cp_id] = value
    for key, values in sorted(fact_buckets.items()):
        normalized = {value.strip().casefold() for value in values.values()}
        if len(normalized) > 1:
            findings.append(
                ConsistencyFinding(
                    case_id=decisions[0].case_id,
                    fact_key=key,
                    cp_ids=sorted(values),
                    values=values,
                )
            )
    if case is not None and case.expected_establishment_name:
        expected = case.expected_establishment_name.casefold()
        for decision in decisions:
            actual = decision.shared_facts.get("_establishment_name", "").strip().casefold()
            if actual and actual != expected:
                findings.append(
                    ConsistencyFinding(
                        case_id=case.case_id,
                        fact_key="_establishment_name_vs_case",
                        cp_ids=[decision.cp_id],
                        values={
                            decision.cp_id: actual,
                            "expected_from_track_1": case.expected_establishment_name,
                        },
                    )
                )
    return findings


def should_arbitrate(
    decision: AuditDecision,
    citation_validation: CitationValidationResult,
    verification: VerificationResult,
    consistency_findings: list[ConsistencyFinding],
    *,
    confidence_threshold: float = 0.65,
) -> bool:
    return any(
        [
            decision.confidence < confidence_threshold,
            not decision.retrieval_complete,
            bool(decision.review_flags),
            not citation_validation.passed,
            verification.status != VerificationStatus.PASS,
            bool(consistency_findings),
        ]
    )


def arbitrate_checkpoint(
    client: JsonChatClient,
    checkpoint: CheckpointDefinition,
    retrieval: RetrievalBundle,
    first_decision: AuditDecision,
) -> ArbitrationResult:
    second = audit_checkpoint(client, checkpoint, retrieval)
    agreement = (
        second.verdict == first_decision.verdict
        and second.applicability == first_decision.applicability
    )
    return ArbitrationResult(
        case_id=first_decision.case_id,
        cp_id=first_decision.cp_id,
        first_verdict=first_decision.verdict,
        second_decision=second,
        agreement=agreement,
        resolution="ACCEPT_AGREEMENT" if agreement else "REVIEW_DISAGREEMENT",
    )
