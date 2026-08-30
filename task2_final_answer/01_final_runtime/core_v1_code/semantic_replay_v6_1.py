#!/usr/bin/env python3
"""Zero-API semantic replay for V6 -> V6.1 proof-closure changes.

This module deliberately reuses the *model relation* and grounded quote already
persisted in ``requirement_result.json``.  It does not call an API and it does
not invent new evidence.  It only recomputes deterministic layers whose
semantics changed in V6.1:

- RequirementPredicateProfile / EvidenceNature compatibility;
- argument admission (DIRECT vs CONDITIONAL vs REJECTED);
- temporal and information-reliability assessments;
- purpose-specific directional coverage; and
- proof-standard acceptance.

A fresh production rerun is still required to measure the effect of the new
predicate-aware retrieval universe.  This replay is a migration/diagnostic tool
for already-completed V6 runs.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import evidence_reasoning_v2
import production_runner_v2


REPLAY_SCHEMA = "freca-v6.1-zero-api-semantic-replay-v1"


def _requirement_index(requirement_result: dict) -> dict[str, dict]:
    return {
        str(row["requirement_id"]): row
        for row in (requirement_result.get("evidence_requirement_plan") or {}).get(
            "requirements", []
        )
    }


def _raw_alignment_from_persisted(row: dict) -> dict:
    relation = str(row.get("model_relation") or row.get("relation") or "AMBIGUOUS")
    if relation not in evidence_reasoning_v2.ALIGNMENT_RELATIONS:
        relation = "AMBIGUOUS"
    return {
        "requirement_id": str(row.get("requirement_id")),
        "evidence_id": str(
            row.get("alignment_evidence_id")
            or (
                str(row.get("evidence_id"))
                + "#"
                + str(row.get("fact_candidate_id") or "legacy-fact")
            )
        ),
        "relation": relation,
        "exact_quote": str(
            row.get("exact_quote")
            or (row.get("fact_candidate") or {}).get("quote")
            or ""
        ),
        "reason_code": str(row.get("reason_code") or "SEMANTIC_REPLAY"),
        "reason": str(row.get("reason") or "Replayed from persisted model relation."),
        "alignment_method": str(row.get("alignment_method") or "MODEL"),
    }


def _pair_from_persisted(row: dict, requirement: dict) -> dict:
    fact = copy.deepcopy(row.get("fact_candidate") or {})
    quote = str(row.get("exact_quote") or fact.get("quote") or "")
    if not fact:
        fact = {
            "fact_candidate_id": str(row.get("fact_candidate_id") or "legacy-fact"),
            "parent_evidence_id": str(row.get("evidence_id") or "legacy-evidence"),
            "source_id": str(row.get("evidence_id") or "legacy-evidence").split(":", 1)[0],
            "quote": quote,
            "quote_start": 0,
            "quote_end": len(quote),
            "grounding_valid": bool(quote),
        }
    fact.setdefault("quote", quote)
    fact.setdefault("grounding_valid", True)

    parent_id = str(row.get("evidence_id") or fact.get("parent_evidence_id") or "")
    alignment_id = str(
        row.get("alignment_evidence_id")
        or parent_id + "#" + str(row.get("fact_candidate_id") or fact.get("fact_candidate_id"))
    )
    return {
        "requirement": requirement,
        "evidence_id": alignment_id,
        "parent_evidence_id": parent_id,
        "fact_candidate_id": str(row.get("fact_candidate_id") or fact.get("fact_candidate_id")),
        "fact_candidate": fact,
        "evidence_text": str(fact.get("quote") or quote),
        "parent_evidence_text": str(fact.get("quote") or quote),
        "retrieval_need_ids": list(row.get("retrieval_need_ids", []) or []),
        "identity_relation_to_case": row.get("identity_relation_to_case"),
        "identity_use_decision": row.get("identity_use_decision", "ADMIT_DIRECT"),
        "identity_decisive_proof_eligible": bool(
            row.get("identity_decisive_proof_eligible", True)
        ),
        "identity_reason_code": row.get("identity_reason_code"),
    }


def _sync_fact_typing(row: dict) -> dict:
    """Keep FactCandidate's cached typing consistent with replayed row typing."""
    result = copy.deepcopy(row)
    fact = copy.deepcopy(result.get("fact_candidate") or {})
    nature = copy.deepcopy(result.get("evidence_nature") or {})
    if nature:
        fact["evidence_nature"] = nature
        assertion = nature.get("assertion_mode") or {}
        if assertion.get("modality"):
            fact["modality"] = assertion["modality"]
        if assertion.get("speech_act"):
            fact["speech_act"] = assertion["speech_act"]
        if assertion.get("inference_scope"):
            fact["assertion_mode"] = assertion["inference_scope"]
    result["fact_candidate"] = fact
    return result


