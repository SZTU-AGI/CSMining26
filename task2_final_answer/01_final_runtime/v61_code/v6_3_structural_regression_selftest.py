#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import structured_witness_v6_3 as structural

EXPECTED = {
    ("case-023", "CP1"): "ATTACK",
    ("case-035", "CP1"): "SUPPORT",
    ("case-038", "CP1"): "SUPPORT",
    ("case-065", "CP1"): "SUPPORT",
    ("case-074", "CP1"): "SUPPORT",
    ("case-023", "CP26"): "SUPPORT",
    ("case-035", "CP26"): "ATTACK",
    ("case-038", "CP26"): "SUPPORT",
    ("case-065", "CP26"): "ATTACK",
    ("case-074", "CP26"): "ATTACK",
}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    args = ap.parse_args()
    failures = []
    passed = 0
    for (case, cp), expected in EXPECTED.items():
        rr_path = args.run_root / "tasks" / case / cp / "initial" / "requirement_result.json"
        chunks_path = args.run_root / "cases" / case / "evidence_chunks.json"
        if not rr_path.exists() or not chunks_path.exists():
            failures.append(f"MISSING {case}/{cp}")
            continue
        rr, audit = structural.enrich_requirement_result(load(rr_path), load(chunks_path))
        rows = [
            r for r in rr.get("alignments", [])
            if r.get("generator") == "STRUCTURAL_AUDIT_V6_3"
        ]
        relations = {str(r.get("relation")) for r in rows if r.get("argument_truth_bearing") is True}
        if expected not in relations:
            failures.append(f"{case}/{cp}: expected {expected}, got {sorted(relations)} audit={audit}")
        else:
            passed += 1
    if failures:
        print("V6.3 structural regression: FAIL")
        for x in failures:
            print(" -", x)
        return 1
    print(f"V6.3 structural regression: PASS ({passed}/{len(EXPECTED)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
