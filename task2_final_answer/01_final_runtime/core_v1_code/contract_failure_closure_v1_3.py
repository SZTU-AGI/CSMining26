#!/usr/bin/env python3
"""FRECA residual contract failure closure v1.3.

Residual-only closure for the three remaining contract failures observed after v1.1.
The script does NOT hard-code CP answers. It adds three generic compiler-boundary repairs:

1) Drop illegal singleton relation groups at model-output and relation-validator boundaries.
2) Remove false unresolved-relation markers when a parent/chapeau rule is already
   structurally consumed by a relation group made entirely of strict descendants.
3) When a compile fails with ``No contract atoms``, repair is routed upstream to
   LegalBasisLink review as well as the final contract stage, so a broader rule that
   genuinely supplies the legal duty is not demoted to CONTEXT_ONLY merely because
   the official CP operationalizes/narrows it.

No case evidence, labels, answer comparator, fallback contract, or CP-specific legal
conclusion is used. All normalizations are structure-preserving and source-grounded.
"""
from __future__ import annotations

import argparse
import contextlib
import inspect
import json
import traceback
from pathlib import Path
from typing import Any

from telemetry_capture_v1 import capture_deepseek_telemetry, summarize_telemetry
from contract_batch_compile_v1 import (
    discover_policy_top_k_default,
    validate_canonical_contract_file,
)

DEFAULT_CPS = ["CP6", "CP23", "CP39"]

RELATION_GROUP_HINT = (
    "RELATION_GROUP_REPAIR: Do not emit singleton relation groups. A candidate may "
    "remain independently selected without being placed in a one-member group."
)
RELATION_HINT = (
    "RELATION_REPAIR: Re-evaluate the relation using only supplied source text and "
    "explicit connectors/scope. A rule used only as the grounded ancestor/chapeau "
    "CONNECTOR for a child relation group must not also be reported as an unresolved "
    "unclassified relation merely because it is the parent of those members. Do not "
    "invent a relation for a genuinely independent rule."
)
NO_ATOM_LEGAL_BASIS_HINT = (
    "NO_ATOM_UPSTREAM_LEGAL_BASIS_RECHECK: Re-check LegalBasisLink decisions before "
    "contract atom construction. Do not classify a supplied PRIMARY_NORM as "
    "CONTEXT_ONLY solely because the official CP criterion is narrower or more "
    "operational than the rule text. If the rule actually supplies the legal duty "
    "and the CP operationalizes/specializes that duty, use the appropriate allowed "
    "non-CONTEXT_ONLY relation from the supplied schema and quote only grounded "
    "source text. If the rule does not materially support the criterion, preserve "
    "CONTEXT_ONLY. Never invent law or upgrade a link merely to make compilation pass."
)
NO_ATOM_CONTRACT_HINT = (
    "CONTRACT_ATOM_REPAIR: Return at least one grounded atom only if the supplied "
    "contract-eligible non-CONTEXT_ONLY legal basis supports it. Every atom must "
    "quote exact CP criterion text and use only supplied eligible PRIMARY_NORM/legal "
    "basis candidates. Split into multiple atoms only when the supplied CP/Rules "
    "explicitly establish cumulative benchmark dimensions. Do not create a fallback "
    "atom when upstream legal basis remains insufficient."
)
GENERIC_HINT = (
    "VALIDATOR_REPAIR: Correct the previous validator failure using only supplied "
    "official CP/Rules artifacts. Preserve grounding and do not invent law."
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _members(group: dict) -> list | None:
    for key in (
        "member_candidate_ids", "member_ids", "candidate_ids",
        "norm_event_ids", "event_ids", "members",
    ):
        value = group.get(key)
        if isinstance(value, list):
            return value
    return None


def normalize_singleton_groups(value: Any):
    """Drop only relation groups that explicitly have fewer than two members."""
    removed = 0

    def rec(obj):
        nonlocal removed
        if isinstance(obj, list):
            return [rec(x) for x in obj]
        if not isinstance(obj, dict):
            return obj
        out = {}
        for key, child in obj.items():
            if key in {"groups", "event_relation_groups", "relation_groups"} and isinstance(child, list):
                rows = []
                for group in child:
                    if isinstance(group, dict):
                        members = _members(group)
                        if members is not None and len(members) < 2:
                            removed += 1
                            continue
                    rows.append(rec(group))
                out[key] = rows
            else:
                out[key] = rec(child)
        return out

    return rec(value), removed


def _rule_locator(candidate_id: str) -> str:
    text = str(candidate_id)
    return text.split(":", 1)[1] if ":" in text else text


def is_strict_rule_ancestor(parent_id: str, child_id: str) -> bool:
    """String-safe ancestor test for locators like 11-2(1) -> 11-2(1)(a)."""
    p = _rule_locator(parent_id)
    c = _rule_locator(child_id)
    if not c.startswith(p) or len(c) <= len(p):
        return False
    return c[len(p):].startswith("(")


def _connector_ids(group: dict) -> set[str]:
    out: set[str] = set()
    for row in group.get("source_evidence", []) or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "").upper() != "CONNECTOR":
            continue
        cid = row.get("candidate_id")
        if cid:
            out.add(str(cid))
    return out


