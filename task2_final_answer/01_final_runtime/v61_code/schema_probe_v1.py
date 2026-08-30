#!/usr/bin/env python3
"""Preflight: does a real run carry the fields the validation tools read?

WHY THIS EXISTS
---------------
Every field name in the validation tooling was derived by reading the producing
code, not by inspecting a run. Two of those derivations were wrong:

  * `task_meta.json` nests `case_uid`/`cp_id` under "inputs"; reading the top
    level returned None for every coordinate, so every locator collided on one
    dict key and the eligible count read 1 whatever the run contained;
  * the deletable spans for H7 are `support_proof.basis_evidence_ids`, not
    `supporting_statement_ids`, which holds statement identifiers that cannot
    be located in the case text and cannot be deleted.

Both produced zeros rather than errors, and both passed self-tests written
against the same wrong assumption. The failure mode is established, not
hypothetical, and the remaining assumptions carry the same risk.

This probe converts that risk into a report. Point it at a smoke run of a few
cases before committing to a full run: every field the tools depend on is
looked up in the artifact that is supposed to carry it, and reported present or
absent with a count. A check reading 0/N is a schema mismatch in the tooling,
not a finding about the data.

It reads only; it never blocks a run and never calls an API.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import support_locator_export_v1 as loc


def build_checks() -> list[tuple[str, str, Callable[[dict], bool]]]:
    checks: list[tuple[str, str, Callable[[dict], bool]]] = []

    def add(artifact: str, field: str, fn: Callable[[dict], bool]) -> None:
        checks.append((artifact, field, fn))

    add("task_meta.json", "inputs.case_uid",
        lambda d: bool((d.get("inputs") or {}).get("case_uid")))
    add("task_meta.json", "inputs.cp_id",
        lambda d: bool((d.get("inputs") or {}).get("cp_id")))

    add("fold_decision_v3.json", "label",
        lambda d: str(d.get("label")) in {"1", "0", "N/A"})
    add("fold_decision_v3.json", "finality", lambda d: bool(d.get("finality")))
    add("fold_decision_v3.json", "benchmark_fallback",
        lambda d: isinstance(d.get("benchmark_fallback"), bool))
    add("fold_decision_v3.json", "forced",
        lambda d: isinstance(d.get("forced"), bool))

    add("core_outcome_adapter_v1.json", "common_internal_outcome",
        lambda d: bool(d.get("common_internal_outcome")))
    add("core_outcome_adapter_v1.json", "fold_gate_report",
        lambda d: isinstance(d.get("fold_gate_report"), dict))

    add("initial/requirement_result.json", "evidence_requirement_plan",
        lambda d: isinstance(d.get("evidence_requirement_plan"), dict))
    add("initial/requirement_result.json", "requirements[].decisiveness",
        lambda d: any(
            r.get("decisiveness") for r in
            ((d.get("evidence_requirement_plan") or {}).get("requirements")
             or [])))
    add("initial/requirement_result.json", "at least one DECISIVE",
        lambda d: bool(loc.decisive_requirement_ids(d)))
    add("initial/requirement_result.json", "resolvable evidence text",
        lambda d: bool(loc.index_chunk_text(d)))

    add("<final proof>", "requirement_reports",
        lambda d: bool(d.get("requirement_reports")))
    add("<final proof>", "support_proof.accepted_direction",
        lambda d: any("accepted_direction" in (r.get("support_proof") or {})
                      for r in d.get("requirement_reports") or []))
    add("<final proof>", "support_proof.basis_evidence_ids",
        lambda d: any((r.get("support_proof") or {}).get("basis_evidence_ids")
                      for r in d.get("requirement_reports") or []))
    add("<final proof>", "basis ids look like chunk ids",
        lambda d: any(
            isinstance(e, str) and ":" in e
            for r in d.get("requirement_reports") or []
            for e in ((r.get("support_proof") or {}).get("basis_evidence_ids")
                      or [])))
    return checks


def probe(run_dir: Path, limit: int | None = None) -> dict[str, Any]:
    checks = build_checks()

    found: dict[str, int] = {f"{a} :: {f}": 0 for a, f, _ in checks}
    seen: dict[str, int] = {k: 0 for k in found}
    errors: dict[str, int] = {k: 0 for k in found}
    error_detail: dict[str, str] = {}
    unreadable: list[str] = []
    task_count = 0

    # Sampled by striding, not by taking a prefix.
    #
    # Task directories sort as tasks/<case>/<cp>, so the first N paths are all
    # the same case: a limit of 40 inspected case-001 and nothing else, and any
    # field that varies by case would have gone unseen while the probe reported
    # a clean sweep. Striding spreads the sample across every case present at
    # the same cost.
    all_meta = sorted(run_dir.rglob("task_meta.json"))
    if limit is not None and 0 < limit < len(all_meta):
        stride = len(all_meta) / limit
        picked = [all_meta[int(i * stride)] for i in range(limit)]
        # int() on a float stride can repeat an index; keep order, drop repeats.
        seen_paths: set[Path] = set()
        all_meta = [p for p in picked
                    if not (p in seen_paths or seen_paths.add(p))]

    for meta_path in all_meta:
        task = meta_path.parent
        task_count += 1

        docs: dict[str, dict | None] = {}
        for name, path in (
            ("task_meta.json", meta_path),
            ("fold_decision_v3.json", task / "fold_decision_v3.json"),
            ("core_outcome_adapter_v1.json",
             task / "core_outcome_adapter_v1.json"),
            ("initial/requirement_result.json",
             task / "initial" / "requirement_result.json"),
            ("<final proof>", loc.final_proof_path(task)),
        ):
            if not path.is_file():
                docs[name] = None
                unreadable.append(f"MISSING {name} @ {task}")
                continue
            try:
                docs[name] = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                docs[name] = None
                unreadable.append(
                    f"UNREADABLE {name} @ {task}: {type(exc).__name__}")

        for artifact, field, fn in checks:
            key = f"{artifact} :: {field}"
            doc = docs.get(artifact)
            if doc is None:
                continue
            seen[key] += 1
            try:
                if fn(doc):
                    found[key] += 1
            except Exception as exc:
                # A check that raises is counted, not swallowed. Swallowed, a
                # bug in the check itself reads as ABSENT on every task, and
                # the operator goes looking for a field that is present.
                errors[key] += 1
                if key not in error_detail:
                    error_detail[key] = f"{type(exc).__name__}: {exc}"[:200]

    rows = []
    for key in found:
        n, d, e = found[key], seen[key], errors[key]
        # A check that raised on every document it saw is broken tooling, and
        # is reported as such rather than as a field the run does not carry.
        status = ("NO_ARTIFACT" if d == 0
                  else "CHECK_ERROR" if e == d
                  else "ABSENT" if n == 0
                  else "PARTIAL" if n < d
                  else "PRESENT")
        row = {"check": key, "present": n, "inspected": d,
               "rate": (n / d) if d else None, "status": status}
        if e:
            row["raised"] = e
            row["error"] = error_detail.get(key)
        rows.append(row)

    absent = [r["check"] for r in rows if r["status"] == "ABSENT"]
    no_artifact = [r["check"] for r in rows if r["status"] == "NO_ARTIFACT"]
    check_errors = [r["check"] for r in rows if r["status"] == "CHECK_ERROR"]

    return {
        "schema": "freca-schema-probe-v1",
        "run_dir": str(run_dir),
        "task_dirs_inspected": task_count,
        "checks": sorted(rows,
                         key=lambda r: (r["status"] != "ABSENT", r["check"])),
        "absent": absent,
        "no_artifact": no_artifact,
        "check_errors": check_errors,
        "unreadable_sample": unreadable[:20],
        "schema_probe_clean": (bool(task_count) and not absent
                               and not no_artifact and not check_errors),
        "note": ("A check reading 0/N is a field-name mismatch in the tooling, "
                 "not a fact about the run. PARTIAL is expected for fields only "
                 "some coordinates carry, such as an accepted support direction "
                 "on a non-compliant coordinate."),
    }


def _write_fixture(d: Path) -> None:
    (d / "initial" / "layer7").mkdir(parents=True)
    (d / "task_meta.json").write_text(json.dumps(
        {"schema": "freca-production-task-meta-v1", "input_fingerprint": "x",
         "inputs": {"case_uid": "case_a", "cp_id": "CP1"}}), encoding="utf-8")
    (d / "fold_decision_v3.json").write_text(json.dumps(
        {"label": "1", "finality": "EVIDENCE_DEMONSTRATED",
         "benchmark_fallback": False, "forced": False}), encoding="utf-8")
    (d / "core_outcome_adapter_v1.json").write_text(json.dumps(
        {"common_internal_outcome": "PROVEN_COMPLIANT",
         "fold_gate_report": {"decisive_support_standing": True}}),
        encoding="utf-8")
    (d / "initial" / "requirement_result.json").write_text(json.dumps(
        {"evidence_requirement_plan": {"requirements": [
            {"requirement_id": "ER1", "decisiveness": "DECISIVE"}]},
         "alignments": [{"evidence_id": "f1.docx:T1:R2",
                         "exact_quote": "ROW"}]}), encoding="utf-8")
    (d / "initial" / "layer7" / "proof_standard_v1_1.json").write_text(
        json.dumps({"requirement_reports": [
            {"requirement_id": "ER1",
             "support_proof": {"accepted_direction": True,
                               "basis_evidence_ids": ["f1.docx:T1:R2"]}}]}),
        encoding="utf-8")


def run_self_tests(tmp: Path) -> None:
    import shutil
    root = tmp / "_probe_selftest"
    if root.exists():
        shutil.rmtree(root)
    d = root / "tasks" / "case_a" / "CP1"
    _write_fixture(d)

    rep = probe(root)
    assert rep["schema_probe_clean"], rep["absent"] + rep["no_artifact"]
    assert rep["task_dirs_inspected"] == 1

    # Break one field: the probe must name it rather than stay clean. This is
    # the exact defect that shipped, so the probe is tested against it.
    (d / "task_meta.json").write_text(json.dumps(
        {"case_uid": "case_a", "cp_id": "CP1"}), encoding="utf-8")
    rep = probe(root)
    assert not rep["schema_probe_clean"]
    assert "task_meta.json :: inputs.case_uid" in rep["absent"], rep["absent"]

    # A missing artifact must read NO_ARTIFACT rather than silently pass.
    (d / "initial" / "layer7" / "proof_standard_v1_1.json").unlink()
    rep = probe(root)
    assert any(c.startswith("<final proof>") for c in rep["no_artifact"]), rep

    # A limited sample must spread across cases, not stop inside the first one.
    # Task paths sort as tasks/<case>/<cp>, so a prefix of 40 was 40 coordinates
    # of case-001 and no other case at all.
    shutil.rmtree(root)
    for c in range(1, 7):
        for p in range(1, 42):
            _write_fixture(root / "tasks" / f"case-{c:03d}" / f"CP{p}")
    rep = probe(root, limit=40)
    assert rep["task_dirs_inspected"] == 40, rep["task_dirs_inspected"]
    all_meta = sorted(root.rglob("task_meta.json"))
    stride = len(all_meta) / 40
    touched = {all_meta[int(i * stride)].parent.parent.name for i in range(40)}
    assert len(touched) == 6, sorted(touched)

    # A limit at or above the population inspects everything.
    assert probe(root, limit=10_000)["task_dirs_inspected"] == len(all_meta)
    assert probe(root)["task_dirs_inspected"] == len(all_meta)

    # A check that raises must be reported as broken tooling, not as a field
    # the run does not carry: swallowed, it reads ABSENT on every task and
    # sends the operator looking for a field that is present.
    original = build_checks
    try:
        def exploding():
            out = original()
            out.append(("task_meta.json", "deliberately broken",
                        lambda d: 1 / 0))
            return out
        globals()["build_checks"] = exploding
        rep = probe(root)
        assert not rep["schema_probe_clean"], rep
        assert "task_meta.json :: deliberately broken" in rep["check_errors"], rep
        assert "task_meta.json :: deliberately broken" not in rep["absent"], rep
        row = next(r for r in rep["checks"]
                   if r["check"].endswith("deliberately broken"))
        assert row["status"] == "CHECK_ERROR" and row["raised"] == row["inspected"]
        assert "ZeroDivisionError" in row["error"]
    finally:
        globals()["build_checks"] = original

    # An empty tree must not read clean by vacuity.
    shutil.rmtree(root)
    root.mkdir(parents=True)
    assert not probe(root)["schema_probe_clean"], "empty tree read clean"

    shutil.rmtree(root)
    print("schema_probe_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--limit", type=int, default=None,
                    help="inspect at most N task directories")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests(a.run_dir)
        return 0

    rep = probe(a.run_dir, limit=a.limit)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(
            json.dumps(rep, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["schema_probe_clean"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
