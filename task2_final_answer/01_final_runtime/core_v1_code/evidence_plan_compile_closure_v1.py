#!/usr/bin/env python3
"""Resume-safe EvidenceRequirement plan compiler closure v1.

Uses the CURRENT live FRECA compiler prompt + validator, but adds the missing
production closure around model schema/validation failures:

- validates an existing plan before deciding to skip it;
- persists every raw model JSON BEFORE validation;
- on deterministic validator failure, retries with a generic validator-repair
  instruction (no CP-specific answer, no case evidence, no comparator);
- atomically writes only a CURRENT-validator-valid plan;
- supports one CP or all CP1..CP41;
- checkpoints a report after every CP.

This script does not change contract logic and does not read case evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evidence_reasoning_v2 as er
import freca_core_v1 as core


CP_IDS = [f"CP{i}" for i in range(1, 42)]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    return value


def save_json_atomic(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def sha256_json(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def contract_inputs(contract_dir: Path, cp_id: str):
    cp = core.get_cp(cp_id)
    cp_id = cp["cp_id"]

    paths = {
        "contract": contract_dir / f"{cp_id}.json",
        "ledger": contract_dir / f"{cp_id}_candidate_ledger.json",
        "relation": contract_dir / f"{cp_id}_rule_set_relation.json",
        "plan": contract_dir / f"{cp_id}_evidence_requirements.json",
    }

    missing = [k for k, p in paths.items() if k != "plan" and not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"{cp_id}: missing dependencies: {', '.join(missing)}"
        )

    return (
        cp,
        load_json(paths["contract"]),
        load_json(paths["ledger"]),
        load_json(paths["relation"]),
        paths,
    )


def validate_current(
    raw: dict,
    cp: dict,
    contract: dict,
    ledger: dict,
    relation: dict,
) -> dict:
    return er.validate_evidence_requirements(
        raw,
        cp,
        contract,
        ledger,
        relation,
    )


def existing_plan_status(
    plan_path: Path,
    cp: dict,
    contract: dict,
    ledger: dict,
    relation: dict,
) -> tuple[bool, str | None]:
    if not plan_path.exists():
        return False, "MISSING"

    try:
        raw = load_json(plan_path)
        validate_current(raw, cp, contract, ledger, relation)
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def repair_suffix(
    *,
    attempt: int,
    validator_error: str,
    prior_raw: dict,
) -> str:
    # Generic schema/grounding repair only. It does not add substantive CP logic.
    previous = json.dumps(
        prior_raw,
        ensure_ascii=False,
        indent=2,
    )

    return (
        "\n\n"
        "VALIDATOR_REPAIR_REQUEST:\n"
        f"This is repair attempt {attempt}. The previous JSON was rejected by "
        "the deterministic EvidenceRequirement validator.\n"
        "Do NOT reinterpret the CP, add legal duties, use case evidence, or "
        "change the supplied FACET_SEEDS.\n"
        "Return ONLY one JSON object whose TOP-LEVEL key is exactly "
        "\"requirements\" and whose value is a non-empty array.\n"
        "Return EXACTLY ONE requirement for EVERY supplied FACET_SEED and no "
        "extra requirements.\n"
        "For every requirement, copy the supplied facet_seed_id and atom_id "
        "exactly; use only basis_candidate_ids/source_group_ids/query-source "
        "quotes allowed by that seed.\n"
        "The validator error was:\n"
        f"{validator_error}\n\n"
        "PREVIOUS_JSON_TO_REPAIR:\n"
        f"{previous}\n"
    )


def compile_one(
    *,
    contract_dir: Path,
    results_dir: Path,
    cp_id: str,
    force: bool,
    max_attempts: int,
) -> dict:
    started = now_iso()
    cp, contract, ledger, relation, paths = contract_inputs(
        contract_dir, cp_id
    )
    cp_id = cp["cp_id"]

    existing_valid, existing_error = existing_plan_status(
        paths["plan"], cp, contract, ledger, relation
    )

    if existing_valid and not force:
        return {
            "cp_id": cp_id,
            "status": "SKIPPED_CURRENT_VALID",
            "started_at": started,
            "finished_at": now_iso(),
            "attempts": 0,
            "existing_error": None,
            "plan_path": str(paths["plan"]),
            "answer_comparator_used": False,
            "case_evidence_used": False,
        }

    cp_dir = results_dir / cp_id
    cp_dir.mkdir(parents=True, exist_ok=True)

    base_prompt = er.make_evidence_requirement_prompt(
        cp, contract, ledger, relation
    )

    last_error = existing_error
    prior_raw = None
    attempt_rows = []

    for attempt in range(1, max_attempts + 1):
        user_prompt = base_prompt
        if prior_raw is not None and last_error is not None:
            user_prompt += repair_suffix(
                attempt=attempt,
                validator_error=last_error,
                prior_raw=prior_raw,
            )

        attempt_row = {
            "attempt": attempt,
            "started_at": now_iso(),
        }

        try:
            raw = core.deepseek_json(
                model=er.EVIDENCE_PLAN_MODEL,
                system_prompt=er.EVIDENCE_REQUIREMENT_SYSTEM,
                user_prompt=user_prompt,
                thinking=False,
                max_tokens=6000,
            )

            raw_path = cp_dir / f"{cp_id}_attempt{attempt}_raw.json"
            save_json_atomic(raw, raw_path)

            attempt_row["raw_path"] = str(raw_path)
            attempt_row["raw_sha256"] = sha256_json(raw)
            attempt_row["raw_top_level_keys"] = sorted(raw.keys())

            try:
                validated = validate_current(
                    raw, cp, contract, ledger, relation
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                attempt_row["status"] = "VALIDATION_FAILED"
                attempt_row["error"] = last_error
                attempt_row["finished_at"] = now_iso()
                attempt_rows.append(attempt_row)

                error_path = cp_dir / f"{cp_id}_attempt{attempt}_validator_error.txt"
                error_path.write_text(
                    last_error + "\n",
                    encoding="utf-8",
                )

                prior_raw = raw
                continue

            # Persist only validated canonical plan.
            save_json_atomic(validated, paths["plan"])

            # Replay current validator against bytes just written.
            replayed = load_json(paths["plan"])
            validate_current(
                replayed, cp, contract, ledger, relation
            )

            attempt_row["status"] = "VALIDATED_AND_SAVED"
            attempt_row["finished_at"] = now_iso()
            attempt_rows.append(attempt_row)

            return {
                "cp_id": cp_id,
                "status": "COMPILED_CURRENT_VALID",
                "started_at": started,
                "finished_at": now_iso(),
                "attempts": attempt,
                "existing_error": existing_error,
                "plan_path": str(paths["plan"]),
                "plan_sha256": sha256_json(replayed),
                "requirement_count": len(
                    replayed.get("requirements") or []
                ),
                "facet_seed_count": len(
                    replayed.get("facet_seeds") or []
                ),
                "attempt_log": attempt_rows,
                "answer_comparator_used": False,
                "case_evidence_used": False,
            }

        except Exception as exc:
            # This captures call/parser/runtime failures, distinct from validator
            # failures. A raw artifact may not exist in this branch.
            last_error = f"{type(exc).__name__}: {exc}"
            attempt_row["status"] = "CALL_OR_RUNTIME_FAILED"
            attempt_row["error"] = last_error
            attempt_row["traceback"] = traceback.format_exc()
            attempt_row["finished_at"] = now_iso()
            attempt_rows.append(attempt_row)

            (cp_dir / f"{cp_id}_attempt{attempt}_traceback.txt").write_text(
                attempt_row["traceback"],
                encoding="utf-8",
            )

            # No prior parsed JSON => generic repeat is allowed on later attempt.
            # Preserve prior_raw if one exists from an earlier validation failure.
            continue

    return {
        "cp_id": cp_id,
        "status": "FAILED_AFTER_RETRIES",
        "started_at": started,
        "finished_at": now_iso(),
        "attempts": max_attempts,
        "existing_error": existing_error,
        "last_error": last_error,
        "plan_path": str(paths["plan"]),
        "attempt_log": attempt_rows,
        "answer_comparator_used": False,
        "case_evidence_used": False,
    }


def checkpoint(rows: list[dict], output: Path) -> None:
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    report = {
        "schema": "freca-evidence-plan-compile-closure-v1",
        "rows": rows,
        "status_counts": dict(sorted(counts.items())),
        "all_processed_successfully": all(
            row["status"] in {
                "SKIPPED_CURRENT_VALID",
                "COMPILED_CURRENT_VALID",
            }
            for row in rows
        ),
        "answer_comparator_used": False,
        "case_evidence_used": False,
    }
    save_json_atomic(report, output)


def main() -> None:
    p = argparse.ArgumentParser()
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--cp", action="append")
    target.add_argument("--all", action="store_true")

    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path(
            "results_v2/evidence_plan_compile_closure_v1"
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results_v2/evidence_plan_compile_closure_v1/report.json"
        ),
    )
    p.add_argument("--force", action="store_true")
    p.add_argument("--max-attempts", type=int, default=3)

    args = p.parse_args()
    if args.max_attempts < 1 or args.max_attempts > 5:
        p.error("--max-attempts must be between 1 and 5")

    cp_ids = CP_IDS if args.all else args.cp

    rows = []
    for cp_id in cp_ids:
        print("\n" + "=" * 80)
        print("EVIDENCE PLAN CLOSURE", cp_id)
        print("=" * 80)

        row = compile_one(
            contract_dir=args.contract_dir,
            results_dir=args.results_dir,
            cp_id=cp_id,
            force=args.force,
            max_attempts=args.max_attempts,
        )
        rows.append(row)
        checkpoint(rows, args.output)

        print("status:", row["status"])
        print("attempts:", row["attempts"])
        if row.get("last_error"):
            print("last_error:", row["last_error"])
        if row.get("requirement_count") is not None:
            print("requirements:", row["requirement_count"])
        if row.get("facet_seed_count") is not None:
            print("facet_seeds:", row["facet_seed_count"])

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for row in rows:
        print(row["cp_id"], row["status"])
    print("Saved:", args.output)

    if any(
        row["status"] == "FAILED_AFTER_RETRIES"
        for row in rows
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
