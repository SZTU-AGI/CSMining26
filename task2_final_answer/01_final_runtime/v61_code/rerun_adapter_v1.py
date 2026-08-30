#!/usr/bin/env python3
"""Bind the metamorphic harness to the real pipeline.

WHY THIS EXISTS
---------------
`mas_harness_v1` and `run_invariants_v1` both take an injected
`rerun(case) -> observation`. Neither could run against the real system,
because nothing implemented that function: the harness was a framework with no
subject. Every H1-H8 result and the determinism check were therefore
unmeasured, whatever the self-tests said.

WHAT IT DOES
------------
`run_task` accepts `chunks` as a parameter rather than parsing them itself, so
a mutated case can be replayed by handing it a mutated chunk list. That is the
whole mechanism: the harness mutates chunks (see `mas_harness_v1.edit_track`,
which keeps the track view and the chunks synchronised), this module hands them
to `run_task`, and reads the verdict back out of the artifacts the run writes.

WHAT IT DELIBERATELY DOES NOT COVER
-----------------------------------
The document parser. Mutations are applied to chunks, so `parse_docx` is not
exercised. That is the correct boundary for these tests, which are about the
reasoning over evidence rather than the extraction of it, but it means a parser
regression is outside their reach and must not be claimed as covered.

WHAT THE MUTATIONS DO NOT REACH
-------------------------------
`run_task` also reads `re_number_candidate` from the case record, out of band
from the evidence text, for the identity countercheck. Mutating the chunks does
not change it, and this module deliberately leaves it alone.

That is the right behaviour for H3a, not an oversight. H3a rewrites the case's
own registration number in the evidence to a foreign one, and its premise is
that the system is being asked about establishment X while the evidence now
describes Y. Rewriting the candidate as well would model a question about Y
answered with evidence about Y, which is not the test. The consequence to keep
in mind is narrower: the case record travels unmutated, so any mutation whose
meaning depends on a field of that record rather than on the evidence would not
be constructed by this path at all.

COST
----
Every call is a full coordinate: a real run with real model calls. A suite of
eight mutations over N cases costs N * 9 coordinates (one baseline plus eight
mutants). Sample deliberately.

OBSERVATION FIELDS
------------------
The oracles need six observables. Five come straight from the run artifacts;
`observed_span_days` is derived here from the dates in every chunk the verdict
engaged with, support and attack together, and is labelled a derived observable
rather than a system output, because the pipeline has no notion of an observed
span.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable

import mas_harness_v1 as harness
import support_locator_export_v1 as loc


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def decisive_basis(proof: dict, decisive: set[str]) -> dict[str, list[str]]:
    """Evidence ids each decisive requirement rested on, by direction."""
    support: list[str] = []
    attack: list[str] = []
    for row in proof.get("requirement_reports", []) or []:
        if str(row.get("requirement_id")) not in decisive:
            continue
        for key, sink in (("support_proof", support), ("attack_proof", attack)):
            directional = row.get(key) or {}
            if directional.get("accepted_direction") is not True:
                continue
            for eid in directional.get("basis_evidence_ids") or []:
                if isinstance(eid, str) and eid.strip() and eid != "None":
                    sink.append(eid)
    return {"support": sorted(set(support)), "attack": sorted(set(attack))}


def _to_ordinal(d: tuple[int, int, int]) -> int:
    """Day number for a (year, month, day) triple, clamped to a real date.

    An out-of-range day is clamped to the last day of that month, not to a
    fixed 28. Clamping to 28 silently compresses every date after the 28th onto
    the same day, so `2021-01-30` and `2021-01-31` both became the 28th and a
    one-day span measured as zero. H5 compares spans, so a distorted span turns
    into a verdict about the system rather than about the arithmetic.
    """
    import calendar
    import datetime
    year, month, day = d
    month = min(max(month, 1), 12)
    year = min(max(year, datetime.MINYEAR), datetime.MAXYEAR)
    day = min(max(day, 1), calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day).toordinal()


def observed_span_days(chunks_by_id: dict[str, str],
                       evidence_ids: list[str]) -> int | None:
    """Span in days between the earliest and latest date in the cited evidence.

    A derived observable, not something the pipeline reports. H5 removes the
    earliest dated line and asserts only that the span must not GROW, which is
    a weak claim that this derivation can support honestly: it is computed from
    the same evidence the system named as its basis, so if the basis is
    unchanged the span cannot grow, and if the basis moved the number reflects
    the move rather than concealing it.

    Returns None when fewer than two dates are visible, which the oracle reads
    as "not asserted" rather than as a pass.
    """
    seen: list[tuple[int, int, int]] = []
    for eid in evidence_ids:
        text = chunks_by_id.get(eid)
        if not text:
            continue
        for line in text.splitlines():
            when = harness.earliest_date_in(line)
            if when is not None:
                seen.append(when)
    if len(seen) < 2:
        return None

    lo, hi = min(seen), max(seen)
    return abs(_to_ordinal(hi) - _to_ordinal(lo))


def evaluation_of(outcome_doc: dict) -> dict:
    """The single interpretation evaluation inside an outcome bundle.

    `build_argument_evaluation_bundle` returns `{"evaluations": [evaluation],
    ...}` and spreads the root states into that evaluation, so
    `unresolved_reason_codes`, `applicability_state` and the rest live one level
    down. Reading them from the bundle top level returns None every time, which
    is silent: the H4 fingerprint kept computing, just over a constant, and its
    sensitivity quietly dropped to whatever the remaining fields carried.
    """
    evaluations = outcome_doc.get("evaluations")
    if isinstance(evaluations, list) and evaluations:
        first = evaluations[0]
        if isinstance(first, dict):
            return first
    return outcome_doc


def read_observation(task_dir: Path, chunks: list[dict]) -> dict[str, Any]:
    """Turn one finished coordinate into the observation the oracles read."""
    fold_path = task_dir / "fold_decision_v3.json"
    outcome_path = task_dir / "core_outcome_adapter_v1.json"
    rr_path = task_dir / "initial" / "requirement_result.json"
    proof_path = loc.final_proof_path(task_dir)

    if not fold_path.is_file():
        return {"label": None, "error": "NO_FOLD_DECISION",
                "task_dir": str(task_dir)}

    fold = json.loads(fold_path.read_text(encoding="utf-8"))
    outcome = (json.loads(outcome_path.read_text(encoding="utf-8"))
               if outcome_path.is_file() else {})

    basis = {"support": [], "attack": []}
    if rr_path.is_file() and proof_path.is_file():
        try:
            rr = json.loads(rr_path.read_text(encoding="utf-8"))
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            basis = decisive_basis(proof, loc.decisive_requirement_ids(rr))
        except Exception:
            basis = {"support": [], "attack": []}

    chunks_by_id = {str(c.get("id")): str(c.get("text") or "") for c in chunks}
    cited = sorted(set(basis["support"]) | set(basis["attack"]))

    # The fingerprint answers "did anything observable move", which is what H4
    # asks. It deliberately excludes the label so that an internal change with
    # a held label still counts as observable, and it reads the per-evaluation
    # fields from where they are actually written: taken from the bundle top
    # level they were always None, so they contributed nothing and H4 was less
    # sensitive than it looked.
    evaluation = evaluation_of(outcome)
    fingerprint = _sha({
        "internal_outcome": outcome.get("common_internal_outcome"),
        "finality": fold.get("finality"),
        "support": basis["support"],
        "attack": basis["attack"],
        "unresolved": sorted(evaluation.get("unresolved_reason_codes") or []),
        "applicability": evaluation.get("applicability_state"),
        "non_applicability": evaluation.get("non_applicability_state"),
        "satisfaction": evaluation.get("satisfaction_four_valued_state"),
        "accepted_argument": evaluation.get("accepted_argument_state"),
    })

    return {
        "label": None if fold.get("label") is None else str(fold["label"]),
        "internal_outcome": outcome.get("common_internal_outcome"),
        "finality": fold.get("finality"),
        "benchmark_fallback": bool(fold.get("benchmark_fallback")),
        "semantic_fingerprint": fingerprint,
        "support_evidence_ids": basis["support"],
        "attack_evidence_ids": basis["attack"],
        "cited_sources": sorted({str(e).split(":", 1)[0] for e in cited}),
        # Computed over every chunk the verdict engaged with, support and
        # attack together, rather than the support leg alone. A coordinate
        # often rests on a single supporting chunk, and a single chunk rarely
        # carries two dates, so the narrower basis returned None almost always
        # and H5 would have been constructed on every case while asserting
        # nothing on any of them.
        "observed_span_days": observed_span_days(chunks_by_id, cited),
        "task_dir": str(task_dir),
    }


# Keys `run_task` reads from the case record. Kept here as data so the check
# is one edit away from the runner if that ever changes.
REQUIRED_CASE_KEYS = ("case_uid", "serial", "physical_case_dir",
                      "re_number_candidate", "track_assignments")


def require_case_record(case_record: dict) -> None:
    """Raise unless the record carries every key `run_task` will read."""
    if not isinstance(case_record, dict):
        raise ValueError(f"case_record must be a dict, got "
                         f"{type(case_record).__name__}")
    missing = [k for k in REQUIRED_CASE_KEYS if k not in case_record]
    if missing:
        raise ValueError(
            "case_record is missing keys run_task reads: "
            + ", ".join(missing)
            + ". Pass the case record from the logical case manifest, not a "
              "harness case.")


def make_rerun(
    *,
    case_record: dict,
    cp_id: str,
    manifest: dict,
    contract_dir: Path,
    policy: dict,
    policy_path: Path,
    runtime: dict,
    work_root: Path,
    repair_enabled: bool,
    retrieval_top_k: int = 8,
    keep_artifacts: bool = False,
) -> Callable[[dict], dict]:
    """Return `rerun(harness_case) -> observation`.

    Imported lazily so this module stays importable, and its helpers testable,
    on a machine with no API access and no runner dependencies installed.

    `repair_enabled` has no default and must be stated, because neither value
    is safe to assume. The production launcher runs WITH repair, so testing
    with it off establishes relations about a configuration that is not the one
    being submitted; testing with it on multiplies the model calls per
    coordinate and makes a mutation's effect harder to attribute. That is a
    trade between cost and validity, and a default would let one side of it be
    chosen by accident. The value is recorded in every observation so a report
    cannot be read as covering the other configuration.
    """
    # Validated before the runner is imported, so a configuration error is
    # reported on a machine that cannot run the pipeline at all, and reported
    # before any heavy dependency is loaded.
    #
    # `run_task` reads these keys directly. A missing one raises KeyError
    # inside the call, where the handler below turns it into
    # {"label": None, "error": ...}; every oracle then abstains and the suite
    # reports abstentions across the board. A misconfigured adapter would look
    # exactly like a system that declined to answer, which is the most
    # expensive way to discover a typo: after paying for the whole suite.
    require_case_record(case_record)

    import production_runner_v1 as runner

    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    counter = {"n": 0}

    def rerun(case: dict) -> dict:
        counter["n"] += 1
        chunks = harness.case_to_chunks(case)

        # A distinct case_uid per replay keeps the input fingerprint distinct
        # and makes the task directory self-describing. `case_source_fingerprint`
        # hashes the source file list, not the chunks, so two mutants of one
        # case would otherwise be indistinguishable in the recorded provenance.
        tag = f"{case['case_uid']}@r{counter['n']:04d}"
        record = dict(case_record)
        record["case_uid"] = tag

        run_dir = work_root / tag
        try:
            runner.run_task(
                case=record,
                cp_id=cp_id,
                chunks=chunks,
                manifest=manifest,
                contract_dir=Path(contract_dir),
                policy=policy,
                policy_path=Path(policy_path),
                run_dir=run_dir,
                runtime=runtime,
                retrieval_top_k=retrieval_top_k,
                repair_enabled=repair_enabled,
            )
        except Exception as exc:
            # A crashed coordinate is reported, never silently treated as a
            # label. An oracle receiving {"label": None} abstains, which is the
            # honest reading of "the system did not answer".
            return {"label": None, "error": type(exc).__name__,
                    "error_detail": str(exc)[:400], "case_uid": tag}

        task_dir = run_dir / "tasks" / tag / cp_id
        observation = read_observation(task_dir, chunks)
        observation["case_uid"] = tag
        observation["chunk_count"] = len(chunks)
        # Recorded per observation: production runs with repair on, so a suite
        # run with it off has not tested the submitted configuration and the
        # report must carry that fact rather than rely on the reader knowing.
        observation["repair_enabled"] = repair_enabled

        if not keep_artifacts:
            shutil.rmtree(run_dir, ignore_errors=True)
        return observation

    return rerun


def run_self_tests() -> None:
    """Exercise the derivations without importing the runner or calling an API."""
    proof = {"requirement_reports": [
        {"requirement_id": "ER1",
         "support_proof": {"accepted_direction": True,
                           "basis_evidence_ids": ["f1.docx:T1:R2"]},
         "attack_proof": {"accepted_direction": False,
                          "basis_evidence_ids": ["f2.docx:P1"]}},
        {"requirement_id": "ER2",
         "support_proof": {"accepted_direction": True,
                           "basis_evidence_ids": ["f3.docx:P9"]},
         "attack_proof": {}},
        {"requirement_id": "ER9",
         "support_proof": {"accepted_direction": True,
                           "basis_evidence_ids": ["ignored.docx:P1"]},
         "attack_proof": {}},
    ]}
    basis = decisive_basis(proof, {"ER1", "ER2"})
    assert basis["support"] == ["f1.docx:T1:R2", "f3.docx:P9"], basis
    # A rejected direction contributes nothing, and a non-decisive requirement
    # is excluded even when its support was accepted.
    assert basis["attack"] == [], basis

    chunks_by_id = {
        "a:1": "Inspection Date | 2021-03-04",
        "a:2": "Review Date | 2021-04-03",
        "a:3": "no date here",
    }
    assert observed_span_days(chunks_by_id, ["a:1", "a:2"]) == 30
    assert observed_span_days(chunks_by_id, ["a:1"]) is None
    assert observed_span_days(chunks_by_id, ["a:3"]) is None
    # Mixed formats must be comparable, or the span is computed from a subset.
    chunks_by_id["a:4"] = "Issued | 4 March 2021"
    chunks_by_id["a:5"] = "Closed | 3/4/2021"
    assert observed_span_days(chunks_by_id, ["a:4", "a:5"]) == 30

    # Days past the 28th must not be compressed onto one another. Clamping to a
    # fixed 28 measured this pair as a zero-day span, and H5 compares spans.
    for lo, hi, expected in (("2021-01-01", "2021-01-31", 30),
                             ("2021-01-15", "2021-03-30", 74),
                             ("2020-02-29", "2020-03-31", 31),
                             ("2021-01-30", "2021-01-31", 1)):
        idx = {"lo": f"Date | {lo}", "hi": f"Date | {hi}"}
        got = observed_span_days(idx, ["lo", "hi"])
        assert got == expected, f"{lo}..{hi} -> {got}, expected {expected}"

    # An impossible day must be clamped to a real one rather than raising.
    assert _to_ordinal((2021, 2, 31)) == _to_ordinal((2021, 2, 28))
    assert _to_ordinal((2020, 2, 31)) == _to_ordinal((2020, 2, 29))
    assert _to_ordinal((2021, 13, 40)) == _to_ordinal((2021, 12, 31))

    root = Path(__file__).resolve().parent
    missing = read_observation(root / "_does_not_exist", [])
    assert missing["label"] is None
    assert missing["error"] == "NO_FOLD_DECISION"

    # `repair_enabled` must be stated. A default would let the suite silently
    # test a configuration other than the one being submitted.
    import inspect as _inspect
    _param = _inspect.signature(make_rerun).parameters["repair_enabled"]
    assert _param.default is _inspect.Parameter.empty, (
        "repair_enabled must have no default")

    # A case record missing a key `run_task` reads must fail loudly. Swallowed,
    # it becomes an abstention indistinguishable from a system that declined to
    # answer, discovered only after the whole suite has been paid for.
    good = {k: "x" for k in REQUIRED_CASE_KEYS}
    require_case_record(good)
    for drop in REQUIRED_CASE_KEYS:
        bad = {k: v for k, v in good.items() if k != drop}
        try:
            require_case_record(bad)
        except ValueError as exc:
            assert drop in str(exc), (drop, str(exc))
        else:
            raise AssertionError(f"missing {drop!r} was accepted")
    for wrong in (None, [], "case-001"):
        try:
            require_case_record(wrong)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{wrong!r} was accepted as a case record")

    # The check must run before the runner is imported, so that a machine
    # without the reference core still reports the configuration error rather
    # than an unrelated ImportError.
    import inspect
    src = inspect.getsource(make_rerun)
    assert src.index("require_case_record(case_record)") < src.index(
        "import production_runner_v1"), (
        "validation must precede the runner import")

    _test_rerun_plumbing()
    print("rerun_adapter_v1 self-tests: PASS")


def _test_rerun_plumbing() -> None:
    """Exercise the rerun closure against a stand-in runner.

    The closure cannot be tested with the real runner on a machine without the
    reference core, and the part most likely to be wrong is silent: if the task
    directory is assembled differently from the way `run_task` writes it,
    `read_observation` finds no fold decision, returns {"label": None}, every
    oracle abstains and the suite reports abstentions rather than an error. A
    stand-in runner that writes where the real one writes pins the path down.
    """
    import shutil
    import sys
    import tempfile
    import types

    tmp = Path(tempfile.mkdtemp(prefix="freca_rerun_"))
    try:
        seen: list[dict] = []

        def fake_run_task(*, case, cp_id, chunks, manifest, contract_dir,
                          policy, policy_path, run_dir, runtime,
                          retrieval_top_k, repair_enabled):
            seen.append({"case_uid": case["case_uid"], "cp_id": cp_id,
                         "chunks": len(chunks),
                         "repair_enabled": repair_enabled})
            # Written exactly where production_runner_v1.run_task writes it.
            task_dir = Path(run_dir) / "tasks" / case["case_uid"] / cp_id
            (task_dir / "initial" / "layer7").mkdir(parents=True)
            (task_dir / "fold_decision_v3.json").write_text(json.dumps(
                {"label": "1", "finality": "EVIDENCE_DEMONSTRATED",
                 "benchmark_fallback": False}), encoding="utf-8")
            # Real nesting: the root states and reason codes live inside
            # evaluations[0], not at the bundle top level.
            (task_dir / "core_outcome_adapter_v1.json").write_text(json.dumps(
                {"common_internal_outcome": "PROVEN_COMPLIANT",
                 "evaluations": [{
                     "internal_outcome": "PROVEN_COMPLIANT",
                     "unresolved_reason_codes": ["SOME_CODE"],
                     "applicability_state": "TRUE",
                     "non_applicability_state": "FALSE",
                     "satisfaction_four_valued_state": "TRUE",
                     "accepted_argument_state": "PRO"}]}),
                encoding="utf-8")
            (task_dir / "initial" / "requirement_result.json").write_text(
                json.dumps({"evidence_requirement_plan": {"requirements": [
                    {"requirement_id": "ER1", "decisiveness": "DECISIVE"}]}}),
                encoding="utf-8")
            (task_dir / "initial" / "layer7" /
             "proof_standard_v1_1.json").write_text(json.dumps(
                {"requirement_reports": [
                    {"requirement_id": "ER1",
                     "support_proof": {"accepted_direction": True,
                                       "basis_evidence_ids": ["f.docx:P1"]},
                     "attack_proof": {"accepted_direction": True,
                                      "basis_evidence_ids": ["f.docx:P2"]}}]}),
                encoding="utf-8")
            return {"status": "COMPLETE"}

        stand_in = types.ModuleType("production_runner_v1")
        stand_in.run_task = fake_run_task
        real = sys.modules.get("production_runner_v1")
        sys.modules["production_runner_v1"] = stand_in
        try:
            rerun = make_rerun(
                case_record={k: "x" for k in REQUIRED_CASE_KEYS},
                cp_id="CP1", manifest={}, contract_dir=tmp,
                policy={}, policy_path=tmp, runtime={},
                work_root=tmp / "work", repair_enabled=False)

            case = harness.case_from_chunks("case-001", [
                {"id": "f.docx:P1", "file": "f.docx",
                 "text": "Inspection Date | 2021-03-04"},
                {"id": "f.docx:P2", "file": "f.docx",
                 "text": "Review Date | 2021-04-03"}])

            obs = rerun(case)
            assert obs["label"] == "1", obs
            assert obs["internal_outcome"] == "PROVEN_COMPLIANT", obs
            assert obs["support_evidence_ids"] == ["f.docx:P1"], obs
            assert obs["attack_evidence_ids"] == ["f.docx:P2"], obs
            assert obs["cited_sources"] == ["f.docx"], obs
            # Spanned across support and attack together. Restricted to the
            # support leg this read None, because one chunk carries one date,
            # and H5 would have asserted nothing on any coordinate.
            assert obs["observed_span_days"] == 30, obs
            assert obs["chunk_count"] == 2, obs
            # Which configuration was tested must travel with the result: the
            # production launcher runs with repair on, so a suite run without
            # it has not exercised the submitted configuration.
            assert obs["repair_enabled"] is False, obs
            assert obs["semantic_fingerprint"], obs

            # The fingerprint must actually depend on the per-evaluation
            # fields. Read from the bundle top level they were always None, so
            # H4 would have looked sensitive while ignoring every root state.
            import copy as _copy
            probe_doc = {"common_internal_outcome": "PROVEN_COMPLIANT",
                         "evaluations": [{"unresolved_reason_codes": ["A"],
                                          "applicability_state": "TRUE"}]}
            other = _copy.deepcopy(probe_doc)
            other["evaluations"][0]["applicability_state"] = "FALSE"
            assert evaluation_of(probe_doc)["applicability_state"] == "TRUE"
            assert evaluation_of(other)["applicability_state"] == "FALSE"
            assert evaluation_of({"applicability_state": "TRUE"}) == {
                "applicability_state": "TRUE"}, "top-level fallback broken"

            # The runner saw the mutated chunks, and repair stays off so a
            # suite does not silently multiply its own cost.
            assert seen[0]["chunks"] == 2, seen
            assert seen[0]["repair_enabled"] is False, seen

            # Each replay gets its own identity, or two mutants of one case
            # would share a task directory and the second would read the
            # first one's verdict.
            second = rerun(case)
            assert second["case_uid"] != obs["case_uid"], (obs, second)
            assert seen[0]["case_uid"] != seen[1]["case_uid"], seen

            # Artifacts are removed unless the caller asks to keep them.
            assert not (tmp / "work" / obs["case_uid"]).exists()

            keeper = make_rerun(
                case_record={k: "x" for k in REQUIRED_CASE_KEYS},
                cp_id="CP1", manifest={}, contract_dir=tmp,
                policy={}, policy_path=tmp, runtime={},
                work_root=tmp / "kept", keep_artifacts=True,
                repair_enabled=False)
            kept = keeper(case)
            assert (tmp / "kept" / kept["case_uid"]).exists()

            # A crashing coordinate is reported, never mistaken for a label.
            def boom(**kwargs):
                raise RuntimeError("contract missing")

            stand_in.run_task = boom
            broken = make_rerun(
                case_record={k: "x" for k in REQUIRED_CASE_KEYS},
                cp_id="CP1", manifest={}, contract_dir=tmp,
                policy={}, policy_path=tmp, runtime={},
                work_root=tmp / "boom", repair_enabled=False)(case)
            assert broken["label"] is None
            assert broken["error"] == "RuntimeError", broken
            assert "contract missing" in broken["error_detail"]
        finally:
            if real is None:
                sys.modules.pop("production_runner_v1", None)
            else:
                sys.modules["production_runner_v1"] = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        run_self_tests()
        raise SystemExit(0)
    ap.error("this module is driven by import; see make_rerun()")
