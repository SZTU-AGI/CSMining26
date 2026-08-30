#!/usr/bin/env python3
"""N/A reachability: is the third label reachable at all? Offline, no API.

WHY THIS EXISTS
---------------
V5 §9.2 makes N/A *reachability* a hard gate and downgrades a zero N/A *count*
to a warning, on the reasoning that a run may legitimately produce no N/A while
the path is intact, but a dead path is a defect whatever the count. Neither
half was implemented, and the count-only view cannot tell the two apart.

It was worth implementing, because the path was dead. `build_fold_gate_report`
takes an `na_countercheck` and leaves `na_countercheck_passed` False without
one; `production_runner_v1.build_outcome_and_fold` never passed one. A
coordinate whose upstream reasoning concluded non-applicability therefore fell
through to UNKNOWN and folded to "0":

    without countercheck   UNKNOWN                 -> "0"   UNKNOWN_BENCHMARK_FALLBACK
    with countercheck      PROVEN_NOT_APPLICABLE   -> "N/A" RULE_FIXED_NA

One of the three permitted labels could not be produced, so every genuinely
non-applicable coordinate was answered 0.

WHAT THIS CHECKS
----------------
The decision chain, not a mock of it: the real `build_fold_gate_report`,
`aggregate_internal_outcome` and `fold_branch`, driven across the root-state
combinations that matter, plus the truth table of the countercheck the runner
now supplies. It is deliberately offline: reachability is a property of the
decision logic, and burning model calls to observe it would measure the same
thing at a cost.

It does NOT establish that N/A is ever the *correct* answer for a real
coordinate, only that the system is capable of reaching it.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import core_outcome_adapter_v1 as adapter
import fold_policy_v3_core as fold
import na_countercheck_v1 as ncc


def _requirement_result() -> dict:
    return {
        "cp_id": "CP1", "case_id": "case-check",
        "evidence_requirement_plan": {"requirements": [
            {"requirement_id": "ER1", "decisiveness": "DECISIVE"}]},
    }


def _proof(support: bool = True, attack: bool = False) -> dict:
    return {"bundle_sha256": "na-check", "requirement_reports": [
        {"requirement_id": "ER1", "statement_id": "stmt-er1",
         "accepted_state": "TRUE", "decisiveness": "DECISIVE",
         "support_proof": {"accepted_direction": support,
                           "basis_evidence_ids": ["f.docx:P1"] if support else []},
         "attack_proof": {"accepted_direction": attack,
                          "basis_evidence_ids": ["f.docx:P2"] if attack else []}}]}


def _roots(applicable: str, non_applicable: str,
           violation: str = "UNKNOWN") -> dict:
    return {
        "applicability_state": applicable,
        "non_applicability_state": non_applicable,
        "satisfaction_state": "UNKNOWN",
        "violation_state": violation,
        "satisfaction_four_valued_state": "UNKNOWN",
        "accepted_argument_state": "UNDECIDED",
        "unresolved_reason_codes": [],
    }


def decide(roots: dict, *, with_countercheck: bool) -> dict[str, Any]:
    """Run the real gate, aggregation and fold for one root state.

    `with_countercheck` asks what the system is *capable* of, so it enables the
    flag for the duration rather than reading the ambient environment. The
    capability and the default are separate questions: the countercheck ships
    disabled, and this check still has to be able to prove the branch is alive.
    """
    countercheck = None
    if with_countercheck:
        import os
        prior = os.environ.get(ncc.ENABLE_ENV)
        os.environ[ncc.ENABLE_ENV] = "1"
        try:
            countercheck = ncc.derive_na_countercheck(roots)
        finally:
            if prior is None:
                del os.environ[ncc.ENABLE_ENV]
            else:
                os.environ[ncc.ENABLE_ENV] = prior
    gates = adapter.build_fold_gate_report(
        requirement_result=_requirement_result(),
        proof_bundle=_proof(),
        root_states=roots,
        na_countercheck=countercheck)
    outcome, reasons = adapter.aggregate_internal_outcome(
        root_states=roots, fold_gate_report=gates)
    try:
        decision = fold.fold_branch({"internal_outcome": outcome,
                                     "fold_gate_report": gates})
        label, finality = decision["label"], decision["finality"]
        error = None
    except Exception as exc:
        label, finality, error = None, None, f"{type(exc).__name__}: {exc}"
    return {"countercheck": countercheck, "internal_outcome": outcome,
            "reasons": reasons, "label": label, "finality": finality,
            "error": error}


def report() -> dict[str, Any]:
    scenarios = {
        "not_applicable": _roots("FALSE", "TRUE"),
        "not_applicable_but_violation_standing": _roots("FALSE", "TRUE",
                                                        violation="TRUE"),
        "applicable": _roots("TRUE", "FALSE"),
        "both_standing": _roots("TRUE", "TRUE"),
        "neither_established": _roots("UNKNOWN", "UNKNOWN"),
    }

    rows = {}
    for name, roots in scenarios.items():
        rows[name] = {
            "without_countercheck": decide(roots, with_countercheck=False),
            "with_countercheck": decide(roots, with_countercheck=True),
        }

    reachable = rows["not_applicable"]["with_countercheck"]["label"] == "N/A"
    previously = rows["not_applicable"]["without_countercheck"]["label"]
    import os
    enabled = os.environ.get(ncc.ENABLE_ENV) == "1"

    return {
        "schema": "freca-na-reachability-check-v1",
        "scenarios": rows,
        "na_reachable_as_capability": reachable,
        "na_countercheck_enabled": enabled,
        "na_reachable_by_default": bool(reachable and enabled),
        "label_when_countercheck_withheld": previously,
        "reason_code": None if reachable else "N/A_PATH_BROKEN",
        "note": ("Two separate questions. The capability is a property of the "
                 "decision logic, established offline. Whether it is enabled "
                 "is a decision: the 615 human-labelled cells contain no N/A, "
                 "so every 0 that becomes N/A is a certain loss against them "
                 "while the gain rests on a rate nothing here can measure. "
                 "Neither answer establishes that N/A is correct for any real "
                 "coordinate."),
    }


def _cc(roots):
    """Truth table of the countercheck itself, with the capability enabled."""
    import os
    prior = os.environ.get(ncc.ENABLE_ENV)
    os.environ[ncc.ENABLE_ENV] = "1"
    try:
        return ncc.derive_na_countercheck(roots)
    finally:
        if prior is None:
            del os.environ[ncc.ENABLE_ENV]
        else:
            os.environ[ncc.ENABLE_ENV] = prior


def run_self_tests() -> None:
    # ---- the default -------------------------------------------------------
    # The capability ships disabled, so an unset environment must withhold it.
    import os
    assert os.environ.get(ncc.ENABLE_ENV) != "1", (
        "run the self-test with the flag unset")
    assert ncc.derive_na_countercheck(_roots("FALSE", "TRUE")) is None, (
        "the countercheck must be off unless explicitly enabled")
    assert report()["na_reachable_by_default"] is False

    # ---- the countercheck's truth table ------------------------------------
    cc = _cc(_roots("FALSE", "TRUE"))
    assert cc and cc["passed"] is True, cc
    assert cc["activity_counterevidence_standing"] is False

    # A standing violation is counter-evidence that the activity occurs, so
    # non-applicability must not be waved through.
    cc = _cc(_roots("FALSE", "TRUE", violation="TRUE"))
    assert cc and cc["passed"] is False, cc
    assert cc["activity_counterevidence_standing"] is True

    # Applicability standing as well is a contradiction, not an N/A.
    assert _cc(_roots("TRUE", "TRUE"))["passed"] is False
    # Nothing established is not the same as established non-applicability.
    assert _cc(_roots("UNKNOWN", "UNKNOWN"))["passed"] is False
    assert _cc(None) is None
    assert _cc({}) is None

    # ---- the chain ---------------------------------------------------------
    rep = report()
    assert rep["na_reachable_as_capability"], rep["scenarios"]["not_applicable"]

    na = rep["scenarios"]["not_applicable"]
    assert na["with_countercheck"]["label"] == "N/A"
    assert na["with_countercheck"]["finality"] == "RULE_FIXED_NA"
    # The regression this guards: without the countercheck the same coordinate
    # is answered 0, so a passing reachability check must actually depend on
    # the countercheck being supplied.
    assert na["without_countercheck"]["label"] == "0", na["without_countercheck"]

    # An applicable coordinate must not become N/A by supplying a countercheck.
    app = rep["scenarios"]["applicable"]["with_countercheck"]
    assert app["label"] != "N/A", app

    # Non-applicability with a standing violation must not reach N/A either.
    viol = rep["scenarios"]["not_applicable_but_violation_standing"]
    assert viol["with_countercheck"]["label"] != "N/A", viol

    # ---- the wiring, executed when the runner can be imported --------------
    #
    # Stronger than the source check below: it drives the real
    # `build_outcome_and_fold`, so it establishes that the countercheck is
    # reached at run time rather than merely mentioned in the file. Skipped
    # when the reference core is absent, which is the normal state of the code
    # archive, so the source check stays as the fallback rather than the only
    # evidence.
    try:
        import production_runner_v1 as runner
    except Exception:
        runner = None

    if runner is not None:
        contract = {"contract": {
            "cp_id": "CP1",
            "applicability": {"op": "CONST", "value": False},
            "non_applicability": {"op": "CONST", "value": True},
            "satisfaction": {"op": "ATOM", "atom_id": "A1"},
            "atoms": [{"atom_id": "A1"}]}}
        rr = {"case_id": "case-check", "cp_id": "CP1",
              "evidence_requirement_plan": {"requirements": [
                  {"requirement_id": "ER1", "decisiveness": "DECISIVE",
                   "atom_id": "A1"}]}}
        root = {"requirement_result": rr, "proof": _proof()}

        import os as _os
        prior = _os.environ.get(ncc.ENABLE_ENV)
        try:
            _os.environ.pop(ncc.ENABLE_ENV, None)
            _, fold_off = runner.build_outcome_and_fold(
                root=root, contract=contract)
            _os.environ[ncc.ENABLE_ENV] = "1"
            _, fold_on = runner.build_outcome_and_fold(
                root=root, contract=contract)
        finally:
            if prior is None:
                _os.environ.pop(ncc.ENABLE_ENV, None)
            else:
                _os.environ[ncc.ENABLE_ENV] = prior

        assert fold_off.get("label") == "0", fold_off
        assert fold_on.get("label") == "N/A", fold_on
        assert fold_on.get("finality") == "RULE_FIXED_NA", fold_on

    # ---- the wiring, by source when the runner cannot be imported ----------
    # Proving the branch reachable is worthless if the runner does not call it.
    # The runner cannot be imported without the reference core, which the code
    # archive deliberately omits, so the call site is checked in the source.
    from pathlib import Path
    src = Path(__file__).resolve().parent / "production_runner_v1.py"
    text = src.read_text(encoding="utf-8")
    assert "from na_countercheck_v1 import derive_na_countercheck" in text, (
        "production_runner_v1 no longer imports the countercheck")
    assert "na_countercheck=derive_na_countercheck(root_states)" in text, (
        "build_outcome_and_fold no longer supplies the countercheck")
    assert "root_states = adapter.derive_root_states(" in text, (
        "build_outcome_and_fold no longer derives the root states it passes")

    # ---- enabling it -------------------------------------------------------
    os.environ[ncc.ENABLE_ENV] = "1"
    try:
        assert ncc.derive_na_countercheck(_roots("FALSE", "TRUE"))["passed"]
        assert report()["na_reachable_by_default"] is True
    finally:
        del os.environ[ncc.ENABLE_ENV]

    print("na_reachability_check_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests()
        return 0

    rep = report()
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.output:
        from pathlib import Path
        p = Path(a.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if rep["na_reachable_as_capability"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
