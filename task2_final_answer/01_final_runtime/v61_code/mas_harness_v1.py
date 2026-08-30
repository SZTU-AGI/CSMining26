#!/usr/bin/env python3
"""Metamorphic mutation harness (V5 §8); no API calls of its own.

WHY THIS EXISTS
---------------
V5 §8 specifies Hard-MAS H1-H8 as gates that block official-ready export.
A grep of the production bundle finds no metamorphic, mutation, perturbation or
H7 implementation anywhere: the entire suite existed on paper only. Nothing
currently tests how the system responds when the evidence is changed in ways
whose correct response is known without labels.

WHAT A MUTATION ORACLE BUYS YOU
-------------------------------
The task supplies no labels, so accuracy is unmeasurable. But f(x) and f(T(x))
must stand in a known relation for many transformations T, and that relation is
checkable. Reordering documents must not move a label. Duplicating evidence
must not upgrade one. Deleting the sole support for a mandatory fact must not
leave a compliant verdict standing.

STRUCTURE
---------
Mutations are pure functions over a parsed case. Oracles are pure predicates
over (before, after) outcomes. Neither calls a model. The driver takes a
`rerun` callable supplied by the caller, so the same oracles run against a stub
here and against the real pipeline on the run host.

HARD vs DIAGNOSTIC (§8.1 vs §8.3)
---------------------------------
Hard oracles assert relations that are mechanically true regardless of case
semantics. Diagnostic ones involve semantic truth and are reported only; they
are never summed into an accuracy figure. Getting that boundary wrong turns
metamorphic testing into a suite that quietly assumes its own answer, so each
oracle below states which side it is on and why.

DELIBERATELY WEAK ORACLES
-------------------------
H4 asserts only that a decisive threshold crossing changes *something*
observable, not that the label flips: other requirements may dominate the fold,
so a label-level flip is not mechanically entailed. H5 asserts monotonicity of
observed span, not a label relation. Overstating either would manufacture
failures that are not defects.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Callable

TRACK_HEADER = re.compile(r"^TRACK (\d+) —— 文件名: (.+)$", re.M)
RE_NUMBER = re.compile(r"\bRE-[A-Z]{2,3}-\d{4}-\d{3,4}\b")
NUM_UNIT = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(ha|hectares?|years?|months?|days?|kg|t|%)\b", re.I)
DATE = re.compile(r"\b(\d{1,2} [A-Z][a-z]+ 20\d\d)\b")

# The case files write dates three ways, and a scan that sees only one of them
# reads roughly a third of the dates present. H5 claims to remove the EARLIEST
# dated line, so a partial scan does not merely reduce coverage: it removes
# whichever line happens to come first in document order among the formats it
# can see, which need not be the earliest, and the premise that the observed
# span shortens no longer holds.
DATE_ISO = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")
DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b")
DATE_NAMED = re.compile(r"\b(\d{1,2}) ([A-Z][a-z]+) (20\d\d)\b")

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def earliest_date_in(text: str) -> tuple[int, int, int] | None:
    """Smallest (year, month, day) appearing in `text`, across all formats.

    Ambiguous day/month order in the slash form is resolved by treating a value
    above twelve as the day; where both are twelve or below the reading is
    genuinely undecidable from the text, so day-first is assumed and the
    resulting date is still a real date in the file. H5 only needs a consistent
    ordering to pick a line, not a calendar-correct parse.
    """
    found: list[tuple[int, int, int]] = []
    for m in DATE_ISO.finditer(text):
        found.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in DATE_SLASH.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        day, month = (a, b) if a > 12 or b <= 12 else (b, a)
        found.append((y, month, day))
    for m in DATE_NAMED.finditer(text):
        month = _MONTHS.get(m.group(2).lower())
        if month:
            found.append((int(m.group(3)), month, int(m.group(1))))
    return min(found) if found else None


def stable_hash(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_case_dump(path: Path) -> dict[str, Any]:
    """Parse a case dump into {case_uid, tracks:[{no, filename, text, chunks}]}.

    One container may hold more than nine tracks; that is the known
    99-containers/100-logical-cases split, not a parse error, so the loader
    keeps every track it finds rather than truncating to nine.

    Each track is given a single synthetic chunk. The dumps are flat text and
    carry no chunk boundaries, so this is the honest representation: it keeps
    the structure uniform with `case_from_chunks` without inventing a
    segmentation the dump does not contain. Cases loaded this way exercise the
    mutations and oracles; only cases built from real chunks can be replayed
    through the pipeline.
    """
    text = path.read_text(encoding="utf-8")
    marks = list(TRACK_HEADER.finditer(text))
    tracks = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        filename = m.group(2).strip()
        body = text[m.end():end]
        tracks.append({
            "no": int(m.group(1)),
            "filename": filename,
            "text": body,
            "chunks": [{"id": f"{filename}:P0", "file": filename,
                        "text": body}],
        })
    uid = path.stem
    found = RE_NUMBER.findall(text)
    return {
        "case_uid": uid,
        "re_number": found[0] if found else None,
        "tracks": tracks,
        "chunk_native": False,
    }


def case_from_chunks(case_uid: str, chunks: list[dict],
                     re_number: str | None = None) -> dict[str, Any]:
    """Build a mutable case from the evidence chunks the pipeline really uses.

    `freca_core_v1.parse_docx` emits `{"id": "<file>:P3" | "<file>:T1:R5",
    "file": <file>, "text": ...}`. Grouping by file reproduces the track view
    the mutations are written against, while keeping every chunk addressable so
    a mutated case can be handed straight back to `run_task`.

    Track order follows first appearance, which is the order the parser emitted
    and therefore the order the pipeline would have seen.
    """
    order: list[str] = []
    by_file: dict[str, list[dict]] = {}
    for ch in chunks:
        f = str(ch.get("file") or str(ch.get("id", "")).split(":", 1)[0])
        if f not in by_file:
            by_file[f] = []
            order.append(f)
        by_file[f].append({"id": ch.get("id"), "file": f,
                           "text": str(ch.get("text") or "")})

    tracks = []
    for i, f in enumerate(order, start=1):
        group = by_file[f]
        tracks.append({
            "no": i,
            "filename": f,
            "text": "\n".join(c["text"] for c in group),
            "chunks": group,
        })

    if re_number is None:
        joined = "\n".join(t["text"] for t in tracks)
        found = RE_NUMBER.findall(joined)
        re_number = found[0] if found else None

    return {"case_uid": case_uid, "re_number": re_number,
            "tracks": tracks, "chunk_native": True}


def case_to_chunks(case: dict) -> list[dict]:
    """Flatten a (possibly mutated) case back into a chunk list for `run_task`.

    Chunk ids must stay unique: H2 duplicates a whole track, and two chunks
    sharing an id would collide in every downstream index, silently dropping
    one copy and defeating the very mutation under test. Duplicates are
    therefore suffixed, and the suffix is visible in the id so a reviewer can
    see which chunks came from a duplication.
    """
    out: list[dict] = []
    seen: dict[str, int] = {}
    for t in case["tracks"]:
        group = t.get("chunks")
        if not group:
            group = [{"id": f"{t['filename']}:P0", "file": t["filename"],
                      "text": t["text"]}]
        for c in group:
            cid = str(c.get("id") or f"{t['filename']}:P0")
            if cid in seen:
                seen[cid] += 1
                cid = f"{cid}#dup{seen[cid]}"
            else:
                seen[cid] = 0
            out.append({"id": cid, "file": t["filename"],
                        "text": str(c.get("text") or "")})
    return out


def edit_track(track: dict, fn) -> int:
    """Apply a text transformation to a track, chunk by chunk.

    The chunks are authoritative because they are what the pipeline consumes;
    the track text is a view over them and is recomputed here rather than
    edited in parallel. Editing both independently desynchronises them whenever
    a match spans a chunk boundary: the concatenated text would be rewritten
    while no individual chunk changed, so the oracle would see a mutation the
    pipeline never received, and the test would judge a system on input it was
    never given.

    A match that spans a boundary therefore changes nothing, and the returned
    count is zero. Callers must treat zero as "not constructed" rather than
    assuming the edit landed.
    """
    group = track.get("chunks")
    if not group:
        group = [{"id": f"{track['filename']}:P0",
                  "file": track["filename"], "text": track["text"]}]
        track["chunks"] = group

    changed = 0
    for c in group:
        new = fn(c["text"])
        if new != c["text"]:
            c["text"] = new
            changed += 1

    track["text"] = "\n".join(c["text"] for c in group)
    return changed


def case_signature(case: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            [[t["no"], t["filename"], t["text"]] for t in case["tracks"]],
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------------
# Mutations: pure functions. Return (case, meta) or None when inapplicable.
# --------------------------------------------------------------------------

def m_h1_reorder_tracks(case: dict, rng: random.Random):
    if len(case["tracks"]) < 2:
        return None
    out = copy.deepcopy(case)
    order = list(range(len(out["tracks"])))
    rng.shuffle(order)
    if order == sorted(order):
        order.reverse()
    out["tracks"] = [out["tracks"][i] for i in order]
    return out, {"permutation": order}


def m_h2_duplicate_track(case: dict, rng: random.Random):
    if not case["tracks"]:
        return None
    out = copy.deepcopy(case)
    i = rng.randrange(len(out["tracks"]))
    out["tracks"].insert(i + 1, copy.deepcopy(out["tracks"][i]))
    return out, {"duplicated_track_no": out["tracks"][i]["no"]}


def m_h3a_replace_identity(case: dict, rng: random.Random):
    """Replace every occurrence of this case's RE with a different RE."""
    re_no = case.get("re_number")
    if not re_no:
        return None
    foreign = re_no[:-4] + "9" + re_no[-3:]
    if foreign == re_no:
        return None
    out = copy.deepcopy(case)
    pattern = re.compile(re.escape(re_no))
    n = 0
    for t in out["tracks"]:
        # Counted per chunk, matching where the substitution actually happens.
        # Counting on the concatenated track text would include any occurrence
        # straddling a chunk boundary, which `edit_track` cannot rewrite.
        for c in t.get("chunks") or []:
            n += len(pattern.findall(c["text"]))
        edit_track(t, lambda s: pattern.sub(foreign, s))
    if not n:
        return None
    return out, {"original_re": re_no, "foreign_re": foreign,
                 "replacements": n}


