"""Deterministic evidence-integrity checks that run before LLM compliance judgment."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

from freca.models import CaseRecord, EvidenceChunk


@dataclass(frozen=True)
class IntegrityFinding:
    case_id: int
    code: str
    severity: str
    message: str
    track: int | None = None
    business_verdict: None = None


@dataclass(frozen=True)
class CaseIntegrityStatus:
    case_id: int
    status: str
    finding_count: int


@dataclass(frozen=True)
class EvidenceIntegrityReport:
    findings: list[IntegrityFinding]
    case_statuses: list[CaseIntegrityStatus]
    summary: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "findings": [asdict(finding) for finding in self.findings],
            "case_statuses": [asdict(status) for status in self.case_statuses],
            "summary": self.summary,
        }


def assess_evidence_integrity(
    *,
    cases: Iterable[CaseRecord],
    chunks: Iterable[EvidenceChunk],
) -> EvidenceIntegrityReport:
    """Report data/identity defects without converting them into CP verdicts.

    BLOCKER means evidence must not silently support a compliance claim.
    REVIEW means the case has an evidence gap that must be shown to the later
    applicability and LLM stages. PASS means no deterministic defect was found.
    """

    cases = sorted(cases, key=lambda case: case.case_id)
    chunks_by_source: dict[str, list[EvidenceChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.case_id is not None:
            chunks_by_source[chunk.source_id].append(chunk)

    findings: list[IntegrityFinding] = []
    for case in cases:
        for track in sorted(set(case.missing_tracks)):
            findings.append(
                IntegrityFinding(
                    case_id=case.case_id,
                    track=track,
                    code="missing_track",
                    severity="REVIEW",
                    message=f"required evidence track {track} is absent from the case package",
                )
            )
        for source in sorted(case.sources, key=lambda item: item.track or 0):
            if "shared_re_directory" in source.flags:
                findings.append(
                    IntegrityFinding(
                        case_id=case.case_id,
                        track=source.track,
                        code="shared_re_directory",
                        severity="BLOCKER",
                        message="source belongs to a directory shared by multiple logical cases",
                    )
                )
            source_chunks = chunks_by_source.get(source.source_id, [])
            if not any(chunk.content.strip() for chunk in source_chunks):
                findings.append(
                    IntegrityFinding(
                        case_id=case.case_id,
                        track=source.track,
                        code="empty_parsed_source",
                        severity="REVIEW",
                        message="source produced no non-empty parsed evidence chunks",
                    )
                )
            source_flags = {flag for chunk in source_chunks for flag in chunk.flags}
            if "embedded_re_number_mismatch" in source_flags:
                findings.append(
                    IntegrityFinding(
                        case_id=case.case_id,
                        track=source.track,
                        code="embedded_re_number_mismatch",
                        severity="BLOCKER",
                        message="parsed evidence contains an RE number that conflicts with its case",
                    )
                )
            if "exclude_from_compliance_evidence" in source_flags:
                findings.append(
                    IntegrityFinding(
                        case_id=case.case_id,
                        track=source.track,
                        code="contaminated_evidence",
                        severity="BLOCKER",
                        message="evidence is marked as contaminated and cannot support compliance",
                    )
                )

    findings.sort(key=lambda item: (item.case_id, item.track or 0, item.code))
    by_case: dict[int, list[IntegrityFinding]] = defaultdict(list)
    for finding in findings:
        by_case[finding.case_id].append(finding)
    statuses = []
    for case in cases:
        case_findings = by_case[case.case_id]
        status = (
            "BLOCKER"
            if any(finding.severity == "BLOCKER" for finding in case_findings)
            else "REVIEW"
            if case_findings
            else "PASS"
        )
        statuses.append(
            CaseIntegrityStatus(
                case_id=case.case_id,
                status=status,
                finding_count=len(case_findings),
            )
        )
    counts = Counter(finding.severity for finding in findings)
    return EvidenceIntegrityReport(
        findings=findings,
        case_statuses=statuses,
        summary={
            "BLOCKER": counts["BLOCKER"],
            "REVIEW": counts["REVIEW"],
            "PASS": sum(status.status == "PASS" for status in statuses),
        },
    )
