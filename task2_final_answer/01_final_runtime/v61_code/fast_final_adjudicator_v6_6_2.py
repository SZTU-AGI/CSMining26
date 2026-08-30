#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, re, hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import fast_final_adjudicator_v6_6 as v66
import production_runner_v1 as base
import freca_core_v1 as core

FINAL_VERSION = "V6.6.2_HYBRID_FORCED_NA_GATE_1"
GATE_SCHEMA = "freca-v6.6.2-forced-na-gate-v1"
ALLOWED = {"0", "1", "N/A"}

# Candidate discovery is generic and reads only official CP text + automatically
# retrieved policy.  It does not contain a CP-number map.
PREFIX_CONDITIONAL = re.compile(
    r"^\s*(?:where\s+applicable|where\s+required|if\s+applicable|if\s+required|when\s+applicable|if\b)",
    re.I,
)
POLICY_ACTIVITY_CONDITIONAL = re.compile(
    r"\b(?:where\s+applicable|where\s+required|if\s+applicable|if\s+required|"
    r"if\s+[^.;]{2,220}?\b(?:carried\s+out|performed|undertaken|conducted|used|installed|present|required))\b",
    re.I,
)
POLICY_LINK_STOP = {"prescribed", "record", "records", "goods", "phytosanitary"}
TOKEN_STOP = {
    "the","and","for","from","with","that","this","are","was","were","been","being",
    "must","shall","should","where","when","which","their","there","these","those","have",
    "has","into","export","operations","operation","establishment","plants","plant","products",
    "product","registered","applicable","required","checking","point","carried","out","conducted",
    "performed","undertaken","used","present","all","any","not","may","can","will","would",
}