def m_h3b_inject_foreign_contradiction(case: dict, rng: random.Random):
    """Append a block that explicitly belongs to a different establishment."""
    re_no = case.get("re_number")
    if not re_no:
        return None
    foreign = re_no[:-4] + "8" + re_no[-3:]
    out = copy.deepcopy(case)
    body = (
        f"\nRE Number\n | {foreign}\n | \n"
        "Compliance Finding\n | Requirement not met at this establishment"
        "\n | \nRecord Status\n | Adverse\n | \n"
    )
    out["tracks"].append({
        "no": 99,
        "filename": "foreign_record.docx",
        "text": body,
        "chunks": [{"id": "foreign_record.docx:P0",
                    "file": "foreign_record.docx", "text": body}],
    })
    return out, {"foreign_re": foreign}


def m_h4_cross_threshold(case: dict, rng: random.Random):
    """Move one numeric value across an order of magnitude.

    The mutation records what it changed so the oracle can require an
    observable response only when that quantity was decisive.
    """
    for ti, t in enumerate(case["tracks"]):
        for ci, c in enumerate(t.get("chunks") or []):
            for m in NUM_UNIT.finditer(c["text"]):
                val, unit = float(m.group(1)), m.group(2)
                if val <= 0:
                    continue
                new = round(val / 10.0, 3) if val >= 2 else round(val * 10.0, 3)
                out = copy.deepcopy(case)
                tt = out["tracks"][ti]
                cc = tt["chunks"][ci]
                # Edited inside the chunk rather than by an offset into the
                # track text: the offsets come from a match on the chunk, and
                # applying them to the concatenated track text would splice at
                # the wrong position for any track with more than one chunk.
                cc["text"] = (cc["text"][:m.start(1)]
                              + str(new) + cc["text"][m.end(1):])
                tt["text"] = "\n".join(x["text"] for x in tt["chunks"])
                return out, {"track_no": t["no"], "unit": unit,
                             "from": val, "to": new}
    return None


