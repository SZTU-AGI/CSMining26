#!/usr/bin/env python3
"""FRECA 41-CP grounded contract batch compiler v1.

Purpose
-------
Run the CURRENT server-side ``freca_core_v2.compile_cp_v2(cp_id, policy_top_k)``
uniformly across CP1..CP41.

This is a build-census / productionization tool, not a tuning loop.

Frozen safeguards
-----------------
- No case evidence is read by this orchestrator.
- No historical labels / answer comparator are read.
- Existing canonical ``CPx.json`` files are skipped by default.
- Every CP is independent: one failure does not block later CPs.
- ``policy_top_k`` is discovered from the CURRENT freca_core_v2.py CLI source
  unless explicitly overridden; it is never guessed.
- Per-CP DeepSeek telemetry, traceback, stdout/stderr log and output hashes are
  persisted.
- The aggregate report is checkpointed after every CP.
- Failed partial side artifacts are retained for diagnosis; only a valid
  canonical CPx.json counts as build success.

This script does NOT compile EvidenceRequirement plans yet.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_capture_v1 import (
    capture_deepseek_telemetry,
    summarize_telemetry,
)


CP_IDS = [f"CP{i}" for i in range(1, 42)]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_policy_top_k_default(source_path: Path) -> int:
    """Read the current CLI source; never import parser state or guess."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    matches: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "add_argument"
        ):
            continue

        option_strings = [
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
        ]

        normalized = " ".join(option_strings).lower().replace("_", "-")

        if not (
            "policy" in normalized
            and "top" in normalized
            and "k" in normalized
        ):
            continue

        for kw in node.keywords:
            if kw.arg != "default":
                continue

            if (
                isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, int)
            ):
                matches.append(int(kw.value.value))

    matches = sorted(set(matches))

    if len(matches) != 1:
        raise RuntimeError(
            "Could not uniquely discover compile policy_top_k default "
            f"from {source_path}; found {matches}. "
            "Pass --policy-top-k explicitly rather than guessing."
        )

    return matches[0]


def unwrap_contract(bundle: dict) -> dict:
    value = bundle.get("contract")
    return value if isinstance(value, dict) else bundle