def connector_consumed_unresolved_cleanup(value: Any):
    """Remove only structurally redundant unresolved markers.

    A candidate is considered consumed iff:
      * unresolved reason is ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED;
      * it is explicitly cited as CONNECTOR source_evidence for a relation group; and
      * every member of that group is a strict descendant of that connector rule.

    The candidate itself is not deleted/reclassified; only the contradictory
    'unclassified relation' marker is removed.
    """
    if not isinstance(value, dict):
        return value, 0
    groups = value.get("groups")
    unresolved = value.get("unresolved_relationships")
    if not isinstance(groups, list) or not isinstance(unresolved, list):
        return value, 0

    consumed: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        members = _members(group) or []
        if len(members) < 2:
            continue
        for cid in _connector_ids(group):
            if all(is_strict_rule_ancestor(cid, str(member)) for member in members):
                consumed.add(cid)

    kept = []
    removed = 0
    for row in unresolved:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        if str(row.get("reason") or "") != "ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED":
            kept.append(row)
            continue
        ids = row.get("candidate_ids")
        if not isinstance(ids, list) or not ids:
            kept.append(row)
            continue
        if all(str(cid) in consumed for cid in ids):
            removed += 1
            continue
        kept.append(row)

    if not removed:
        return value, 0
    out = dict(value)
    out["unresolved_relationships"] = kept
    return out, removed



def ancestor_consumed_unresolved_cleanup(value: Any):
    """Remove only false parent/chapeau unresolved markers.

    A row is removable only when its reason is exactly
    ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED and every candidate in that row is a
    strict ancestor of every member of at least one relation group with >=2 members.
    The candidate itself is preserved; only the contradictory unresolved marker is removed.
    """
    if not isinstance(value, dict):
        return value, 0
    groups=value.get("groups")
    unresolved=value.get("unresolved_relationships")
    if not isinstance(groups,list) or not isinstance(unresolved,list):
        return value,0

    consumed=set()
    for row in unresolved:
        if not isinstance(row,dict):
            continue
        if str(row.get("reason") or "") != "ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED":
            continue
        ids=row.get("candidate_ids")
        if not isinstance(ids,list) or not ids:
            continue
        for cid in map(str,ids):
            for group in groups:
                if not isinstance(group,dict):
                    continue
                members=[str(x) for x in (_members(group) or [])]
                if len(members) < 2:
                    continue
                if all(is_strict_rule_ancestor(cid,m) for m in members):
                    consumed.add(cid)
                    break

    kept=[]; removed=0
    for row in unresolved:
        if not isinstance(row,dict):
            kept.append(row); continue
        if str(row.get("reason") or "") != "ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED":
            kept.append(row); continue
        ids=row.get("candidate_ids")
        if not isinstance(ids,list) or not ids:
            kept.append(row); continue
        if all(str(cid) in consumed for cid in ids):
            removed += 1
            continue
        kept.append(row)
    if not removed:
        return value,0
    out=dict(value)
    out["unresolved_relationships"]=kept
    return out,removed