def m_h5_truncate_series(case: dict, rng: random.Random):
    """Remove the earliest dated line, shortening any observed span."""
    # Genuinely the earliest, not merely the first encountered. Scanning in
    # document order and stopping at the first dated line removes an arbitrary
    # date, which need not shorten the observed span at all.
    best = None
    for ti, t in enumerate(case["tracks"]):
        for ci, c in enumerate(t.get("chunks") or []):
            for line in c["text"].splitlines():
                when = earliest_date_in(line)
                if when is not None and (best is None or when < best[0]):
                    best = (when, ti, ci, line)
    if best is None:
        return None

    when, ti, ci, line = best
    out = copy.deepcopy(case)
    tt = out["tracks"][ti]
    cc = tt["chunks"][ci]
    # Remove the line from the chunk that holds it. A chunk need not end in a
    # newline, so the trailing-newline form is tried first and the bare line
    # second; deleting nothing would leave the mutation silently inert.
    if line + "\n" in cc["text"]:
        cc["text"] = cc["text"].replace(line + "\n", "", 1)
    elif line in cc["text"]:
        cc["text"] = cc["text"].replace(line, "", 1)
    else:
        return None
    tt["text"] = "\n".join(x["text"] for x in tt["chunks"])
    return out, {"removed_line": line.strip()[:120],
                 "removed_date": "%04d-%02d-%02d" % when}


def m_h6_delete_track(case: dict, rng: random.Random):
    if len(case["tracks"]) < 2:
        return None
    out = copy.deepcopy(case)
    i = rng.randrange(len(out["tracks"]))
    gone = out["tracks"].pop(i)
    return out, {"deleted_track_no": gone["no"],
                 "deleted_filename": gone["filename"]}


def m_h7_delete_sole_support(case: dict, rng: random.Random,
                             locator: dict | None = None):
    """Remove the span named by `locator`, i.e. the sole admissible support.

    The locator shape follows what the pipeline already produces. Evidence
    chunks are built by `freca_core_v1.parse_docx` as
    `{"id": "<file>:T1:R5", "file": "<file>", "text": ...}`, so a support
    locator is `{"file": ..., "text": ...}`; `evidence_id` is accepted as a
    synonym for the file part, and `track_no` still works when supplied.

    Table rows are joined with " | " when chunked, so the stored text will not
    always appear verbatim in the dump. The fallback therefore matches on the
    longest cell of the row, which does appear, rather than silently failing.

    Without a locator this cannot be constructed honestly: it returns None
    rather than deleting something arbitrary and calling the result H7.
    """
    if not locator:
        return None
    span = (locator.get("text") or "").strip()
    if not span:
        return None

    want_file = locator.get("file")
    if not want_file and locator.get("evidence_id"):
        want_file = str(locator["evidence_id"]).split(":", 1)[0]
    want_track = locator.get("track_no")

    def candidates(text: str) -> list[str]:
        out = [span]
        if " | " in span:
            out.extend(sorted((p.strip() for p in span.split(" | ") if
                               len(p.strip()) >= 8), key=len, reverse=True))
        return out

    out = copy.deepcopy(case)

    # Pick the span to remove from the located file, then remove EVERY
    # occurrence of it across the whole case.
    #
    # Removing a single occurrence does not construct H7. Support text such as
    # a business name recurs across tracks, so deleting one instance leaves the
    # support standing and the oracle would then fail an honest system for
    # continuing to answer 1. The faithful mutation of "delete the sole
    # admissible support" is to delete the content wherever it appears, and to
    # report how many occurrences that took so a reviewer can see when the
    # "sole" support was textually duplicated.
    # The candidate must be deletable, so it is sought inside a single chunk.
    # A span found only in the concatenated track text may straddle a chunk
    # boundary, and `edit_track` would then remove nothing while the mutation
    # reported success: H7 would be recorded as constructed against an input
    # the pipeline never saw.
    chosen = None
    for t in out["tracks"]:
        if want_file and t["filename"] != want_file:
            continue
        if want_track is not None and t["no"] != want_track:
            continue
        for c in t.get("chunks") or []:
            for cand in candidates(c["text"]):
                if cand and cand in c["text"]:
                    chosen = (cand, t["filename"], t["no"])
                    break
            if chosen:
                break
        if chosen:
            break
    if not chosen:
        return None

    cand, matched_file, matched_no = chosen
    removed = 0
    for t in out["tracks"]:
        for c in t.get("chunks") or []:
            removed += c["text"].count(cand)
        edit_track(t, lambda s: s.replace(cand, ""))
    if not removed:
        return None

    return out, {"matched_file": matched_file,
                 "track_no": matched_no,
                 "removed_chars": len(cand) * removed,
                 "occurrences_removed": removed,
                 "support_text_was_duplicated": removed > 1,
                 "matched_whole_row": cand == span}


