#!/usr/bin/env python3
"""Submission composition canaries (V5 §9.1); no API calls, no state change.

WHY THIS EXISTS
---------------
V5 §9.1 specifies run-level canaries as hard gates. None of them existed in
code: `majority_label_share`, single-class output, all-three-labels, and the
N/A handling of §9.2 were all specified on paper only. Until this gate runs,
nothing prevents a degenerate output from being exported and submitted.

The gate matters because of the direction of the pipeline's error. A label is
"1" only if a conjunction holds; every way that conjunction can fail yields
"0". A run can therefore pass every structural check while collapsing towards a
single class, and the existing smoke summary would still report GO, because it
tests execution health and *nonzero* semantic reachability, not composition.

WHAT IS AND IS NOT ENFORCED
---------------------------
HARD (blocks, exit 2)
    coordinate_count           matches --expected-coordinates when supplied
    single_class_output        forbidden
    majority_label_share       must stay below --max-majority-share

WARN (reported, never blocks)
    all_three_labels_absent    §9.2 downgrades this from gate to warning
    na_zero_production         §9.2: zero N/A is a warning, not a failure;
                               the hard gate belongs to N/A *reachability*,
                               which is a fixture concern, not a count concern
    zero_fallback_share_high   see zero_provenance_report_v1.py
    cp_constancy_high          per-CP near-constant output

NOT COMPUTABLE HERE
    synthetic_production_decisions == 0
        The replay summary carries no synthetic-origin marker. This gate
        reports the field as UNVERIFIABLE rather than silently passing it.
        Passing an unverifiable invariant is worse than declaring it.

The gate reads the same v2 replay report as the smoke summary, or a plain
{case_uid, cp_id, label} record list, so it can run before or after export.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

VALID_LABELS = {"1", "0", "N/A"}

# Shared with `zero_provenance_report_v1` through `fold_finality_v1`, whose
# self-test derives the emitted codes from the fold policy and fails if any is
# unclassified. A local copy here would drift from that one the first time
# either is edited, and the two reports would then disagree about the same run.
from fold_finality_v1 import FALLBACK_FINALITIES  # noqa: E402
import zero_provenance_report_v1 as zp  # noqa: E402


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def extract_cells(doc: dict) -> list[dict]:
    """Accept either a v2 replay report or a bare cell list."""
    rows = doc.get("coordinate_summaries")
    if rows is None:
        rows = doc.get("cells") or []
    out = []
    for r in rows:
        label = r.get("v2_fold_label")
        if label is None:
            label = r.get("label")
        out.append({
            "case_uid": r.get("case_uid"),
            "cp_id": r.get("cp_id"),
            "label": None if label is None else str(label),
            "finality": r.get("v2_fold_finality") or r.get("finality"),
        })
    return out


def evaluate(cells: list[dict], *, expected_coordinates: int | None,
             max_majority_share: float, warn_fallback_share_above: float | None,
             warn_cp_constancy_above: float | None) -> dict[str, Any]:
    n = len(cells)
    labels = [c["label"] for c in cells]
    counts = collections.Counter(labels)
    present = {k for k in counts if k in VALID_LABELS}

    majority_share = _rate(max(counts.values()), n) if n else None
    single_class = len(present) <= 1 and n > 0

    invalid = sorted({str(k) for k in counts if k not in VALID_LABELS})

    # Per-CP constancy: share of cases receiving that CP's modal label.
    #
    # Computed by the same function `zero_provenance_report_v1` reports, not by
    # a second implementation here. The gate warns on this number while that
    # report explains it, so two implementations drifting apart would have the
    # gate and the report disagreeing about one run with nothing to say which
    # was right.
    cp_constancy_mean = zp.cp_constancy(cells, "label")["mean"]

    zeros = [c for c in cells if c["label"] == "0"]
    fb = sum(1 for c in zeros if str(c["finality"]) in FALLBACK_FINALITIES)
    fallback_share = _rate(fb, len(zeros))

    hard: list[str] = []
    warn: list[str] = []

    # No coordinates at all is the most degenerate output there is, and it must
    # not pass by vacuity. Every other test here is written over the cells, so
    # with none of them `single_class` is False, `majority_share` is None and
    # nothing fires: a run that crashed before producing anything would be
    # reported as "not degenerate". The count check below cannot be relied on
    # to catch it either, since `expected_coordinates` is optional.
    if n == 0:
        hard.append("NO_COORDINATES: the report contains no cells to check")

    if expected_coordinates is not None and n != expected_coordinates:
        hard.append(
            f"COORDINATE_COUNT_MISMATCH expected={expected_coordinates} got={n}")
    if invalid:
        hard.append(f"INVALID_LABEL_VALUES {invalid}")
    if single_class:
        hard.append(f"SINGLE_CLASS_OUTPUT label={sorted(present)}")
    if majority_share is not None and majority_share >= max_majority_share:
        hard.append(
            f"MAJORITY_LABEL_SHARE_TOO_HIGH {majority_share:.4f} "
            f">= {max_majority_share:.4f}")

    if present and present != VALID_LABELS:
        warn.append(f"ALL_THREE_LABELS_ABSENT present={sorted(present)}")
    if counts.get("N/A", 0) == 0:
        warn.append("N/A_ZERO_PRODUCTION_WARNING")
    if (warn_fallback_share_above is not None and fallback_share is not None
            and fallback_share > warn_fallback_share_above):
        warn.append(
            f"ZERO_FALLBACK_SHARE_HIGH {fallback_share:.4f} > "
            f"{warn_fallback_share_above:.4f}")
    if (warn_cp_constancy_above is not None and cp_constancy_mean is not None
            and cp_constancy_mean > warn_cp_constancy_above):
        warn.append(
            f"CP_CONSTANCY_HIGH {cp_constancy_mean:.4f} > "
            f"{warn_cp_constancy_above:.4f}")

    # Which failures a small sample could produce on its own.
    #
    # A smoke run of a few cases can cross the majority-share threshold by
    # sampling alone, and blocking on that would cost time without evidence.
    # Nothing else here is a sampling artifact: no coordinates, a count that
    # does not match, or a label outside the permitted set are wrong at any
    # size. Callers that relax the gate on smoke runs must relax only the first
    # kind, so the distinction is drawn here rather than left to each caller to
    # guess from the message text.
    SAMPLING_SENSITIVE = ("MAJORITY_LABEL_SHARE_TOO_HIGH", "SINGLE_CLASS_OUTPUT")
    structural = [h for h in hard
                  if not h.startswith(SAMPLING_SENSITIVE)]

    return {
        "schema": "freca-submission-composition-gate-v1",
        "coordinate_count": n,
        "label_counts": dict(sorted(counts.items(), key=lambda kv: str(kv[0]))),
        "majority_label_share": majority_share,
        "single_class_output": single_class,
        "cp_constancy_mean": cp_constancy_mean,
        "zero_count": len(zeros),
        "zero_fallback_share": fallback_share,
        "synthetic_production_decisions": "UNVERIFIABLE_FROM_THIS_INPUT",
        "hard_failures": hard,
        "structural_failures": structural,
        "sampling_sensitive_only": bool(hard) and not structural,
        "warnings": warn,
        "composition_gate_pass": not hard,
        "note": (
            "Passing establishes that the output is not degenerate. It does "
            "not establish accuracy."
        ),
    }


def run_self_tests() -> None:
    def cells(spec):
        out = []
        for i, (cp, labs) in enumerate(spec.items()):
            for j, l in enumerate(labs):
                out.append({"case_uid": f"c{j}", "cp_id": cp,
                            "label": l, "finality": "EVIDENCE_REBUTTED"})
        return out

    # all zeros -> single class must block
    r = evaluate(cells({"CP1": ["0", "0"], "CP2": ["0", "0"]}),
                 expected_coordinates=None, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert not r["composition_gate_pass"]
    assert any(x.startswith("SINGLE_CLASS_OUTPUT") for x in r["hard_failures"])

    # 96% zeros -> majority share must block even though two classes appear
    spec = {"CP%d" % i: ["0"] * 25 for i in range(1, 5)}
    spec["CP1"] = ["1"] + ["0"] * 24
    r = evaluate(cells(spec), expected_coordinates=None,
                 max_majority_share=0.95, warn_fallback_share_above=None,
                 warn_cp_constancy_above=None)
    assert not r["composition_gate_pass"]
    assert any(x.startswith("MAJORITY_LABEL_SHARE_TOO_HIGH")
               for x in r["hard_failures"])

    # balanced -> passes hard gates, still warns about absent N/A
    r = evaluate(cells({"CP1": ["1", "0"], "CP2": ["0", "1"]}),
                 expected_coordinates=4, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert r["composition_gate_pass"], r["hard_failures"]
    assert "N/A_ZERO_PRODUCTION_WARNING" in r["warnings"]

    # invalid label value must block
    r = evaluate([{"case_uid": "c", "cp_id": "CP1", "label": "TRUE",
                   "finality": None}],
                 expected_coordinates=None, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert any(x.startswith("INVALID_LABEL_VALUES") for x in r["hard_failures"])

    # An empty report must not pass. With no cells every other test is
    # vacuously satisfied, so this is the one case the gate could report as
    # clean while the run produced nothing at all.
    r = evaluate([], expected_coordinates=None, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert not r["composition_gate_pass"], r
    assert any(x.startswith("NO_COORDINATES") for x in r["hard_failures"]), r

    # A truncated run is caught by the count check when one is supplied, and
    # still caught by the emptiness check when one is not.
    r = evaluate([], expected_coordinates=4100, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert not r["composition_gate_pass"]
    assert any(x.startswith("COORDINATE_COUNT_MISMATCH")
               for x in r["hard_failures"])

    # Sampling-sensitive versus structural. A caller that relaxes the gate on a
    # smoke run must relax only the first kind; softening an empty report or a
    # bad label because the sample was small would be the gate failing open.
    spec = {"CP%d" % i: ["0"] * 25 for i in range(1, 5)}
    spec["CP1"] = ["1"] + ["0"] * 24
    r = evaluate(cells(spec), expected_coordinates=None,
                 max_majority_share=0.95, warn_fallback_share_above=None,
                 warn_cp_constancy_above=None)
    assert r["sampling_sensitive_only"] is True, r["hard_failures"]
    assert r["structural_failures"] == []

    r = evaluate([], expected_coordinates=None, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert r["sampling_sensitive_only"] is False, r
    assert r["structural_failures"], r

    r = evaluate([{"case_uid": "c", "cp_id": "CP1", "label": "TRUE",
                   "finality": None}],
                 expected_coordinates=None, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert r["sampling_sensitive_only"] is False, r

    # A clean report is neither, and must not read as "sampling only".
    r = evaluate(cells({"CP1": ["1", "0"], "CP2": ["0", "1"]}),
                 expected_coordinates=4, max_majority_share=0.95,
                 warn_fallback_share_above=None, warn_cp_constancy_above=None)
    assert r["composition_gate_pass"] and r["sampling_sensitive_only"] is False

    # The gate and the zero-provenance report must agree about constancy on
    # the same run: the gate warns on this number and the report explains it.
    rows = [{"cp_id": f"CP{i % 5 + 1}", "case_uid": f"c{i}",
             "v2_fold_label": "0" if i % 3 else "1",
             "v2_fold_finality": "EVIDENCE_REBUTTED"} for i in range(60)]
    gate_mean = evaluate(extract_cells({"coordinate_summaries": rows}),
                         expected_coordinates=None, max_majority_share=0.99,
                         warn_fallback_share_above=None,
                         warn_cp_constancy_above=None)["cp_constancy_mean"]
    assert gate_mean == zp.cp_constancy(rows, "v2_fold_label")["mean"]

    print("submission_composition_gate_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path,
                    help="v2 replay report, or {cells:[{cp_id,label}]}")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--expected-coordinates", type=int, default=None)
    ap.add_argument("--max-majority-share", type=float, default=0.95)
    ap.add_argument("--warn-fallback-share-above", type=float, default=0.60)
    ap.add_argument("--warn-cp-constancy-above", type=float, default=0.95)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests()
        return 0

    if not a.report or not a.output:
        ap.error("--report and --output are required unless --self-test")

    doc = json.loads(a.report.read_text(encoding="utf-8"))
    result = evaluate(
        extract_cells(doc),
        expected_coordinates=a.expected_coordinates,
        max_majority_share=a.max_majority_share,
        warn_fallback_share_above=a.warn_fallback_share_above,
        warn_cp_constancy_above=a.warn_cp_constancy_above)
    result["source_report"] = str(a.report)

    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["composition_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
