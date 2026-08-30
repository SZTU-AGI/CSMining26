#!/usr/bin/env python3
"""FRECA EvidenceRequirement production batch compiler v2.

Purpose
-------
Materialize CP1..CP41 EvidenceRequirement plans using the CURRENT live
EvidenceRequirement prompt + validator, with deterministic normalization around
model output.

Production safeguards
---------------------
- No case evidence.
- No answer comparator.
- Existing CURRENT-validator-valid plans are skipped by default.
- Every raw model JSON is persisted before any normalization.
- Deterministic structural normalization fixes only authoritative schema fields:
    requirement_id
    facet_seed_id
    atom_id
    polarity=SUPPORT
    decisiveness=DECISIVE
    source_group_ids
    query_sources[*].source casing
- Deterministic grounding normalization fixes only quote metadata:
    criterion_quote -> exact official CP criterion when ungrounded
    CP query quote -> exact official CP criterion when ungrounded
    RULES query quote -> exact own_text for the SAME candidate_id when ungrounded
- Substantive fields are not rewritten:
    proposition_to_establish
    basis_candidate_ids
    RULES candidate_id selection
    reason
- CURRENT live validator is final authority.
- Validator failures may trigger generic repair retries.
- Each CP is independent and aggregate report is checkpointed after every CP.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evidence_reasoning_v2 as er
import freca_core_v1 as core
import multi_atom_support_v1 as ma


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


def unwrap_contract(bundle: dict) -> dict:
    inner = bundle.get("contract")
    return inner if isinstance(inner, dict) else bundle


def contract_inputs(contract_dir: Path, cp_id: str):
    cp = core.get_cp(cp_id)
    cp_id = cp["cp_id"]

    paths = {
        "contract": contract_dir / f"{cp_id}.json",
        "ledger": contract_dir / f"{cp_id}_candidate_ledger.json",
        "relation": contract_dir / f"{cp_id}_rule_set_relation.json",
        "plan": contract_dir / f"{cp_id}_evidence_requirements.json",
    }

    missing = [
        name for name, path in paths.items()
        if name != "plan" and not path.exists()
    ]
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
        value = load_json(plan_path)
        validate_current(
            value,
            cp,
            contract,
            ledger,
            relation,
        )
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def eligible_ids(ledger: dict) -> set[str]:
    _candidates, decisions = er._candidate_maps(ledger)
    return {
        str(candidate_id)
        for candidate_id, decision in decisions.items()
        if (
            decision.get("selected")
            and decision.get("relation") == "PRIMARY_NORM"
            and decision.get("contract_eligible", False)
        )
    }


def extract_requirements(raw: dict) -> tuple[list[dict], str]:
    direct = raw.get("requirements")
    if isinstance(direct, list) and direct:
        return direct, "requirements"

    candidates = []

    for key, value in raw.items():
        if isinstance(value, dict):
            reqs = value.get("requirements")
            if isinstance(reqs, list) and reqs:
                candidates.append((f"{key}.requirements", reqs))

        elif (
            isinstance(value, list)
            and value
            and all(isinstance(x, dict) for x in value)
        ):
            if all(
                ("facet_seed_id" in x or "requirement_id" in x)
                for x in value
            ):
                candidates.append((key, value))

    if len(candidates) != 1:
        raise ValueError(
            "Could not unambiguously recover requirements list; "
            f"candidates={[x[0] for x in candidates]}"
        )

    return candidates[0][1], candidates[0][0]


def deterministic_normalize(
    *,
    raw: dict,
    cp: dict,
    contract_bundle: dict,
    ledger: dict,
    relation: dict,
) -> tuple[dict, dict]:
    """Normalize only schema + grounding metadata; never semantic proposition."""
    contract = unwrap_contract(contract_bundle)
    candidates, _decisions = er._candidate_maps(ledger)

    seeds = ma.build_multi_atom_facet_seeds(
        contract,
        relation,
        eligible_ids(ledger),
    )
    seed_order = [s["facet_seed_id"] for s in seeds]
    seed_map = {s["facet_seed_id"]: s for s in seeds}

    requirements, recovered_from = extract_requirements(raw)

    rows_by_seed: dict[str, list[dict]] = {}
    extras = []

    for row in requirements:
        if not isinstance(row, dict):
            raise ValueError(
                "EvidenceRequirement item must be an object"
            )

        seed_id = str(
            row.get("facet_seed_id") or ""
        ).strip()

        if seed_id not in seed_map:
            extras.append(seed_id or "<missing>")
            continue

        rows_by_seed.setdefault(
            seed_id,
            [],
        ).append(row)

    missing = [
        sid
        for sid in seed_order
        if sid not in rows_by_seed
    ]

    duplicates = {
        sid: len(rows)
        for sid, rows in rows_by_seed.items()
        if len(rows) != 1
    }

    if missing or duplicates or extras:
        raise ValueError(
            "Raw output cannot be deterministically mapped to "
            "authoritative FACET_SEEDs: "
            f"missing={missing}, duplicates={duplicates}, extras={extras}"
        )

    normalized = []
    changes = []

    def record_change(seed_id, field, before, after_source):
        changes.append({
            "facet_seed_id": seed_id,
            "field": field,
            "before": before,
            "after_source": after_source,
        })

    for index, seed_id in enumerate(
        seed_order,
        start=1,
    ):
        seed = seed_map[seed_id]
        row = copy.deepcopy(
            rows_by_seed[seed_id][0]
        )

        fixed = {
            "requirement_id": f"ER{index}",
            "facet_seed_id": seed_id,
            "atom_id": seed["atom_id"],
            "polarity": "SUPPORT",
            "decisiveness": "DECISIVE",
            "source_group_ids": list(
                seed.get("source_group_ids") or []
            ),
        }

        for key, value in fixed.items():
            before = row.get(key)
            if before != value:
                record_change(
                    seed_id,
                    key,
                    before,
                    "AUTHORITATIVE_FACET_SEED",
                )
            row[key] = value

        # criterion_quote is pure grounding metadata.
        criterion_quote = str(
            row.get("criterion_quote") or ""
        ).strip()

        if (
            er.quote_match_mode(
                criterion_quote,
                cp["criterion"],
            )
            is None
        ):
            before = row.get("criterion_quote")
            row["criterion_quote"] = cp["criterion"]
            record_change(
                seed_id,
                "criterion_quote",
                before,
                "OFFICIAL_CP_FULL_CRITERION",
            )

        query_sources = row.get("query_sources")

        if isinstance(query_sources, list):
            fixed_sources = []

            for i, source in enumerate(
                query_sources
            ):
                if not isinstance(source, dict):
                    fixed_sources.append(source)
                    continue

                source = copy.deepcopy(source)

                before_type = source.get("source")
                source_type = str(
                    before_type or ""
                ).upper()

                if before_type != source_type:
                    record_change(
                        seed_id,
                        f"query_sources[{i}].source",
                        before_type,
                        "NORMALIZED_ENUM_CASE",
                    )

                source["source"] = source_type
                quote = str(
                    source.get("quote") or ""
                ).strip()

                if source_type == "CP":
                    if (
                        er.quote_match_mode(
                            quote,
                            cp["criterion"],
                        )
                        is None
                    ):
                        before = source.get("quote")
                        source["quote"] = cp["criterion"]
                        source["candidate_id"] = None

                        record_change(
                            seed_id,
                            f"query_sources[{i}].quote",
                            before,
                            "OFFICIAL_CP_FULL_CRITERION",
                        )

                elif source_type == "RULES":
                    candidate_id = str(
                        source.get("candidate_id")
                        or ""
                    )

                    # Never invent/change candidate identity.
                    candidate = candidates.get(
                        candidate_id
                    )

                    if candidate is not None:
                        own_text = str(
                            candidate.get(
                                "own_text",
                                candidate.get(
                                    "text",
                                    "",
                                ),
                            )
                            or ""
                        )

                        if (
                            er.quote_match_mode(
                                quote,
                                own_text,
                            )
                            is None
                        ):
                            before = source.get(
                                "quote"
                            )
                            source["quote"] = own_text

                            record_change(
                                seed_id,
                                f"query_sources[{i}].quote",
                                before,
                                "RULES_OWN_TEXT:"
                                + candidate_id,
                            )

                fixed_sources.append(source)

            row["query_sources"] = fixed_sources

        normalized.append(row)

    return {
        "requirements": normalized,
    }, {
        "recovered_from": recovered_from,
        "expected_seed_ids": seed_order,
        "change_count": len(changes),
        "changes": changes,
        "substantive_fields_rewritten": False,
        "api_called": False,
        "answer_comparator_used": False,
    }


def repair_suffix(
    *,
    attempt: int,
    validator_error: str,
    normalized_raw: dict | None,
) -> str:
    previous = (
        json.dumps(
            normalized_raw,
            ensure_ascii=False,
            indent=2,
        )
        if normalized_raw is not None
        else "{}"
    )

    return (
        "\n\n"
        "VALIDATOR_REPAIR_REQUEST:\n"
        f"Repair attempt {attempt}. "
        "The deterministic validator rejected the previous "
        "normalized output.\n"
        "Do NOT reinterpret the CP, add legal duties, use case "
        "evidence, or change FACET_SEED identities.\n"
        "Return only one JSON object with top-level "
        "\"requirements\".\n"
        "Return exactly one requirement per supplied FACET_SEED.\n"
        "Focus only on substantive fields that remain invalid, "
        "especially proposition_to_establish, basis_candidate_ids, "
        "RULES candidate_id selection, or query source selection.\n"
        "Do not fight deterministic fields: requirement_id, "
        "facet_seed_id, atom_id, polarity, decisiveness, "
        "source_group_ids and grounded quote text may be normalized "
        "after your response.\n"
        "Validator error:\n"
        f"{validator_error}\n\n"
        "PREVIOUS_NORMALIZED_JSON:\n"
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

    cp, contract, ledger, relation, paths = (
        contract_inputs(
            contract_dir,
            cp_id,
        )
    )
    cp_id = cp["cp_id"]

    existing_valid, existing_error = (
        existing_plan_status(
            paths["plan"],
            cp,
            contract,
            ledger,
            relation,
        )
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
    cp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    base_prompt = er.make_evidence_requirement_prompt(
        cp,
        contract,
        ledger,
        relation,
    )

    last_error = existing_error
    last_normalized = None
    attempt_rows = []

    for attempt in range(
        1,
        max_attempts + 1,
    ):
        prompt = base_prompt

        if (
            attempt > 1
            and last_error is not None
        ):
            prompt += repair_suffix(
                attempt=attempt,
                validator_error=last_error,
                normalized_raw=last_normalized,
            )

        row = {
            "attempt": attempt,
            "started_at": now_iso(),
        }

        try:
            raw = core.deepseek_json(
                model=er.EVIDENCE_PLAN_MODEL,
                system_prompt=er.EVIDENCE_REQUIREMENT_SYSTEM,
                user_prompt=prompt,
                thinking=False,
                max_tokens=6000,
            )

            raw_path = (
                cp_dir
                / f"{cp_id}_attempt{attempt}_raw.json"
            )
            save_json_atomic(
                raw,
                raw_path,
            )

            row["raw_path"] = str(
                raw_path
            )
            row["raw_sha256"] = (
                sha256_json(raw)
            )
            row["raw_top_level_keys"] = (
                sorted(raw.keys())
            )

            try:
                normalized, norm_audit = (
                    deterministic_normalize(
                        raw=raw,
                        cp=cp,
                        contract_bundle=contract,
                        ledger=ledger,
                        relation=relation,
                    )
                )
            except Exception as exc:
                last_error = (
                    f"{type(exc).__name__}: {exc}"
                )
                last_normalized = None

                row["status"] = (
                    "NORMALIZATION_FAILED"
                )
                row["error"] = last_error
                row["finished_at"] = (
                    now_iso()
                )
                attempt_rows.append(row)

                (
                    cp_dir
                    / f"{cp_id}_attempt{attempt}_"
                      "normalization_error.txt"
                ).write_text(
                    last_error + "\n",
                    encoding="utf-8",
                )
                continue

            normalized_path = (
                cp_dir
                / f"{cp_id}_attempt{attempt}_"
                  "normalized.json"
            )

            save_json_atomic(
                {
                    "schema":
                        "freca-evidence-plan-"
                        "deterministic-normalization-v2",
                    "cp_id": cp_id,
                    "source_raw": str(
                        raw_path
                    ),
                    "audit": norm_audit,
                    "normalized_raw": normalized,
                },
                normalized_path,
            )

            row["normalized_path"] = str(
                normalized_path
            )
            row["normalization_changes"] = (
                norm_audit["change_count"]
            )

            last_normalized = normalized

            try:
                validated = validate_current(
                    normalized,
                    cp,
                    contract,
                    ledger,
                    relation,
                )
            except Exception as exc:
                last_error = (
                    f"{type(exc).__name__}: {exc}"
                )

                row["status"] = (
                    "VALIDATION_FAILED"
                )
                row["error"] = last_error
                row["finished_at"] = (
                    now_iso()
                )
                attempt_rows.append(row)

                (
                    cp_dir
                    / f"{cp_id}_attempt{attempt}_"
                      "validator_error.txt"
                ).write_text(
                    last_error + "\n",
                    encoding="utf-8",
                )
                continue

            # Only CURRENT-validator-valid bytes become canonical plan.
            save_json_atomic(
                validated,
                paths["plan"],
            )

            replayed = load_json(
                paths["plan"]
            )

            validate_current(
                replayed,
                cp,
                contract,
                ledger,
                relation,
            )

            row["status"] = (
                "VALIDATED_AND_SAVED"
            )
            row["finished_at"] = (
                now_iso()
            )
            attempt_rows.append(row)

            return {
                "cp_id": cp_id,
                "status":
                    "COMPILED_CURRENT_VALID",
                "started_at": started,
                "finished_at": now_iso(),
                "attempts": attempt,
                "existing_error":
                    existing_error,
                "plan_path":
                    str(paths["plan"]),
                "plan_sha256":
                    sha256_json(replayed),
                "requirement_count":
                    len(
                        replayed.get(
                            "requirements"
                        )
                        or []
                    ),
                "facet_seed_count":
                    len(
                        replayed.get(
                            "facet_seeds"
                        )
                        or []
                    ),
                "attempt_log":
                    attempt_rows,
                "answer_comparator_used":
                    False,
                "case_evidence_used":
                    False,
            }

        except Exception as exc:
            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

            row["status"] = (
                "CALL_OR_RUNTIME_FAILED"
            )
            row["error"] = last_error
            row["traceback"] = (
                traceback.format_exc()
            )
            row["finished_at"] = (
                now_iso()
            )
            attempt_rows.append(row)

            (
                cp_dir
                / f"{cp_id}_attempt{attempt}_"
                  "traceback.txt"
            ).write_text(
                row["traceback"],
                encoding="utf-8",
            )

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


def checkpoint(
    rows: list[dict],
    output: Path,
) -> None:
    counts = {}

    for row in rows:
        counts[row["status"]] = (
            counts.get(
                row["status"],
                0,
            )
            + 1
        )

    report = {
        "schema":
            "freca-evidence-plan-"
            "production-batch-v2",
        "processed_cp_count":
            len(rows),
        "status_counts":
            dict(sorted(counts.items())),
        "rows": rows,
        "all_processed_successfully":
            all(
                row["status"]
                in {
                    "SKIPPED_CURRENT_VALID",
                    "COMPILED_CURRENT_VALID",
                }
                for row in rows
            ),
        "answer_comparator_used":
            False,
        "case_evidence_used":
            False,
    }

    save_json_atomic(
        report,
        output,
    )


def main() -> None:
    p = argparse.ArgumentParser()

    target = p.add_mutually_exclusive_group(
        required=True
    )
    target.add_argument(
        "--cp",
        action="append",
    )
    target.add_argument(
        "--all",
        action="store_true",
    )

    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path(
            "results_v2/"
            "evidence_plan_production_batch_v2"
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results_v2/"
            "evidence_plan_production_batch_v2/"
            "report.json"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=3,
    )

    args = p.parse_args()

    if not (
        1
        <= args.max_attempts
        <= 5
    ):
        p.error(
            "--max-attempts must be 1..5"
        )

    cp_ids = (
        CP_IDS
        if args.all
        else args.cp
    )

    rows = []

    for cp_id in cp_ids:
        print(
            "\n"
            + "=" * 80
        )
        print(
            "EVIDENCE PLAN BATCH V2",
            cp_id,
        )
        print("=" * 80)

        try:
            row = compile_one(
                contract_dir=
                    args.contract_dir,
                results_dir=
                    args.results_dir,
                cp_id=cp_id,
                force=args.force,
                max_attempts=
                    args.max_attempts,
            )
        except Exception as exc:
            row = {
                "cp_id": cp_id,
                "status":
                    "PRECHECK_FAILED",
                "started_at":
                    now_iso(),
                "finished_at":
                    now_iso(),
                "attempts": 0,
                "last_error":
                    f"{type(exc).__name__}: {exc}",
                "traceback":
                    traceback.format_exc(),
                "answer_comparator_used":
                    False,
                "case_evidence_used":
                    False,
            }

        rows.append(row)
        checkpoint(
            rows,
            args.output,
        )

        print(
            "status:",
            row["status"],
        )
        print(
            "attempts:",
            row.get(
                "attempts",
                0,
            ),
        )

        if row.get("last_error"):
            print(
                "last_error:",
                row["last_error"],
            )

        if (
            row.get(
                "requirement_count"
            )
            is not None
        ):
            print(
                "requirements:",
                row[
                    "requirement_count"
                ],
            )

        if (
            row.get(
                "facet_seed_count"
            )
            is not None
        ):
            print(
                "facet_seeds:",
                row[
                    "facet_seed_count"
                ],
            )

    print(
        "\n"
        + "=" * 80
    )
    print("SUMMARY")
    print("=" * 80)

    for row in rows:
        print(
            row["cp_id"],
            row["status"],
        )

    print(
        "Saved:",
        args.output,
    )

    if any(
        row["status"]
        not in {
            "SKIPPED_CURRENT_VALID",
            "COMPILED_CURRENT_VALID",
        }
        for row in rows
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