# --------------------------------------------------------------------------
# Diagnostic mutations (§8.3). These involve semantic truth, so no oracle can
# assert the correct response: injecting a document that *looks* unrelated does
# not make it unrelated, and negating a sentence may genuinely change the case.
# They are reported as CHANGED / UNCHANGED and never enter hard_mas_pass, and
# they must not be summed into anything resembling an accuracy figure.
# --------------------------------------------------------------------------

def m_d1_inject_unrelated(case: dict, rng: random.Random):
    out = copy.deepcopy(case)
    body = ("\nNotice Type\n | Car park resurfacing schedule\n | \n"
            "Contractor\n | Municipal works\n | \n")
    out["tracks"].append({
        "no": 90, "filename": "unrelated_notice.docx", "text": body,
        "chunks": [{"id": "unrelated_notice.docx:P0",
                    "file": "unrelated_notice.docx", "text": body}],
    })
    return out, {"injected": "unrelated_notice.docx"}


def m_d2_inject_opposite_activity(case: dict, rng: random.Random):
    out = copy.deepcopy(case)
    body = ("\nActivity\n | No export activity is carried out at this "
            "establishment\n | \n")
    out["tracks"].append({
        "no": 91, "filename": "activity_statement.docx", "text": body,
        "chunks": [{"id": "activity_statement.docx:P0",
                    "file": "activity_statement.docx", "text": body}],
    })
    return out, {"injected": "activity_statement.docx"}


def m_d3_negate_sentence(case: dict, rng: random.Random):
    """Flip one affirmative row to its negation."""
    for ti, t in enumerate(case["tracks"]):
        for line in t["text"].splitlines():
            row = line.strip()
            if row.startswith("|") and " is " in row and " not " not in row:
                negated = row.replace(" is ", " is not ", 1)
                out = copy.deepcopy(case)
                edit_track(out["tracks"][ti],
                           lambda x: x.replace(row, negated, 1))
                return out, {"line": row[:100]}
    return None


def m_d4_alias_substitute(case: dict, rng: random.Random):
    """Replace the establishment name with an alias spelling."""
    for t in case["tracks"]:
        m = re.search(r"\|\s*([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){1,4})\s*$",
                      t["text"], re.M)
        if m:
            name = m.group(1)
            alias = name.replace(" ", " ").upper()
            if alias == name:
                alias = name + " Pty Ltd"
            out = copy.deepcopy(case)
            for tt in out["tracks"]:
                edit_track(tt, lambda x: x.replace(name, alias))
            return out, {"name": name, "alias": alias}
    return None


def o_diag(before, after, meta):
    """Report-only: state whether the system moved, never whether it was right."""
    if before is None or after is None:
        return "NOT_APPLICABLE", "missing outcome"
    if _label(before) != _label(after):
        return "CHANGED", f"label {_label(before)} -> {_label(after)}"
    if (before.get("internal_outcome") != after.get("internal_outcome")):
        return "CHANGED", "internal outcome moved, label held"
    return "UNCHANGED", "no observable change"


DIAGNOSTICS: dict[str, dict[str, Any]] = {
    "D1": {"fn": m_d1_inject_unrelated,
           "desc": "inject an apparently unrelated document"},
    "D2": {"fn": m_d2_inject_opposite_activity,
           "desc": "inject an opposite-activity statement"},
    "D3": {"fn": m_d3_negate_sentence,
           "desc": "negate one affirmative row"},
    "D4": {"fn": m_d4_alias_substitute,
           "desc": "substitute an alias spelling of the establishment"},
}


MUTATIONS: dict[str, dict[str, Any]] = {
    "H1": {"fn": m_h1_reorder_tracks, "kind": "hard",
           "desc": "reorder tracks"},
    "H2": {"fn": m_h2_duplicate_track, "kind": "hard",
           "desc": "duplicate a track"},
    "H3a": {"fn": m_h3a_replace_identity, "kind": "hard",
            "desc": "replace this case's RE with a foreign RE"},
    "H3b": {"fn": m_h3b_inject_foreign_contradiction, "kind": "hard",
            "desc": "inject an adverse record belonging to a foreign RE"},
    "H4": {"fn": m_h4_cross_threshold, "kind": "hard",
           "desc": "move a numeric value across an order of magnitude"},
    "H5": {"fn": m_h5_truncate_series, "kind": "hard",
           "desc": "remove the earliest dated line"},
    "H6": {"fn": m_h6_delete_track, "kind": "hard",
           "desc": "delete a whole track"},
    "H7": {"fn": m_h7_delete_sole_support, "kind": "hard",
           "desc": "delete the sole admissible support (locator required)"},
}


