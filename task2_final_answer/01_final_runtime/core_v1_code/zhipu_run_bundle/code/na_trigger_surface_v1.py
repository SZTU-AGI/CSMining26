#!/usr/bin/env python3
"""Where would N/A land? Post-hoc, zero API, from a run made with it disabled.

WHY THIS EXISTS
---------------
The N/A countercheck ships disabled (`na_countercheck_v1`), because enabling it
moves coordinates from 0 to N/A and nothing available measures whether that is
right. Deciding by argument would be a bet either way.

It does not have to be a bet. `core_outcome_adapter_v1` spreads the root states
into the evaluation it writes, so `applicability_state`,
`non_applicability_state` and `violation_state` are already on disk for every
coordinate of every completed run. The countercheck is a pure function of those
three. The set of coordinates that *would* become N/A is therefore recoverable
from a run that was made with the feature off, at no cost and with no rerun.

WHAT IT REPORTS
---------------
How many coordinates would move, and where they land. The where is the
informative part: ten of the forty-one checking points carry a conditional
clause ("where applicable", "where required", "if applicable", "(if any)"), and
those are the points at which non-applicability is linguistically available at
all. Triggers concentrating there is evidence the mechanism is tracking
something real; triggers spread evenly across all forty-one is evidence of
overreach, since the other thirty-one make no provision for the case not
arising.

WHAT IT DOES NOT DO
-------------------
It does not say whether any individual N/A is correct. No labels are consulted
and none are trusted. It measures trigger behaviour, which is a different and
weaker thing, and the difference must survive into how the result is described.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any

import na_countercheck_v1 as ncc

CONDITIONAL = re.compile(
    r"where applicable|where required|\(if any\)|if applicable", re.I)


def conditional_cps(cp_text_path: Path | None) -> set[str]:
    """Checking points whose own wording admits the case not arising.

    Read from the checking-point text rather than hard-coded, so the set tracks
    the source. An absent file yields an empty set and the concentration
    figures are reported as unavailable rather than computed against a guess.
    """
    if not cp_text_path or not cp_text_path.is_file():
        return set()
    out = set()
    for line in cp_text_path.read_text(
            encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"(CP\d+)\s", line.strip())
        if m and CONDITIONAL.search(line):
            out.add(m.group(1))
    return out


def root_states_of(outcome_doc: dict) -> dict | None:
    """Pull the root states back out of a written evaluation bundle."""
    evaluations = outcome_doc.get("evaluations")
    if isinstance(evaluations, list) and evaluations:
        first = evaluations[0]
        if isinstance(first, dict) and "non_applicability_state" in first:
            return first
    if "non_applicability_state" in outcome_doc:
        return outcome_doc
    return None


def would_be_na(roots: dict) -> bool:
    """Apply the real countercheck, regardless of the ambient flag.

    Calls `na_countercheck_v1` rather than restating its rule. A second copy of
    the condition would drift the moment either is edited, and this report is
    what the decision to enable N/A rests on: a stale copy would describe a
    trigger surface the pipeline does not actually have.

    The flag is forced on for the duration because the question here is what
    the capability *would* do, not whether it is currently switched on.
    """
    import os
    prior = os.environ.get(ncc.ENABLE_ENV)
    os.environ[ncc.ENABLE_ENV] = "1"
    try:
        result = ncc.derive_na_countercheck(roots)
    finally:
        if prior is None:
            os.environ.pop(ncc.ENABLE_ENV, None)
        else:
            os.environ[ncc.ENABLE_ENV] = prior
    return bool(result and result.get("passed"))


def survey(run_dir: Path, cp_text: Path | None = None) -> dict[str, Any]:
    conditional = conditional_cps(cp_text)

    total = 0
    unreadable = 0
    flips: list[dict] = []
    labels = collections.Counter()
    by_cp = collections.Counter()
    by_case = collections.Counter()
    flip_from = collections.Counter()

    for meta_path in sorted(run_dir.rglob("task_meta.json")):
        task = meta_path.parent
        outcome_path = task / "core_outcome_adapter_v1.json"
        fold_path = task / "fold_decision_v3.json"
        if not (outcome_path.is_file() and fold_path.is_file()):
            continue
        total += 1

        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            fold = json.loads(fold_path.read_text(encoding="utf-8"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            unreadable += 1
            continue

        inputs = meta.get("inputs") if isinstance(meta.get("inputs"), dict) else {}
        case_uid = inputs.get("case_uid") or task.parent.name
        cp_id = inputs.get("cp_id") or task.name

        label = str(fold.get("label"))
        labels[label] += 1

        roots = root_states_of(outcome)
        if roots is None:
            unreadable += 1
            continue

        if would_be_na(roots) and label != "N/A":
            flips.append({"case_uid": case_uid, "cp_id": cp_id,
                          "current_label": label,
                          "current_finality": fold.get("finality")})
            by_cp[cp_id] += 1
            by_case[case_uid] += 1
            flip_from[label] += 1

    n_flip = len(flips)
    on_conditional = sum(v for k, v in by_cp.items() if k in conditional)
    concentration = (on_conditional / n_flip) if n_flip and conditional else None
    # Share of checking points that are conditional, i.e. what concentration a
    # mechanism triggering at random would produce. Comparing against this,
    # rather than against zero, is what makes the figure mean anything.
    baseline = (len(conditional) / 41.0) if conditional else None

    return {
        "schema": "freca-na-trigger-surface-v1",
        "run_dir": str(run_dir),
        "coordinates_read": total,
        "unreadable": unreadable,
        "current_label_counts": dict(sorted(labels.items())),
        "would_flip_to_na": n_flip,
        "would_flip_share": (n_flip / total) if total else None,
        "flip_from_label": dict(sorted(flip_from.items())),
        "conditional_cps": sorted(conditional),
        "flips_by_cp": dict(sorted(by_cp.items(),
                                   key=lambda kv: (-kv[1], kv[0]))),
        "flips_on_conditional_cps": on_conditional,
        "concentration_on_conditional": concentration,
        "random_baseline_concentration": baseline,
        "distinct_cases_touched": len(by_case),
        "sample_flips": flips[:40],
        "note": (
            "Trigger behaviour only. No label is consulted and none is "
            "trusted, so this cannot say whether any individual N/A is right. "
            "Concentration above the random baseline is evidence the mechanism "
            "tracks the conditional wording; at or below it is evidence of "
            "overreach."
        ),
    }


def run_self_tests(tmp: Path) -> None:
    import shutil
    root = tmp / "_na_surface_selftest"
    if root.exists():
        shutil.rmtree(root)

    def make(case, cp, label, app, nonapp, viol="UNKNOWN"):
        d = root / "tasks" / case / cp
        d.mkdir(parents=True)
        (d / "task_meta.json").write_text(json.dumps(
            {"inputs": {"case_uid": case, "cp_id": cp}}), encoding="utf-8")
        (d / "fold_decision_v3.json").write_text(json.dumps(
            {"label": label, "finality": "UNKNOWN_BENCHMARK_FALLBACK"}),
            encoding="utf-8")
        # Real shape: the adapter spreads the root states into the evaluation.
        (d / "core_outcome_adapter_v1.json").write_text(json.dumps(
            {"evaluations": [{"applicability_state": app,
                              "non_applicability_state": nonapp,
                              "violation_state": viol,
                              "internal_outcome": "UNKNOWN"}],
             "common_internal_outcome": "UNKNOWN"}), encoding="utf-8")

    make("case-001", "CP1", "0", "FALSE", "TRUE")      # flips
    make("case-001", "CP2", "0", "TRUE", "FALSE")      # applicable, no flip
    make("case-002", "CP6", "0", "FALSE", "TRUE")      # flips, conditional CP
    make("case-002", "CP3", "0", "FALSE", "TRUE",
         viol="TRUE")                                   # violation blocks it
    make("case-003", "CP9", "1", "UNKNOWN", "UNKNOWN")  # nothing established

    cps = tmp / "_cps.txt"
    cps.write_text("CP1 something where applicable\n"
                   "CP2 plain requirement\n"
                   "CP3 plain requirement\n"
                   "CP6 records (if any)\n"
                   "CP9 plain requirement\n", encoding="utf-8")

    rep = survey(root, cps)
    assert rep["coordinates_read"] == 5, rep
    assert rep["would_flip_to_na"] == 2, rep["sample_flips"]
    assert rep["flips_by_cp"] == {"CP1": 1, "CP6": 1}, rep["flips_by_cp"]
    assert rep["flip_from_label"] == {"0": 2}, rep["flip_from_label"]
    assert set(rep["conditional_cps"]) == {"CP1", "CP6"}
    assert rep["flips_on_conditional_cps"] == 2
    assert rep["concentration_on_conditional"] == 1.0
    assert rep["distinct_cases_touched"] == 2

    # A standing violation must block the flip, or the countercheck is not
    # being applied and this report would overstate the trigger surface.
    assert not any(f["cp_id"] == "CP3" for f in rep["sample_flips"])

    # The report must not depend on whether the feature is switched on, and
    # must leave the environment exactly as it found it either way.
    import os
    before_env = os.environ.get(ncc.ENABLE_ENV)
    assert before_env != "1", "run the self-test with the flag unset"
    assert survey(root, cps)["would_flip_to_na"] == 2
    assert os.environ.get(ncc.ENABLE_ENV) == before_env, "flag leaked"

    os.environ[ncc.ENABLE_ENV] = "1"
    try:
        assert survey(root, cps)["would_flip_to_na"] == 2
        assert os.environ.get(ncc.ENABLE_ENV) == "1", "flag clobbered"
    finally:
        del os.environ[ncc.ENABLE_ENV]

    # The trigger set must come from the real countercheck, not from a copy of
    # its rule. A copy drifts silently, and this report is what the decision to
    # enable N/A rests on.
    import inspect
    assert "ncc.derive_na_countercheck" in inspect.getsource(would_be_na), (
        "would_be_na must call the countercheck, not restate it")
    for roots, expected in (
        ({"non_applicability_state": "TRUE", "applicability_state": "FALSE",
          "violation_state": "UNKNOWN"}, True),
        ({"non_applicability_state": "TRUE", "applicability_state": "TRUE",
          "violation_state": "UNKNOWN"}, False),
        ({"non_applicability_state": "TRUE", "applicability_state": "FALSE",
          "violation_state": "TRUE"}, False),
        ({"non_applicability_state": "UNKNOWN", "applicability_state": "UNKNOWN",
          "violation_state": "UNKNOWN"}, False),
    ):
        assert would_be_na(roots) is expected, roots

    # A run with no root states must report them unreadable, not zero flips.
    d = root / "tasks" / "case-004" / "CP5"
    d.mkdir(parents=True)
    (d / "task_meta.json").write_text(json.dumps(
        {"inputs": {"case_uid": "case-004", "cp_id": "CP5"}}), encoding="utf-8")
    (d / "fold_decision_v3.json").write_text(json.dumps(
        {"label": "0"}), encoding="utf-8")
    (d / "core_outcome_adapter_v1.json").write_text(json.dumps(
        {"common_internal_outcome": "UNKNOWN"}), encoding="utf-8")
    assert survey(root, cps)["unreadable"] == 1

    shutil.rmtree(root)
    cps.unlink()
    print("na_trigger_surface_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--cp-text", type=Path, default=None,
                    help="checking-point text, for the conditional-CP set")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests(a.run_dir)
        return 0

    rep = survey(a.run_dir, a.cp_text)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