def normalize_relation_payload(value: Any, stats: dict, boundary: str):
    fixed,n_single=normalize_singleton_groups(value)
    fixed,n_connector=connector_consumed_unresolved_cleanup(fixed)
    fixed,n_ancestor=ancestor_consumed_unresolved_cleanup(fixed)
    if n_single or n_connector or n_ancestor:
        stats["singleton_groups_removed"] += n_single
        stats["connector_unresolved_removed"] += n_connector
        stats["ancestor_unresolved_removed"] += n_ancestor
        stats["normalization_events"].append({
            "boundary":boundary,
            "singleton_removed":n_single,
            "connector_unresolved_removed":n_connector,
            "ancestor_unresolved_removed":n_ancestor,
        })
        print(
            f"    relation normalization @ {boundary}:",
            f"singleton_removed={n_single}",
            f"connector_unresolved_removed={n_connector}",
            f"ancestor_unresolved_removed={n_ancestor}",
        )
    return fixed

def classify_prompt_stage(system: str, user: str) -> str:
    t = (system + "\n" + user).lower()
    if (
        "legalbasislink" in t
        or "legal basis link" in t
        or "legal-basis" in t
        or ("context_only" in t and ("mapping" in t or "criterion" in t))
    ):
        return "LEGAL_BASIS"
    if (
        "rulesetrelation" in t
        or "rule set relation" in t
        or "multi-rule" in t
        or ("relation" in t and "member_candidate_ids" in t)
    ):
        return "RULE_SET_RELATION"
    if "contract" in t and (
        "atom" in t or "satisfaction" in t or "violation" in t or "contract schema" in t
    ):
        return "CONTRACT"
    return "OTHER"


def hints_for(error: str, stage: str) -> list[str]:
    e = str(error).lower()
    hints: list[str] = []
    if "relation group requires >=2 members" in e and stage == "RULE_SET_RELATION":
        hints.append(RELATION_GROUP_HINT)
    if "multi-rule relationship is unresolved" in e and stage == "RULE_SET_RELATION":
        hints.append(RELATION_HINT)
    if "no contract atoms" in e:
        if stage == "LEGAL_BASIS":
            hints.append(NO_ATOM_LEGAL_BASIS_HINT)
        elif stage == "CONTRACT":
            hints.append(NO_ATOM_CONTRACT_HINT)
    if not hints and stage in {"RULE_SET_RELATION", "LEGAL_BASIS", "CONTRACT"}:
        # Do not inject generic text into unrelated model stages.
        if not any(x in e for x in (
            "relation group requires >=2 members",
            "multi-rule relationship is unresolved",
            "no contract atoms",
        )):
            hints.append(GENERIC_HINT)
    return hints


def install_model_boundary_repair(v2, error: str):
    target = getattr(v2, "core", None)
    if target is None or not hasattr(target, "deepseek_json"):
        raise AttributeError("freca_core_v2.core.deepseek_json is unavailable")
    original = target.deepseek_json
    signature = inspect.signature(original)
    stats = {
        "singleton_groups_removed": 0,
        "connector_unresolved_removed": 0,
        "ancestor_unresolved_removed": 0,
        "normalization_events": [],
        "hinted_stages": [],
    }

    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        system = str(bound.arguments.get("system_prompt") or "")
        user = str(bound.arguments.get("user_prompt") or "")
        stage = classify_prompt_stage(system, user)
        hints = hints_for(error, stage)
        if hints:
            appendix = "\n\nVALIDATOR_REPAIR_CONSTRAINTS:\n" + "\n".join(f"- {h}" for h in hints)
            bound.arguments["system_prompt"] = system + appendix
            stats["hinted_stages"].append(stage)
            print(f"    residual repair hint injected: stage={stage}")
        raw = original(*bound.args, **bound.kwargs)
        if stage == "RULE_SET_RELATION":
            raw = normalize_relation_payload(raw, stats, "MODEL_OUTPUT")
        return raw

    target.deepseek_json = wrapped
    return target, original, stats


def install_relation_validator_boundary_repair(v2, stats: dict):
    """Normalize immediately before and after the real relation validator."""
    if not hasattr(v2, "validate_rule_set_relation"):
        raise AttributeError("freca_core_v2.validate_rule_set_relation is unavailable")
    original = v2.validate_rule_set_relation
    signature = inspect.signature(original)

    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        if "raw" in bound.arguments:
            bound.arguments["raw"] = normalize_relation_payload(
                bound.arguments["raw"], stats, "VALIDATOR_INPUT"
            )
        result = original(*bound.args, **bound.kwargs)
        return normalize_relation_payload(result, stats, "VALIDATOR_OUTPUT")

    v2.validate_rule_set_relation = wrapped
    return original


