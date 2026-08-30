#!/usr/bin/env python3
"""Grounding-only recovery for FRECA EvidenceRequirement raw/normalized output.

ZERO API.

Only repairs quote-grounding metadata from authoritative supplied sources:
- invalid criterion_quote -> exact official CP criterion text;
- invalid CP query_source quote -> exact official CP criterion text;
- invalid RULES query_source quote -> exact own_text of the SAME candidate_id.

It does NOT change:
- proposition_to_establish
- facet_seed_id / atom_id
- basis_candidate_ids
- RULES candidate_id selection
- source_group_ids
- reason
- contract logic

The CURRENT live EvidenceRequirement validator remains final authority.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import evidence_reasoning_v2 as er
import freca_core_v1 as core


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


def extract_requirements_container(value: dict) -> dict:
    # Accept output from recover_evidence_plan_from_raw_v1.py.
    if isinstance(value.get("normalized_raw"), dict):
        return copy.deepcopy(value["normalized_raw"])

    if isinstance(value.get("requirements"), list):
        return copy.deepcopy(value)

    raise ValueError(
        "Input must contain requirements or normalized_raw.requirements"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cp", required=True)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("contracts_v2"),
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--install-valid-plan", action="store_true")
    args = p.parse_args()

    cp = core.get_cp(args.cp)
    cp_id = cp["cp_id"]

    contract_path = args.contract_dir / f"{cp_id}.json"
    ledger_path = args.contract_dir / f"{cp_id}_candidate_ledger.json"
    relation_path = args.contract_dir / f"{cp_id}_rule_set_relation.json"
    plan_path = args.contract_dir / f"{cp_id}_evidence_requirements.json"

    contract = load_json(contract_path)
    ledger = load_json(ledger_path)
    relation = load_json(relation_path)

    candidates, _decisions = er._candidate_maps(ledger)

    source = load_json(args.input)
    raw = extract_requirements_container(source)

    changes = []

    for req in raw.get("requirements", []):
        rid = str(req.get("requirement_id") or "")

        criterion_quote = str(req.get("criterion_quote") or "").strip()
        if er.quote_match_mode(criterion_quote, cp["criterion"]) is None:
            before = req.get("criterion_quote")
            req["criterion_quote"] = cp["criterion"]
            changes.append({
                "requirement_id": rid,
                "field": "criterion_quote",
                "before": before,
                "after_source": "OFFICIAL_CP_FULL_CRITERION",
            })

        query_sources = req.get("query_sources")
        if not isinstance(query_sources, list):
            continue

        for i, qs in enumerate(query_sources):
            if not isinstance(qs, dict):
                continue

            source_type = str(qs.get("source") or "").upper()
            qs["source"] = source_type
            quote = str(qs.get("quote") or "").strip()

            if source_type == "CP":
                if er.quote_match_mode(quote, cp["criterion"]) is None:
                    before = qs.get("quote")
                    qs["quote"] = cp["criterion"]
                    qs["candidate_id"] = None
                    changes.append({
                        "requirement_id": rid,
                        "field": f"query_sources[{i}].quote",
                        "before": before,
                        "after_source": "OFFICIAL_CP_FULL_CRITERION",
                    })

            elif source_type == "RULES":
                candidate_id = str(qs.get("candidate_id") or "")
                candidate = candidates.get(candidate_id)
                if candidate is None:
                    # Candidate selection is substantive; do not invent/replace it.
                    continue

                own_text = str(
                    candidate.get("own_text", candidate.get("text", ""))
                    or ""
                )
                if er.quote_match_mode(quote, own_text) is None:
                    before = qs.get("quote")
                    qs["quote"] = own_text
                    changes.append({
                        "requirement_id": rid,
                        "field": f"query_sources[{i}].quote",
                        "before": before,
                        "after_source": f"RULES_OWN_TEXT:{candidate_id}",
                    })

    audit = {
        "schema": "freca-evidence-plan-grounding-recovery-v1",
        "cp_id": cp_id,
        "source_input": str(args.input),
        "change_count": len(changes),
        "changes": changes,
        "substantive_fields_rewritten": False,
        "api_called": False,
        "answer_comparator_used": False,
        "normalized_raw": raw,
    }
    save_json_atomic(audit, args.output)

    print("=" * 80)
    print("FRECA EVIDENCE PLAN GROUNDING RECOVERY V1")
    print("=" * 80)
    print("CP:", cp_id)
    print("changes:", len(changes))
    for c in changes:
        print(
            " ",
            c["requirement_id"],
            c["field"],
            "->",
            c["after_source"],
        )

    try:
        validated = er.validate_evidence_requirements(
            raw,
            cp,
            contract,
            ledger,
            relation,
        )
    except Exception as exc:
        print()
        print("CURRENT VALIDATOR: FAIL")
        print(type(exc).__name__ + ":", exc)
        print("Saved:", args.output)
        print("API called: False")
        raise SystemExit(2)

    print()
    print("CURRENT VALIDATOR: PASS")
    print("requirements:", len(validated.get("requirements") or []))
    print("facet_seeds:", len(validated.get("facet_seeds") or []))

    if args.install_valid_plan:
        save_json_atomic(validated, plan_path)
        replayed = load_json(plan_path)
        er.validate_evidence_requirements(
            replayed,
            cp,
            contract,
            ledger,
            relation,
        )
        print("Installed valid plan:", plan_path)

    print("Saved:", args.output)
    print("API called: False")
    print("Answer comparator used: False")


if __name__ == "__main__":
    main()