def validate_canonical_contract_file(
    path: Path,
    expected_cp_id: str,
) -> dict:
    """Minimal local shape validation after current compiler returns.

    This is intentionally NOT a replacement for the compiler's own grounded
    validators. It catches corrupt/missing/misrouted output before the batch
    census marks success.
    """
    errors: list[str] = []

    if not path.exists():
        return {
            "valid_local_shape": False,
            "errors": ["CANONICAL_FILE_MISSING"],
            "sha256": None,
        }

    try:
        bundle = load_json(path)
    except Exception as exc:
        return {
            "valid_local_shape": False,
            "errors": [f"JSON_READ_FAILED:{type(exc).__name__}:{exc}"],
            "sha256": sha256_file(path),
        }

    if not isinstance(bundle, dict):
        errors.append("TOP_LEVEL_NOT_OBJECT")
        contract = {}
    else:
        contract = unwrap_contract(bundle)

    if not isinstance(contract, dict):
        errors.append("CONTRACT_NOT_OBJECT")
        contract = {}

    atoms = contract.get("atoms")
    if not isinstance(atoms, list) or not atoms:
        errors.append("ATOMS_MISSING_OR_EMPTY")

    for root_name in (
        "applicability",
        "satisfaction",
        "non_applicability",
    ):
        if not isinstance(contract.get(root_name), dict):
            errors.append(f"{root_name.upper()}_ROOT_MISSING")

    cp_meta = (
        bundle.get("cp")
        if isinstance(bundle, dict)
        and isinstance(bundle.get("cp"), dict)
        else {}
    )

    embedded_cp_id = (
        contract.get("cp_id")
        or cp_meta.get("cp_id")
        or (
            bundle.get("cp_id")
            if isinstance(bundle, dict)
            else None
        )
    )

    if (
        embedded_cp_id is not None
        and str(embedded_cp_id) != expected_cp_id
    ):
        errors.append(
            "CP_ID_MISMATCH:"
            f"expected={expected_cp_id}:embedded={embedded_cp_id}"
        )

    return {
        "valid_local_shape": not errors,
        "errors": errors,
        "sha256": sha256_file(path),
        "embedded_cp_id": embedded_cp_id,
        "atom_count": (
            len(atoms)
            if isinstance(atoms, list)
            else None
        ),
        "schema": (
            bundle.get("schema")
            if isinstance(bundle, dict)
            else None
        ),
    }


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def classify_failure(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"

    if "COMPILE_REVIEW_REQUIRED" in text:
        return "COMPILE_REVIEW_REQUIRED"

    if isinstance(exc, FileNotFoundError):
        return "BUILD_FAILED_MISSING_INPUT"

    return "BUILD_FAILED_EXCEPTION"


def select_cp_ids(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(CP_IDS)

    if args.cp:
        selected: list[str] = []

        for cp_id in args.cp:
            cp_id = str(cp_id).upper()

            if cp_id not in CP_IDS:
                raise ValueError(
                    f"Invalid CP ID {cp_id}; expected CP1..CP41"
                )

            if cp_id not in selected:
                selected.append(cp_id)

        return selected

    raise ValueError("Choose --all or at least one --cp CPx")


def build_initial_report(
    *,
    source_path: Path,
    policy_top_k: int,
    selected_cp_ids: list[str],
) -> dict:
    return {
        "schema":
            "freca-core-contract-batch-build-v1",

        "started_at_utc":
            now_iso(),

        "finished_at_utc":
            None,

        "freca_core_v2_path":
            str(source_path),

        "freca_core_v2_sha256":
            sha256_file(source_path),

        "policy_top_k":
            policy_top_k,

        "selected_cp_ids":
            selected_cp_ids,

        "case_evidence_accessed_by_orchestrator":
            False,

        "answer_comparator_used":
            False,

        "historical_labels_used":
            False,

        "evidence_requirement_plans_compiled":
            False,

        "cp_results":
            {},

        "summary":
            {},
    }


def update_summary(report: dict) -> None:
    counts: dict[str, int] = {}

    for row in report["cp_results"].values():
        status = str(row.get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1

    report["summary"] = {
        "status_counts": dict(sorted(counts.items())),
        "processed_count": len(report["cp_results"]),
        "selected_count": len(report["selected_cp_ids"]),
        "canonical_success_or_existing_count": sum(
            1
            for row in report["cp_results"].values()
            if row.get("status")
            in {
                "EXISTING_CANONICAL_VALID",
                "COMPILED_CANONICAL_VALID",
            }
        ),
        "review_required_count": counts.get(
            "COMPILE_REVIEW_REQUIRED",
            0,
        ),
        "failed_count": sum(
            value
            for key, value in counts.items()
            if key.startswith("BUILD_FAILED")
            or key == "COMPILED_CANONICAL_INVALID_LOCAL_SHAPE"
        ),
    }


def run_self_tests() -> None:
    import tempfile

    fixture = """
import argparse
p = argparse.ArgumentParser()
s = p.add_subparsers(dest="command")
c = s.add_parser("compile")
c.add_argument("--policy-top-k", type=int, default=80)
"""

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        source = td / "fake_v2.py"
        source.write_text(fixture, encoding="utf-8")

        assert discover_policy_top_k_default(source) == 80

        good = td / "CP12.json"
        good.write_text(
            json.dumps(
                {
                    "cp": {"cp_id": "CP12"},
                    "contract": {
                        "atoms": [{"atom_id": "A1"}],
                        "applicability": {
                            "op": "CONST",
                            "value": True,
                        },
                        "satisfaction": {
                            "op": "ATOM",
                            "atom_id": "A1",
                        },
                        "non_applicability": {
                            "op": "CONST",
                            "value": False,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        checked = validate_canonical_contract_file(
            good,
            "CP12",
        )

        assert checked["valid_local_shape"] is True

        bad = validate_canonical_contract_file(
            td / "missing.json",
            "CP1",
        )

        assert bad["valid_local_shape"] is False

    print("contract_batch_compile_v1 self-tests: PASS")
    print("  policy_top_k discovered from current CLI source")
    print("  canonical contract local shape validated")
    print("  missing output cannot count as success")
    print("  no case / label / answer comparator input")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--all",
        action="store_true",
        help="Process CP1..CP41.",
    )

    parser.add_argument(
        "--cp",
        action="append",
        help="Process one CP; may be repeated.",
    )

    parser.add_argument(
        "--policy-top-k",
        type=int,
        help=(
            "Override current compile CLI default. "
            "If omitted, discover it from freca_core_v2.py."
        ),
    )

    parser.add_argument(
        "--source",
        type=Path,
        default=Path("freca_core_v2.py"),
    )

    parser.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )

    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("results_v2/contract_build_v1"),
    )

    parser.add_argument(
        "--force-existing",
        action="store_true",
        help=(
            "Recompile even if canonical CPx.json exists. "
            "Do NOT use for frozen CP12 unless intentionally rebuilding it."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve plan only; do not call compile_cp_v2/API.",
    )

    parser.add_argument(
        "--stop-on-error",
        action="store_true",
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if not args.all and not args.cp:
            return

    if not args.source.exists():
        parser.error(f"Missing {args.source}")

    selected = select_cp_ids(args)

    policy_top_k = (
        args.policy_top_k
        if args.policy_top_k is not None
        else discover_policy_top_k_default(args.source)
    )

    args.run_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    logs_dir = args.run_dir / "logs"
    logs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path = (
        args.run_dir
        / "contract_batch_build_report_v1.json"
    )

    report = build_initial_report(
        source_path=args.source,
        policy_top_k=policy_top_k,
        selected_cp_ids=selected,
    )

    print("=" * 80)
    print("FRECA 41-CP GROUNDED CONTRACT BATCH BUILD V1")
    print("=" * 80)
    print("Selected CPs :", len(selected))
    print("policy_top_k :", policy_top_k)
    print("source SHA   :", report["freca_core_v2_sha256"])
    print("dry run      :", args.dry_run)
    print("force existing:", args.force_existing)
    print()

    if args.dry_run:
        for cp_id in selected:
            canonical_path = (
                args.contract_dir
                / f"{cp_id}.json"
            )

            check = validate_canonical_contract_file(
                canonical_path,
                cp_id,
            )

            status = (
                "WOULD_SKIP_EXISTING"
                if (
                    canonical_path.exists()
                    and not args.force_existing
                )
                else "WOULD_COMPILE"
            )

            report["cp_results"][cp_id] = {
                "cp_id": cp_id,
                "status": status,
                "canonical_path": str(canonical_path),
                "existing_check": check,
                "api_called": False,
            }

            print(cp_id, status)

        update_summary(report)
        report["finished_at_utc"] = now_iso()
        save_json(report, report_path)
        print()
        print("Saved:", report_path)
        return

    # Import only after the zero-API plan is resolved.
    import freca_core_v2 as v2

    if not hasattr(v2, "compile_cp_v2"):
        raise RuntimeError(
            "Current freca_core_v2 has no compile_cp_v2"
        )

    for index, cp_id in enumerate(selected, start=1):
        canonical_path = (
            args.contract_dir
            / f"{cp_id}.json"
        )

        print()
        print("#" * 80)
        print(
            f"[{index}/{len(selected)}] {cp_id}"
        )
        print("#" * 80)

        existing = validate_canonical_contract_file(
            canonical_path,
            cp_id,
        )

        if (
            canonical_path.exists()
            and not args.force_existing
        ):
            status = (
                "EXISTING_CANONICAL_VALID"
                if existing["valid_local_shape"]
                else "EXISTING_CANONICAL_INVALID_LOCAL_SHAPE"
            )

            row = {
                "cp_id": cp_id,
                "status": status,
                "started_at_utc": now_iso(),
                "finished_at_utc": now_iso(),
                "canonical_path": str(canonical_path),
                "canonical_validation": existing,
                "api_called": False,
                "cost_telemetry": None,
                "exception_type": None,
                "exception_message": None,
                "traceback_path": None,
                "log_path": None,
            }

            report["cp_results"][cp_id] = row
            update_summary(report)
            save_json(report, report_path)

            print(
                "SKIP existing:",
                status,
                existing.get("sha256"),
            )
            continue

        log_path = (
            logs_dir
            / f"{cp_id}_compile.log"
        )

        traceback_path = (
            logs_dir
            / f"{cp_id}_traceback.txt"
        )

        started = now_iso()
        exc: Exception | None = None
        telemetry = None

        original_stdout = sys.stdout
        original_stderr = sys.stderr

        with log_path.open(
            "w",
            encoding="utf-8",
        ) as log_handle:
            tee_out = Tee(
                original_stdout,
                log_handle,
            )
            tee_err = Tee(
                original_stderr,
                log_handle,
            )

            try:
                with (
                    contextlib.redirect_stdout(tee_out),
                    contextlib.redirect_stderr(tee_err),
                ):
                    with capture_deepseek_telemetry() as events:
                        v2.compile_cp_v2(
                            cp_id,
                            policy_top_k,
                        )

                    telemetry = summarize_telemetry(
                        events
                    )

            except Exception as caught:
                exc = caught

                # capture_deepseek_telemetry exits before this handler;
                # events remains available if context entered successfully.
                try:
                    telemetry = summarize_telemetry(
                        events
                    )
                except Exception:
                    telemetry = None

                tb = traceback.format_exc()

                traceback_path.write_text(
                    tb,
                    encoding="utf-8",
                )

                tee_err.write("\n" + tb + "\n")

        finished = now_iso()

        post = validate_canonical_contract_file(
            canonical_path,
            cp_id,
        )

        if exc is None:
            if post["valid_local_shape"]:
                status = "COMPILED_CANONICAL_VALID"
            else:
                status = (
                    "COMPILED_CANONICAL_INVALID_LOCAL_SHAPE"
                )
        else:
            status = classify_failure(exc)

        row = {
            "cp_id": cp_id,
            "status": status,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "canonical_path": str(canonical_path),
            "canonical_validation": post,
            "api_called": bool(
                telemetry
                and telemetry.get(
                    "request_attempt_count",
                    0,
                )
            ),
            "cost_telemetry": telemetry,
            "exception_type": (
                type(exc).__name__
                if exc is not None
                else None
            ),
            "exception_message": (
                str(exc)
                if exc is not None
                else None
            ),
            "traceback_path": (
                str(traceback_path)
                if traceback_path.exists()
                else None
            ),
            "log_path": str(log_path),
        }

        report["cp_results"][cp_id] = row
        update_summary(report)
        save_json(report, report_path)

        print()
        print(
            "RESULT:",
            cp_id,
            status,
        )

        if telemetry:
            print(
                "TOKENS:",
                telemetry.get("total_tokens"),
                "| calls:",
                telemetry.get(
                    "request_attempt_count"
                ),
            )

        if exc is not None:
            print(
                "ERROR:",
                type(exc).__name__,
                str(exc),
            )

            if args.stop_on_error:
                report["finished_at_utc"] = now_iso()
                update_summary(report)
                save_json(report, report_path)
                raise exc

    report["finished_at_utc"] = now_iso()
    update_summary(report)
    save_json(report, report_path)

    print()
    print("=" * 80)
    print("BATCH BUILD CENSUS")
    print("=" * 80)

    for status, count in (
        report["summary"][
            "status_counts"
        ].items()
    ):
        print(
            f"{status}: {count}"
        )

    print()
    print(
        "Canonical success/existing:",
        report["summary"][
            "canonical_success_or_existing_count"
        ],
        "/",
        len(selected),
    )

    print(
        "Review required:",
        report["summary"][
            "review_required_count"
        ],
    )

    print(
        "Failed:",
        report["summary"][
            "failed_count"
        ],
    )

    print("Saved:", report_path)


if __name__ == "__main__":
    main()
