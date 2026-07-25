from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from freca.models import CaseManifest, CaseRecord, SourceRecord, SourceType


_EARLY_TRACK_CASE = re.compile(
    r"^[123]_(?:Farm|HACCPPlan|PestControlRecord)_(\d{1,3})_",
    re.IGNORECASE,
)
_LATE_TRACK_CASE = re.compile(r"_([0-9]{3})\.(?:docx|xlsx)$", re.IGNORECASE)
_TRACK = re.compile(r"^([1-9])_")


def recover_case_id(filename: str) -> int | None:
    early = _EARLY_TRACK_CASE.search(filename)
    if early:
        return int(early.group(1))
    late = _LATE_TRACK_CASE.search(filename)
    if late and filename[0] in {"8", "9"}:
        return int(late.group(1))
    return None


def recover_track(filename: str) -> int | None:
    match = _TRACK.match(filename)
    return int(match.group(1)) if match else None


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _case_signature(filename: str, case_id: int) -> str | None:
    marker = f"_{case_id}_"
    stem = Path(filename).stem
    if marker not in stem:
        return None
    return stem.split(marker, 1)[1].casefold()


def _assign_directory_files(directory: Path) -> dict[int, list[Path]]:
    files = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.casefold() in {".docx", ".xlsx"}
        ),
        key=lambda path: path.name.casefold(),
    )
    case_ids = sorted(
        {case_id for path in files if (case_id := recover_case_id(path.name)) is not None}
    )
    if not case_ids:
        raise ValueError(f"cannot recover a case id from {directory}")

    assignments: dict[int, list[Path]] = defaultdict(list)
    signatures: dict[int, set[str]] = defaultdict(set)
    for path in files:
        case_id = recover_case_id(path.name)
        if case_id is not None:
            assignments[case_id].append(path)
            signature = _case_signature(path.name, case_id)
            if signature:
                signatures[case_id].add(signature)

    if len(case_ids) == 1:
        case_id = case_ids[0]
        assigned = set(assignments[case_id])
        assignments[case_id].extend(path for path in files if path not in assigned)
        return assignments

    for path in files:
        if recover_case_id(path.name) is not None:
            continue
        folded = path.stem.casefold()
        matches = [
            case_id
            for case_id in case_ids
            if any(signature in folded for signature in signatures[case_id])
        ]
        if len(matches) != 1:
            raise ValueError(f"ambiguous mixed-directory source: {path}")
        assignments[matches[0]].append(path)

    return assignments


def build_manifest(
    cases_root: Path,
    *,
    expected_case_ids: set[int] | None = None,
    signature_truth=None,
) -> CaseManifest:
    cases_root = cases_root.resolve()
    if not cases_root.is_dir():
        raise FileNotFoundError(cases_root)
    expected = set(range(1, 101)) if expected_case_ids is None else set(expected_case_ids)

    assigned: dict[int, tuple[str, list[Path], bool]] = {}
    for directory in sorted(cases_root.iterdir(), key=lambda path: path.name.casefold()):
        if not directory.is_dir() or not directory.name.startswith("RE-"):
            continue
        by_case = _assign_directory_files(directory)
        shared = len(by_case) > 1
        for case_id, files in by_case.items():
            if case_id in assigned:
                raise ValueError(f"case {case_id} appears in multiple directories")
            assigned[case_id] = (directory.name, files, shared)

    actual = set(assigned)
    if actual != expected:
        raise ValueError(
            f"case id mismatch: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )

    cases: list[CaseRecord] = []
    for case_id in sorted(assigned):
        re_number, files, shared = assigned[case_id]
        sources: list[SourceRecord] = []
        tracks: set[int] = set()
        for path in sorted(files, key=lambda item: item.name.casefold()):
            track = recover_track(path.name)
            if track is None:
                continue
            tracks.add(track)
            source_type = (
                SourceType.DOCX if path.suffix.casefold() == ".docx" else SourceType.XLSX
            )
            sources.append(
                SourceRecord(
                    source_id=f"case-{case_id:03d}-t{track}",
                    case_id=case_id,
                    track=track,
                    re_number=re_number,
                    path=path.resolve(),
                    source_type=source_type,
                    sha256=sha256_file(path),
                    flags=["shared_re_directory"] if shared else [],
                )
            )
        if len(tracks) != len(sources):
            raise ValueError(f"case {case_id} has duplicate track assignments")
        missing = sorted(set(range(1, 10)) - tracks)
        flags = []
        if shared:
            flags.extend(["shared_re_directory", "duplicate_re_number"])
        flags.extend(f"missing_track_{track}" for track in missing)
        metadata: dict = {}
        contaminated_tracks: dict[int, str] = {}
        if signature_truth is not None:
            index = signature_truth.get(re_number)
            if index is not None:
                contaminated_tracks = {
                    int(track): relation
                    for track, relation in index.contaminated.items()
                }
                if index.is_foreign and "signature_foreign" not in flags:
                    flags.append("signature_foreign")
                for track_number, relation in sorted(index.contaminated.items()):
                    flags.append(f"track_contaminated:{track_number}:{relation}")
                metadata["expected_establishment_name"] = index.expected_name
                metadata["signature_truth"] = index.to_dict()
        case = CaseRecord(
            case_id=case_id,
            re_number=re_number,
            sources=sources,
            missing_tracks=missing,
            flags=flags,
            contaminated_tracks=contaminated_tracks,
            metadata=metadata,
        )
        cases.append(case)

    return CaseManifest(
        cases_root=cases_root,
        cases=cases,
        source_count=sum(len(case.sources) for case in cases),
    )