# --------------------------------------------------------------------------
# Oracles: pure predicates over (before, after). Return (verdict, reason).
#   verdict in {PASS, FAIL, NOT_APPLICABLE}
# --------------------------------------------------------------------------

def _label(o: dict) -> str | None:
    return None if o is None else o.get("label")


def o_h1(before, after, meta):
    if _label(before) == _label(after):
        return "PASS", "label invariant under reordering"
    return "FAIL", f"label moved {_label(before)} -> {_label(after)}"


def o_h2(before, after, meta):
    # Duplication may not upgrade a verdict. Downgrades are not asserted:
    # a duplicate cannot add information, but the system is free to remain
    # unconvinced, so only escalation is a defect.
    if _label(before) != "1" and _label(after) == "1":
        return "FAIL", "duplication upgraded the verdict to 1"
    return "PASS", "no upgrade from duplication"


def o_h3a(before, after, meta):
    # Every locator carrying the case's own RE has been made foreign, so no
    # self-borne support survives. A verdict of 1 that persists is resting on
    # evidence that no longer belongs to this establishment.
    if _label(before) == "1" and _label(after) == "1":
        return "FAIL", "compliant verdict survived loss of self-borne identity"
    return "PASS", "no self-identity leakage"


def o_h3b(before, after, meta):
    if _label(before) == "1" and _label(after) == "0":
        return "FAIL", "foreign adverse record produced a false reject"
    return "PASS", "foreign evidence did not rebut"


def o_h4(before, after, meta):
    # Weak by design: a threshold crossing must be *observable* somewhere, but
    # the fold may still be dominated by another requirement, so a label flip
    # is not mechanically entailed.
    if before is None or after is None:
        return "NOT_APPLICABLE", "missing outcome"
    changed = (before.get("label") != after.get("label")
               or before.get("internal_outcome") != after.get("internal_outcome")
               or before.get("semantic_fingerprint")
               != after.get("semantic_fingerprint"))
    if changed:
        return "PASS", "threshold crossing produced an observable change"
    return "NOT_APPLICABLE", (
        "no observable change; the quantity may not be decisive here")


def o_h5(before, after, meta):
    b = (before or {}).get("observed_span_days")
    a = (after or {}).get("observed_span_days")
    if b is None or a is None:
        return "NOT_APPLICABLE", "observed span not reported"
    if a > b:
        return "FAIL", f"observed span grew after truncation {b} -> {a}"
    return "PASS", "observed span did not grow"


def o_h6(before, after, meta):
    gone = meta.get("deleted_filename")
    cited = set((after or {}).get("cited_sources") or [])
    if gone and gone in cited:
        return "FAIL", f"deleted source still cited: {gone}"
    return "PASS", "no dangling citation"


def o_h7(before, after, meta):
    if _label(before) != "1" or _label(after) != "1":
        return "PASS", "verdict did not remain compliant"

    if (after or {}).get("alternate_support_found"):
        return "PASS", "alternate admissible support was found"

    # The real pipeline reports no such flag, so it is derived: support that
    # the baseline did not rest on is, by definition, support found elsewhere
    # after the sole admissible span was removed. Deriving it here rather than
    # demanding the flag keeps the oracle usable against real output instead of
    # only against a stub that volunteers the answer.
    #
    # Absent both the flag and any recorded support, nothing distinguishes
    # "found an alternate" from "kept answering 1 on nothing", so the oracle
    # declines rather than guessing in either direction.
    b = (before or {}).get("support_evidence_ids")
    a = (after or {}).get("support_evidence_ids")
    if a is None or b is None:
        return "NOT_APPLICABLE", "supporting evidence not reported"
    fresh = sorted(set(a) - set(b))
    if fresh:
        return "PASS", f"alternate support found: {fresh[:3]}"
    if not a:
        return "FAIL", "compliant verdict survived with no support recorded"
    return "FAIL", "compliant verdict survived deletion of its sole support"


