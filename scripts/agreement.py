"""Method-agreement analysis: where do audit methods agree / disagree per CP?

Without a silver reference, cross-method consensus is the strongest available
ground-truth proxy. This script loads every method's verdicts for each
(case, track3) from ``build/experiments`` and computes:

* pairwise agreement rate between methods,
* the per-CP verdict table across methods,
* ``consensus_non_compliant`` — CPs where >=2 methods say "0" (highest-confidence
  real findings, no silver needed),
* ``blanket_approve_suspect`` — CPs a one-shot method passes ("1") while a denser
  method flags non-compliant / N-A.

Writes ``build/experiments/agreement.json`` + a text summary to stdout.

Usage::

    python scripts/agreement.py
    python scripts/agreement.py --root build/experiments --min-flaggers 2
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(WORKTREE_SRC))

from freca.state import atomic_write_json, read_json


def _load_all(root: Path) -> dict[str, dict[str, dict[str, str]]]:
    """Walk root/**/result.json -> {case_name: {method: {cp_id: verdict}}}.

    Path layout is ``<method>/case-NNN/track3-<cond>/.../result.json``; method and
    case are the first two path segments relative to ``root``.
    """
    by_case: dict[str, dict[str, dict[str, str]]] = {}
    for rj in sorted(root.rglob("result.json")):
        rel = rj.relative_to(root).parts
        if len(rel) < 2:
            continue
        # Skip intermediate artifacts: agent_audit reuses stage_audit internally
        # (cp-NNN/stage_audit/result.json) and verify_audit writes base/result.json.
        # Only the leaf cp-*/unit-*/verify-cp-* result is a final per-CP verdict.
        if rj.parent.name == "base" or rj.parent.name.startswith("stage"):
            continue
        method, case_name = rel[0], rel[1]
        data = read_json(rj)
        for v in data.get("verdicts", []):
            by_case.setdefault(case_name, {}).setdefault(method, {})[v["cp_id"]] = v["verdict"]
    return by_case


def _pairwise_agreement(methods: dict[str, dict[str, str]]) -> list[dict]:
    rows: list[dict] = []
    names = sorted(methods)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            common = set(methods[a]) & set(methods[b])
            if not common:
                continue
            same = sum(1 for c in common if methods[a][c] == methods[b][c])
            rows.append(
                {
                    "a": a,
                    "b": b,
                    "agree": same,
                    "common": len(common),
                    "agreement": round(same / len(common), 4),
                }
            )
    return rows


def _analyze_case(*, methods: dict[str, dict[str, str]], min_flaggers: int) -> dict:
    if len(methods) < 2:
        return {}
    all_cps = sorted(set().union(*[set(m) for m in methods.values()]), key=lambda c: int(c[2:]))
    per_cp: list[dict] = []
    consensus_non_compliant: list[dict] = []
    blanket_approve_suspect: list[dict] = []
    for cp in all_cps:
        row = {"cp_id": cp}
        for name in sorted(methods):
            row[name] = methods[name].get(cp)
        per_cp.append(row)
        # Consensus non-compliant: >=min_flaggers methods independently say "0".
        flaggers = [n for n in methods if methods[n].get(cp) == "0"]
        if len(flaggers) >= min_flaggers:
            consensus_non_compliant.append({"cp_id": cp, "flaggers": sorted(flaggers), "n": len(flaggers)})
        # Blanket-approve suspect: a one-shot method says "1" while any denser
        # method says "0" or "N/A".
        if any(methods[n].get(cp) == "1" for n in methods):
            dissent = {n: methods[n].get(cp) for n in methods if methods[n].get(cp) in ("0", "N/A")}
            if dissent:
                blanket_approve_suspect.append({"cp_id": cp, "dissent": dissent})
    return {
        "methods": sorted(methods),
        "cp_count": len(all_cps),
        "pairwise_agreement": _pairwise_agreement(methods),
        "per_cp": per_cp,
        "consensus_non_compliant": consensus_non_compliant,
        "blanket_approve_suspect": blanket_approve_suspect,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("build/experiments"))
    parser.add_argument("--min-flaggers", type=int, default=2, help="methods flagging '0' to count as consensus")
    parser.add_argument("--output", type=Path, default=Path("build/experiments/agreement.json"))
    args = parser.parse_args()

    by_case = _load_all(args.root)
    cases: list[dict] = []
    for case_name in sorted(by_case):
        methods = by_case[case_name]
        if len(methods) < 2:
            continue
        result = _analyze_case(methods=methods, min_flaggers=args.min_flaggers)
        if result:
            result["case"] = case_name
            cases.append(result)

    atomic_write_json(args.output, {"min_flaggers": args.min_flaggers, "cases": cases})

    for c in cases:
        print(f"\n=== case {c['case']}  (methods: {', '.join(c['methods'])}, {c['cp_count']} CPs) ===")
        print("pairwise agreement:")
        for r in c["pairwise_agreement"]:
            print(f"  {r['a']} vs {r['b']}: {r['agree']}/{r['common']} ({r['agreement']*100:.1f}%)")
        print(f"consensus non-compliant (>={args.min_flaggers} methods say 0): {len(c['consensus_non_compliant'])} CPs")
        for f in c["consensus_non_compliant"]:
            print(f"  {f['cp_id']}: {f['n']} flaggers {f['flaggers']}")
        print(f"blanket-approve suspects (one-shot=1, denser method flags 0/N-A): {len(c['blanket_approve_suspect'])} CPs")
    print(f"\nagreement analysis -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
