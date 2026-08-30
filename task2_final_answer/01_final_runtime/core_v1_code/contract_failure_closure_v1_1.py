#!/usr/bin/env python3
"""FRECA contract failure closure compiler v1.

Closes observed missing contracts without CP-specific legal hardcoding.
No case evidence, labels, or answer comparator. No fallback contract creation.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
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

DEFAULT_CPS = ["CP5", "CP6", "CP20", "CP23", "CP25", "CP34", "CP36", "CP39", "CP40"]

HINTS = {
    "No contract atoms": (
        "CONTRACT_ATOM_REPAIR: Return at least one grounded atom. Every atom "
        "must quote exact CP criterion text and use only supplied contract-eligible "
        "PRIMARY_NORM candidates. Split into multiple atoms only when the supplied "
        "CP/Rules explicitly establish cumulative benchmark dimensions."
    ),
    "ungrounded Rules source evidence": (
        "SOURCE_GROUNDING_REPAIR: Every Rules quote/evidence anchor must be copied "
        "exactly from the supplied text for the same candidate_id. Never paraphrase "
        "a Rules quote and never change candidate_id just to obtain a matching quote."
    ),
    "multi-rule relationship is unresolved": (
        "RELATION_REPAIR: Re-evaluate the relation using only supplied source text "
        "and explicit connectors/scope. SUPPORTS_SAME_CRITERION is allowed only when "
        "separate grounded primary norms independently support the same CP proposition "
        "and no grounded cumulative/alternative connector exists. If genuinely "
        "unresolved, preserve unresolved rather than guess."
    ),
    "relation group requires >=2 members": (
        "RELATION_GROUP_REPAIR: Do not emit singleton relation groups. A candidate may "
        "remain independently selected without being placed in a one-member group."
    ),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def hint_for(error: str) -> str:
    for needle, hint in HINTS.items():
        if needle.lower() in str(error).lower():
            return hint
    return (
        "VALIDATOR_REPAIR: Correct the previous validator failure using only the "
        "supplied CP/Rules artifacts. Preserve grounding and do not invent law."
    )


def _members(group: dict):
    for key in (
        "member_candidate_ids", "member_ids", "candidate_ids",
        "norm_event_ids", "event_ids", "members",
    ):
        value = group.get(key)
        if isinstance(value, list):
            return value
    return None


def normalize_singleton_groups(value: Any):
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


def _collect_candidate_texts(value: Any, out: dict[str, str]):
    if isinstance(value, list):
        for x in value:
            _collect_candidate_texts(x, out)
        return
    if not isinstance(value, dict):
        return
    cid = value.get("candidate_id") or value.get("id") or value.get("chunk_id")
    text = value.get("own_text") or value.get("policy_quote") or value.get("text")
    if cid and text:
        out.setdefault(str(cid), str(text).strip())
    for child in value.values():
        _collect_candidate_texts(child, out)


def canonicalize_rules_quotes(value: Any, candidate_texts: dict[str, str]):
    changed = 0
    def rec(obj):
        nonlocal changed
        if isinstance(obj, list):
            return [rec(x) for x in obj]
        if not isinstance(obj, dict):
            return obj
        out = {k: rec(v) for k, v in obj.items()}
        source = str(out.get("source") or out.get("source_type") or "").upper()
        if source != "RULES":
            return out
        cid = out.get("candidate_id") or out.get("chunk_id") or out.get("source_candidate_id")
        if not cid or str(cid) not in candidate_texts:
            return out
        quote_key = next((k for k in ("quote", "exact_quote", "source_quote", "policy_quote") if k in out), None)
        if quote_key is None:
            return out
        canonical = candidate_texts[str(cid)]
        current = str(out.get(quote_key) or "").strip()
        if current and current in canonical:
            return out
        out[quote_key] = canonical
        changed += 1
        return out
    return rec(value), changed


def source_has(fn, needle: str) -> bool:
    try:
        return needle in inspect.getsource(fn)
    except Exception:
        return False


def install_validator_wrappers(v2):
    installed = []
    modules = [v2]
    if getattr(v2, "core", None) is not None:
        modules.append(v2.core)

    for mod in modules:
        for name, fn in list(vars(mod).items()):
            if not inspect.isfunction(fn):
                continue
            singleton = source_has(fn, "relation group requires >=2 members")
            grounding = source_has(fn, "ungrounded Rules source evidence")
            if not singleton and not grounding:
                continue
            original = fn
            signature = inspect.signature(original)

            def factory(original=original, signature=signature, singleton=singleton, grounding=grounding, label=f"{mod.__name__}.{name}"):
                def wrapped(*args, **kwargs):
                    bound = signature.bind_partial(*args, **kwargs)
                    texts = {}
                    for value in bound.arguments.values():
                        _collect_candidate_texts(value, texts)
                    removed = 0
                    changed = 0
                    for key, value in list(bound.arguments.items()):
                        new_value = value
                        if singleton:
                            new_value, n = normalize_singleton_groups(new_value)
                            removed += n
                        if grounding and texts:
                            new_value, n = canonicalize_rules_quotes(new_value, texts)
                            changed += n
                        bound.arguments[key] = new_value
                    if removed or changed:
                        print(
                            "    validator normalization:", label,
                            f"singleton_removed={removed}",
                            f"rules_quotes_canonicalized={changed}",
                        )
                    return original(*bound.args, **bound.kwargs)
                wrapped.__name__ = original.__name__
                wrapped.__doc__ = original.__doc__
                return wrapped

            setattr(mod, name, factory())
            installed.append({
                "module": mod.__name__,
                "function": name,
                "singleton_group_normalizer": singleton,
                "rules_quote_canonicalizer": grounding,
            })
    return installed


def stage_relevant(prompt: str, hint: str) -> bool:
    p = prompt.lower()
    tag = hint.split(":", 1)[0]
    if tag == "CONTRACT_ATOM_REPAIR":
        return "contract" in p and "atom" in p
    if tag == "SOURCE_GROUNDING_REPAIR":
        return "rules" in p and ("quote" in p or "source" in p or "contract" in p)
    if tag in {"RELATION_REPAIR", "RELATION_GROUP_REPAIR"}:
        return "relation" in p or "multi-rule" in p
    return True


def install_prompt_hint(v2, hint: str):
    target = getattr(v2, "core", None)
    if target is None or not hasattr(target, "deepseek_json"):
        raise AttributeError("freca_core_v2.core.deepseek_json is unavailable")
    original = target.deepseek_json
    signature = inspect.signature(original)
    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        system = str(bound.arguments.get("system_prompt") or "")
        user = str(bound.arguments.get("user_prompt") or "")
        if stage_relevant(system + "\n" + user, hint):
            bound.arguments["system_prompt"] = system + "\n\nVALIDATOR_REPAIR_CONSTRAINT:\n" + hint
        return original(*bound.args, **bound.kwargs)
    target.deepseek_json = wrapped
    return target, original


def prior_errors(path: Path):
    if not path.exists():
        return {}
    data = load_json(path)
    return {
        str(cp): str(row.get("exception_message") or row.get("error") or row.get("status") or "")
        for cp, row in (data.get("cp_results", {}) or {}).items()
    }


def review_packet(cp_id, v2, contract_dir: Path, final_error: str, attempts: list[dict]):
    cp = v2.core.get_cp(cp_id)
    packet = {
        "schema": "freca-contract-closure-review-packet-v1",
        "cp_id": cp_id,
        "criterion": cp.get("criterion"),
        "subelement": cp.get("subelement"),
        "final_error": final_error,
        "attempts": attempts,
        "answer_comparator_used": False,
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
        return {"cp_id": cp_id, "status": "EXISTING_CANONICAL_VALID", "attempts": [], "canonical_check": check}

    attempts = []
    error = initial_error or "previous compile failed"

    for attempt in range(1, max_attempts + 1):
        hint = hint_for(error)
        deepseek_target, original_deepseek = install_prompt_hint(v2, hint)
        log = run_dir / "logs" / f"{cp_id}_attempt{attempt}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open("w", encoding="utf-8") as stream, \
                 contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream), \
                 capture_deepseek_telemetry() as events:
                print(f"FRECA CONTRACT CLOSURE {cp_id} attempt {attempt}/{max_attempts}")
                print("Repair hint:", hint)
                v2.compile_cp_v2(cp_id, policy_top_k)
            cost = summarize_telemetry(events)
            check = validate_canonical_contract_file(canonical, cp_id)
            attempts.append({
                "attempt": attempt,
                "repair_hint": hint,
                "status": "CANONICAL_VALID" if check["valid_local_shape"] else "NO_VALID_CANONICAL",
                "canonical_check": check,
                "cost_telemetry": cost,
                "log_path": str(log),
            })
            if check["valid_local_shape"]:
                return {"cp_id": cp_id, "status": "CLOSED_CANONICAL_VALID", "attempts": attempts, "canonical_check": check}
            error = "compile returned without a valid canonical contract"
        except Exception as exc:
            try:
                cost = summarize_telemetry(events)
            except Exception:
                cost = {"status": "TELEMETRY_UNAVAILABLE"}
            error = f"{type(exc).__name__}: {exc}"
            tb = run_dir / "logs" / f"{cp_id}_attempt{attempt}_traceback.txt"
            tb.write_text(traceback.format_exc(), encoding="utf-8")
            attempts.append({
                "attempt": attempt,
                "repair_hint": hint,
                "status": "FAILED",
                "error": error,
                "cost_telemetry": cost,
                "log_path": str(log),
                "traceback_path": str(tb),
            })
        finally:
            deepseek_target.deepseek_json = original_deepseek

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
    fixture = {
        "groups": [
            {"group_id": "G1", "member_candidate_ids": ["a", "b"]},
            {"group_id": "G2", "member_candidate_ids": ["c"]},
        ]
    }
    fixed, removed = normalize_singleton_groups(fixture)
    assert removed == 1
    assert [g["group_id"] for g in fixed["groups"]] == ["G1"]

    payload = {
        "candidates": [{"id": "rules2021:1", "own_text": "Exact official sentence."}],
        "source": {"source": "RULES", "candidate_id": "rules2021:1", "quote": "paraphrase"},
    }
    texts = {}
    _collect_candidate_texts(payload, texts)
    fixed, changed = canonicalize_rules_quotes(payload, texts)
    assert changed == 1
    assert fixed["source"]["candidate_id"] == "rules2021:1"
    assert fixed["source"]["quote"] == "Exact official sentence."
    assert "at least one" in hint_for("No contract atoms")
    print("contract_failure_closure_v1_1 self-tests: PASS")
    print("  singleton relation-group normalization")
    print("  same-candidate Rules quote canonicalization")
    print("  bounded error-conditioned repair")
    print("  no fallback contract / no case evidence / no answer comparator")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cp", action="append")
    p.add_argument("--source", type=Path, default=Path("freca_core_v2.py"))
    p.add_argument("--contract-dir", type=Path, default=Path("contracts_v2"))
    p.add_argument("--prior-report", type=Path, default=Path("results_v2/contract_build_full_v1/contract_batch_build_report_v1.json"))
    p.add_argument("--run-dir", type=Path, default=Path("results_v2/contract_failure_closure_v1"))
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
    args.run_dir.mkdir(parents=True, exist_ok=True)

    plan = {
        "schema": "freca-contract-failure-closure-plan-v1",
        "selected_cp_ids": selected,
        "policy_top_k": top_k,
        "max_attempts": args.max_attempts,
        "normalizations": [
            "DROP_SINGLETON_RELATION_GROUP_ONLY",
            "CANONICALIZE_RULES_QUOTE_WITH_SAME_CANDIDATE_ID_ONLY",
        ],
        "bounded_prompt_repair": True,
        "fallback_contract_generation": False,
        "case_evidence_used": False,
        "answer_comparator_used": False,
    }
    save_json(plan, args.run_dir / "closure_plan.json")

    print("=" * 80)
    print("FRECA CONTRACT FAILURE CLOSURE V1")
    print("=" * 80)
    print("Selected:", ", ".join(selected))
    print("policy_top_k:", top_k)
    print("max_attempts:", args.max_attempts)
    print("dry_run:", args.dry_run)

    if args.dry_run:
        for cp in selected:
            err = old_errors.get(cp, "")
            print(cp, "| previous:", err, "| hint:", hint_for(err))
        return

    import freca_core_v2 as v2
    wrappers = install_validator_wrappers(v2)
    print("Validator wrappers:")
    for row in wrappers:
        print(" ", row)

    results = {}
    for i, cp in enumerate(selected, 1):
        print("\n" + "#" * 80)
        print(f"[{i}/{len(selected)}] {cp}")
        print("#" * 80)
        result = compile_one(
            v2, cp, args.contract_dir, args.run_dir, top_k,
            args.max_attempts, old_errors.get(cp, "")
        )
        results[cp] = result
        print(cp, result["status"])
        save_json({
            "schema": "freca-contract-failure-closure-report-v1",
            "plan": plan,
            "validator_wrappers": wrappers,
            "results": results,
        }, args.run_dir / "closure_report.json")

    closed = sum(r["status"] in {"CLOSED_CANONICAL_VALID", "EXISTING_CANONICAL_VALID"} for r in results.values())
    unresolved = [cp for cp, r in results.items() if r["status"] == "STILL_REVIEW_REQUIRED"]
    print("\n" + "=" * 80)
    print("CLOSURE SUMMARY")
    print("=" * 80)
    print("Closed/existing:", closed, "/", len(selected))
    print("Still review required:", ", ".join(unresolved) if unresolved else "<none>")
    print("Saved:", args.run_dir / "closure_report.json")


if __name__ == "__main__":
    main()
