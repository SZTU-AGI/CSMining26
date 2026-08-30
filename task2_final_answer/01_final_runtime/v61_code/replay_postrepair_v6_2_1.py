#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import production_runner_v2
import semantic_replay_v6_1
import structured_witness_v6_2_1


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--repair2", type=Path, required=True)
    ap.add_argument("--run-root", type=Path, required=True,
                    help="Initial shard root containing cases/<case>/evidence_chunks.json")
    ap.add_argument("--contracts", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for line in args.targets.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case, cp = line.split()[:2]
        rr_path = args.repair2 / case / cp / "repair" / "round-1" / "after" / "requirement_result_v2.json"
        chunks_path = args.run_root / "cases" / case / "evidence_chunks.json"
        contract_path = args.contracts / f"{cp}.json"
        if not (rr_path.exists() and chunks_path.exists() and contract_path.exists()):
            rows.append({"case": case, "cp": cp, "outcome": "ERROR", "diagnosis": "MISSING_INPUT"})
            continue

        rr0 = load(rr_path)
        chunks = load(chunks_path)
        contract = load(contract_path)

        # Revalidate all persisted model alignments under V6.2 semantics first.
        replayed, replay_audit = semantic_replay_v6_1.replay_requirement_result(rr0)
        enriched, struct_audit = structured_witness_v6_2_1.enrich_requirement_result(replayed, chunks)
        root = production_runner_v2.build_layer7_v2(requirement_result=enriched, contract=contract)
        summary = semantic_replay_v6_1.summarize_layer7(root)
        outcome, fold = production_runner_v2.build_outcome_and_fold(root, contract)

        injected = struct_audit.get("injected", [])
        diagnosis = "NO_STRUCTURAL_WITNESS_INJECTED"
        if injected and summary.get("accepted_direction_count", 0):
            diagnosis = "STRUCTURAL_WITNESS_REACHED_PROOF"
        elif injected:
            diagnosis = "STRUCTURAL_WITNESS_INJECTED_BUT_PROOF_STILL_BLOCKED"
        elif cp == "CP15":
            diagnosis = "CONDITIONAL_GUARD_UNPROVEN_NA_DISABLED"

        rows.append({
            "case": case,
            "cp": cp,
            "outcome": outcome.get("common_internal_outcome"),
            "label": fold.get("label"),
            "finality": fold.get("finality"),
            "truth_bearing": summary.get("direct_truth_bearing_count", 0),
            "accepted_directions": summary.get("accepted_direction_count", 0),
            "proof_failures": summary.get("proof_failure_counts", {}),
            "structural_injected": len(injected),
            "structural_rows": injected,
            "semantic_replay_failures": replay_audit.get("validation_failure_count"),
            "diagnosis": diagnosis,
        })

    payload = {
        "schema": "freca-v6.2.1-postrepair-structural-replay-v1",
        "coordinates": len(rows),
        "outcome_counts": dict(Counter(r.get("outcome") for r in rows)),
        "diagnosis_counts": dict(Counter(r.get("diagnosis") for r in rows)),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("# V6.2.1 zero-API structural replay")
    print()
    print("Outcome counts:", payload["outcome_counts"])
    print("Diagnosis counts:", payload["diagnosis_counts"])
    print()
    print("| Case | CP | outcome | TB | accepted | injected | diagnosis |")
    print("|---|---|---|---:|---:|---:|---|")
    for r in rows:
        print(f"| {r['case']} | {r['cp']} | {r['outcome']} | {r.get('truth_bearing',0)} | {r.get('accepted_directions',0)} | {r.get('structural_injected',0)} | {r['diagnosis']} |")
        for s in r.get("structural_rows", []):
            print(f"  - {s.get('relation')} {s.get('reason_code')} :: {s.get('evidence_id')}")
    print()
    print("Saved:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
