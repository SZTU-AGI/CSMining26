#!/usr/bin/env python3
"""Evidence-to-verdict funnel and blocker inventory for one V6 witness.

The report keeps stages separate.  Retrieval breadth is not treated as proof
breadth, and a model-produced alignment is not treated as a decisive basis.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DECISIVE = "DECISIVE"
TRUTH_RELATIONS = {"SUPPORT", "ATTACK"}


def _id(row: dict) -> str | None:
    value = row.get("evidence_id") or row.get("id")
    return str(value) if value else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _digest_ids(values: set[str]) -> str:
    import hashlib
    payload = "\n".join(sorted(values)).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def decisive_requirement_ids(requirement_result: dict) -> set[str]:
    plan = requirement_result.get("evidence_requirement_plan") or {}
    return {
        str(row.get("requirement_id"))
        for row in plan.get("requirements", []) or []
        if str(row.get("decisiveness", "")).upper() == DECISIVE
        and row.get("requirement_id")
    }


def source_chunk_ids(chunks_path: Path | None) -> set[str]:
    if chunks_path is None or not chunks_path.is_file():
        return set()
    value = json.loads(chunks_path.read_text(encoding="utf-8"))
    rows = value if isinstance(value, list) else value.get("chunks", [])
    return {str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id")}


def blocker_inventory(root: dict) -> dict[str, Any]:
    proof_codes: set[str] = set()
    for report in (root.get("proof") or {}).get("requirement_reports", []) or []:
        for direction in ("support_proof", "attack_proof"):
            proof_codes.update(
                str(code)
                for code in (report.get(direction) or {}).get("failure_codes", []) or []
            )

    limitations = (root.get("open_goals") or {}).get("terminal_limitations", []) or []
    terminal_codes = sorted({
        str(row.get("blocker_code") or row.get("reason_code"))
        for row in limitations
        if row.get("blocker_code") or row.get("reason_code")
    })
    non_executable = sorted({
        str(row.get("blocker_code") or row.get("reason_code"))
        for row in limitations
        if row.get("executable_in_current_v2_scope") is False
        and (row.get("blocker_code") or row.get("reason_code"))
    })
    return {
        "proof_failure_codes": sorted(proof_codes),
        "terminal_limitation_codes": terminal_codes,
        "non_executable_terminal_codes": non_executable,
        "terminal_limitations": limitations,
    }


def analyze_root(root: dict, *, chunks_path: Path | None = None) -> dict[str, Any]:
    rr = root.get("requirement_result") or {}
    proof = root.get("proof") or {}
    decisive_ids = decisive_requirement_ids(rr)

    source_ids = source_chunk_ids(chunks_path)
    retrieved: set[str] = set()
    for trace in rr.get("retrieval_traces", []) or []:
        for row in (trace.get("candidate_universe") or trace.get("candidates") or []):
            if isinstance(row, dict) and _id(row):
                retrieved.add(_id(row))

    aligned: set[str] = set()
    truth_bearing: set[str] = set()
    for row in rr.get("alignments", []) or []:
        if not isinstance(row, dict):
            continue
        evidence_id = _id(row)
        if evidence_id:
            aligned.add(evidence_id)
            if (
                row.get("relation") in TRUTH_RELATIONS
                and row.get("argument_admission_channel") == "DIRECT"
                and row.get("argument_truth_bearing") is True
            ):
                truth_bearing.add(evidence_id)

    accepted_basis: set[str] = set()
    accepted_decisive_directions = 0
    decisive_requirements_with_accepted_direction: set[str] = set()
    for report in proof.get("requirement_reports", []) or []:
        requirement_id = str(report.get("requirement_id"))
        if requirement_id not in decisive_ids:
            continue
        accepted_here = False
        for direction in ("support_proof", "attack_proof"):
            directional = report.get(direction) or {}
            if directional.get("accepted_direction") is not True:
                continue
            accepted_here = True
            accepted_decisive_directions += 1
            for key in ("basis_evidence_ids", "basis_artifact_ids"):
                accepted_basis.update(
                    str(value) for value in directional.get(key, []) or [] if value
                )
        if accepted_here:
            decisive_requirements_with_accepted_direction.add(requirement_id)

    available_count = len(source_ids)
    denominator = available_count or len(retrieved)
    result = {
        "schema": "freca-v6-witness-funnel-v1",
        "source_chunks_measurable": bool(source_ids),
        "source_chunk_count": available_count if source_ids else None,
        "retrieved_candidate_count": len(retrieved),
        "aligned_source_count": len(aligned),
        "direct_truth_bearing_source_count": len(truth_bearing),
        "accepted_decisive_basis_count": len(accepted_basis),
        "decisive_requirement_count": len(decisive_ids),
        "decisive_requirements_with_accepted_direction_count": len(
            decisive_requirements_with_accepted_direction
        ),
        "accepted_decisive_direction_count": accepted_decisive_directions,
        "retrieved_share_of_source": _rate(len(retrieved), denominator),
        "aligned_share_of_source": _rate(len(aligned), denominator),
        "truth_bearing_share_of_source": _rate(len(truth_bearing), denominator),
        "accepted_basis_share_of_source": _rate(len(accepted_basis), denominator),
        "stage_id_digests": {
            "retrieved": _digest_ids(retrieved),
            "aligned": _digest_ids(aligned),
            "truth_bearing": _digest_ids(truth_bearing),
            "accepted_decisive_basis": _digest_ids(accepted_basis),
        },
        "retrieved_id_sample": sorted(retrieved)[:20],
        "aligned_id_sample": sorted(aligned)[:20],
        "truth_bearing_id_sample": sorted(truth_bearing)[:20],
        "accepted_decisive_basis_ids": sorted(accepted_basis),
        "decisive_requirement_ids": sorted(decisive_ids),
        "blockers": blocker_inventory(root),
        "interpretation": (
            "Counts are stage-specific diagnostics, not accuracy estimates. "
            "Only an accepted direction on a decisive requirement can support "
            "a substantive witness outcome."
        ),
    }
    return result


def run_self_tests() -> None:
    root = {
        "requirement_result": {
            "evidence_requirement_plan": {"requirements": [
                {"requirement_id": "ER1", "decisiveness": "DECISIVE"}
            ]},
            "retrieval_traces": [{"candidate_universe": [
                {"evidence_id": "e1"}, {"evidence_id": "e2"}
            ]}],
            "alignments": [
                {"evidence_id": "e1", "relation": "SUPPORT",
                 "argument_admission_channel": "DIRECT",
                 "argument_truth_bearing": True},
                {"evidence_id": "e2", "relation": "UNKNOWN"},
            ],
        },
        "proof": {"requirement_reports": [{
            "requirement_id": "ER1",
            "support_proof": {"accepted_direction": True,
                              "basis_evidence_ids": ["e1"]},
            "attack_proof": {"accepted_direction": False,
                             "failure_codes": ["NO_DIRECT_SUPPORT_BASIS"]},
        }]},
        "open_goals": {"terminal_limitations": [{
            "blocker_code": "TEMPORAL_REQUIREMENT_UNRESOLVED",
            "executable_in_current_v2_scope": False,
        }]},
    }
    report = analyze_root(root)
    assert report["retrieved_candidate_count"] == 2
    assert report["aligned_source_count"] == 2
    assert report["direct_truth_bearing_source_count"] == 1
    assert report["accepted_decisive_basis_count"] == 1
    assert report["decisive_requirements_with_accepted_direction_count"] == 1
    assert report["blockers"]["non_executable_terminal_codes"] == [
        "TEMPORAL_REQUIREMENT_UNRESOLVED"
    ]
    print("witness_funnel_v6 self-tests: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--chunks", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        run_self_tests()
        return 0
    if not args.root:
        parser.error("--root is required unless --self-test")
    root = json.loads(args.root.read_text(encoding="utf-8"))
    report = analyze_root(root, chunks_path=args.chunks)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
