#!/usr/bin/env python3
"""How much of the case actually reaches the judge? Post-hoc, zero API.

WHY THIS EXISTS
---------------
A controlled experiment on this project already located the failure that sank
the v7 architecture: the ledger carried 6.6-7.0% of the substantive spans, so
93% of each case never reached the layer that decided the label. The only
change that helped was putting the raw case back in front of the judge, worth
+0.11; five of six other changes were neutral or harmful. The conclusion
recorded at the time was that the ledger belongs on top of the source, not in
place of it.

FRECA-GRACE V5 retrieves `retrieval_top_k` chunks per requirement (default 12)
from cases whose median size is around 1,400 non-empty lines, and every layer
after retrieval - alignment, proof standard, argument, fold - reads the
retrieved material rather than the case. That is the same shape. Whether it is
the same magnitude is a measurement, not an argument, and this module makes it.

WHAT IT MEASURES
----------------
Per coordinate, the distinct evidence chunks that survive to each stage:

    available      every chunk parsed from the case      (the denominator)
    retrieved      chunks appearing in retrieval_traces
    aligned        chunks appearing in alignments
    decisive       chunks carrying an accepted direction on a decisive
                   requirement, i.e. what actually produced the verdict

reported both by chunk count and by character count, because chunks differ
widely in size and "share of the case" is a claim about text, not about items.

HOW IT IDENTIFIES CHUNKS
------------------------
By intersection, not by pattern. Every string in a stage's subtree is collected
and intersected with the set of ids the parser actually emitted for that case.
Nothing is assumed about where an id sits or what key holds it, which is the
one assumption that has repeatedly been wrong in this codebase.

WHAT IT CANNOT SAY
------------------
Nothing about accuracy. A judge that sees 5% of a case may still be right, and
one that sees all of it may still be wrong. The number bears on one specific
prior finding: if coverage here lands near v7's 6.6-7.0%, the architecture
carries the bottleneck that was already measured as fatal there; if it is much
higher, that particular objection does not apply.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path
from typing import Any

import support_locator_export_v1 as loc

# The coverage the v7 post-mortem measured for the layer that fed its judge.
V7_LEDGER_COVERAGE = (0.066, 0.070)


def strings_in(node: Any, out: set[str]) -> None:
    """Every string anywhere in a subtree, keys included."""
    if isinstance(node, str):
        out.add(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                out.add(key)
            strings_in(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            strings_in(item, out)


def ids_in(node: Any, universe: set[str]) -> set[str]:
    """Chunk ids reachable in `node`, by intersection with what the parser emitted.

    Intersecting is what makes this safe. Collecting by key name would need to
    know which key holds an id at each layer, and that assumption has been
    wrong more than once here; collecting by pattern would admit anything that
    looks like `<file>:P<n>`. The parser's own id list is the ground truth for
    what is a chunk, so anything in it is one and anything else is not.
    """
    found: set[str] = set()
    strings_in(node, found)
    return found & universe


def load_case_chunks(run_root: Path, case_uid: str) -> list[dict] | None:
    """The full parsed case, wherever the run put it.

    The runner writes `<run_dir>/cases/<case_uid>/evidence_chunks.json`, and
    `run_dir` is the shard (`.../production_run_v1_full_4100/shard-001`) while
    the validation entry point is usually handed the root above it. Looking
    only beside the given path finds nothing in that case, and finding nothing
    reads as a case with no chunks, which is the same number a real bottleneck
    produces. So the shard directories below are searched too, and a case that
    genuinely cannot be located is reported rather than counted as empty.
    """
    candidates = [run_root / "cases" / case_uid / "evidence_chunks.json"]
    candidates += sorted(
        run_root.glob(f"*/cases/{case_uid}/evidence_chunks.json"))
    candidates += sorted(
        run_root.glob(f"*/*/cases/{case_uid}/evidence_chunks.json"))
    for path in candidates:
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            return value if isinstance(value, list) else None
    return None


def measure_coordinate(task_dir: Path, chunks: list[dict]) -> dict[str, Any]:
    """Coverage at each stage for one case x checking point."""
    universe = {str(c.get("id")) for c in chunks if c.get("id")}
    size = {str(c.get("id")): len(str(c.get("text") or "")) for c in chunks}
    total_chars = sum(size.values())

    rr_path = task_dir / "initial" / "requirement_result.json"
    proof_path = loc.final_proof_path(task_dir)
    if not rr_path.is_file():
        return {"status": "NO_REQUIREMENT_RESULT"}

    try:
        rr = json.loads(rr_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": f"UNREADABLE:{type(exc).__name__}"}

    stages: dict[str, set[str]] = {}
    # Absent sections are reported as such rather than as an empty stage: a
    # renamed key would otherwise read as "the judge saw nothing", which is the
    # same number a real bottleneck produces.
    missing: list[str] = []
    for name, node in (("retrieved", rr.get("retrieval_traces")),
                       ("aligned", rr.get("alignments"))):
        if node is None:
            missing.append(name)
            continue
        stages[name] = ids_in(node, universe)

    decisive: set[str] = set()
    if proof_path.is_file():
        try:
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            want = loc.decisive_requirement_ids(rr)
            for row in proof.get("requirement_reports", []) or []:
                if str(row.get("requirement_id")) not in want:
                    continue
                for key in ("support_proof", "attack_proof"):
                    d = row.get(key) or {}
                    if d.get("accepted_direction") is not True:
                        continue
                    decisive |= {e for e in (d.get("basis_evidence_ids") or [])
                                 if isinstance(e, str)} & universe
            stages["decisive"] = decisive
        except Exception:
            missing.append("decisive")
    else:
        missing.append("decisive")

    out: dict[str, Any] = {
        "status": "OK",
        "available_chunks": len(universe),
        "available_chars": total_chars,
        "missing_stages": missing,
    }
    for name, ids in stages.items():
        chars = sum(size.get(i, 0) for i in ids)
        out[name] = {
            "chunks": len(ids),
            "chunk_share": (len(ids) / len(universe)) if universe else None,
            "chars": chars,
            "char_share": (chars / total_chars) if total_chars else None,
        }
    return out


def survey(run_root: Path) -> dict[str, Any]:
    per_coordinate: list[dict] = []
    chunk_cache: dict[str, list[dict] | None] = {}
    skipped: collections.Counter = collections.Counter()

    for meta_path in sorted(run_root.rglob("task_meta.json")):
        task = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            skipped["UNREADABLE_META"] += 1
            continue
        inputs = meta.get("inputs") if isinstance(meta.get("inputs"), dict) else {}
        case_uid = inputs.get("case_uid") or task.parent.name
        cp_id = inputs.get("cp_id") or task.name

        if case_uid not in chunk_cache:
            chunk_cache[case_uid] = load_case_chunks(run_root, str(case_uid))
        chunks = chunk_cache[case_uid]
        if not chunks:
            skipped["NO_PARSED_CHUNKS"] += 1
            continue

        row = measure_coordinate(task, chunks)
        if row.get("status") != "OK":
            skipped[row.get("status", "UNKNOWN")] += 1
            continue
        row["case_uid"] = str(case_uid)
        row["cp_id"] = str(cp_id)
        per_coordinate.append(row)

    def shares(stage: str, field: str) -> list[float]:
        return [r[stage][field] for r in per_coordinate
                if stage in r and r[stage].get(field) is not None]

    summary: dict[str, Any] = {}
    for stage in ("retrieved", "aligned", "decisive"):
        vals = shares(stage, "char_share")
        cvals = shares(stage, "chunk_share")
        if not vals:
            summary[stage] = None
            continue
        summary[stage] = {
            "coordinates": len(vals),
            "char_share_median": statistics.median(vals),
            "char_share_mean": statistics.mean(vals),
            "char_share_p10": sorted(vals)[max(0, int(0.10 * len(vals)) - 1)],
            "char_share_p90": sorted(vals)[min(len(vals) - 1, int(0.90 * len(vals)))],
            "chunk_share_median": statistics.median(cvals) if cvals else None,
        }

    # Per checking point, so a starved CP is visible rather than averaged away.
    by_cp: dict[str, list[float]] = collections.defaultdict(list)
    for r in per_coordinate:
        if "aligned" in r and r["aligned"].get("char_share") is not None:
            by_cp[r["cp_id"]].append(r["aligned"]["char_share"])
    cp_rows = sorted(
        ({"cp_id": cp, "n": len(v), "aligned_char_share_median":
          statistics.median(v)} for cp, v in by_cp.items()),
        key=lambda d: d["aligned_char_share_median"])

    # The reading, stated against the figure this probe exists to test.
    aligned = summary.get("aligned")
    verdict = None
    if aligned:
        m = aligned["char_share_median"]
        lo, hi = V7_LEDGER_COVERAGE
        if m <= hi * 1.5:
            verdict = ("SAME_MAGNITUDE_AS_V7_LEDGER: the judge sees a share of "
                       "the case comparable to the bottleneck measured as v7's "
                       "root cause")
        elif m < 0.25:
            verdict = ("NARROW_BUT_ABOVE_V7: wider than the v7 ledger, still a "
                       "small minority of the case")
        else:
            verdict = ("BROAD: the judge sees a substantial share of the case; "
                       "the v7 bottleneck objection does not transfer")

    return {
        "schema": "freca-evidence-coverage-probe-v1",
        "run_root": str(run_root),
        "coordinates_measured": len(per_coordinate),
        "skipped": dict(skipped),
        "v7_ledger_coverage_reference": list(V7_LEDGER_COVERAGE),
        "summary": summary,
        "reading": verdict,
        "lowest_coverage_cps": cp_rows[:10],
        "highest_coverage_cps": cp_rows[-5:],
        "sample": per_coordinate[:20],
        "note": (
            "Coverage is not accuracy. A judge shown 5% of a case may still be "
            "right. The figure bears on one prior finding only: v7's judge saw "
            "6.6-7.0% of the substantive spans, and restoring the raw case was "
            "the single change that helped."
        ),
    }


def run_self_tests(tmp: Path) -> None:
    import shutil
    root = tmp / "_coverage_selftest"
    if root.exists():
        shutil.rmtree(root)

    # A case of 100 chunks; the coordinate retrieves 12, aligns 8, rests on 2.
    chunks = [{"id": f"f.docx:P{i}", "file": "f.docx", "text": "x" * 100}
              for i in range(100)]
    (root / "cases" / "case-001").mkdir(parents=True)
    (root / "cases" / "case-001" / "evidence_chunks.json").write_text(
        json.dumps(chunks), encoding="utf-8")

    d = root / "tasks" / "case-001" / "CP1"
    (d / "initial" / "layer7").mkdir(parents=True)
    (d / "task_meta.json").write_text(json.dumps(
        {"inputs": {"case_uid": "case-001", "cp_id": "CP1"}}), encoding="utf-8")
    (d / "initial" / "requirement_result.json").write_text(json.dumps({
        "evidence_requirement_plan": {"requirements": [
            {"requirement_id": "ER1", "decisiveness": "DECISIVE"}]},
        # Ids buried at different depths and under different key names, which
        # is why the probe intersects rather than reads named fields.
        "retrieval_traces": [
            {"need": "n1", "candidates": [{"chunk": {"id": f"f.docx:P{i}"}}
                                          for i in range(12)]}],
        "alignments": [{"requirement_id": "ER1", "evidence_id": f"f.docx:P{i}",
                        "relation": "SUPPORT"} for i in range(8)],
    }), encoding="utf-8")
    (d / "initial" / "layer7" / "proof_standard_v1_1.json").write_text(json.dumps({
        "requirement_reports": [{"requirement_id": "ER1",
                                 "support_proof": {
                                     "accepted_direction": True,
                                     "basis_evidence_ids": ["f.docx:P0",
                                                            "f.docx:P1"]},
                                 "attack_proof": {"accepted_direction": False,
                                                  "basis_evidence_ids": []}}]}),
        encoding="utf-8")

    rep = survey(root)
    assert rep["coordinates_measured"] == 1, rep["skipped"]
    s = rep["summary"]
    assert abs(s["retrieved"]["char_share_median"] - 0.12) < 1e-9, s["retrieved"]
    assert abs(s["aligned"]["char_share_median"] - 0.08) < 1e-9, s["aligned"]
    assert abs(s["decisive"]["char_share_median"] - 0.02) < 1e-9, s["decisive"]
    # 8% sits within 1.5x of v7's 7%, so the reading must say so rather than
    # leaving the comparison to the reader.
    assert rep["reading"].startswith("SAME_MAGNITUDE_AS_V7"), rep["reading"]
    assert rep["lowest_coverage_cps"][0]["cp_id"] == "CP1"

    # A renamed section must be reported, not counted as zero coverage: the two
    # are indistinguishable in the number and opposite in meaning.
    rr = d / "initial" / "requirement_result.json"
    doc = json.loads(rr.read_text(encoding="utf-8"))
    doc["traces"] = doc.pop("retrieval_traces")
    rr.write_text(json.dumps(doc), encoding="utf-8")
    rep = survey(root)
    assert "retrieved" in rep["sample"][0]["missing_stages"], rep["sample"][0]
    assert rep["summary"]["retrieved"] is None
    assert rep["summary"]["aligned"] is not None, "one missing stage killed the rest"

    # Broad coverage must read as broad, or the probe cannot exonerate.
    doc["alignments"] = [{"evidence_id": f"f.docx:P{i}"} for i in range(60)]
    doc["retrieval_traces"] = doc.pop("traces")
    rr.write_text(json.dumps(doc), encoding="utf-8")
    assert survey(root)["reading"].startswith("BROAD"), survey(root)["reading"]

    # The real layout puts cases/ and tasks/ under a shard, while the
    # validation entry point is handed the root above it. Searching only
    # beside the given path found nothing there, and nothing reads as a case
    # with no evidence, which is the number a real bottleneck also produces.
    shard = root / "shard-001"
    shard.mkdir()
    shutil.move(str(root / "cases"), str(shard / "cases"))
    shutil.move(str(root / "tasks"), str(shard / "tasks"))
    rep = survey(root)
    assert rep["coordinates_measured"] == 1, rep["skipped"]
    shutil.move(str(shard / "cases"), str(root / "cases"))
    shutil.move(str(shard / "tasks"), str(root / "tasks"))
    shard.rmdir()

    # No parsed chunks means unmeasurable, not zero.
    shutil.rmtree(root / "cases")
    rep = survey(root)
    assert rep["coordinates_measured"] == 0
    assert rep["skipped"].get("NO_PARSED_CHUNKS") == 1, rep["skipped"]

    shutil.rmtree(root)
    print("evidence_coverage_probe_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True,
                    help="v1 run root containing cases/ and tasks/")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests(a.run_root)
        return 0

    rep = survey(a.run_root)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
