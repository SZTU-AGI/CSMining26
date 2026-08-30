#!/usr/bin/env python3
"""How wide is retrieval, measured without a run? Real parser, real BM25, no API.

WHY THIS EXISTS
---------------
A hypothesis worth testing cheaply: FRECA-GRACE V5 judges on retrieved material
rather than on the case, which is the shape of the failure a controlled
experiment already pinned on the v7 architecture, where the layer feeding the
judge carried 6.6-7.0% of the substantive spans and restoring the raw case was
the only change that helped.

Retrieval is the one stage of that pipeline that can be exercised offline. It
needs no compiled contract, no model and no completed run: the parser, the BM25
ranking and `retrieve_requirement_candidates` all run on case files alone. So
the hypothesis can be tested before committing to anything.

MEASURED, AND THE ANSWER WAS NO
-------------------------------
164 real retrievals over four real cases and all 41 checking points:

    per coordinate   chunk share 10.7% (p10 8.1, p90 24.1)
                     char  share 21.1% (p10 13.1, p90 36.5)
    41 CPs unioned   chunk share 60.7%   char share 69.1%

against the v7 ledger's 6.6-7.0%. Shortening the query from a whole checking
point (18 words) to six words moves the char share only from 22.3% to 17.5%, so
the figure is not an artefact of long queries.

`top_k` is not the binding constraint, which is why estimating from it is
misleading and why this had to be measured. `retrieve_requirement_candidates`
bounds the lexical candidate universe at 40, caps support and attack context at
max(24, top_k) each, and expands to adjacent paragraphs and rows. The width is
several times what top_k alone suggests.

WHAT IT DOES NOT SETTLE
-----------------------
The judge reads alignments, not retrievals. The identity gate and the aligner
both narrow further, and both need a model. A wide retrieval does not establish
a wide input to the judge; it rules out one specific explanation for a narrow
one. Measuring the rest needs a completed run, which is what
`evidence_coverage_probe_v1.survey` reads.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import statistics
from pathlib import Path
from typing import Any

from evidence_coverage_probe_v1 import V7_LEDGER_COVERAGE, ids_in


def load_cp_texts(path: Path) -> dict[str, str]:
    """Checking-point text: a `CPn` heading followed by its indented body."""
    out: dict[str, list[str]] = {}
    current = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        heading = re.match(r"^(CP\d+)\s", line.strip())
        if heading:
            current = heading.group(1)
            out[current] = []
        elif current and line.strip():
            out[current].append(line.strip())
    return {k: " ".join(v) for k, v in out.items() if v}


def needs_for(facet: str) -> list[dict]:
    """Shaped as `build_retrieval_needs` emits it.

    One requirement becomes two needs, SUPPORT and ATTACK, sharing the query
    variants. Getting this shape wrong would measure a different function than
    the one the pipeline calls.
    """
    variants = [{"variant_id": "ER1.proposition",
                 "source": "EVIDENCE_REQUIREMENT", "candidate_id": None,
                 "query": facet, "transformation": "FROZEN_PROPOSITION"}]
    return [{"need_id": f"ER1.{direction.lower()}", "requirement_id": "ER1",
             "atom_id": "A1", "direction": direction,
             "priority_class": ("DECISIVE" if direction == "SUPPORT"
                                else "COUNTEREVIDENCE"),
             "query_facets": [facet], "query_variants": variants,
             "coverage_requirement": "CANDIDATE_DISCOVERY"}
            for direction in ("SUPPORT", "ATTACK")]


def _stat(rows: list[dict], field: str) -> dict[str, float] | None:
    vals = sorted(r[field] for r in rows)
    if not vals:
        return None
    return {"median": statistics.median(vals),
            "p10": vals[max(0, int(0.10 * len(vals)) - 1)],
            "p90": vals[min(len(vals) - 1, int(0.90 * len(vals)))]}


def measure(case_dirs: list[Path], cp_texts: dict[str, str], *,
            top_k: int = 12, query_words: int | None = None) -> dict[str, Any]:
    """Run the real retrieval for every (case, checking point) pair."""
    import freca_core_v1 as core
    import evidence_reasoning_v2 as er

    per_coordinate: list[dict] = []
    per_case_union: list[dict] = []
    failures: list[str] = []

    for case_dir in case_dirs:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                chunks = core.load_case_evidence(case_dir)
        except Exception as exc:
            failures.append(f"{case_dir.name}: {type(exc).__name__}")
            continue

        universe = {str(c.get("id")) for c in chunks if c.get("id")}
        size = {str(c.get("id")): len(str(c.get("text") or "")) for c in chunks}
        total = sum(size.values())
        if not universe or not total:
            failures.append(f"{case_dir.name}: EMPTY")
            continue

        union: set[str] = set()
        for cp_id, text in cp_texts.items():
            query = (text if query_words is None
                     else " ".join(text.split()[:query_words]))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    traces = er.retrieve_requirement_candidates(
                        chunks, needs_for(query), top_k=top_k)
            except Exception as exc:
                failures.append(f"{case_dir.name}/{cp_id}: {type(exc).__name__}")
                continue
            got = ids_in(traces, universe)
            union |= got
            per_coordinate.append({
                "case": case_dir.name, "cp_id": cp_id,
                "chunk_share": len(got) / len(universe),
                "char_share": sum(size[i] for i in got) / total})

        if union:
            per_case_union.append({
                "case": case_dir.name,
                "chunk_share": len(union) / len(universe),
                "char_share": sum(size[i] for i in union) / total,
                "chunks_available": len(universe),
                "chars_available": total})

    coord_char = _stat(per_coordinate, "char_share")
    lo, hi = V7_LEDGER_COVERAGE
    reading = None
    if coord_char:
        median = coord_char["median"]
        if median > hi * 1.5:
            reading = (
                "RETRIEVAL_NOT_THE_V7_BOTTLENECK: the candidate generator puts "
                f"{median:.1%} of case text on the table, against the "
                f"{lo:.1%}-{hi:.1%} the v7 post-mortem measured. Whatever "
                "narrows the judge's input, it is not this stage.")
        else:
            reading = (
                "RETRIEVAL_AS_NARROW_AS_V7_LEDGER: the candidate generator puts "
                f"{median:.1%} of case text on the table, comparable to the "
                "bottleneck measured as v7's root cause.")

    return {
        "schema": "freca-retrieval-ceiling-v1",
        "cases": len(per_case_union),
        "checking_points": len(cp_texts),
        "retrievals": len(per_coordinate),
        "top_k": top_k,
        "query_words": query_words,
        "per_coordinate_chunk_share": _stat(per_coordinate, "chunk_share"),
        "per_coordinate_char_share": coord_char,
        "per_case_union_chunk_share": _stat(per_case_union, "chunk_share"),
        "per_case_union_char_share": _stat(per_case_union, "char_share"),
        "v7_ledger_coverage_reference": list(V7_LEDGER_COVERAGE),
        "reading": reading,
        "per_case": per_case_union,
        "failures": failures[:20],
        "note": (
            "Retrieval only, and retrieval is not what the judge reads. A wide "
            "retrieval rules out one explanation for a narrow input; it does "
            "not establish a wide one. It is also not accuracy: a judge shown "
            "everything can still be wrong."
        ),
    }


def run_self_tests(tmp: Path) -> None:
    # The needs shape is what makes this measure the pipeline's own function
    # rather than a lookalike; a drift there would silently measure something
    # else and still produce a plausible number.
    needs = needs_for("some proposition")
    assert len(needs) == 2 and {n["direction"] for n in needs} == {"SUPPORT",
                                                                   "ATTACK"}
    assert needs[0]["query_variants"][0]["query"] == "some proposition"
    assert needs[0]["priority_class"] == "DECISIVE"
    assert needs[1]["priority_class"] == "COUNTEREVIDENCE"

    cp_file = tmp / "_cps.txt"
    cp_file.write_text(
        "CP1  [Element-1]\n"
        "    The establishment is operating within its registered operations.\n"
        "    Records are retained.\n"
        "CP2  [Element-2]\n"
        "    A documented pest control programme is in place.\n",
        encoding="utf-8")
    texts = load_cp_texts(cp_file)
    assert set(texts) == {"CP1", "CP2"}, texts
    assert texts["CP1"].startswith("The establishment is operating")
    assert "Records are retained." in texts["CP1"], "body lines must join"
    cp_file.unlink()

    # A case directory that cannot be parsed is reported, not counted as a
    # case with no evidence: those two produce the same coverage number and
    # mean opposite things.
    empty = tmp / "_ceiling_selftest"
    (empty / "not_a_case").mkdir(parents=True, exist_ok=True)
    rep = measure([empty / "not_a_case"], {"CP1": "anything"})
    assert rep["cases"] == 0, rep
    assert rep["failures"], "an unparseable case must be reported"
    assert rep["reading"] is None, "no data must not produce a reading"
    (empty / "not_a_case").rmdir()
    empty.rmdir()

    print("retrieval_ceiling_probe_v1 self-tests: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-root", type=Path,
                    help="directory containing one directory per case")
    ap.add_argument("--cp-text", type=Path,
                    help="checking-point text file")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--top-k", type=int, default=12)
    ap.add_argument("--query-words", type=int, default=None,
                    help="truncate each query, for the sensitivity check")
    ap.add_argument("--max-cases", type=int, default=4)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests(Path(a.output or ".").parent if a.output else Path("."))
        return 0

    if not a.evidence_root or not a.cp_text:
        ap.error("--evidence-root and --cp-text are required unless --self-test")

    dirs = sorted(d for d in a.evidence_root.iterdir() if d.is_dir())
    rep = measure(dirs[:a.max_cases], load_cp_texts(a.cp_text),
                  top_k=a.top_k, query_words=a.query_words)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