ORACLES: dict[str, Callable] = {
    "H1": o_h1, "H2": o_h2, "H3a": o_h3a, "H3b": o_h3b,
    "H4": o_h4, "H5": o_h5, "H6": o_h6, "H7": o_h7,
}


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def run_diagnostics(cases: list[dict], rerun: Callable[[dict], dict], *,
                    sample: int | None = None, seed: int = 0) -> dict:
    """Run §8.3 diagnostics. Never blocks; produces sensitivity counts only."""
    picked = sorted(cases, key=lambda c: stable_hash(c["case_uid"]))
    if sample:
        picked = picked[:sample]

    rows = []
    for case in picked:
        base = rerun(case)
        for did, spec in DIAGNOSTICS.items():
            rng = random.Random(f"{seed}:{did}:{case['case_uid']}")
            made = spec["fn"](case, rng)
            if made is None:
                # Same distinction the suite draws: a diagnostic that could not
                # be built never ran, while NOT_APPLICABLE from `o_diag` means
                # it ran and no outcome came back. Merging them would make a
                # column of unconstructible diagnostics read as a column of
                # systems that failed to answer.
                rows.append({"case_uid": case["case_uid"], "id": did,
                             "verdict": "NOT_CONSTRUCTIBLE"})
                continue
            mutated, meta = made
            verdict, reason = o_diag(base, rerun(mutated), meta)
            rows.append({"case_uid": case["case_uid"], "id": did,
                         "verdict": verdict, "reason": reason})

    by_id: dict[str, dict[str, int]] = {}
    for r in rows:
        d = by_id.setdefault(r["id"], {"CHANGED": 0, "UNCHANGED": 0,
                                       "NOT_APPLICABLE": 0,
                                       "NOT_CONSTRUCTIBLE": 0})
        # Counted tolerantly: an unrecognised verdict must show up in the
        # report, not raise KeyError halfway through a paid suite.
        d[r["verdict"]] = d.get(r["verdict"], 0) + 1

    return {
        "schema": "freca-mas-diagnostic-report-v1",
        "case_count": len(picked),
        "by_id": by_id,
        "results": rows,
        "note": ("Diagnostics record sensitivity, not correctness. A CHANGED "
                 "verdict is not a defect and an UNCHANGED verdict is not a "
                 "pass; neither may be summed into an accuracy figure."),
    }


def run_suite(cases: list[dict], rerun: Callable[[dict], dict], *,
              ids: list[str] | None = None, sample: int | None = None,
              seed: int = 0, locators: dict | None = None) -> dict:
    """Run mutations and oracles. `rerun(case) -> outcome dict`.

    Sampling is by stable hash of case_uid so the selection is reproducible
    across runs and hosts (§8.4).
    """
    ids = ids or list(MUTATIONS)
    locators = locators or {}
    picked = sorted(cases, key=lambda c: stable_hash(c["case_uid"]))
    if sample:
        picked = picked[:sample]

    results = []
    for case in picked:
        base = rerun(case)
        for mid in ids:
            spec = MUTATIONS[mid]
            rng = random.Random(f"{seed}:{mid}:{case['case_uid']}")
            if mid == "H7":
                made = spec["fn"](case, rng,
                                  locator=locators.get(case["case_uid"]))
            else:
                made = spec["fn"](case, rng)
            if made is None:
                # The mutation could not be built from this case at all. This
                # is kept distinct from an oracle that declines to judge: only
                # the former means the test never ran, and conflating them lets
                # a suite whose oracles can never assert anything look like a
                # suite that simply had nothing to mutate.
                results.append({"case_uid": case["case_uid"], "id": mid,
                                "verdict": "NOT_CONSTRUCTIBLE",
                                "reason": "mutation not constructible"})
                continue
            mutated, meta = made
            verdict, reason = ORACLES[mid](base, rerun(mutated), meta)
            results.append({"case_uid": case["case_uid"], "id": mid,
                            "verdict": verdict, "reason": reason,
                            "mutation": meta})

    by_id: dict[str, dict[str, int]] = {}
    for r in results:
        d = by_id.setdefault(r["id"], {"PASS": 0, "FAIL": 0,
                                       "NOT_APPLICABLE": 0,
                                       "NOT_CONSTRUCTIBLE": 0})
        # An oracle returning an unexpected verdict must appear in the report
        # rather than raising KeyError partway through a suite that has already
        # been paid for in model calls.
        d[r["verdict"]] = d.get(r["verdict"], 0) + 1

    failures = [r for r in results if r["verdict"] == "FAIL"]

    # A suite in which nothing was constructible must not report a pass.
    #
    # If the dumps fail to parse, or the locators are absent, every mutation
    # returns None, every verdict is NOT_APPLICABLE and `not failures` is
    # vacuously true. That is the most dangerous way for a gate to behave: it
    # is indistinguishable from success while testing nothing. Applicability is
    # therefore reported per id and enforced as its own condition.
    coverage: dict[str, Any] = {}
    starved: list[str] = []
    unasserted: list[str] = []
    for mid, d in by_id.items():
        total = sum(d.values())
        built = total - d.get("NOT_CONSTRUCTIBLE", 0)
        judged = d.get("PASS", 0) + d.get("FAIL", 0)
        coverage[mid] = {
            "constructed": built, "judged": judged, "total": total,
            "construction_rate": (built / total) if total else 0.0,
            "judgement_rate": (judged / built) if built else 0.0,
        }
        # H7 is exempt from starvation: with no eligible coordinate it
        # legitimately has nothing to run, and §8.2 requires that be recorded
        # as a warning rather than forced. Every other id must have been
        # constructed at least once.
        if built == 0 and mid != "H7":
            starved.append(mid)
        # Constructed but never judged is a separate condition: the mutation
        # ran and the oracle declined every time, so the id contributes no
        # evidence either way. It is reported rather than blocking, because an
        # oracle that abstains honestly is doing its job.
        elif built and judged == 0:
            unasserted.append(mid)

    return {
        "schema": "freca-mas-harness-report-v1",
        "case_count": len(picked),
        "mutation_ids": ids,
        "by_id": by_id,
        "coverage": coverage,
        "starved_mutation_ids": starved,
        "unasserted_mutation_ids": unasserted,
        "h7_ran": coverage.get("H7", {}).get("judged", 0) > 0,
        "failure_count": len(failures),
        "failures": failures[:50],
        "hard_mas_pass": (not failures) and (not starved) and bool(picked),
        "note": ("Hard-MAS establishes response relations that hold without "
                 "labels. It does not establish accuracy. A pass additionally "
                 "requires that every mutation other than H7 was actually "
                 "constructed at least once."),
    }


