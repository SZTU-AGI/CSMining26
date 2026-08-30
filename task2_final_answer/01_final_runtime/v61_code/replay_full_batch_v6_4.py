#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import production_runner_v2
import proof_standard_v1_1 as proof_v1
import semantic_replay_v6_1
import structured_witness_v6_3


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(value, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source_rr(initial_root: Path, repaired_root: Path | None, case: str, cp: str) -> tuple[Path, str]:
    if repaired_root is not None:
        candidates = [
            repaired_root / case / cp / "repair" / "round-1" / "after" / "requirement_result_v2.json",
            repaired_root / case / cp / "initial" / "requirement_result_v2.json",
        ]
        for p in candidates:
            if p.exists():
                return p, "REPAIRED_LATEST"
    p = initial_root / "tasks" / case / cp / "initial" / "requirement_result.json"
    return p, "INITIAL"


def _alignment_lookup(rr: dict) -> dict[str, dict]:
    out = {}
    for row in rr.get("alignments", []) or []:
        for key in {
            proof_v1.alignment_id(row),
            str(row.get("fact_candidate_id") or ""),
            str(row.get("evidence_id") or ""),
            str(row.get("alignment_evidence_id") or ""),
        }:
            if key:
                out[key] = row
    return out


def _basis_rows(rr: dict, proof: dict, report: dict, direction: str) -> list[dict]:
    lookup = _alignment_lookup(rr)
    pr = report.get("support_proof" if direction == "SUPPORT" else "attack_proof") or {}
    rows = []
    for aid in pr.get("basis_artifact_ids", []) or []:
        if str(aid) in lookup:
            rows.append(lookup[str(aid)])
    # Evidence id fallback for old serialized bundles whose alignment id field
    # differed from the current proof helper.
    if not rows:
        evidences = {str(x) for x in (pr.get("basis_evidence_ids", []) or [])}
        rows = [r for r in rr.get("alignments", []) or [] if str(r.get("evidence_id")) in evidences]
    return rows


def _compact_basis(row: dict) -> dict:
    quote = str(row.get("exact_quote") or row.get("quote") or row.get("semantic_context") or "").strip()
    parent = str(row.get("parent_evidence_id") or row.get("evidence_id") or "")
    return {
        "relation": row.get("relation"),
        "evidence_id": row.get("evidence_id"),
        "parent_evidence_id": parent,
        "source_id": row.get("source_id") or parent.split(":", 1)[0],
        "fact_candidate_id": row.get("fact_candidate_id"),
        "exact_quote": quote,
        "reason_code": row.get("reason_code"),
        "evidence_nature": row.get("evidence_nature"),
        "argument_admission_channel": row.get("argument_admission_channel"),
        "structural": bool(row.get("structural_witness_key")),
        "structural_witness_key": row.get("structural_witness_key"),
        "temporal_relation": row.get("temporal_relation"),
        "reliability_status": (row.get("information_reliability") or {}).get("status"),
    }


def _conflict_audit(case: str, cp: str, root: dict, outcome: str) -> dict | None:
    if outcome != "CONFLICTING":
        return None
    rr = root["requirement_result"]
    proof = root["proof"]
    reqs = []
    flags = []
    for report in proof.get("requirement_reports", []) or []:
        support_ok = (report.get("support_proof") or {}).get("accepted_direction") is True
        attack_ok = (report.get("attack_proof") or {}).get("accepted_direction") is True
        if not (support_ok or attack_ok):
            continue
        support = [_compact_basis(x) for x in _basis_rows(rr, proof, report, "SUPPORT")] if support_ok else []
        attack = [_compact_basis(x) for x in _basis_rows(rr, proof, report, "ATTACK")] if attack_ok else []
        support_parents = {x["parent_evidence_id"] for x in support}
        attack_parents = {x["parent_evidence_id"] for x in attack}
        support_quotes = {re.sub(r"\s+", " ", x["exact_quote"].lower()).strip() for x in support}
        attack_quotes = {re.sub(r"\s+", " ", x["exact_quote"].lower()).strip() for x in attack}
        local_flags = []
        if support_ok and attack_ok:
            local_flags.append("ACCEPTED_BOTH_DIRECTIONS")
        if support_parents & attack_parents:
            local_flags.append("SUPPORT_ATTACK_SHARE_PARENT_EVIDENCE")
        if support_quotes & attack_quotes:
            local_flags.append("SAME_QUOTE_IN_BOTH_DIRECTIONS")
        if cp == "CP1":
            for a in attack:
                q = a["exact_quote"].lower()
                if "domestic" in q and "export" not in q:
                    local_flags.append("CP1_ATTACK_LOOKS_DOMESTIC_ONLY_REVIEW")
                    break
        if any(x["structural"] for x in support + attack):
            local_flags.append("STRUCTURAL_WITNESS_PARTICIPATES")
        if (
            support_ok and attack_ok
            and cp in {"CP12", "CP26", "CP35"}
            and any(x["structural"] for x in support)
            and any(x["structural"] for x in attack)
        ):
            local_flags.append("STRUCTURAL_BOTH_DIRECTIONS_PRESERVE_CONFLICT")
        flags.extend(local_flags)
        reqs.append({
            "requirement_id": report.get("requirement_id"),
            "raw_state": report.get("raw_state"),
            "accepted_state": report.get("accepted_state"),
            "support_accepted": support_ok,
            "attack_accepted": attack_ok,
            "support_basis": support,
            "attack_basis": attack,
            "flags": sorted(set(local_flags)),
        })
    return {
        "case": case,
        "cp": cp,
        "flags": sorted(set(flags)),
        "requirements": reqs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True, help="Initial shard root")
    ap.add_argument("--contracts", type=Path, required=True)
    ap.add_argument("--repaired-root", type=Path, default=None,
                    help="Optional latest continuation root; used when a coordinate result exists")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--markdown", type=Path, required=True)
    ap.add_argument("--conflicts", type=Path, required=True)
    args = ap.parse_args()

    task_paths = sorted(args.run_root.glob("tasks/case-*/CP*/initial/requirement_result.json"))
    rows = []
    conflicts = []
    for initial_path in task_paths:
        # Path shape is:
        #   <run-root>/tasks/<case>/<cp>/initial/requirement_result.json
        # Avoid brittle parent indexing (parents[0]=initial, [1]=CP,
        # [2]=case, [3]=tasks). Parse relative to run-root instead.
        rel = initial_path.relative_to(args.run_root)
        parts = rel.parts
        if len(parts) < 5 or parts[0] != "tasks":
            rows.append({
                "case": None,
                "cp": None,
                "outcome": "ERROR",
                "diagnosis": "INVALID_TASK_PATH",
                "input_path": str(initial_path),
            })
            continue
        case = parts[1]
        cp = parts[2]
        contract_path = args.contracts / f"{cp}.json"
        chunks_path = args.run_root / "cases" / case / "evidence_chunks.json"
        rr_path, source_state = _source_rr(args.run_root, args.repaired_root, case, cp)
        if not (rr_path.exists() and chunks_path.exists() and contract_path.exists()):
            rows.append({"case": case, "cp": cp, "outcome": "ERROR", "diagnosis": "MISSING_INPUT"})
            continue

        rr0 = load(rr_path)
        chunks = load(chunks_path)
        contract = load(contract_path)
        replayed, replay_audit = semantic_replay_v6_1.replay_requirement_result(rr0)
        enriched, struct_audit = structured_witness_v6_3.enrich_requirement_result(replayed, chunks)
        root = production_runner_v2.build_layer7_v2(requirement_result=enriched, contract=contract)
        summary = semantic_replay_v6_1.summarize_layer7(root)
        outcome_bundle, fold = production_runner_v2.build_outcome_and_fold(root, contract)
        outcome = outcome_bundle.get("common_internal_outcome")

        diagnosis = "SUBSTANTIVE"
        if outcome == "UNKNOWN" and cp == "CP15":
            diagnosis = "CONDITIONAL_GUARD_UNPROVEN_NA_DISABLED"
        elif outcome == "UNKNOWN":
            diagnosis = "UNRESOLVED_AFTER_V6_4"
        elif outcome == "CONFLICTING":
            diagnosis = "CONFLICT_PRESERVED_FOR_PARACONSISTENT_AUDIT"

        row = {
            "case": case,
            "cp": cp,
            "source_state": source_state,
            "outcome": outcome,
            "label": fold.get("label"),
            "finality": fold.get("finality"),
            "truth_bearing": summary.get("direct_truth_bearing_count", 0),
            "accepted_directions": summary.get("accepted_direction_count", 0),
            "proof_failures": summary.get("proof_failure_counts", {}),
            "structural_injected": struct_audit.get("injected_count", 0),
            "structural_rows": struct_audit.get("injected", []),
            "semantic_replay_failures": replay_audit.get("validation_failure_count", 0),
            "diagnosis": diagnosis,
        }
        rows.append(row)
        audit = _conflict_audit(case, cp, root, outcome)
        if audit:
            conflicts.append(audit)

    payload = {
        "schema": "freca-v6.4-full-batch-zero-api-replay-v1",
        "coordinate_count": len(rows),
        "outcome_counts": dict(Counter(r.get("outcome") for r in rows)),
        "label_counts": dict(Counter(str(r.get("label")) for r in rows)),
        "diagnosis_counts": dict(Counter(r.get("diagnosis") for r in rows)),
        "source_state_counts": dict(Counter(r.get("source_state") for r in rows)),
        "structural_witness_total": sum(int(r.get("structural_injected", 0)) for r in rows),
        "semantic_replay_validation_failures": sum(int(r.get("semantic_replay_failures", 0)) for r in rows),
        "conflicting_coordinate_count": len(conflicts),
        "rows": rows,
    }
    save(payload, args.output)
    save({
        "schema": "freca-v6.4-conflict-audit-v1",
        "conflicting_coordinate_count": len(conflicts),
        "flag_counts": dict(Counter(flag for c in conflicts for flag in c.get("flags", []))),
        "coordinates": conflicts,
    }, args.conflicts)

    md = []
    md += ["# V6.4 full-batch zero-API replay", ""]
    md += [f"- Coordinates: {len(rows)}"]
    md += [f"- Outcomes: `{payload['outcome_counts']}`"]
    md += [f"- Labels: `{payload['label_counts']}`"]
    md += [f"- Structural witnesses injected: {payload['structural_witness_total']}"]
    md += [f"- Semantic replay validation failures: {payload['semantic_replay_validation_failures']}"]
    md += [f"- Conflicting coordinates requiring audit: {len(conflicts)}", ""]
    md += ["| Case | CP | source | outcome | TB | accepted | structural | diagnosis |",
           "|---|---|---|---|---:|---:|---:|---|"]
    for r in rows:
        md.append(
            f"| {r['case']} | {r['cp']} | {r.get('source_state')} | {r.get('outcome')} | "
            f"{r.get('truth_bearing',0)} | {r.get('accepted_directions',0)} | "
            f"{r.get('structural_injected',0)} | {r.get('diagnosis')} |"
        )
    md += ["", "## Conflicting coordinates", ""]
    if not conflicts:
        md.append("None.")
    for c in conflicts:
        md.append(f"### {c['case']} / {c['cp']}")
        md.append(f"- Flags: `{c.get('flags', [])}`")
        for req in c.get("requirements", []):
            if not (req.get("support_accepted") or req.get("attack_accepted")):
                continue
            md.append(
                f"- `{req.get('requirement_id')}` accepted={req.get('accepted_state')} "
                f"flags=`{req.get('flags', [])}`"
            )
            for direction, key in (("SUPPORT", "support_basis"), ("ATTACK", "attack_basis")):
                for b in req.get(key, []):
                    quote = str(b.get("exact_quote") or "").replace("\n", " ")[:220]
                    md.append(
                        f"  - {direction} `{b.get('reason_code')}` `{b.get('parent_evidence_id')}` :: {quote}"
                    )
        md.append("")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(md) + "\n", encoding="utf-8")

    print("# V6.4 full-batch zero-API replay")
    print("Outcome counts:", payload["outcome_counts"])
    print("Label counts:", payload["label_counts"])
    print("Structural witnesses:", payload["structural_witness_total"])
    print("Conflicting coordinates:", len(conflicts))
    print("Saved:", args.output)
    print("Markdown:", args.markdown)
    print("Conflict audit:", args.conflicts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
