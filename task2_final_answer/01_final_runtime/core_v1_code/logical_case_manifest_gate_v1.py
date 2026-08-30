#!/usr/bin/env python3
"""FRECA logical-case manifest gate v1.

ZERO API / ZERO MODEL / ZERO ANSWER COMPARATOR.

Builds the 100 logical cases from the observed Task2 evidence bundle using only:
- relative path / physical container;
- filename track number;
- filename serial anchors;
- filename entity-key equality for serial-free T4-T7;
- file bytes/hash.

It never reads document body text and never uses labels.

Frozen observed Task2 structural profile:
- 99 physical RE-* directories
- 898 evidence files = 698 DOCX + 200 XLSX
- serial domain exactly 1..100
- T1 count 98; T2..T9 count 100
- two logical cases have missing T1
- one physical container contains two logical cases
- official evidence-set content hash:
  sha256:e62222e1154f9dfe6427d55a7304eb858bdd598fd50db676cfe9a2a9ca02e1a6

The profile is a dataset identity gate, not a legal/compliance rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


EXPECTED_PHYSICAL_DIRS = 99
EXPECTED_EVIDENCE_FILES = 898
EXPECTED_DOCX = 698
EXPECTED_XLSX = 200
EXPECTED_SERIALS = set(range(1, 101))
EXPECTED_TRACK_COUNTS = {
    1: 98,
    2: 100,
    3: 100,
    4: 100,
    5: 100,
    6: 100,
    7: 100,
    8: 100,
    9: 100,
}
EXPECTED_EVIDENCE_SET_SHA256 = (
    "e62222e1154f9dfe6427d55a7304eb858bdd598fd50db676cfe9a2a9ca02e1a6"
)

TRACK_RE = re.compile(r"^([1-9])_")
NUMERIC_TOKEN_RE = re.compile(r"^\d{1,3}$")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json_atomic(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def entity_key(raw: str) -> str:
    # Filename-only normalization. Never exposed to evidence reasoning.
    return "".join(ch for ch in raw.casefold() if ch.isalnum())


def parse_track(path: Path) -> int:
    m = TRACK_RE.match(path.name)
    if not m:
        raise ValueError(f"cannot parse track: {path.name}")
    return int(m.group(1))


def plausible_serial_tokens(tokens: list[str]) -> list[tuple[int, int]]:
    out = []
    for idx, token in enumerate(tokens):
        if not NUMERIC_TOKEN_RE.match(token):
            continue
        value = int(token)
        if 1 <= value <= 100:
            out.append((idx, value))
    return out


def parse_anchor(path: Path, track: int) -> dict:
    """Parse serial anchor for T1-T3 and T8-T9.

    T1-T3: serial is the only 1..100 numeric filename token; entity follows it.
    T8-T9: serial is the final token.
    """
    stem = path.stem
    tokens = stem.split("_")

    if track in {1, 2, 3}:
        candidates = plausible_serial_tokens(tokens[1:])
        # Rebase indexes because we searched tokens[1:].
        candidates = [(idx + 1, value) for idx, value in candidates]
        if len(candidates) != 1:
            raise ValueError(
                f"{path.name}: expected exactly one serial token for T{track}; "
                f"found {candidates}"
            )
        idx, serial = candidates[0]
        suffix = "_".join(tokens[idx + 1:]).strip("_")
        if not suffix:
            raise ValueError(
                f"{path.name}: no entity suffix after serial {serial}"
            )
        return {
            "serial": serial,
            "entity_raw": suffix,
            "entity_key": entity_key(suffix),
        }

    if track in {8, 9}:
        if len(tokens) < 3 or not NUMERIC_TOKEN_RE.match(tokens[-1]):
            raise ValueError(
                f"{path.name}: expected final serial token for T{track}"
            )
        serial = int(tokens[-1])
        if serial not in EXPECTED_SERIALS:
            raise ValueError(
                f"{path.name}: serial outside 1..100: {serial}"
            )
        return {
            "serial": serial,
            "entity_raw": None,
            "entity_key": None,
        }

    raise ValueError(f"T{track} is not a serial-anchor track")


def anomaly(
    rows: list[dict],
    *,
    code: str,
    severity: str,
    message: str,
    details: Any = None,
) -> None:
    item = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if details is not None:
        item["details"] = details
    rows.append(item)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--case-root",
        type=Path,
        default=Path(
            "/home/MeggieYu/freca/Task2/SFRE_cases/SFRE_cases"
        ),
    )
    p.add_argument(
        "--submission-template",
        type=Path,
        default=Path(
            "/home/MeggieYu/freca/Task2/submission_template.xlsx"
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results_v2/logical_case_manifest_v1.json"
        ),
    )
    args = p.parse_args()

    anomalies: list[dict] = []

    if not args.case_root.is_dir():
        raise SystemExit(f"case root not found: {args.case_root}")

    physical_dirs = sorted(
        pth
        for pth in args.case_root.iterdir()
        if pth.is_dir() and pth.name.startswith("RE-")
    )

    files = []
    for container in physical_dirs:
        for path in sorted(container.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.name.startswith("._"):
                continue
            if path.suffix.lower() not in {".docx", ".xlsx"}:
                continue
            files.append(path)

    rel = {
        path: path.relative_to(args.case_root).as_posix()
        for path in files
    }

    file_rows = {}
    for path in files:
        try:
            track = parse_track(path)
        except Exception as exc:
            anomaly(
                anomalies,
                code="L3_FILENAME_TRACK_PARSE_FAILED",
                severity="FATAL",
                message=str(exc),
                details={"path": rel[path]},
            )
            continue

        file_rows[path] = {
            "relative_path": rel[path],
            "physical_container": path.parent.name,
            "track": track,
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    # Official evidence-set hash: relative_path<TAB>size<TAB>sha256, sorted.
    manifest_lines = [
        (
            row["relative_path"]
            + "\t"
            + str(row["size_bytes"])
            + "\t"
            + row["sha256"].lower()
        )
        for row in sorted(
            file_rows.values(),
            key=lambda x: x["relative_path"],
        )
    ]
    evidence_set_sha256 = sha256_bytes(
        "\n".join(manifest_lines).encode("utf-8")
    )

    # serial -> assembly record
    assemblies = {}
    container_serials = defaultdict(set)
    assigned = {}

    for path, row in file_rows.items():
        track = row["track"]
        if track not in {1, 2, 3, 8, 9}:
            continue

        try:
            parsed = parse_anchor(path, track)
        except Exception as exc:
            anomaly(
                anomalies,
                code="L3_SERIAL_ANCHOR_PARSE_FAILED",
                severity="FATAL",
                message=str(exc),
                details={"path": row["relative_path"]},
            )
            continue

        serial = parsed["serial"]
        container = row["physical_container"]

        record = assemblies.setdefault(
            serial,
            {
                "serial": serial,
                "physical_container": container,
                "entity_candidates": [],
                "tracks": {},
            },
        )

        if record["physical_container"] != container:
            anomaly(
                anomalies,
                code="L3_SERIAL_COLLISION_ACROSS_CONTAINERS",
                severity="FATAL",
                message=(
                    f"serial {serial} occurs in both "
                    f"{record['physical_container']} and {container}"
                ),
            )
            continue

        if track in record["tracks"]:
            anomaly(
                anomalies,
                code="L3_DUPLICATE_TRACK",
                severity="FATAL",
                message=f"serial {serial} has duplicate T{track}",
                details={
                    "existing": record["tracks"][track]["relative_path"],
                    "new": row["relative_path"],
                },
            )
            continue

        record["tracks"][track] = row
        container_serials[container].add(serial)
        assigned[path] = serial

        if parsed["entity_key"]:
            record["entity_candidates"].append({
                "track": track,
                "raw": parsed["entity_raw"],
                "key": parsed["entity_key"],
            })

    # Entity anchor consensus from T1-T3.
    for serial, record in assemblies.items():
        keys = {
            item["key"]
            for item in record["entity_candidates"]
            if item["key"]
        }
        if len(keys) != 1:
            anomaly(
                anomalies,
                code="L3_SERIAL_ANCHOR_ENTITY_CONFLICT",
                severity="FATAL",
                message=(
                    f"serial {serial} has {len(keys)} entity keys "
                    f"across T1-T3"
                ),
                details=record["entity_candidates"],
            )
            record["entity_key"] = None
        else:
            record["entity_key"] = next(iter(keys))

        # Verify T8/T9 filename contains the same entity key.
        if record["entity_key"]:
            for track in (8, 9):
                row = record["tracks"].get(track)
                if row is None:
                    continue
                stem_key = entity_key(
                    Path(row["relative_path"]).stem
                )
                if record["entity_key"] not in stem_key:
                    anomaly(
                        anomalies,
                        code="L3_ANCHOR_ENTITY_MISMATCH",
                        severity="FATAL",
                        message=(
                            f"serial {serial} T{track} does not contain "
                            "the T1-T3 entity key"
                        ),
                        details={"path": row["relative_path"]},
                    )

    # Assign serial-free T4-T7 by exact normalized entity-key containment
    # against serial anchors within the SAME physical container.
    for path, row in file_rows.items():
        track = row["track"]
        if track not in {4, 5, 6, 7}:
            continue

        container = row["physical_container"]
        candidates = []
        stem_key = entity_key(path.stem)

        for serial in sorted(container_serials.get(container, set())):
            key = assemblies.get(serial, {}).get("entity_key")
            if key and key in stem_key:
                candidates.append(serial)

        if len(candidates) != 1:
            anomaly(
                anomalies,
                code=(
                    "L3_SERIAL_FREE_COMPONENT_UNANCHORED"
                    if not candidates
                    else "L3_SERIAL_FREE_COMPONENT_MULTI_ANCHOR"
                ),
                severity="FATAL",
                message=(
                    f"{row['relative_path']}: serial-free T{track} "
                    f"matched serials {candidates}"
                ),
            )
            continue

        serial = candidates[0]
        record = assemblies[serial]

        if track in record["tracks"]:
            anomaly(
                anomalies,
                code="L3_DUPLICATE_TRACK",
                severity="FATAL",
                message=f"serial {serial} has duplicate T{track}",
                details={
                    "existing": record["tracks"][track]["relative_path"],
                    "new": row["relative_path"],
                },
            )
            continue

        record["tracks"][track] = row
        assigned[path] = serial

    # Global structural checks.
    if len(physical_dirs) != EXPECTED_PHYSICAL_DIRS:
        anomaly(
            anomalies,
            code="L3_PHYSICAL_CONTAINER_COUNT_INVALID",
            severity="FATAL",
            message=(
                f"physical dirs {len(physical_dirs)} != "
                f"{EXPECTED_PHYSICAL_DIRS}"
            ),
        )

    if len(file_rows) != EXPECTED_EVIDENCE_FILES:
        anomaly(
            anomalies,
            code="L3_SOURCE_COUNT_INVALID",
            severity="FATAL",
            message=(
                f"evidence files {len(file_rows)} != "
                f"{EXPECTED_EVIDENCE_FILES}"
            ),
        )

    ext_counts = Counter(
        row["extension"]
        for row in file_rows.values()
    )
    if ext_counts.get(".docx", 0) != EXPECTED_DOCX:
        anomaly(
            anomalies,
            code="L3_DOCX_COUNT_INVALID",
            severity="FATAL",
            message=(
                f"DOCX {ext_counts.get('.docx', 0)} != "
                f"{EXPECTED_DOCX}"
            ),
        )
    if ext_counts.get(".xlsx", 0) != EXPECTED_XLSX:
        anomaly(
            anomalies,
            code="L3_XLSX_COUNT_INVALID",
            severity="FATAL",
            message=(
                f"XLSX {ext_counts.get('.xlsx', 0)} != "
                f"{EXPECTED_XLSX}"
            ),
        )

    if evidence_set_sha256 != EXPECTED_EVIDENCE_SET_SHA256:
        anomaly(
            anomalies,
            code="L3_EVIDENCE_SET_HASH_MISMATCH",
            severity="FATAL",
            message=(
                f"evidence-set sha256 {evidence_set_sha256} != "
                f"{EXPECTED_EVIDENCE_SET_SHA256}"
            ),
        )

    serials = set(assemblies)
    if serials != EXPECTED_SERIALS:
        anomaly(
            anomalies,
            code="L3_SERIAL_DOMAIN_INCOMPLETE",
            severity="FATAL",
            message="serial domain is not exactly 1..100",
            details={
                "missing": sorted(EXPECTED_SERIALS - serials),
                "extra": sorted(serials - EXPECTED_SERIALS),
            },
        )

    unassigned = sorted(
        rel[path]
        for path in file_rows
        if path not in assigned
    )
    if unassigned:
        anomaly(
            anomalies,
            code="L3_SOURCE_UNASSIGNED",
            severity="FATAL",
            message=f"{len(unassigned)} evidence files unassigned",
            details=unassigned,
        )

    track_counts = Counter(
        row["track"]
        for row in file_rows.values()
    )
    for track, expected in EXPECTED_TRACK_COUNTS.items():
        actual = track_counts.get(track, 0)
        if actual != expected:
            anomaly(
                anomalies,
                code="L3_TRACK_COUNT_INVALID",
                severity="FATAL",
                message=f"T{track} count {actual} != {expected}",
            )

    cases = []
    shared_containers = {
        container
        for container, serial_set in container_serials.items()
        if len(serial_set) > 1
    }

    for serial in sorted(assemblies):
        record = assemblies[serial]
        present = sorted(record["tracks"])
        missing = [
            track
            for track in range(1, 10)
            if track not in record["tracks"]
        ]

        for track in missing:
            severity = "DEGRADED" if track == 1 else "FATAL"
            anomaly(
                anomalies,
                code="L3_TRACK_MISSING",
                severity=severity,
                message=f"case-{serial:03d} missing T{track}",
                details={
                    "serial": serial,
                    "physical_container": record["physical_container"],
                    "track": track,
                },
            )

        cases.append({
            "case_uid": f"case-{serial:03d}",
            "serial": serial,
            "physical_case_dir": record["physical_container"],
            "re_number_candidate": record["physical_container"],
            "entity_key_internal_assembly_only": record.get(
                "entity_key"
            ),
            "shared_physical_container": (
                record["physical_container"] in shared_containers
            ),
            "present_tracks": present,
            "missing_tracks": missing,
            "track_assignments": {
                f"T{track}": record["tracks"][track]
                for track in sorted(record["tracks"])
            },
        })

    if len(cases) != 100:
        anomaly(
            anomalies,
            code="L3_CASE_COUNT_INVALID",
            severity="FATAL",
            message=f"logical case count {len(cases)} != 100",
        )

    # Expected degraded cases are exactly two missing-T1 cases.
    missing_t1_cases = [
        item["case_uid"]
        for item in cases
        if item["missing_tracks"] == [1]
    ]
    other_missing = [
        {
            "case_uid": item["case_uid"],
            "missing_tracks": item["missing_tracks"],
        }
        for item in cases
        if item["missing_tracks"] not in ([], [1])
    ]

    if len(missing_t1_cases) != 2 or other_missing:
        anomaly(
            anomalies,
            code="L3_MISSING_TRACK_PROFILE_INVALID",
            severity="FATAL",
            message=(
                "expected exactly two cases with only T1 missing "
                "and no other missing tracks"
            ),
            details={
                "missing_t1_cases": missing_t1_cases,
                "other_missing": other_missing,
            },
        )

    shared_case_rows = [
        item
        for item in cases
        if item["shared_physical_container"]
    ]
    if (
        len(shared_containers) != 1
        or len(shared_case_rows) != 2
    ):
        anomaly(
            anomalies,
            code="L3_SHARED_CONTAINER_PROFILE_INVALID",
            severity="FATAL",
            message=(
                "expected exactly one shared physical container "
                "holding exactly two logical cases"
            ),
            details={
                "shared_containers": sorted(shared_containers),
                "shared_cases": [
                    x["case_uid"] for x in shared_case_rows
                ],
            },
        )
    else:
        anomaly(
            anomalies,
            code="L3_SHARED_CONTAINER_SPLIT",
            severity="INFO",
            message=(
                f"{next(iter(shared_containers))} deterministically "
                "split into two logical cases"
            ),
            details=[
                {
                    "case_uid": x["case_uid"],
                    "serial": x["serial"],
                }
                for x in shared_case_rows
            ],
        )

    # Submission template is intentionally profile-only here.
    submission_profile = {
        "path": str(args.submission_template),
        "exists": args.submission_template.exists(),
        "sha256": (
            sha256_file(args.submission_template)
            if args.submission_template.exists()
            else None
        ),
    }
    if not args.submission_template.exists():
        anomaly(
            anomalies,
            code="L3_SUBMISSION_TEMPLATE_MISSING",
            severity="FATAL",
            message=f"missing submission template: {args.submission_template}",
        )

    fatal = [
        row
        for row in anomalies
        if row["severity"] == "FATAL"
    ]
    degraded = [
        row
        for row in anomalies
        if row["severity"] == "DEGRADED"
    ]

    result = {
        "schema": "freca-logical-case-manifest-v1",
        "dataset_structure_profile": {
            "case_root": str(args.case_root),
            "physical_case_dir_count": len(physical_dirs),
            "logical_case_count": len(cases),
            "evidence_file_count": len(file_rows),
            "docx_count": ext_counts.get(".docx", 0),
            "xlsx_count": ext_counts.get(".xlsx", 0),
            "track_counts": {
                f"T{k}": track_counts.get(k, 0)
                for k in range(1, 10)
            },
            "serial_min": min(serials) if serials else None,
            "serial_max": max(serials) if serials else None,
            "serial_domain_complete_1_100": (
                serials == EXPECTED_SERIALS
            ),
            "shared_physical_containers": sorted(shared_containers),
            "missing_t1_cases": missing_t1_cases,
            "evidence_set_sha256": evidence_set_sha256,
            "evidence_set_sha256_matches_frozen": (
                evidence_set_sha256
                == EXPECTED_EVIDENCE_SET_SHA256
            ),
        },
        "submission_template_profile": submission_profile,
        "cases": cases,
        "anomalies": anomalies,
        "fatal_anomaly_count": len(fatal),
        "degraded_anomaly_count": len(degraded),
        "all_sources_assigned_exactly_once": (
            len(assigned) == len(file_rows)
        ),
        "expected_decision_count": len(cases) * 41,
        "answer_comparator_used": False,
        "api_called": False,
    }

    semantic = dict(result)
    result["semantic_sha256"] = (
        "sha256:"
        + sha256_bytes(
            canonical_json(semantic).encode("utf-8")
        )
    )

    result["all_pass"] = (
        len(fatal) == 0
        and len(cases) == 100
        and result["all_sources_assigned_exactly_once"]
        and result["expected_decision_count"] == 4100
    )

    save_json_atomic(result, args.output)

    print("=" * 88)
    print("FRECA LOGICAL CASE MANIFEST GATE V1")
    print("=" * 88)
    print("Physical dirs :", len(physical_dirs))
    print("Logical cases :", len(cases))
    print("Evidence files:", len(file_rows))
    print(
        "DOCX / XLSX  :",
        ext_counts.get(".docx", 0),
        "/",
        ext_counts.get(".xlsx", 0),
    )
    print(
        "Track counts :",
        " ".join(
            f"T{k}={track_counts.get(k, 0)}"
            for k in range(1, 10)
        ),
    )
    print(
        "Serial domain:",
        "1..100 COMPLETE"
        if serials == EXPECTED_SERIALS
        else "INVALID",
    )
    print(
        "Evidence hash:",
        evidence_set_sha256,
        "(MATCH)"
        if evidence_set_sha256 == EXPECTED_EVIDENCE_SET_SHA256
        else "(MISMATCH)",
    )
    print("Missing T1   :", ", ".join(missing_t1_cases) or "none")
    print(
        "Shared dirs  :",
        ", ".join(sorted(shared_containers)) or "none",
    )
    for item in shared_case_rows:
        print(
            "  ",
            item["case_uid"],
            "serial=",
            item["serial"],
            "tracks=",
            ",".join(f"T{x}" for x in item["present_tracks"]),
        )
    print("Fatal anomalies   :", len(fatal))
    print("Degraded anomalies:", len(degraded))
    print("Assigned once     :", result["all_sources_assigned_exactly_once"])
    print("Decisions         :", result["expected_decision_count"])
    print("ALL PASS          :", result["all_pass"])
    print("API called        : False")
    print("Answer comparator : False")
    print("Saved             :", args.output)

    if fatal:
        print()
        print("FATAL ANOMALIES:")
        for row in fatal:
            print(" ", row["code"], "|", row["message"])
        raise SystemExit(2)


if __name__ == "__main__":
    main()