# --------------------------------------------------------------------------
# Self-tests: a well-behaved stub must pass, a broken stub must be caught.
# --------------------------------------------------------------------------

def synthetic_cases() -> list[dict]:
    """Stand-in cases for when the real dumps are not on this machine.

    The code archive deliberately excludes case data, so a self-test that hard
    requires the dumps fails on every machine that has only the archive - which
    is exactly the machine someone runs it on first, to check the archive is
    intact. These carry the structure the suite needs (several tracks, an RE
    number, dates, quantities, table rows) without carrying any case content.
    """
    out = []
    for n in (1, 2):
        re_no = f"RE-NSW-2020-{n:04d}"
        out.append(case_from_chunks(f"synthetic-{n:03d}", [
            {"id": "1_Farm.docx:P1", "file": "1_Farm.docx",
             "text": f"RE Number | {re_no}"},
            {"id": "1_Farm.docx:P2", "file": "1_Farm.docx",
             "text": "Operator | Example Grain Co"},
            {"id": "1_Farm.docx:T1:R1", "file": "1_Farm.docx",
             "text": f"Registered | 2020-03-16 | Holder | {re_no}"},
            {"id": "1_Farm.docx:T1:R2", "file": "1_Farm.docx",
             "text": "| Storage area is 12.5 ha and is maintained"},
            {"id": "2_HACCP.docx:P1", "file": "2_HACCP.docx",
             "text": "Plan Holder | Example Grain Co"},
            {"id": "2_HACCP.docx:T1:R1", "file": "2_HACCP.docx",
             "text": "Review Date | 2022-01-09 | Outcome | Approved"},
            {"id": "3_Pest.xlsx:T1:R1", "file": "3_Pest.xlsx",
             "text": "Inspection | 4 March 2021 | Bait stations | 250 kg"},
        ], re_number=re_no))
    return out