def norm(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def save_json(v: Any, p: Path):
    v66.save_json(v, p)


def sha(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _meaningful_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if t not in TOKEN_STOP}


def _policy_conditional_overlap(cp_text: str, heading: str, policy_rows: list[dict]) -> tuple[bool, list[str]]:
    # For policy-derived candidates, require the *antecedent itself* to share
    # a distinctive term with the CP text or its official subgroup heading.
    # This avoids selecting unrelated CPs merely because a long retrieved
    # Rules passage happens to contain some other conditional provision.
    query_tokens = _meaningful_tokens(cp_text + " " + heading)
    for row in policy_rows:
        txt = row["text"]
        for m in POLICY_ACTIVITY_CONDITIONAL.finditer(txt):
            antecedent = m.group(0)
            overlap = sorted(query_tokens & _meaningful_tokens(antecedent))
            distinctive = [x for x in overlap if x not in POLICY_LINK_STOP]
            if distinctive:
                return True, distinctive
    return False, []


def conditional_candidates(cp_texts: dict[str, str], headings: dict[str, str], policy_docs: list[dict]) -> dict[str, dict]:
    out = {}
    for cp in [f"CP{i}" for i in range(1, 42)]:
        text = cp_texts[cp]
        heading = headings.get(cp, "")
        pol = v66.retrieve_policy(text + " " + heading, policy_docs, 4)
        cp_prefix = bool(PREFIX_CONDITIONAL.search(text))
        policy_conditional, overlap = _policy_conditional_overlap(text, heading, pol)
        if cp_prefix or policy_conditional:
            out[cp] = {
                "official_text": text,
                "heading": heading,
                "policy": pol,
                "candidate_reason": "OFFICIAL_PREFIX_CONDITIONAL" if cp_prefix else "AUTO_RETRIEVED_POLICY_CONDITIONAL",
                "policy_overlap_terms": overlap,
            }
    return out

def build_gate_context(cp_texts: dict[str, str], headings: dict[str, str], policy_docs: list[dict], chunks: list[dict]):
    candidates = conditional_candidates(cp_texts, headings, policy_docs)
    evidence_index = {}
    for cp, meta in candidates.items():
        policy_blob = " ".join(x["text"] for x in meta["policy"])
        query = f"{headings.get(cp, '')} {meta['official_text']} {policy_blob[:5000]}"
        ev = v66.retrieve_evidence(query, chunks, 7)
        meta["evidence"] = ev
        for x in ev:
            evidence_index[x["id"]] = x["text"]
    return candidates, evidence_index


def gate_prompt(candidates: dict[str, dict]):
    blocks = []
    for cp, meta in candidates.items():
        policy = "\n".join(f"[{x['id']}] {x['text']}" for x in meta["policy"])
        evidence = "\n".join(f"[{x['id']}] {x['text']}" for x in meta["evidence"])
        blocks.append(
            f"{cp}\nOFFICIAL CHECKING POINT: {meta['official_text']}\n"
            f"AUTO-RETRIEVED POLICY:\n{policy}\nGROUNDED CASE EVIDENCE:\n{evidence}"
        )
    system = """You are a dedicated applicability auditor. This is NOT a compliance grading task.
For each supplied checking point, first decide whether any conditional language controls the WHOLE checking point rather than only an optional sub-feature.
Then, only for a WHOLE-CP conditional, determine whether the checking point applies to this farm case.

Allowed scope values:
- WHOLE_CP_CONDITIONAL: the condition controls whether the whole checking point applies.
- NOT_WHOLE_CP_CONDITIONAL: there is no whole-CP applicability condition, or the conditional wording modifies only a sub-clause/optional feature.

Allowed applicability decisions when scope is WHOLE_CP_CONDITIONAL:
- APPLICABLE: positive case evidence establishes the triggering situation/activity/entity.
- NOT_APPLICABLE: positive case evidence establishes that the triggering situation did not occur, is absent, is outside scope, or is explicitly not required/applicable.
- CONFLICTING: positive evidence exists on both sides.
- UNKNOWN: neither side is positively established.

Strict rules:
- Missing evidence is never NOT_APPLICABLE.
- Do not judge whether the farm complies with the CP.
- A procedure saying what should happen is not proof that the trigger actually occurred.
- Do not use filenames as facts.
- Every evidence claim must cite an evidence_id from GROUNDED CASE EVIDENCE and an exact_quote that is a literal substring of that evidence.
- Be willing to return NOT_APPLICABLE when explicit evidence establishes that the event/change/activity did not occur or was not required. Do not default to APPLICABLE merely because a CP exists.
Return JSON only."""
    user = f"""Evaluate applicability for these automatically selected checking points:\n\n{chr(10).join(blocks)}\n\nReturn exactly:\n{{\n  \"applicability\": {{\n    \"CPx\": {{\n      \"scope\": \"WHOLE_CP_CONDITIONAL|NOT_WHOLE_CP_CONDITIONAL\",\n      \"decision\": \"APPLICABLE|NOT_APPLICABLE|CONFLICTING|UNKNOWN\",\n      \"applicability_evidence\": [{{\"evidence_id\":\"...\",\"exact_quote\":\"...\"}}],\n      \"non_applicability_evidence\": [{{\"evidence_id\":\"...\",\"exact_quote\":\"...\"}}],\n      \"reason\": \"brief\"\n    }}\n  }}\n}}"""
    return system, user


def validate_items(items: Any, evidence_index: dict[str, str]) -> list[dict]:
    out = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("evidence_id") or "")
        quote = norm(item.get("exact_quote"))
        text = evidence_index.get(eid)
        if not eid or not quote or text is None or quote not in text:
            continue
        if eid not in [x["evidence_id"] for x in out]:
            out.append({"evidence_id": eid, "exact_quote": quote})
    return out


