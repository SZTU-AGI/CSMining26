#!/usr/bin/env python3
"""Single source for FOLD-POLICY-v3 finality classification. No API calls.

WHY THIS EXISTS
---------------
Two reports read the same distinction — did the fold conclude from evidence, or
fall back because it could not conclude — and each carried its own copy of the
code list. Copies of a list that must match are a defect waiting for the first
edit: `zero_provenance_report_v1` and `submission_composition_gate_v1` would
then disagree about the same run, and nothing would say which was right.

Worse, both copies were hand-maintained against `fold_policy_v3_core`. A new
finality added there lands in neither set and is silently counted as
"unclassified", which quietly moves the substantive and fallback shares that
the whole zero-provenance argument rests on.

So the sets live here once, and `check_completeness()` derives the emitted
codes from the fold policy's own source and fails if any is unaccounted for.
The list is checked against the code that produces it rather than trusted.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

# The fold concluded: the label follows from the evidence.
SUBSTANTIVE_FINALITIES = {
    "EVIDENCE_DEMONSTRATED",
    "EVIDENCE_REBUTTED",
    "VACUOUSLY_SATISFIED",
    "RULE_FIXED_NA",
    "SAME_LABEL_ACROSS_VALID_BRANCHES",
}

# The fold could not conclude and applied a precommitted fallback. A zero here
# measures the pipeline's reach, not the farm's compliance.
FALLBACK_FINALITIES = {
    "INSUFFICIENT_EVIDENCE_BENCHMARK_FALLBACK",
    "INTERPRETATION_CONFLICT_FALLBACK",
    "UNKNOWN_BENCHMARK_FALLBACK",
    "SYSTEM_FORCED_FALLBACK",
}

# A precommitted tie-break between branches that disagreed. Neither an evidence
# conclusion nor a failure to conclude: both branches concluded and the policy
# chose between them, so counting these as either would misstate the run.
# Neither yields "0" today (PREFER_ONE gives "1", PREFER_NA gives "N/A"), so
# they cannot reach the zero-provenance shares, but they are classified rather
# than left to fall through as "unclassified".
TIE_BREAK_FINALITIES = {
    "ONE_NA_TIE_PREFER_ONE",
    "ONE_NA_TIE_PREFER_NA",
}

CLASSIFIED = SUBSTANTIVE_FINALITIES | FALLBACK_FINALITIES | TIE_BREAK_FINALITIES


def emitted_finalities(policy_path: Path | None = None) -> set[str]:
    """Every finality string `fold_policy_v3_core` can produce.

    Derived from the source rather than from a maintained list. Any string
    constant appearing anywhere in the value assigned to `finality` counts,
    which covers the plain assignments, the `finality=` keyword arguments and
    the conditional expressions the policy uses in several places. A regex over
    the file misses the conditional forms, and missing one is exactly the
    failure this function exists to prevent.
    """
    if policy_path is None:
        policy_path = Path(__file__).resolve().parent / "fold_policy_v3_core.py"
    tree = ast.parse(io.open(policy_path, encoding="utf-8").read())

    found: set[str] = set()

    def strings_in(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value.isupper() and len(sub.value) > 3:
                    found.add(sub.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "finality":
                    strings_in(node.value)
        elif isinstance(node, ast.keyword) and node.arg == "finality":
            strings_in(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant)
                        and key.value == "finality"):
                    strings_in(value)
    return found


def check_completeness(policy_path: Path | None = None) -> dict:
    """Report any finality the policy emits that this module does not classify."""
    emitted = emitted_finalities(policy_path)
    unclassified = sorted(emitted - CLASSIFIED)
    return {
        "emitted": sorted(emitted),
        "classified": sorted(CLASSIFIED),
        "unclassified": unclassified,
        "complete": not unclassified,
    }


def run_self_tests() -> None:
    report = check_completeness()

    # The whole point: a code the policy can emit but nothing classifies would
    # be counted as neither substantive nor fallback, silently shifting the
    # shares the zero-provenance reading depends on.
    assert report["complete"], (
        "unclassified finalities: " + ", ".join(report["unclassified"]))

    # The extractor must actually find things; an empty result would make the
    # completeness check pass by vacuity.
    assert len(report["emitted"]) >= 8, report["emitted"]
    for expected in ("EVIDENCE_REBUTTED", "UNKNOWN_BENCHMARK_FALLBACK",
                     "RULE_FIXED_NA", "ONE_NA_TIE_PREFER_ONE",
                     "VACUOUSLY_SATISFIED", "EVIDENCE_DEMONSTRATED",
                     "SAME_LABEL_ACROSS_VALID_BRANCHES"):
        assert expected in report["emitted"], (expected, report["emitted"])

    # The three sets must stay disjoint, or a code counted twice would make the
    # shares sum above one.
    assert not (SUBSTANTIVE_FINALITIES & FALLBACK_FINALITIES)
    assert not (SUBSTANTIVE_FINALITIES & TIE_BREAK_FINALITIES)
    assert not (FALLBACK_FINALITIES & TIE_BREAK_FINALITIES)

    # The consumers must take the sets from here rather than keep their own.
    #
    # Checked by source and by value, not by object identity: run as a script
    # this module is `__main__`, while the consumers import `fold_finality_v1`,
    # so the two module objects differ and an identity test would fail on a
    # correct file.
    here = Path(__file__).resolve().parent
    for name, expected in (
        ("zero_provenance_report_v1.py",
         ("FALLBACK_FINALITIES", "SUBSTANTIVE_FINALITIES")),
        ("submission_composition_gate_v1.py", ("FALLBACK_FINALITIES",)),
    ):
        text = io.open(here / name, encoding="utf-8").read()
        assert "from fold_finality_v1 import" in text, (
            f"{name} does not import the shared classification")
        for symbol in expected:
            assert f"{symbol} = {{" not in text, (
                f"{name} still defines its own {symbol}")

    import zero_provenance_report_v1 as zp
    import submission_composition_gate_v1 as gate
    assert zp.FALLBACK_FINALITIES == FALLBACK_FINALITIES
    assert zp.SUBSTANTIVE_FINALITIES == SUBSTANTIVE_FINALITIES
    assert gate.FALLBACK_FINALITIES == FALLBACK_FINALITIES

    print("fold_finality_v1 self-tests: PASS "
          f"({len(report['emitted'])} finalities, all classified)")


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        run_self_tests()
        raise SystemExit(0)
    print(json.dumps(check_completeness(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if check_completeness()["complete"] else 2)
