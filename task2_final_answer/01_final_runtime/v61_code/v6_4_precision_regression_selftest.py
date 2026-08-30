#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import evidence_nature_v1
import semantic_replay_v6_1
import structured_witness_v6_3
import proof_gate_applicability_v2

CASES = ["case-023", "case-035", "case-038", "case-065", "case-074"]

EXPECTED = {
    ("case-023", "CP12"): {("ER1", "SUPPORT"), ("ER2", "SUPPORT")},
    ("case-035", "CP12"): {("ER1", "ATTACK"), ("ER2", "SUPPORT"), ("ER2", "ATTACK")},
    ("case-038", "CP12"): {("ER1", "SUPPORT"), ("ER2", "SUPPORT"), ("ER2", "ATTACK")},
    ("case-065", "CP12"): {("ER1", "SUPPORT"), ("ER2", "SUPPORT"), ("ER2", "ATTACK")},
    ("case-074", "CP12"): {("ER1", "SUPPORT"), ("ER2", "SUPPORT"), ("ER2", "ATTACK")},
    ("case-023", "CP35"): {("ER1", "SUPPORT")},
    ("case-035", "CP35"): {("ER1", "SUPPORT"), ("ER1", "ATTACK")},
    ("case-038", "CP35"): {("ER1", "SUPPORT"), ("ER1", "ATTACK")},
    ("case-065", "CP35"): {("ER1", "SUPPORT"), ("ER1", "ATTACK")},
    ("case-074", "CP35"): {("ER1", "SUPPORT"), ("ER1", "ATTACK")},
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_precision_rules() -> list[str]:
    failures = []
    design_req = {"proposition_to_establish": "The establishment is designed and constructed to minimise contamination, infestation and pest harbourage during export operations."}
    local = evidence_nature_v1.assess_alignment_compatibility(
        design_req,
        "Steel frame, concrete floor, roller-door access, 1,200 t capacity",
        "SUPPORT",
        "CORROBORATION_ONLY",
        fact_context="Steel frame, concrete floor, roller-door access, 1,200 t capacity",
    )
    if local["compatibility_decision"] != "CORROBORATIVE":
        failures.append("local physical feature must not be holistic DIRECT design support")

    risk_req = {"proposition_to_establish": "The risk of contamination or infestation of plants or plant products is maintained at an acceptable level through compliance with applicable phytosanitary requirements."}
    generic = evidence_nature_v1.assess_alignment_compatibility(
        risk_req,
        "All areas assessed as compliant with the hygiene standards required for export.",
        "SUPPORT",
        "CORROBORATION_ONLY",
        fact_context="All areas assessed as compliant with the hygiene standards required for export.",
    )
    if generic["compatibility_decision"] == "DIRECT":
        failures.append("generic hygiene compliance must not be DIRECT acceptable-risk support")

    adverse = evidence_nature_v1.assess_alignment_compatibility(
        risk_req,
        "Trend review identifies repeated rodent activity in receival zones with delayed escalation follow-up.",
        "ATTACK",
        "AMBIGUOUS",
        fact_context="Trend review identifies repeated rodent activity in receival zones with delayed escalation follow-up.",
    )
    if adverse["compatibility_decision"] != "DIRECT":
        failures.append("repeated rodent activity with delayed escalation must be DIRECT risk attack")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    args = ap.parse_args()

    failures = check_precision_rules()
    checks = 3

    for (case, cp), expected in EXPECTED.items():
        rr_path = args.run_root / "tasks" / case / cp / "initial" / "requirement_result.json"
        chunks_path = args.run_root / "cases" / case / "evidence_chunks.json"
        if not rr_path.exists() or not chunks_path.exists():
            failures.append(f"missing input for {case}/{cp}")
            continue
        rr, _ = semantic_replay_v6_1.replay_requirement_result(load(rr_path))
        enriched, audit = structured_witness_v6_3.enrich_requirement_result(rr, load(chunks_path))
        structural_rows = [
            row for row in enriched.get("alignments", []) or []
            if str(row.get("generator") or "").startswith("STRUCTURAL_AUDIT_V6_4")
            and row.get("argument_truth_bearing") is True
        ]
        got = {
            (str(row.get("requirement_id")), str(row.get("relation")))
            for row in structural_rows
        }
        checks += 1
        if not expected.issubset(got):
            failures.append(f"{case}/{cp}: expected {sorted(expected)} subset, got {sorted(got)}")
        req_index = {
            str(x.get("requirement_id")): x
            for x in (enriched.get("evidence_requirement_plan") or {}).get("requirements", []) or []
        }
        for row in structural_rows:
            checks += 1
            req = req_index.get(str(row.get("requirement_id"))) or {}
            rel = proof_gate_applicability_v2.build_information_reliability_assessment(
                row=row, requirement=req, requirement_result=enriched
            )
            if rel.get("schema") != "freca-information-reliability-assessment-v2" or rel.get("status") != "PASS":
                failures.append(
                    f"{case}/{cp}:{row.get('requirement_id')}:{row.get('relation')}: "
                    f"structural reliability must pass through the common gate, got {rel}"
                )

    if failures:
        print("V6.4 precision regression: FAIL")
        for item in failures:
            print(" -", item)
        return 1
    print(f"V6.4 precision regression: PASS ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
