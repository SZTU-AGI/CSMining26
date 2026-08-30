#!/usr/bin/env python3
"""Run-level invariants: determinism and N/A reachability. No API calls here.

WHY THIS EXISTS
---------------
Two invariants the design depends on had no implementation.

1. DETERMINISM. The competition disqualifies submissions that cannot be
   reproduced from the declared prompt and model. The existing replay compares
   the short-circuit path against the full path, which is a different question:
   it establishes that two *paths* agree, not that the same input twice gives
   the same label. Model calls are stochastic unless decoding is pinned, so
   without this check the qualification requirement is untested.

2. N/A REACHABILITY. §9.2 downgrades a zero N/A *count* to a warning and makes
   *reachability* the hard gate, on the reasoning that a run may legitimately
   produce no N/A while the path is intact, but a broken path is a defect
   whatever the count. Nothing implemented either half.

The distinction matters: the composition gate can only see counts. A run that
emits no N/A because no case warranted one, and a run that emits no N/A because
the branch is dead, look identical from the output alone.

USAGE
-----
Both checks need to execute the pipeline, so the caller injects `rerun`. The
module never imports the runner itself, which keeps it importable and testable
on a machine with no API access.

    from run_invariants_v1 import check_determinism, check_na_reachability
    rep = check_determinism(cases, rerun, repeats=2)
"""

from __future__ import annotations

import argparse
from typing import Any, Callable


def check_determinism(cases: list[dict], rerun: Callable[[dict], dict], *,
                      repeats: int = 2,
                      min_agreement: float = 1.0) -> dict[str, Any]:
    """Run the same input `repeats` times and compare labels.

    `min_agreement` defaults to 1.0: with decoding pinned there is no principled
    reason to accept disagreement, and a lower bar would quietly license the
    irreproducibility the competition disqualifies for. Callers who have not yet
    pinned decoding can lower it, but the report records the value used so the
    concession is visible rather than assumed.
    """
    rows = []
    for case in cases:
        labels = []
        for _ in range(max(2, repeats)):
            out = rerun(case) or {}
            labels.append(str(out.get("label")))
        agree = len(set(labels)) == 1
        rows.append({"case_uid": case.get("case_uid"), "labels": labels,
                     "agree": agree})

    n = len(rows)
    agreed = sum(1 for r in rows if r["agree"])
    rate = (agreed / n) if n else None
    failures = [r for r in rows if not r["agree"]]

    return {
        "schema": "freca-determinism-canary-v1",
        "case_count": n,
        "repeats": max(2, repeats),
        "agreement_rate": rate,
        "min_agreement_required": min_agreement,
        "disagreeing": failures[:20],
        "determinism_pass": (rate is not None and rate >= min_agreement),
        "note": ("Agreement establishes that the declared prompt and model "
                 "reproduce the same labels. It says nothing about whether "
                 "those labels are right."),
    }


def check_na_reachability(fixtures: list[dict],
                          rerun: Callable[[dict], dict]) -> dict[str, Any]:
    """Every fixture built to warrant N/A must actually yield N/A.

    A fixture that does not is `N/A_PATH_BROKEN`: the branch cannot be reached,
    so a production run of zero N/A carries no information. This is the hard
    half of §9.2; the count half stays a warning in the composition gate.
    """
    rows = []
    for fx in fixtures:
        out = rerun(fx) or {}
        label = str(out.get("label"))
        rows.append({"fixture_id": fx.get("case_uid"), "label": label,
                     "reached": label == "N/A"})

    broken = [r for r in rows if not r["reached"]]
    return {
        "schema": "freca-na-reachability-v1",
        "fixture_count": len(rows),
        "reached_count": len(rows) - len(broken),
        "broken": broken,
        "na_reachability_pass": (bool(rows) and not broken),
        "reason_code": None if (rows and not broken) else "N/A_PATH_BROKEN",
        "note": ("Reachability is the gate; the production N/A count is only "
                 "a warning. Zero N/A with a reachable path is permitted; "
                 "zero N/A with a broken path is not interpretable."),
    }


def summarise(determinism: dict | None,
              na: dict | None) -> dict[str, Any]:
    """Combine the two invariant reports.

    A check that was not run is a hard failure, not a silent pass. Skipping
    both used to leave `hard` empty and report `run_invariants_pass: True`,
    so a caller that forgot to run either one would be told everything held.
    Absent evidence and evidence of absence are different, and only the second
    is a pass.
    """
    hard: list[str] = []
    skipped: list[str] = []

    if determinism is None:
        skipped.append("determinism")
        hard.append("DETERMINISM_NOT_RUN")
    elif not determinism.get("determinism_pass"):
        hard.append(
            "DETERMINISM_BELOW_THRESHOLD "
            f"{determinism.get('agreement_rate')}")

    if na is None:
        skipped.append("na_reachability")
        hard.append("NA_REACHABILITY_NOT_RUN")
    elif not na.get("na_reachability_pass"):
        hard.append("N/A_PATH_BROKEN")

    return {
        "schema": "freca-run-invariants-v1",
        "determinism": determinism,
        "na_reachability": na,
        "skipped_checks": skipped,
        "hard_failures": hard,
        "run_invariants_pass": not hard,
    }


def run_self_tests() -> None:
    cases = [{"case_uid": f"c{i}"} for i in range(4)]

    stable = lambda c: {"label": "1"}
    rep = check_determinism(cases, stable, repeats=3)
    assert rep["determinism_pass"] and rep["agreement_rate"] == 1.0

    flip = {"n": 0}

    def flaky(c):
        flip["n"] += 1
        return {"label": "1" if flip["n"] % 2 else "0"}

    rep = check_determinism(cases, flaky, repeats=2)
    assert not rep["determinism_pass"], "flaky system not caught"
    assert rep["agreement_rate"] == 0.0

    fx = [{"case_uid": "na_fixture_1"}, {"case_uid": "na_fixture_2"}]
    ok = check_na_reachability(fx, lambda c: {"label": "N/A"})
    assert ok["na_reachability_pass"] and ok["reason_code"] is None

    bad = check_na_reachability(fx, lambda c: {"label": "0"})
    assert not bad["na_reachability_pass"]
    assert bad["reason_code"] == "N/A_PATH_BROKEN"

    # An empty fixture list must not pass by vacuity.
    empty = check_na_reachability([], lambda c: {"label": "N/A"})
    assert not empty["na_reachability_pass"], "empty fixtures passed vacuously"

    combined = summarise(rep, bad)
    assert not combined["run_invariants_pass"]
    assert "N/A_PATH_BROKEN" in combined["hard_failures"]

    # A check that was never run must not read as a check that passed.
    assert not summarise(None, None)["run_invariants_pass"]
    assert summarise(None, None)["skipped_checks"] == [
        "determinism", "na_reachability"]
    assert not summarise(None, ok)["run_invariants_pass"]
    assert not summarise(
        check_determinism(cases, stable, repeats=2), None
    )["run_invariants_pass"]

    # Both present and both passing is the only combination that passes.
    both = summarise(check_determinism(cases, stable, repeats=2), ok)
    assert both["run_invariants_pass"], both["hard_failures"]
    assert both["skipped_checks"] == []

    print("run_invariants_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        run_self_tests()
        return 0
    ap.error("this module is driven by import; the run host injects `rerun`")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