def validate_gate(raw: dict, candidates: dict[str, dict], evidence_index: dict[str, str]):
    rows = raw.get("applicability") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        rows = {}
    out = {}
    for cp in candidates:
        r = rows.get(cp) if isinstance(rows.get(cp), dict) else {}
        scope = str(r.get("scope") or "NOT_WHOLE_CP_CONDITIONAL").strip().upper()
        if scope not in {"WHOLE_CP_CONDITIONAL", "NOT_WHOLE_CP_CONDITIONAL"}:
            scope = "NOT_WHOLE_CP_CONDITIONAL"
        decision = str(r.get("decision") or "UNKNOWN").strip().upper()
        if decision not in {"APPLICABLE", "NOT_APPLICABLE", "CONFLICTING", "UNKNOWN"}:
            decision = "UNKNOWN"
        app = validate_items(r.get("applicability_evidence"), evidence_index)
        nonapp = validate_items(r.get("non_applicability_evidence"), evidence_index)
        if scope != "WHOLE_CP_CONDITIONAL":
            decision = "UNKNOWN"
            app, nonapp = [], []
        else:
            if decision == "APPLICABLE" and not app:
                decision = "UNKNOWN"
            if decision == "NOT_APPLICABLE" and not nonapp:
                decision = "UNKNOWN"
            if decision == "CONFLICTING" and not (app and nonapp):
                decision = "UNKNOWN"
            if app and nonapp:
                decision = "CONFLICTING"
        out[cp] = {
            "scope": scope,
            "decision": decision,
            "applicability_evidence": app,
            "non_applicability_evidence": nonapp,
            "reason": norm(r.get("reason"))[:1200],
            "candidate_reason": candidates[cp]["candidate_reason"],
        }
    return out


def countercheck_prompt(proposed: dict[str, dict], candidates: dict[str, dict]):
    blocks = []
    for cp in proposed:
        meta = candidates[cp]
        evidence = "\n".join(f"[{x['id']}] {x['text']}" for x in meta["evidence"])
        nonapp = "\n".join(
            f"[{x['evidence_id']}] {x['exact_quote']}" for x in proposed[cp]["non_applicability_evidence"]
        )
        blocks.append(
            f"{cp}\nOFFICIAL CHECKING POINT: {meta['official_text']}\n"
            f"PRIMARY NON-APPLICABILITY EVIDENCE:\n{nonapp}\n"
            f"ALL RETRIEVED CP EVIDENCE:\n{evidence}"
        )
    system = """You are an independent countercheck for proposed N/A decisions.
A first applicability evaluator found positive evidence that each listed whole checking point is NOT_APPLICABLE.
Your only job is to look for decisive contrary evidence that the triggering event/activity/entity actually occurred, exists, or applies.
- FOUND_APPLICABLE requires a grounded exact quote.
- NOT_FOUND means no supplied evidence positively establishes applicability.
- UNKNOWN means the supplied evidence is ambiguous.
Do not judge ordinary compliance and do not reject N/A merely because the checking point exists in the checklist.
Return JSON only."""
    user = f"""Countercheck these proposed N/A decisions:\n\n{chr(10).join(blocks)}\n\nReturn:\n{{\n  \"countercheck\": {{\n    \"CPx\": {{\n      \"status\": \"FOUND_APPLICABLE|NOT_FOUND|UNKNOWN\",\n      \"evidence\": [{{\"evidence_id\":\"...\",\"exact_quote\":\"...\"}}],\n      \"reason\": \"brief\"\n    }}\n  }}\n}}"""
    return system, user