def run_self_tests(dump_dir: Path) -> None:
    files = sorted(dump_dir.glob("*.txt"))[:3]
    if files:
        cases = [parse_case_dump(p) for p in files]
        assert all(c["tracks"] for c in cases), "track parsing produced nothing"
        assert any(c["re_number"] for c in cases), "no RE number parsed"
        source = f"{len(cases)} real cases, {sum(len(c['tracks']) for c in cases)} tracks"
    else:
        # Reported, not silently substituted: a suite that quietly swapped in
        # fixtures would claim the dump parser was exercised when it was not.
        cases = synthetic_cases()
        source = (f"{len(cases)} SYNTHETIC cases - no dumps under {dump_dir}, "
                  "so the dump parser was NOT exercised")

    # ---- chunk/track consistency -------------------------------------------
    # The pipeline consumes chunks; the oracles read track text. If a mutation
    # moves one without the other, the oracle judges the system on input it was
    # never given, and the whole suite silently measures nothing. The invariant
    # is checked after every mutation, on a case whose tracks hold SEVERAL
    # chunks, because a single-chunk track satisfies it trivially.

    def consistent(case: dict) -> bool:
        return all(
            t["text"] == "\n".join(c["text"] for c in t["chunks"])
            for t in case["tracks"])

    multi = case_from_chunks("case-multi", [
        {"id": "farm.docx:P1", "file": "farm.docx",
         "text": "RE Number | RE-NSW-2020-0033"},
        {"id": "farm.docx:P2", "file": "farm.docx",
         "text": "Operator | Murray River Pulse Co"},
        {"id": "farm.docx:T1:R1", "file": "farm.docx",
         "text": "Inspection Date | 2021-03-04 | Result | Pass"},
        {"id": "farm.docx:T1:R2", "file": "farm.docx",
         "text": "Storage Temperature | 4.0 degC | Verified | Yes"},
        {"id": "haccp.docx:P1", "file": "haccp.docx",
         "text": "Plan Holder | Murray River Pulse Co"},
        {"id": "haccp.docx:T1:R1", "file": "haccp.docx",
         "text": "Review Date | 2022-01-09 | Outcome | Approved"},
    ])
    assert multi["chunk_native"] is True
    assert len(multi["tracks"]) == 2
    assert len(multi["tracks"][0]["chunks"]) == 4
    assert consistent(multi), "case_from_chunks built an inconsistent case"
    assert multi["re_number"] == "RE-NSW-2020-0033"

    rng0 = random.Random(0)
    applied = []
    for mid, spec in list(MUTATIONS.items()) + list(DIAGNOSTICS.items()):
        fn = spec["fn"]
        if mid == "H7":
            got = fn(multi, rng0,
                     locator={"file": "farm.docx",
                              "text": "Storage Temperature | 4.0 degC"})
        else:
            got = fn(multi, rng0)
        if got is None:
            continue
        mutated, _meta = got
        applied.append(mid)
        assert consistent(mutated), f"{mid} desynchronised track and chunks"
        assert consistent(multi), f"{mid} mutated the input case in place"

    # If almost nothing applied, the check above proved almost nothing.
    assert len(applied) >= 8, f"only {applied} were constructible"

    # Chunk ids must stay unique after a duplication, or one copy is dropped
    # by every downstream index and H2 tests nothing.
    dup, _ = m_h2_duplicate_track(multi, random.Random(1))
    ids = [c["id"] for c in case_to_chunks(dup)]
    assert len(ids) == len(set(ids)), "duplicate chunk ids after H2"
    assert len(ids) == len(case_to_chunks(multi)) + 4, ids

    # ---- o_h7 against real-shaped observations -----------------------------
    # The pipeline emits no `alternate_support_found` flag, so the oracle
    # derives it from the recorded support. All three derivation branches are
    # checked, because an oracle that silently abstains on real output would
    # make H7 look clean while asserting nothing.
    one = {"label": "1", "support_evidence_ids": ["f1.docx:T1:R2"]}
    assert o_h7(one, {"label": "1",
                      "support_evidence_ids": ["f9.docx:P3"]},
                {})[0] == "PASS", "fresh support should pass"
    assert o_h7(one, {"label": "1",
                      "support_evidence_ids": ["f1.docx:T1:R2"]},
                {})[0] == "FAIL", "same support should fail"
    assert o_h7(one, {"label": "1", "support_evidence_ids": []},
                {})[0] == "FAIL", "no support at all should fail"
    assert o_h7(one, {"label": "0", "support_evidence_ids": []},
                {})[0] == "PASS", "a downgraded verdict is the expected result"
    assert o_h7(one, {"label": "1"}, {})[0] == "NOT_APPLICABLE", (
        "an observation without support ids must abstain, not pass")

    # A span crossing a chunk boundary must not report a phantom edit.
    probe = case_from_chunks("case-split", [
        {"id": "a.docx:P1", "file": "a.docx", "text": "Murray River"},
        {"id": "a.docx:P2", "file": "a.docx", "text": "Pulse Co"},
    ])
    assert edit_track(probe["tracks"][0],
                      lambda s: s.replace("River\nPulse", "X")) == 0
    assert consistent(probe), "boundary-spanning edit desynchronised"

    def good(case: dict) -> dict:
        """A system that respects every asserted relation."""
        sig = case_signature(case)
        foreign_only = case.get("re_number") is None or not any(
            case["re_number"] in t["text"] for t in case["tracks"])
        return {
            "label": "0" if foreign_only else "1",
            "internal_outcome": "PROVEN_COMPLIANT",
            "semantic_fingerprint": sig,
            "observed_span_days": 100 - 10 * sum(
                1 for t in case["tracks"] if DATE.search(t["text"])),
            "cited_sources": [t["filename"] for t in case["tracks"]],
            "alternate_support_found": False,
        }

    def sticky(case: dict) -> dict:
        """A system that answers 1 no matter what is done to the evidence."""
        out = good(case)
        out["label"] = "1"
        return out

    ok = run_suite(cases, good, ids=["H1", "H2", "H3a", "H3b", "H6"])
    assert ok["hard_mas_pass"], ok["failures"]

    bad = run_suite(cases, sticky, ids=["H3a"])
    assert not bad["hard_mas_pass"], "H3a failed to catch identity leakage"

    # H1 must catch a system whose label depends on track order.
    def order_sensitive(case: dict) -> dict:
        out = good(case)
        out["label"] = "1" if case["tracks"][0]["no"] == 1 else "0"
        return out

    h1 = run_suite(cases, order_sensitive, ids=["H1"])
    assert not h1["hard_mas_pass"], "H1 failed to catch order sensitivity"

    # H6 must catch a citation that survives deletion of its source.
    def dangling(case: dict) -> dict:
        out = good(case)
        out["cited_sources"] = [t["filename"] for t in cases[0]["tracks"]]
        return out

    h6 = run_suite(cases[:1], dangling, ids=["H6"])
    assert not h6["hard_mas_pass"], "H6 failed to catch dangling citation"

    # A suite that could not construct anything must not report a pass.
    # This is the failure mode that matters most on a costly run: if the dumps
    # do not parse, every verdict is NOT_APPLICABLE and a naive `not failures`
    # would look identical to success.
    empty_cases = [{"case_uid": "empty", "re_number": None, "tracks": []}]
    starved = run_suite(empty_cases, good, ids=["H1", "H2", "H6"])
    assert not starved["hard_mas_pass"], "starved suite reported a pass"
    assert set(starved["starved_mutation_ids"]) == {"H1", "H2", "H6"}
    assert starved["failure_count"] == 0, "starvation must not fake failures"

    # No cases at all must also not pass.
    assert not run_suite([], good, ids=["H1"])["hard_mas_pass"]

    # H7 alone with no locator is a warning, not a starvation failure (§8.2).
    h7 = run_suite(cases, good, ids=["H7"])
    assert h7["hard_mas_pass"], "H7 with no locator should not block"
    assert h7["h7_ran"] is False

    print(f"mas_harness_v1 self-tests: PASS ({source})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-dumps", type=Path, default=Path("../../eval/case_dumps"),
                    help="real case dumps; absent, the self-test uses "
                         "synthetic cases and says so")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        run_self_tests(a.case_dumps)
        return 0

    # No production rerun is wired here on purpose: this module never calls a
    # model. The run host injects `rerun` via import.
    ap.error("no rerun callable configured; import run_suite() and supply one")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
