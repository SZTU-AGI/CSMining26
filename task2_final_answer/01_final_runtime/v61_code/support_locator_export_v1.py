#!/usr/bin/env python3
"""Export sole-admissible-support locators so H7 can be constructed.

WHY THIS EXISTS
---------------
H7 is the load-bearing metamorphic test: delete the only admissible support for
a mandatory fact and the coordinate must not remain compliant. It cannot be
constructed by deleting something arbitrary. It needs the system to say which
span is carrying the verdict, and only coordinates whose baseline is already
COMPLIANT/1 are eligible, otherwise `external_label != 1` is satisfied
vacuously by a coordinate that was 0 to begin with.

This script walks a completed run, keeps the coordinates that qualify, and
writes the locators that `mas_harness_v1.m_h7_delete_sole_support` consumes.

WHAT COUNTS AS ELIGIBLE
-----------------------
    fold label == "1"
    internal outcome == PROVEN_COMPLIANT
    the fold was not reached by a benchmark fallback
    exactly one supporting statement is recorded

The last condition is what makes the support *sole*. Coordinates with several
supports are reported separately as `multi_support` rather than being deleted
one-at-a-time, because removing one of several supports entails nothing.

PATHS
-----
Follows the layout `production_v2_replay_454.py` already reads:

    <task_dir>/task_meta.json
    <task_dir>/core_outcome_adapter_v1.json
    <task_dir>/fold_decision_v3.json
    <task_dir>/initial/requirement_result.json

Evidence chunk ids are `"<file>:P3"` / `"<file>:T1:R5"` (freca_core_v1.parse_docx),
so the file part of a statement id gives the track filename directly.

No API calls; reads a finished run only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_of(statement_id: str) -> str | None:
    """`"1_Farm.docx:T1:R5"` -> `"1_Farm.docx"`."""
    if not statement_id or ":" not in statement_id:
        return None
    return statement_id.split(":", 1)[0]


def read_identity(meta: dict, task_dir: Path) -> tuple[str | None, str | None]:
    """Resolve (case_uid, cp_id) from a task_meta document.

    `prepare_task_meta` writes

        {"schema": ..., "input_fingerprint": ..., "inputs": {case_uid, cp_id, ...}}

    so the identifiers are nested under `inputs`, not at the top level. Reading
    the top level yields None for every coordinate, which then collide on a
    single dict key and make the eligible count read 1 regardless of the run.
    That failure is silent, so the nesting is probed explicitly and the run
    directory layout `run_dir/tasks/<case_uid>/<cp_id>` is used as a fallback.
    """
    inputs = meta.get("inputs") if isinstance(meta.get("inputs"), dict) else {}
    case_uid = inputs.get("case_uid") or meta.get("case_uid")
    cp_id = inputs.get("cp_id") or meta.get("cp_id")
    if not case_uid:
        case_uid = task_dir.parent.name or None
    if not cp_id:
        cp_id = task_dir.name or None
    return (str(case_uid) if case_uid else None,
            str(cp_id) if cp_id else None)


def index_chunk_text(requirement_result: dict) -> dict[str, str]:
    """Map statement/evidence id -> its text, wherever the run recorded it.

    The requirement result carries aligned fact candidates; different layers
    spell the id key differently (`id` / `evidence_id` / `chunk_id`), which is
    why `freca_core_v1` probes all three. The same tolerance is applied here.
    """
    out: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ident = None
            for key in ("evidence_id", "chunk_id", "id"):
                if isinstance(node.get(key), str):
                    ident = node[key]
                    break
            text = None
            for key in ("exact_quote", "evidence_text", "text", "quote"):
                if isinstance(node.get(key), str) and node[key].strip():
                    text = node[key]
                    break
            if ident and text and ident not in out:
                out[ident] = text
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(requirement_result)
    return out


def final_proof_path(task_dir: Path) -> Path:
    """The proof bundle whose verdict actually stands, as replay resolves it.

    Repair rounds supersede the initial layer, so the last `after` bundle wins.
    Mirrors `production_v2_replay_454.final_v1_proof_path`; if the two ever
    diverge, H7 would be built from a superseded verdict.
    """
    after = sorted(task_dir.glob("repair/round-*/after/proof_standard_v1_1.json"))
    if after:
        return after[-1]
    return task_dir / "initial" / "layer7" / "proof_standard_v1_1.json"


def decisive_requirement_ids(requirement_result: dict) -> set[str]:
    """Same filter `core_outcome_adapter_v1.decisive_requirement_reports` uses.

    Non-decisive requirements do not carry the verdict, so evidence supporting
    only those is not what H7 must delete.
    """
    plan = requirement_result.get("evidence_requirement_plan") or {}
    return {
        str(row["requirement_id"])
        for row in plan.get("requirements", [])
        if str(row.get("decisiveness", "")).upper() == "DECISIVE"
        and row.get("requirement_id") is not None
    }


def supporting_evidence_ids(proof_bundle: dict,
                            decisive: set[str]) -> list[str]:
    """Evidence chunk ids that carry an accepted support direction.

    `core_outcome_adapter_v1` exports `supporting_statement_ids`, but those are
    statement identifiers (`row["statement_id"]`, falling back to
    `f"stmt-{requirement_id}"`), not evidence chunk ids. They cannot be located
    in the case text and cannot be deleted, so H7 cannot be built from them.
    The deletable spans are `support_proof.basis_evidence_ids`, whose members
    are chunk ids of the form `<file>:T1:R5` produced by `freca_core_v1`.
    """
    out: list[str] = []
    for row in proof_bundle.get("requirement_reports", []) or []:
        if str(row.get("requirement_id")) not in decisive:
            continue
        support = row.get("support_proof") or {}
        if support.get("accepted_direction") is not True:
            continue
        for eid in support.get("basis_evidence_ids") or []:
            if isinstance(eid, str) and eid.strip() and eid != "None":
                out.append(eid)
    return sorted(set(out))


def collect(run_dir: Path) -> dict[str, Any]:
    eligible: dict[str, dict] = {}
    rows: list[dict] = []
    stats = {
        "task_dirs": 0, "complete": 0, "label_1": 0,
        "proven_compliant": 0, "not_fallback": 0,
        "sole_support": 0, "locator_resolved": 0, "multi_support": 0,
        "identity_unresolved": 0, "proof_missing": 0, "no_decisive": 0,
        "no_support_evidence": 0,
    }

    for meta_path in sorted(run_dir.rglob("task_meta.json")):
        task = meta_path.parent
        stats["task_dirs"] += 1

        fold_path = task / "fold_decision_v3.json"
        outcome_path = task / "core_outcome_adapter_v1.json"
        rr_path = task / "initial" / "requirement_result.json"
        if not (fold_path.is_file() and outcome_path.is_file()):
            continue
        stats["complete"] += 1

        meta = load(meta_path)
        fold = load(fold_path)
        outcome = load(outcome_path)

        case_uid, cp_id = read_identity(meta, task)
        if case_uid is None:
            rows.append({"case_uid": None, "cp_id": cp_id,
                         "status": "IDENTITY_UNRESOLVED",
                         "task_dir": str(task)})
            stats["identity_unresolved"] += 1
            continue

        if str(fold.get("label")) != "1":
            continue
        stats["label_1"] += 1

        internal = (outcome.get("common_internal_outcome")
                    or outcome.get("internal_outcome"))
        if internal != "PROVEN_COMPLIANT":
            continue
        stats["proven_compliant"] += 1

        if fold.get("benchmark_fallback") or fold.get("forced"):
            continue
        stats["not_fallback"] += 1

        proof_path = final_proof_path(task)
        if not (proof_path.is_file() and rr_path.is_file()):
            rows.append({"case_uid": case_uid, "cp_id": cp_id,
                         "status": "PROOF_OR_REQUIREMENT_RESULT_MISSING",
                         "proof_path": str(proof_path)})
            stats["proof_missing"] += 1
            continue

        try:
            requirement_result = load(rr_path)
            proof = load(proof_path)
        except Exception as exc:
            rows.append({"case_uid": case_uid, "cp_id": cp_id,
                         "status": "JSON_LOAD_FAILED",
                         "detail": type(exc).__name__})
            stats["proof_missing"] += 1
            continue

        decisive = decisive_requirement_ids(requirement_result)
        if not decisive:
            rows.append({"case_uid": case_uid, "cp_id": cp_id,
                         "status": "NO_DECISIVE_REQUIREMENTS"})
            stats["no_decisive"] += 1
            continue

        supports = supporting_evidence_ids(proof, decisive)

        if len(supports) != 1:
            if len(supports) > 1:
                stats["multi_support"] += 1
                rows.append({"case_uid": case_uid, "cp_id": cp_id,
                             "status": "MULTI_SUPPORT",
                             "support_count": len(supports)})
            else:
                stats["no_support_evidence"] += 1
                rows.append({"case_uid": case_uid, "cp_id": cp_id,
                             "status": "NO_SUPPORT_EVIDENCE_RECORDED"})
            continue
        stats["sole_support"] += 1

        sid = supports[0]
        try:
            text = index_chunk_text(requirement_result).get(sid)
        except Exception:
            text = None

        if not text:
            rows.append({"case_uid": case_uid, "cp_id": cp_id,
                         "status": "LOCATOR_TEXT_NOT_FOUND",
                         "evidence_id": sid})
            continue
        stats["locator_resolved"] += 1

        locator = {"evidence_id": sid, "file": file_of(sid), "text": text,
                   "cp_id": cp_id}
        # One locator per case: H7 deletes from a case copy, so a second CP on
        # the same case would need its own copy. Keep the first and report the
        # rest, rather than silently overwriting.
        if case_uid in eligible:
            rows.append({"case_uid": case_uid, "cp_id": cp_id,
                         "status": "ADDITIONAL_ELIGIBLE_COORDINATE"})
        else:
            eligible[case_uid] = locator
        rows.append({"case_uid": case_uid, "cp_id": cp_id,
                     "status": "ELIGIBLE", "evidence_id": sid,
                     "file": locator["file"], "text_len": len(text)})

    return {
        "schema": "freca-h7-support-locator-export-v1",
        "run_dir": str(run_dir),
        "stats": stats,
        "real_copy_h7_eligible_count": len(eligible),
        "locators": eligible,
        "coordinates": rows,
        "note": (
            "real_copy_h7_eligible_count is itself a measurement: a small "
            "count means few coordinates were held compliant on a single "
            "admissible support, which reports how conservative the "
            "admission gate is. Zero means H7 must fall back to synthetic "
            "fixtures and must be recorded as a warning, not as coverage."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests(a.run_dir)
        return 0

    report = collect(a.run_dir)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()
                      if k != "locators" and k != "coordinates"},
                     ensure_ascii=False, indent=2))
    return 0


def run_self_tests(tmp: Path) -> None:
    """Build a run tree in the shapes the runner and proof layer actually write.

    Two earlier versions of this test invented shapes that the pipeline does not
    produce, so they validated the reader against its own assumptions:

      * task_meta identifiers were read at the top level, while
        `prepare_task_meta` nests them under "inputs";
      * the sole support was taken from `supporting_statement_ids`, which holds
        statement identifiers, not the evidence chunk ids H7 has to delete.

    Both would have passed the old fixtures and found nothing on a real run.
    The fixtures below therefore mirror the producing code exactly.
    """
    import shutil
    root = tmp / "_selftest_run"
    if root.exists():
        shutil.rmtree(root)

    def make(case_uid, cp_id, label, internal, evidence,
             fallback=False, decisive=True, meta_identity=True,
             accepted=True, repair_evidence=None, text="ROW TEXT"):
        d = root / "tasks" / case_uid / cp_id
        (d / "initial" / "layer7").mkdir(parents=True)

        meta = {"schema": "freca-production-task-meta-v1",
                "input_fingerprint": "deadbeef"}
        meta["inputs"] = ({"case_uid": case_uid, "cp_id": cp_id}
                          if meta_identity else {"retrieval_top_k": 8})
        (d / "task_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        (d / "fold_decision_v3.json").write_text(json.dumps(
            {"label": label, "finality": "EVIDENCE_DEMONSTRATED",
             "benchmark_fallback": fallback, "forced": fallback}),
            encoding="utf-8")

        (d / "core_outcome_adapter_v1.json").write_text(json.dumps(
            {"common_internal_outcome": internal,
             "supporting_statement_ids": ["stmt-er1"]}), encoding="utf-8")

        all_ev = sorted(set(evidence) | set(repair_evidence or []))
        (d / "initial" / "requirement_result.json").write_text(json.dumps(
            {"case_uid": case_uid, "cp_id": cp_id,
             "evidence_requirement_plan": {"requirements": [
                 {"requirement_id": "ER1",
                  "decisiveness": "DECISIVE" if decisive else "CORROBORATIVE"}]},
             "alignments": [{"requirement_id": "ER1", "evidence_id": e,
                             "exact_quote": text} for e in all_ev]}),
            encoding="utf-8")

        def proof(ev):
            return {"requirement_reports": [
                {"requirement_id": "ER1",
                 "statement_id": "stmt-er1",
                 "support_proof": {"accepted_direction": accepted,
                                   "basis_evidence_ids": list(ev)},
                 "attack_proof": {"accepted_direction": False,
                                  "basis_evidence_ids": []}}]}

        (d / "initial" / "layer7" / "proof_standard_v1_1.json").write_text(
            json.dumps(proof(evidence)), encoding="utf-8")

        if repair_evidence is not None:
            r = d / "repair" / "round-1" / "after"
            r.mkdir(parents=True)
            (r / "proof_standard_v1_1.json").write_text(
                json.dumps(proof(repair_evidence)), encoding="utf-8")

    make("case_a", "CP1", "1", "PROVEN_COMPLIANT", ["f1.docx:T1:R2"])
    make("case_b", "CP1", "1", "PROVEN_COMPLIANT", ["f9.docx:T3:R7"])
    make("case_c", "CP1", "0", "PROVEN_NON_COMPLIANT", ["f1.docx:T1:R2"])
    make("case_d", "CP1", "1", "PROVEN_COMPLIANT", ["f1.docx:T1:R2"],
         fallback=True)
    make("case_e", "CP1", "1", "PROVEN_COMPLIANT",
         ["f1.docx:T1:R2", "f2.docx:T1:R3"])
    make("case_f", "CP1", "1", "PROVEN_COMPLIANT", ["f5.docx:T1:R1"],
         decisive=False)
    make("case_g", "CP1", "1", "PROVEN_COMPLIANT", ["f6.docx:T1:R1"],
         accepted=False)

    rep = collect(root)
    st = rep["stats"]

    # Distinct eligible cases must not collide on a single dict key. With the
    # identifiers read from the wrong nesting this count reads 1 whatever the
    # run contains.
    assert rep["real_copy_h7_eligible_count"] == 2, st
    assert set(rep["locators"]) == {"case_a", "case_b"}, rep["locators"]
    assert rep["locators"]["case_a"]["evidence_id"] == "f1.docx:T1:R2"
    assert rep["locators"]["case_a"]["file"] == "f1.docx"
    assert rep["locators"]["case_a"]["text"] == "ROW TEXT"
    assert rep["locators"]["case_b"]["file"] == "f9.docx"

    assert st["label_1"] == 6, st          # every case but case_c
    assert st["not_fallback"] == 5, st     # case_d excluded
    assert st["multi_support"] == 1, st    # case_e
    assert st["no_decisive"] == 1, st      # case_f: ER1 is CORROBORATIVE
    assert st["no_support_evidence"] == 1, st   # case_g: not accepted
    assert st["identity_unresolved"] == 0, st
    assert st["locator_resolved"] == 2, st

    # A repair round supersedes the initial bundle, exactly as replay resolves
    # it. Building H7 from the superseded verdict would delete the wrong span.
    shutil.rmtree(root)
    make("case_r", "CP2", "1", "PROVEN_COMPLIANT", ["old.docx:T1:R1"],
         repair_evidence=["new.docx:T2:R4"])
    rep = collect(root)
    assert rep["real_copy_h7_eligible_count"] == 1, rep["stats"]
    assert rep["locators"]["case_r"]["evidence_id"] == "new.docx:T2:R4"

    # task_meta without identifiers falls back to the directory layout.
    shutil.rmtree(root)
    make("case_z", "CP7", "1", "PROVEN_COMPLIANT", ["f4.docx:T2:R1"],
         meta_identity=False)
    rep = collect(root)
    assert rep["real_copy_h7_eligible_count"] == 1, rep["stats"]
    assert rep["locators"]["case_z"]["cp_id"] == "CP7"

    shutil.rmtree(root)
    print("support_locator_export_v1 self-tests: PASS")


if __name__ == "__main__":
    raise SystemExit(main())