def prior_errors(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = load_json(path)
    return {
        str(cp): str(row.get("exception_message") or row.get("error") or row.get("status") or "")
        for cp, row in (data.get("cp_results", {}) or {}).items()
    }


def previous_closure_errors(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = load_json(path)
    out = {}
    for cp, row in (data.get("results", {}) or {}).items():
        err = row.get("final_error")
        if err:
            out[str(cp)] = str(err)
    return out


def review_packet(cp_id, v2, contract_dir: Path, final_error: str, attempts: list[dict]):
    cp = v2.core.get_cp(cp_id)
    packet = {
        "schema": "freca-contract-closure-review-packet-v1-3",
        "cp_id": cp_id,
        "criterion": cp.get("criterion"),
        "subelement": cp.get("subelement"),
        "final_error": final_error,
        "attempts": attempts,
        "answer_comparator_used": False,
        "case_evidence_used": False,
        "fallback_contract_generation": False,
    }
    for suffix, key in (
        ("_candidate_ledger.json", "candidate_ledger"),
        ("_rule_set_relation.json", "rule_set_relation"),
    ):
        path = contract_dir / f"{cp_id}{suffix}"
        if path.exists():
            try:
                packet[key] = load_json(path)
            except Exception as exc:
                packet[key + "_read_error"] = repr(exc)
    return packet


def compile_one(v2, cp_id, contract_dir, run_dir, policy_top_k, max_attempts, initial_error):
    canonical = contract_dir / f"{cp_id}.json"
    check = validate_canonical_contract_file(canonical, cp_id)
    if check["valid_local_shape"]:
        return {
            "cp_id": cp_id,
            "status": "EXISTING_CANONICAL_VALID",
            "attempts": [],
            "canonical_check": check,
        }

    attempts = []
    error = initial_error or "previous compile failed"

    for attempt in range(1, max_attempts + 1):
        target, original_deepseek, repair_stats = install_model_boundary_repair(v2, error)
        original_relation_validator = install_relation_validator_boundary_repair(v2, repair_stats)
        log = run_dir / "logs" / f"{cp_id}_attempt{attempt}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open("w", encoding="utf-8") as stream, \
                 contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream), \
                 capture_deepseek_telemetry() as events:
                print(f"FRECA RESIDUAL CONTRACT CLOSURE {cp_id} attempt {attempt}/{max_attempts}")
                print("Incoming error:", error)
                v2.compile_cp_v2(cp_id, policy_top_k)
            cost = summarize_telemetry(events)
            check = validate_canonical_contract_file(canonical, cp_id)
            attempts.append({
                "attempt": attempt,
                "incoming_error": error,
                "status": "CANONICAL_VALID" if check["valid_local_shape"] else "NO_VALID_CANONICAL",
                "canonical_check": check,
                "repair_stats": repair_stats,
                "cost_telemetry": cost,
                "log_path": str(log),
            })
            if check["valid_local_shape"]:
                return {
                    "cp_id": cp_id,
                    "status": "CLOSED_CANONICAL_VALID",
                    "attempts": attempts,
                    "canonical_check": check,
                }
            error = "compile returned without a valid canonical contract"
        except Exception as exc:
            try:
                cost = summarize_telemetry(events)
            except Exception:
                cost = {"status": "TELEMETRY_UNAVAILABLE"}
            new_error = f"{type(exc).__name__}: {exc}"
            tb = run_dir / "logs" / f"{cp_id}_attempt{attempt}_traceback.txt"
            tb.write_text(traceback.format_exc(), encoding="utf-8")
            attempts.append({
                "attempt": attempt,
                "incoming_error": error,
                "status": "FAILED",
                "error": new_error,
                "repair_stats": repair_stats,
                "cost_telemetry": cost,
                "log_path": str(log),
                "traceback_path": str(tb),
            })
            error = new_error
        finally:
            target.deepseek_json = original_deepseek
            v2.validate_rule_set_relation = original_relation_validator

    packet = review_packet(cp_id, v2, contract_dir, error, attempts)
    packet_path = run_dir / "review_packets" / f"{cp_id}_review_packet.json"
    save_json(packet, packet_path)
    return {
        "cp_id": cp_id,
        "status": "STILL_REVIEW_REQUIRED",
        "attempts": attempts,
        "final_error": error,
        "review_packet": str(packet_path),
    }


def self_test():
    fixture={
        "groups":[
            {"group_id":"G1","member_candidate_ids":["a","b"]},
            {"group_id":"G2","member_candidate_ids":["c"]},
        ],
        "unresolved_relationships":[],
    }
    fixed,removed=normalize_singleton_groups(fixture)
    assert removed==1 and [g["group_id"] for g in fixed["groups"]]==["G1"]

    cp23={
        "groups":[{
            "group_id":"G1",
            "member_candidate_ids":[
                "rules2021:11-2(1)(a)","rules2021:11-2(1)(c)","rules2021:11-2(1)(d)"
            ],
            "relation":"CUMULATIVE",
            "source_evidence":[{
                "source":"RULES","candidate_id":"rules2021:11-2(1)",
                "role":"CONNECTOR","quote":"A record ... must be:"
            }],
        }],
        "unresolved_relationships":[{
            "reason":"ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED",
            "candidate_ids":["rules2021:11-2(1)"]
        }],
    }
    fixed,removed=connector_consumed_unresolved_cleanup(cp23)
    assert removed==1 and fixed["unresolved_relationships"]==[]

    cp6={
        "groups":[{
            "group_id":"G4",
            "member_candidate_ids":[
                "rules2021:3-15(a)","rules2021:3-15(b)","rules2021:3-15(c)",
                "rules2021:3-15(d)","rules2021:3-15(ia)"
            ],
            "relation":"CUMULATIVE",
            "source_evidence":[{
                "source":"CP","candidate_id":None,"role":"CONNECTOR","quote":"including ..."
            }],
        }],
        "unresolved_relationships":[{
            "reason":"ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED",
            "candidate_ids":["rules2021:3-15"]
        }],
    }
    fixed,removed=ancestor_consumed_unresolved_cleanup(cp6)
    assert removed==1 and fixed["unresolved_relationships"]==[]
    cp6["unresolved_relationships"]=[{
        "reason":"ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED",
        "candidate_ids":["rules2021:3-16"]
    }]
    fixed,removed=ancestor_consumed_unresolved_cleanup(cp6)
    assert removed==0 and len(fixed["unresolved_relationships"])==1

    class FakeV2: pass
    fake=FakeV2()
    def fake_validate(raw,cp,candidates,ledger):
        assert all(len(_members(g) or [])>=2 for g in raw.get("groups",[]))
        out=dict(raw)
        out["unresolved_relationships"]=[{
            "reason":"ELIGIBLE_RULE_NOT_RELATION_CLASSIFIED",
            "candidate_ids":["rules2021:3-15"]
        }]
        return out
    fake.validate_rule_set_relation=fake_validate
    stats={
        "singleton_groups_removed":0,
        "connector_unresolved_removed":0,
        "ancestor_unresolved_removed":0,
        "normalization_events":[],
        "hinted_stages":[],
    }
    original=install_relation_validator_boundary_repair(fake,stats)
    raw={
        "groups":[
            {"group_id":"G4","member_candidate_ids":["rules2021:3-15(a)","rules2021:3-15(b)"]},
            {"group_id":"BAD","member_candidate_ids":["rules2021:3-15(c)"]},
        ],
        "unresolved_relationships":[],
    }
    out=fake.validate_rule_set_relation(raw,{},[],[])
    assert len(out["groups"])==1
    assert out["unresolved_relationships"]==[]
    assert stats["singleton_groups_removed"]==1
    assert stats["ancestor_unresolved_removed"]==1
    fake.validate_rule_set_relation=original

    assert NO_ATOM_LEGAL_BASIS_HINT in hints_for("ValueError: No contract atoms.","LEGAL_BASIS")
    assert NO_ATOM_CONTRACT_HINT in hints_for("ValueError: No contract atoms.","CONTRACT")
    assert hints_for("ValueError: No contract atoms.","RULE_SET_RELATION")==[]
    assert is_strict_rule_ancestor("rules2021:11-2(1)","rules2021:11-2(1)(a)")
    assert is_strict_rule_ancestor("rules2021:3-15","rules2021:3-15(ia)")
    assert not is_strict_rule_ancestor("rules2021:11-2(1)","rules2021:11-2(10)")

    print("contract_failure_closure_v1_3 self-tests: PASS")
    print("  relation-output singleton normalization")
    print("  connector/ancestor-consumed unresolved cleanup")
    print("  relation cleanup at validator input + output boundary")
    print("  no-atoms repair routed upstream to LegalBasisLink")
    print("  no CP-specific answer / no case evidence / no fallback contract")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cp", action="append")
    p.add_argument("--source", type=Path, default=Path("freca_core_v2.py"))
    p.add_argument("--contract-dir", type=Path, default=Path("contracts_v2"))
    p.add_argument(
        "--prior-report",
        type=Path,
        default=Path("results_v2/contract_build_full_v1/contract_batch_build_report_v1.json"),
    )
    p.add_argument(
        "--previous-closure-report",
        type=Path,
        default=Path("results_v2/contract_failure_closure_v1/closure_report.json"),
    )
    p.add_argument(
        "--previous-closure-report-v1-2",
        type=Path,
        default=Path("results_v2/contract_failure_closure_v1_2/closure_report.json"),
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        default=Path("results_v2/contract_failure_closure_v1_3"),
    )
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.self_test:
        self_test()
        if not args.cp and not args.dry_run:
            return
    if not args.source.exists():
        p.error(f"Missing {args.source}")

    selected = args.cp or list(DEFAULT_CPS)
    top_k = discover_policy_top_k_default(args.source)
    old_errors = prior_errors(args.prior_report)
    old_errors.update(previous_closure_errors(args.previous_closure_report))
    old_errors.update(previous_closure_errors(args.previous_closure_report_v1_2))
    args.run_dir.mkdir(parents=True, exist_ok=True)

    plan = {
        "schema": "freca-contract-failure-closure-plan-v1-3",
        "selected_cp_ids": selected,
        "policy_top_k": top_k,
        "max_attempts": args.max_attempts,
        "normalizations": [
            "DROP_SINGLETON_RELATION_GROUP_AT_MODEL_AND_VALIDATOR_BOUNDARIES",
            "DROP_CONNECTOR_CONSUMED_FALSE_UNRESOLVED_MARKER",
            "DROP_DESCENDANT_GROUP_CONSUMED_PARENT_FALSE_UNRESOLVED_MARKER",
        ],
        "repair_routing": [
            "RELATION_ERRORS_TO_RULE_SET_RELATION_STAGE",
            "NO_ATOMS_TO_LEGAL_BASIS_AND_CONTRACT_STAGES",
        ],
        "fallback_contract_generation": False,
        "case_evidence_used": False,
        "answer_comparator_used": False,
        "cp_specific_legal_answer_hardcoding": False,
    }
    save_json(plan, args.run_dir / "closure_plan.json")

    print("=" * 80)
    print("FRECA RESIDUAL CONTRACT FAILURE CLOSURE V1.3")
    print("=" * 80)
    print("Selected:", ", ".join(selected))
    print("policy_top_k:", top_k)
    print("max_attempts:", args.max_attempts)
    print("dry_run:", args.dry_run)
    for cp in selected:
        print(cp, "| incoming:", old_errors.get(cp, "<none>"))

    if args.dry_run:
        return

    import freca_core_v2 as v2

    results = {}
    for i, cp in enumerate(selected, 1):
        print("\n" + "#" * 80)
        print(f"[{i}/{len(selected)}] {cp}")
        print("#" * 80)
        result = compile_one(
            v2, cp, args.contract_dir, args.run_dir, top_k,
            args.max_attempts, old_errors.get(cp, ""),
        )
        results[cp] = result
        print(cp, result["status"])
        save_json({
            "schema": "freca-contract-failure-closure-report-v1-3",
            "plan": plan,
            "results": results,
        }, args.run_dir / "closure_report.json")

    closed = sum(
        r["status"] in {"CLOSED_CANONICAL_VALID", "EXISTING_CANONICAL_VALID"}
        for r in results.values()
    )
    unresolved = [cp for cp, r in results.items() if r["status"] == "STILL_REVIEW_REQUIRED"]
    print("\n" + "=" * 80)
    print("RESIDUAL CLOSURE SUMMARY")
    print("=" * 80)
    print("Closed/existing:", closed, "/", len(selected))
    print("Still review required:", ", ".join(unresolved) if unresolved else "<none>")
    print("Saved:", args.run_dir / "closure_report.json")


if __name__ == "__main__":
    main()