def run_forced_na_gate(case_uid: str, chunks: list[dict], cp_texts, headings, policy_docs, cdir: Path, model: str):
    path = cdir / "na_gate_v6_6_2.json"
    candidates, evidence_index = build_gate_context(cp_texts, headings, policy_docs, chunks)
    fp = sha({
        "schema": GATE_SCHEMA,
        "case": case_uid,
        "model": model,
        "candidates": {
            cp: {
                "text": m["official_text"],
                "policy": [(x["id"], x["text"]) for x in m["policy"]],
                "evidence": [(x["id"], x["text"]) for x in m["evidence"]],
            } for cp, m in candidates.items()
        },
    })
    if path.exists():
        try:
            old = v66.load_json(path)
            if old.get("schema") == GATE_SCHEMA and old.get("input_fingerprint") == fp and old.get("status") == "COMPLETE":
                return old, True
        except Exception:
            pass
    if not candidates:
        result = {"schema": GATE_SCHEMA, "status": "COMPLETE", "input_fingerprint": fp,
                  "case_uid": case_uid, "model": model, "candidates": {}, "confirmed_na": [], "model_calls": 0}
        save_json(result, path)
        return result, False

    sys_p, user_p = gate_prompt(candidates)
    raw = core.deepseek_json(model=model, system_prompt=sys_p, user_prompt=user_p, thinking=False, max_tokens=4200)
    validated = validate_gate(raw, candidates, evidence_index)
    proposed = {cp: r for cp, r in validated.items() if r["scope"] == "WHOLE_CP_CONDITIONAL" and r["decision"] == "NOT_APPLICABLE"}
    confirmed = []
    counter_raw = None
    counter = {}
    calls = 1
    if proposed:
        cs, cu = countercheck_prompt(proposed, candidates)
        counter_raw = core.deepseek_json(model=model, system_prompt=cs, user_prompt=cu, thinking=False, max_tokens=2200)
        calls += 1
        rr = counter_raw.get("countercheck") if isinstance(counter_raw, dict) else {}
        if not isinstance(rr, dict):
            rr = {}
        for cp, p in proposed.items():
            r = rr.get(cp) if isinstance(rr.get(cp), dict) else {}
            status = str(r.get("status") or "UNKNOWN").strip().upper()
            ev = validate_items(r.get("evidence"), evidence_index)
            if status == "FOUND_APPLICABLE" and not ev:
                status = "UNKNOWN"
            if status not in {"FOUND_APPLICABLE", "NOT_FOUND", "UNKNOWN"}:
                status = "UNKNOWN"
            counter[cp] = {"status": status, "evidence": ev, "reason": norm(r.get("reason"))[:1200]}
            if status == "NOT_FOUND":
                confirmed.append(cp)
    result = {
        "schema": GATE_SCHEMA,
        "status": "COMPLETE",
        "input_fingerprint": fp,
        "case_uid": case_uid,
        "model": model,
        "candidate_cps": list(candidates),
        "applicability": validated,
        "countercheck": counter,
        "confirmed_na": confirmed,
        "model_calls": calls,
        "raw_applicability": raw,
        "raw_countercheck": counter_raw,
        "prompt_log": {"applicability_system": sys_p, "applicability_user": user_p},
    }
    save_json(result, path)
    return result, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, default=Path("/home/MeggieYu/freca/core_v1/results_v2/logical_case_manifest_v1.json"))
    ap.add_argument("--cp-workbook", type=Path, default=Path("/home/MeggieYu/freca/Task2/checkingpoints_all_elements_onesheet.xlsx"))
    ap.add_argument("--policy-pdf", type=Path, default=Path("/home/MeggieYu/freca/Task2/1-Export Control (Plants and Plant Products)Rules 2021.pdf"))
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default=os.environ.get("FRECA_ALIGNMENT_MODEL", "deepseek-v4-flash"))
    args = ap.parse_args()

    manifest = v66.load_json(args.manifest)
    base.validate_manifest(manifest)
    cmap = base.manifest_case_map(manifest)
    selected = [f"case-{i:03d}" for i in range(1, 101)] if args.all else [base.normalize_case_selector(x) for x in args.case]
    if not selected:
        ap.error("Use --all or --case")
    selected = sorted(set(selected), key=lambda x: int(x.split("-")[1]))
    for c in selected:
        if c not in cmap:
            raise RuntimeError(f"Manifest missing {c}")

    cp_texts, headings = v66.load_cp_sheet(args.cp_workbook)
    policy_docs = v66.load_policy_passages(args.policy_pdf, args.run_root / "_cache" / "policy_passages.json")
    runtime = base.runtime_hashes()
    parser_hash = runtime.get("freca_core_v1.py") or "unknown"
    case_root = Path(manifest["dataset_structure_profile"]["case_root"])
    args.run_root.mkdir(parents=True, exist_ok=True)
    cand = conditional_candidates(cp_texts, headings, policy_docs)
    print(f"V6.6.2 hybrid forced-N/A gate: cases={len(selected)} model={args.model}")
    print("Automatically selected applicability candidates:", list(cand))
    print("Primary element outputs from V6.6 are cache-reused when unchanged.")

    for i, uid in enumerate(selected, 1):
        case = cmap[uid]
        cdir = args.run_root / "cases" / uid
        final_path = cdir / "final_case.json"
        if final_path.exists():
            old = v66.load_json(final_path)
            if old.get("version") == FINAL_VERSION and len(old.get("verdicts") or {}) == 41:
                print(f"[{i}/{len(selected)}] {uid}: COMPLETE cache")
                continue

        staged = base.stage_case(case=case, case_root=case_root, run_dir=args.run_root)
        chunks, cache_hit = base.parse_case_cached(case=case, stage_dir=staged, run_dir=args.run_root, parser_hash=parser_hash)
        chunks = v66.normalize_chunks(chunks)
        print(f"[{i}/{len(selected)}] {uid}: chunks={len(chunks)} {'cache' if cache_hit else 'parsed'}")

        verdicts = {}
        element_meta = {}
        for element in v66.ELEMENTS:
            r, hit = v66.run_element(uid, element, chunks, cp_texts, headings, policy_docs, cdir, args.model)
            print(f"  {element}: {'cache' if hit else 'model'} labels={dict(Counter(x['verdict'] for x in r['verdicts'].values()))}")
            verdicts.update(r["verdicts"])
            element_meta[element] = {"path": str(cdir / "elements" / f"{element}.json"), "cache": hit}

        gate, gate_hit = run_forced_na_gate(uid, chunks, cp_texts, headings, policy_docs, cdir, args.model)
        confirmed = set(gate.get("confirmed_na") or [])

        # The main 3-way LLM is not trusted to emit N/A.  Every N/A must be
        # independently confirmed by the forced applicability gate.
        for cp, row in verdicts.items():
            if row.get("verdict") == "N/A" and cp not in confirmed:
                row["verdict"] = "0"
                row["na_primary_rejected"] = True
                row["na_primary_rejected_reason"] = "N/A not confirmed by V6.6.2 forced applicability gate"
        for cp in confirmed:
            g = (gate.get("applicability") or {}).get(cp) or {}
            row = verdicts[cp]
            row["verdict"] = "N/A"
            row["na_verified"] = True
            row["na_gate_version"] = FINAL_VERSION
            row["na_gate_reason"] = g.get("reason")
            row["na_gate_non_applicability_evidence"] = g.get("non_applicability_evidence") or []
            row["reason"] = g.get("reason") or row.get("reason")
            row["evidence_ids"] = [x.get("evidence_id") for x in g.get("non_applicability_evidence") or [] if x.get("evidence_id")]

        if set(verdicts) != {f"CP{x}" for x in range(1, 42)}:
            raise RuntimeError(f"{uid}: incomplete CP output {len(verdicts)}")
        bad = {cp: r.get("verdict") for cp, r in verdicts.items() if r.get("verdict") not in ALLOWED}
        if bad:
            raise RuntimeError(f"{uid}: bad verdicts {bad}")

        final = {
            "schema": "freca-v6.6.2-hybrid-final-case-v1",
            "version": FINAL_VERSION,
            "primary_version": v66.VERSION,
            "case_uid": uid,
            "model": args.model,
            "verdicts": verdicts,
            "element_outputs": element_meta,
            "na_gate": {"path": str(cdir / "na_gate_v6_6_2.json"), "cache": gate_hit,
                        "candidate_cps": gate.get("candidate_cps") or [], "confirmed_na": list(confirmed),
                        "model_calls": gate.get("model_calls", 0)},
            "label_counts": dict(Counter(x["verdict"] for x in verdicts.values())),
        }
        save_json(final, final_path)
        print("  N/A gate:", "cache" if gate_hit else "model", "confirmed=", sorted(confirmed), "calls=", gate.get("model_calls", 0))
        print("  FINAL", final["label_counts"])
    print("DONE", len(selected), "cases")


if __name__ == "__main__":
    main()
