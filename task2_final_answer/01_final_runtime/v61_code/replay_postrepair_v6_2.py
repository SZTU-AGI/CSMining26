#!/usr/bin/env python3
"""Zero-API replay of post-repair coordinates under V6.2 deterministic semantics."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import production_runner_v2
import semantic_replay_v6_1


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--repair2", type=Path, required=True)
    ap.add_argument("--contracts", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for line in args.targets.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case, cp = line.split()[:2]
        rr_path = args.repair2 / case / cp / "repair" / "round-1" / "after" / "requirement_result_v2.json"
        contract_path = args.contracts / f"{cp}.json"
        if not rr_path.exists():
            rows.append({"case": case, "cp": cp, "error": f"missing {rr_path}"})
            continue
        rr = load(rr_path)
        contract = load(contract_path)
        root, summary = semantic_replay_v6_1.replay_layer7(rr, contract)
        outcome, fold = production_runner_v2.build_outcome_and_fold(root, contract)
        proposition = " ".join(
            str(r.get("proposition_to_establish") or "")
            for r in (rr.get("evidence_requirement_plan") or {}).get("requirements", [])
        ).lower()
        tb = summary.get("direct_truth_bearing_count", 0)
        accepted = summary.get("accepted_direction_count", 0)
        if cp == "CP15" and "if carried out" in proposition and tb == 0:
            diagnosis = "CONDITIONAL_GUARD_UNPROVEN_NA_DISABLED"
        elif accepted:
            diagnosis = "DETERMINISTIC_SEMANTIC_CHANGE_REACHED_PROOF"
        else:
            diagnosis = "STILL_UNRESOLVED_AFTER_V6_2_REPLAY"
        rows.append({
            "case": case,
            "cp": cp,
            "outcome": outcome.get("common_internal_outcome"),
            "label": fold.get("label"),
            "finality": fold.get("finality"),
            "truth_bearing": tb,
            "accepted_directions": accepted,
            "accepted_requirement_states": summary.get("accepted_requirement_state_counts"),
            "proof_failures": summary.get("proof_failure_counts"),
            "diagnosis": diagnosis,
            "semantic_replay_failures": (summary.get("semantic_replay") or {}).get("validation_failure_count"),
        })

    payload = {
        "schema": "freca-v6.2-postrepair-zero-api-replay-v1",
        "coordinates": len(rows),
        "outcome_counts": dict(Counter(r.get("outcome", "ERROR") for r in rows)),
        "diagnosis_counts": dict(Counter(r.get("diagnosis", "ERROR") for r in rows)),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("# V6.2 zero-API post-repair replay")
    print()
    print("Outcome counts:", payload["outcome_counts"])
    print("Diagnosis counts:", payload["diagnosis_counts"])
    print()
    print("| Case | CP | outcome | TB | accepted dirs | diagnosis |")
    print("|---|---|---|---:|---:|---|")
    for r in rows:
        if r.get("error"):
            print(f"| {r['case']} | {r['cp']} | ERROR | 0 | 0 | {r['error']} |")
        else:
            print(f"| {r['case']} | {r['cp']} | {r['outcome']} | {r['truth_bearing']} | {r['accepted_directions']} | {r['diagnosis']} |")
    print()
    print("Saved:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