def replay_requirement_result(requirement_result: dict) -> tuple[dict, dict]:
    """Revalidate persisted alignments under V6.1 deterministic semantics."""
    source = copy.deepcopy(requirement_result)
    requirements = _requirement_index(source)
    replayed: list[dict] = []
    failures: list[dict] = []

    for index, old in enumerate(source.get("alignments", []) or []):
        rid = str(old.get("requirement_id"))
        requirement = requirements.get(rid)
        if requirement is None:
            failures.append({
                "alignment_index": index,
                "requirement_id": rid,
                "code": "REQUIREMENT_NOT_FOUND",
            })
            replayed.append(copy.deepcopy(old))
            continue
        try:
            new_row = evidence_reasoning_v2.validate_alignment(
                _raw_alignment_from_persisted(old),
                _pair_from_persisted(old, requirement),
            )
            replayed.append(_sync_fact_typing(new_row))
        except Exception as exc:  # diagnostic utility must preserve failed legacy rows
            failed = copy.deepcopy(old)
            failed["argument_admission_channel"] = "REJECTED"
            failed["accepted_for_argument"] = False
            failed["argument_truth_bearing"] = False
            failed["accepted_for_alignment"] = False
            failed["accepted_for_proof"] = False
            failed["semantic_replay_failure"] = type(exc).__name__ + ": " + str(exc)
            codes = list(failed.get("rejection_codes", []) or [])
            if "SEMANTIC_REPLAY_VALIDATION_FAILED" not in codes:
                codes.append("SEMANTIC_REPLAY_VALIDATION_FAILED")
            failed["rejection_codes"] = codes
            replayed.append(failed)
            failures.append({
                "alignment_index": index,
                "requirement_id": rid,
                "alignment_evidence_id": old.get("alignment_evidence_id"),
                "code": "SEMANTIC_REPLAY_VALIDATION_FAILED",
                "detail": type(exc).__name__ + ": " + str(exc),
            })

    source["alignments"] = replayed
    source["semantic_replay"] = {
        "schema": REPLAY_SCHEMA,
        "source_alignment_count": len(requirement_result.get("alignments", []) or []),
        "replayed_alignment_count": len(replayed),
        "validation_failure_count": len(failures),
        "validation_failures": failures,
        "api_calls": 0,
        "new_evidence_added": False,
        "model_relations_reused": True,
        "grounded_quotes_reused": True,
    }
    return source, source["semantic_replay"]


def summarize_layer7(root: dict) -> dict:
    proof = root.get("proof") or {}
    reports = proof.get("requirement_reports", []) or []
    state_counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    accepted_directions = 0
    for row in reports:
        accepted = str(row.get("accepted_state") or "UNKNOWN")
        raw = str(row.get("raw_state") or "UNKNOWN")
        state_counts[accepted] = state_counts.get(accepted, 0) + 1
        raw_counts[raw] = raw_counts.get(raw, 0) + 1
        for code in row.get("failure_codes", []) or []:
            failure_counts[str(code)] = failure_counts.get(str(code), 0) + 1
        for direction in ("support_proof", "attack_proof"):
            if (row.get(direction) or {}).get("accepted_direction") is True:
                accepted_directions += 1
    rr = root.get("requirement_result") or {}
    return {
        "cp_id": rr.get("cp_id"),
        "case_id": rr.get("case_id"),
        "case_uid": rr.get("case_uid"),
        "alignment_count": len(rr.get("alignments", []) or []),
        "direct_truth_bearing_count": sum(
            1 for row in rr.get("alignments", []) or []
            if row.get("argument_admission_channel") == "DIRECT"
            and row.get("argument_truth_bearing") is True
        ),
        "conditional_alignment_count": sum(
            1 for row in rr.get("alignments", []) or []
            if row.get("argument_admission_channel") == "CONDITIONAL"
        ),
        "raw_requirement_state_counts": raw_counts,
        "accepted_requirement_state_counts": state_counts,
        "accepted_direction_count": accepted_directions,
        "proof_failure_counts": failure_counts,
        "proof_coverage_complete": bool((root.get("coverage") or {}).get("proof_coverage_complete")),
        "temporal_unresolved_count": sum(
            1
            for row in (root.get("gate_applicability") or {}).get("temporal_classifications", []) or []
            if row.get("state") == "TEMPORAL_UNRESOLVED"
        ),
    }


def replay_layer7(requirement_result: dict, contract: dict) -> tuple[dict, dict]:
    replayed, audit = replay_requirement_result(requirement_result)
    root = production_runner_v2.build_layer7_v2(
        requirement_result=replayed,
        contract=contract,
    )
    summary = summarize_layer7(root)
    summary["semantic_replay"] = audit
    return root, summary


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirement-result", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New directory; the original initial artifacts are never modified.",
    )
    args = parser.parse_args()

    rr = _load_json(args.requirement_result)
    contract = _load_json(args.contract)
    root, summary = replay_layer7(rr, contract)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Any] = {
        "requirement_result_v6_1.json": root["requirement_result"],
        "coverage_v6_1.json": root["coverage"],
        "proof_standard_v6_1.json": root["proof"],
        "proof_gate_applicability_v6_1.json": root["gate_applicability"],
        "open_goals_v6_1.json": root["open_goals"],
        "semantic_replay_summary.json": summary,
    }
    for name, payload in outputs.items():
        (args.output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
