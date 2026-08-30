#!/usr/bin/env python3
"""Zero-provenance and output-sensitivity report; no API calls, no state change.

WHY THIS EXISTS
---------------
The label for a coordinate is "1" only when a conjunction holds: applicability
standing, every decisive requirement meeting standard, no standing rebuttal, no
standing attack. Enumerate the ways that conjunction can fail and every one of
them yields "0": nothing retrieved, retrieved but inadmissible, coverage not
complete, no valid interpretation. Nothing in the pipeline fails towards "1".

So a run can look healthy on every structural gate while the score is being
driven by the system's own recall rather than by the audited farms. The existing
smoke summary counts labels and internal outcomes *separately*, which cannot
answer the question that matters:

    of the coordinates labelled 0, how many were rebutted on evidence,
    and how many fell back because the system could not conclude?

FOLD-POLICY-v3 already records the answer per coordinate (`fold_finality`,
`internal_outcome`, `benchmark_fallback`). Nobody aggregates it. This script
does only that, plus two label-free sensitivity measures. It adds no gate and
changes no decision; it reads the same v2 replay report the smoke summary reads.

READING THE OUTPUT
------------------
`zero_provenance.substantive_share` high
    Zeros are being produced by evidence that rebuts compliance. The system is
    adjudicating.

`zero_provenance.fallback_share` high
    Zeros are being produced because the system could not conclude. The score is
    then a measurement of pipeline recall, not of farm compliance.

`cp_constancy.cp_over_case_dominance` clearly positive
    Grouping by checking point is far more constant than grouping by case: the
    checking point is fixing the label and case evidence is not moving it. Read
    this rather than `cp_constancy.mean` on its own, which cannot separate that
    case from a run whose labels simply follow the corpus-wide mix. Negative
    means the case is doing the work; near zero means neither grouping explains
    the output.

`v1_to_v2_label_shift_rate`
    How much the second pass moves labels. High values mean the pipeline stage,
    not the source material, dominates the output.

None of these establish accuracy. They bound how much of the output is
attributable to evidence at all.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

# Classification lives in `fold_finality_v1`, checked against the fold policy's
# own source. It used to be a hand-maintained copy here and another in
# `submission_composition_gate_v1`; two copies of a list that must agree
# disagree the first time one is edited, and a finality added to the policy
# lands in neither and is silently counted as unclassified.
from fold_finality_v1 import (  # noqa: E402
    FALLBACK_FINALITIES,
    SUBSTANTIVE_FINALITIES,
    TIE_BREAK_FINALITIES,
)


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _counts(values) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(v) for v in values).items()))


def zero_provenance(rows: list[dict], label_key: str, finality_key: str,
                    outcome_key: str) -> dict[str, Any]:
    zeros = [r for r in rows if str(r.get(label_key)) == "0"]
    fin = collections.Counter(str(r.get(finality_key)) for r in zeros)

    substantive = sum(n for k, n in fin.items() if k in SUBSTANTIVE_FINALITIES)
    fallback = sum(n for k, n in fin.items() if k in FALLBACK_FINALITIES)
    # A precommitted tie-break is neither an evidence conclusion nor a failure
    # to conclude, so it gets its own count rather than inflating either share.
    # Neither tie-break yields "0" today, so this is normally zero; leaving the
    # category out would have made a future policy change look like an
    # unexplained gap instead of a known third kind.
    tie_break = sum(n for k, n in fin.items() if k in TIE_BREAK_FINALITIES)
    unclassified = len(zeros) - substantive - fallback - tie_break

    return {
        "zero_count": len(zeros),
        "by_finality": dict(sorted(fin.items())),
        "by_internal_outcome": _counts(r.get(outcome_key) for r in zeros),
        "substantive_count": substantive,
        "fallback_count": fallback,
        "tie_break_count": tie_break,
        "unclassified_count": unclassified,
        "substantive_share": _rate(substantive, len(zeros)),
        "fallback_share": _rate(fallback, len(zeros)),
        # Named so the reader cannot mistake it for an accuracy statement.
        "interpretation": (
            "fallback_share is the fraction of zeros the system produced "
            "because it could not conclude, not because it found the farm "
            "non-compliant."
        ),
    }


def label_by_finality_matrix(rows: list[dict], label_key: str,
                             finality_key: str) -> dict[str, dict[str, int]]:
    out: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for r in rows:
        out[str(r.get(label_key))][str(r.get(finality_key))] += 1
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def cp_constancy(rows: list[dict], label_key: str) -> dict[str, Any]:
    """Per checking point, the share of cases receiving that CP's modal label.

    Near 1.0 means the system is emitting a constant for that CP regardless of
    the case, i.e. case evidence is not influencing the decision.
    """
    by_cp: dict[str, list[str]] = collections.defaultdict(list)
    for r in rows:
        cp = r.get("cp_id")
        if cp is None:
            continue
        by_cp[str(cp)].append(str(r.get(label_key)))

    per_cp = {}
    for cp, labels in sorted(by_cp.items()):
        if not labels:
            continue
        modal = collections.Counter(labels).most_common(1)[0]
        per_cp[cp] = {
            "n": len(labels),
            "modal_label": modal[0],
            "constancy": modal[1] / len(labels),
        }

    vals = [v["constancy"] for v in per_cp.values()]
    mean = (sum(vals) / len(vals)) if vals else None

    # Constancy needs a comparison, and the useful one is the other grouping.
    #
    # An absolute number says little: a labeller that ignored the case but
    # reproduced the corpus label mix already scores the global majority share,
    # so with two labels in play 0.6 is close to what indifference produces.
    # Comparing against that share does not help either, because when every
    # checking point sees the same mix the two are equal by construction and
    # the difference is zero whatever is driving the labels.
    #
    # Grouping by case instead is the comparison that discriminates. If the
    # checking point determines the label, grouping by checking point is
    # constant and grouping by case is not. If the case determines it, the
    # reverse. If neither does, both sit near the global majority share.
    #
    # An earlier version reported `case_sensitivity = 1 - mean`, which invited
    # the opposite reading: it scored a run driven entirely by the corpus mix
    # as substantially case-sensitive.
    all_labels = [lab for labels in by_cp.values() for lab in labels]
    global_counts = collections.Counter(all_labels)
    global_majority = (global_counts.most_common(1)[0][1] / len(all_labels)
                       if all_labels else None)

    by_case: dict[str, list[str]] = collections.defaultdict(list)
    for r in rows:
        case = r.get("case_uid")
        if case is None:
            continue
        by_case[str(case)].append(str(r.get(label_key)))
    case_vals = [
        collections.Counter(labels).most_common(1)[0][1] / len(labels)
        for labels in by_case.values() if labels
    ]
    case_mean = (sum(case_vals) / len(case_vals)) if case_vals else None

    dominance = None
    if mean is not None and case_mean is not None:
        dominance = mean - case_mean

    return {
        "per_cp": per_cp,
        "mean": mean,
        "fully_constant_cp_count": sum(1 for v in vals if v >= 1.0),
        "labels_in_play": sorted(global_counts),
        "global_majority_share": global_majority,
        "case_grouped_constancy_mean": case_mean,
        "cp_over_case_dominance": dominance,
        "reading": (
            "Read `cp_over_case_dominance`, not `mean` alone. Clearly positive: "
            "the checking point is fixing the label and case evidence is not "
            "moving it, which is the failure this measure exists to catch. Near "
            "zero with both means close to `global_majority_share`: neither "
            "grouping explains the output. Negative: the case is doing the work. "
            "None of these is an accuracy claim."
        ),
    }


def label_shift(rows: list[dict], a_key: str, b_key: str) -> dict[str, Any]:
    paired = [r for r in rows
              if r.get(a_key) is not None and r.get(b_key) is not None]
    moved = [r for r in paired if str(r[a_key]) != str(r[b_key])]
    return {
        "compared_count": len(paired),
        "shifted_count": len(moved),
        "shift_rate": _rate(len(moved), len(paired)),
        "transitions": _counts(
            f"{r[a_key]}->{r[b_key]}" for r in moved),
    }


def blocker_frequency(rows: list[dict]) -> dict[str, Any]:
    flat: collections.Counter = collections.Counter()
    for r in rows:
        for b in (r.get("v2_final_blockers") or []):
            flat[str(b)] += 1
    term: collections.Counter = collections.Counter()
    for r in rows:
        for t in (r.get("terminal_limitations") or []):
            term[str(t)] += 1
    return {
        "final_blockers": dict(flat.most_common(20)),
        "terminal_limitations": dict(term.most_common(20)),
    }


def run_self_tests() -> None:
    """The only module here that had no self-test. Written after review."""
    import random

    # ---- zero provenance ---------------------------------------------------
    rows = (
        [{"v2_fold_label": "0", "v2_fold_finality": "EVIDENCE_REBUTTED",
          "v2_internal_outcome": "PROVEN_NON_COMPLIANT"}] * 3
        + [{"v2_fold_label": "0",
            "v2_fold_finality": "UNKNOWN_BENCHMARK_FALLBACK",
            "v2_internal_outcome": "UNKNOWN"}] * 5
        + [{"v2_fold_label": "0", "v2_fold_finality": "SOMETHING_NEW",
            "v2_internal_outcome": "UNKNOWN"}]
        + [{"v2_fold_label": "1", "v2_fold_finality": "EVIDENCE_DEMONSTRATED",
            "v2_internal_outcome": "PROVEN_COMPLIANT"}] * 4
    )
    zp = zero_provenance(rows, "v2_fold_label", "v2_fold_finality",
                         "v2_internal_outcome")
    assert zp["zero_count"] == 9, zp
    assert zp["substantive_count"] == 3 and zp["fallback_count"] == 5
    assert zp["tie_break_count"] == 0
    # An unrecognised finality must surface as unclassified rather than be
    # folded into either share, which is what the completeness check in
    # `fold_finality_v1` exists to prevent happening unnoticed.
    assert zp["unclassified_count"] == 1, zp
    assert abs(zp["fallback_share"] - 5 / 9) < 1e-9

    # A tie-break gets its own count rather than inflating either share or
    # falling through as unexplained. Constructed here because no tie-break
    # yields "0" today, so only a fixture can exercise the branch.
    tied = zero_provenance(
        [{"v2_fold_label": "0", "v2_fold_finality": "ONE_NA_TIE_PREFER_ONE",
          "v2_internal_outcome": "UNKNOWN"}],
        "v2_fold_label", "v2_fold_finality", "v2_internal_outcome")
    assert tied["tie_break_count"] == 1, tied
    assert tied["unclassified_count"] == 0, tied
    assert tied["substantive_count"] == 0 and tied["fallback_count"] == 0

    # The ones are not counted: this report is about zeros only.
    assert zero_provenance([{"v2_fold_label": "1", "v2_fold_finality": "X",
                             "v2_internal_outcome": "Y"}],
                           "v2_fold_label", "v2_fold_finality",
                           "v2_internal_outcome")["zero_count"] == 0

    # ---- constancy ---------------------------------------------------------
    # The dominance figure has to separate a checking-point-driven run from a
    # case-driven one and from one that is neither. An absolute constancy
    # cannot: all three sit near the corpus majority share.
    def grid(fn, n=200, ncp=5):
        return [{"cp_id": f"CP{i % ncp + 1}", "case_uid": f"case-{i // ncp:03d}",
                 "v2_fold_label": fn(i % ncp, i // ncp)} for i in range(n)]

    rng = random.Random(3)
    cp_driven = cp_constancy(grid(lambda cp, cs: "1" if cp < 2 else "0"),
                             "v2_fold_label")
    case_driven = cp_constancy(grid(lambda cp, cs: "1" if cs % 2 else "0"),
                               "v2_fold_label")
    neither = cp_constancy(
        grid(lambda cp, cs: rng.choice(["0", "0", "0", "1"])), "v2_fold_label")

    assert cp_driven["cp_over_case_dominance"] > 0.3, cp_driven
    assert case_driven["cp_over_case_dominance"] < -0.3, case_driven
    assert abs(neither["cp_over_case_dominance"]) < 0.15, neither
    assert cp_driven["fully_constant_cp_count"] == 5
    assert neither["labels_in_play"] == ["0", "1"]

    # ---- label shift -------------------------------------------------------
    shift = label_shift(
        [{"a": "0", "b": "1"}, {"a": "1", "b": "1"}, {"a": "0", "b": None}],
        "a", "b")
    assert shift["compared_count"] == 2 and shift["shifted_count"] == 1
    assert shift["shift_rate"] == 0.5
    assert shift["transitions"] == {"0->1": 1}

    # ---- blockers ----------------------------------------------------------
    bl = blocker_frequency([
        {"v2_final_blockers": ["A", "B"], "terminal_limitations": ["T"]},
        {"v2_final_blockers": ["A"], "terminal_limitations": []},
        {},
    ])
    assert bl["final_blockers"]["A"] == 2 and bl["final_blockers"]["B"] == 1
    assert bl["terminal_limitations"] == {"T": 1}

    # ---- empty input -------------------------------------------------------
    # Observation-only, so emptiness is reported rather than blocked, but it
    # must not raise and must not invent a rate.
    empty = zero_provenance([], "v2_fold_label", "v2_fold_finality",
                            "v2_internal_outcome")
    assert empty["zero_count"] == 0 and empty["fallback_share"] is None
    blank = cp_constancy([], "v2_fold_label")
    assert blank["mean"] is None and blank["cp_over_case_dominance"] is None
    assert label_shift([], "a", "b")["shift_rate"] is None

    print("zero_provenance_report_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-report", type=Path,
                    help="production v2 replay report containing "
                         "coordinate_summaries")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--expected-coordinates", type=int, default=None)
    # Optional thresholds. Absent by default: this tool observes, it does not gate.
    ap.add_argument("--warn-fallback-share-above", type=float, default=None)
    ap.add_argument("--warn-cp-constancy-above", type=float, default=None)
    ap.add_argument("--fail-on-warning", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests()
        return 0

    if not a.v2_report or not a.output:
        ap.error("--v2-report and --output are required unless --self-test")

    doc = json.loads(a.v2_report.read_text(encoding="utf-8"))
    rows = doc.get("coordinate_summaries") or []

    report: dict[str, Any] = {
        "schema": "freca-zero-provenance-report-v1",
        "source_report": str(a.v2_report),
        "coordinate_count": len(rows),
        "label_counts": _counts(r.get("v2_fold_label") for r in rows),
        "label_by_finality": label_by_finality_matrix(
            rows, "v2_fold_label", "v2_fold_finality"),
        "zero_provenance": zero_provenance(
            rows, "v2_fold_label", "v2_fold_finality", "v2_internal_outcome"),
        "cp_constancy": cp_constancy(rows, "v2_fold_label"),
        "v1_to_v2_label_shift": label_shift(
            rows, "v1_fold_label", "v2_fold_label"),
        "blockers": blocker_frequency(rows),
        "warning": (
            "This report bounds how much of the output is attributable to "
            "evidence. It does not estimate accuracy."
        ),
    }

    if a.expected_coordinates is not None:
        report["expected_coordinate_count"] = a.expected_coordinates
        report["coordinate_count_matches"] = (
            len(rows) == a.expected_coordinates)

    warnings: list[str] = []
    fb = report["zero_provenance"]["fallback_share"]
    if a.warn_fallback_share_above is not None and fb is not None:
        if fb > a.warn_fallback_share_above:
            warnings.append(
                f"ZERO_FALLBACK_SHARE_HIGH {fb:.3f} > "
                f"{a.warn_fallback_share_above:.3f}")
    cc = report["cp_constancy"]["mean"]
    if a.warn_cp_constancy_above is not None and cc is not None:
        if cc > a.warn_cp_constancy_above:
            warnings.append(
                f"CP_CONSTANCY_HIGH {cc:.3f} > "
                f"{a.warn_cp_constancy_above:.3f}")
    report["warnings"] = warnings

    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if warnings and a.fail_on_warning:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
