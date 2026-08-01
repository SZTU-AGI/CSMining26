"""Scoreboard: aggregate experiment artifacts into metrics + an HTML dashboard.

Walks ``build/experiments/<method>/case-NNN/track3-<cond>/**/result.json``,
merges every unit's verdicts into one logical result per (method, case,
track3-condition), computes :class:`RunMetrics` for each, and writes both
``scoreboard.json`` (machine-readable) and ``scoreboard.html`` (a self-contained
human dashboard).

Accuracy columns require a silver reference (anomaly report / human labels).
When none is supplied those columns stay empty but the dashboard still compares
methods on the behaviour metrics that need no ground truth: valid rate,
citation validity, N/A rate, verdict distribution, and token cost — which is
enough to surface things like blanket-approve bias early.

Usage::

    python scripts/scoreboard.py
    python scripts/scoreboard.py --root build/experiments --anomaly-report build/parsed/anomaly_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(WORKTREE_SRC))

from freca.experiments.metrics import compute_run_metrics
from freca.experiments.models import (
    ExecutionResult,
    RunCostMetric,
    RunMetrics,
    Track3Condition,
)
from freca.experiments.silver import build_silver_reference
from freca.models import CheckpointDefinition
from freca.state import atomic_write_json, read_json


def _load_checkpoints(path: Path) -> list[CheckpointDefinition]:
    """Load CP definitions from the parsed checkpoints.json (preferred) or xlsx."""
    parsed = path / "checkpoints.json"
    if parsed.exists():
        raw = read_json(parsed)
        return [CheckpointDefinition.model_validate(item) for item in raw]
    raise FileNotFoundError(
        f"no checkpoints.json under {path}; run the ingest/pipeline step first"
    )


def _merge_results(results: list[ExecutionResult]) -> ExecutionResult:
    """Concatenate verdicts from many unit results into one logical result.

    compute_run_metrics iterates ``result.verdicts`` and reads
    ``result.unit.case_id``; the merged unit/sha are taken from the first result
    (they are only used for tagging, not for per-CP validation).
    """
    first = results[0]
    verdicts = [v for r in results if r.valid for v in r.verdicts]
    return first.model_copy(
        update={
            "verdicts": tuple(verdicts),
            "valid": all(r.valid for r in results),
        }
    )


def _merge_cost(dirs: list[Path]) -> RunCostMetric | None:
    """Sum usage.json (calls, tokens) across a run's unit directories."""
    calls = input_tokens = output_tokens = 0
    found = False
    for d in dirs:
        usage = d / "usage.json"
        if not usage.exists():
            continue
        payload = read_json(usage)
        calls += int(payload.get("calls", 0) or 0)
        input_tokens += int(payload.get("input_tokens", 0) or 0)
        output_tokens += int(payload.get("output_tokens", 0) or 0)
        found = True
    if not found:
        return None
    return RunCostMetric(
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        elapsed_seconds=0.0,
    )


def _unit_dirs_with_results(case_dir: Path) -> list[Path]:
    """Final result dirs under a case/track3 tree (excludes intermediate artifacts).

    agent_audit reuses stage_audit internally (writes ``cp-NNN/stage_audit/result.json``)
    and verify_audit writes a ``base/result.json``; both are intermediate, not the final
    per-CP verdict. Only the leaf ``cp-*``/``unit-*``/``verify-cp-*`` result is final.
    Empty scaffolds (valid=False, verdicts=[]) are also dropped.
    """
    import json as _json
    finals: list[Path] = []
    for p in case_dir.rglob("result.json"):
        parent_name = p.parent.name
        if parent_name == "base" or parent_name.startswith("stage"):
            continue
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data.get("valid") or not data.get("verdicts"):
            continue
        finals.append(p.parent)
    return sorted(set(finals))


def collect_rows(
    *,
    root: Path,
    checkpoints: list[CheckpointDefinition],
    silver,
) -> list[dict]:
    rows: list[dict] = []
    for method_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for case_dir in sorted(p for p in method_dir.iterdir() if p.is_dir()):
            for track3_dir in sorted(p for p in case_dir.iterdir() if p.is_dir()):
                unit_dirs = _unit_dirs_with_results(track3_dir)
                if not unit_dirs:
                    continue
                results = [
                    ExecutionResult.model_validate(read_json(d / "result.json"))
                    for d in unit_dirs
                ]
                merged = _merge_results(results)
                track3 = (
                    Track3Condition.MASKED
                    if "masked" in track3_dir.name
                    else Track3Condition.RAW
                )
                metrics = compute_run_metrics(
                    result=merged,
                    checkpoints=checkpoints,
                    silver=silver,
                    cost=_merge_cost(unit_dirs),
                    track3_condition=track3,
                )
                verdict_counts = Counter(v.verdict.value for v in merged.verdicts)
                rows.append(
                    _metrics_to_row(metrics, unit_count=len(results), verdict_counts=verdict_counts)
                )
    return rows


def _metrics_to_row(metrics: RunMetrics, *, unit_count: int, verdict_counts: Counter) -> dict:
    na = metrics.na_classification
    cite = metrics.citations
    cost = metrics.cost
    # Verdict distribution is derived from the candidate's own verdicts, so it
    # needs no silver — it is the clearest blanket-approve / blanket-deny signal.
    compliant = verdict_counts.get("1", 0)
    non_compliant = verdict_counts.get("0", 0)
    not_applicable = verdict_counts.get("N/A", 0)
    return {
        "method": str(metrics.method),
        "case_id": metrics.case_id,
        "track3": metrics.track3_condition,
        "units": unit_count,
        "verdicts_total": metrics.verdicts_total,
        "valid_rate": round(metrics.valid_rate, 4),
        "overall_accuracy": round(metrics.overall_accuracy, 4),
        "anchored_total": metrics.anchored_total,
        "anchored_correct": metrics.anchored_correct,
        "compliant": compliant,
        "non_compliant": non_compliant,
        "not_applicable": not_applicable,
        "na_rate": round(not_applicable / metrics.verdicts_total, 4) if metrics.verdicts_total else 0.0,
        "citation_validity": round(cite.validity_rate, 4) if cite else None,
        "calls": cost.calls if cost else 0,
        "total_tokens": cost.total_tokens if cost else 0,
    }


def _method_summary(rows: list[dict]) -> list[dict]:
    """Aggregate rows per method: verdict distribution + mean behaviour metrics."""
    by_method: dict[str, list[dict]] = {}
    for row in rows:
        by_method.setdefault(row["method"], []).append(row)
    summary: list[dict] = []
    for method, group in sorted(by_method.items()):
        total_verdicts = sum(r["verdicts_total"] for r in group)
        summary.append(
            {
                "method": method,
                "cases": len({r["case_id"] for r in group}),
                "runs": len(group),
                "verdicts_total": total_verdicts,
                "compliant": sum(r["compliant"] for r in group),
                "non_compliant": sum(r["non_compliant"] for r in group),
                "not_applicable": sum(r["not_applicable"] for r in group),
                "na_rate": round(sum(r["not_applicable"] for r in group) / total_verdicts, 4) if total_verdicts else 0.0,
                "mean_valid_rate": round(sum(r["valid_rate"] for r in group) / len(group), 4),
                "mean_citation_validity": round(
                    sum(r["citation_validity"] for r in group if r["citation_validity"] is not None)
                    / max(1, sum(1 for r in group if r["citation_validity"] is not None)),
                    4,
                ),
                "total_calls": sum(r["calls"] for r in group),
                "total_tokens": sum(r["total_tokens"] for r in group),
            }
        )
    return summary


# Display order: by call-cost / complexity ascending, so charts read left-to-right
# from the cheapest one-shot to the densest multi-stage method.
_CHART_METHOD_ORDER = [
    "case_full", "element_full", "checkpoint_full",
    "automatic_retrieval", "verify_audit", "agent_audit", "stage_audit",
]


def _ordered_summary(summary: list[dict]) -> list[dict]:
    idx = {m: i for i, m in enumerate(_CHART_METHOD_ORDER)}
    return sorted(summary, key=lambda s: idx.get(s["method"], 99))


def _short(method: str) -> str:
    return {
        "case_full": "case_full",
        "element_full": "elem_full",
        "checkpoint_full": "cp_full",
        "automatic_retrieval": "auto_rag",
        "verify_audit": "verify",
        "agent_audit": "agent",
        "stage_audit": "stage",
    }.get(method, method)


def _svg_distribution(summary: list[dict]) -> str:
    """Stacked bar: 合规 / 不合规 / N-A per method (counts out of 41)."""
    rows = _ordered_summary(summary)
    if not rows:
        return ""
    n = len(rows)
    W, H = 900, 330
    left, right, top, bottom = 60, 30, 30, 70
    plot_w = W - left - right
    plot_h = H - top - bottom
    step = plot_w / n
    bar_w = step * 0.62
    max_total = 41
    parts: list[str] = []
    for v in (0, 10, 20, 30, 40, 41):
        gy = top + plot_h - v / max_total * plot_h
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{W-right}" y2="{gy:.1f}" stroke="#eee"/>')
        parts.append(f'<text x="{left-6}" y="{gy+3:.1f}" text-anchor="end" font-size="9" fill="#888">{v}</text>')
    for i, s in enumerate(rows):
        x = left + i * step + (step - bar_w) / 2
        comp, nonc, na = s["compliant"], s["non_compliant"], s["not_applicable"]
        y = top + plot_h
        for h, fill in (
            (comp / max_total * plot_h, "#1b7f3b"),
            (nonc / max_total * plot_h, "#b00020"),
            (na / max_total * plot_h, "#9aa0a6"),
        ):
            parts.append(f'<rect x="{x:.1f}" y="{y-h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{fill}"><title>{s["method"]}: {comp}/{nonc}/{na}</title></rect>')
            y -= h
        cx = x + bar_w / 2
        parts.append(f'<text x="{cx:.1f}" y="{H-42:.1f}" text-anchor="middle" font-size="10" fill="#333">{_short(s["method"])}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{H-28:.1f}" text-anchor="middle" font-size="9" fill="#888">{comp}/{nonc}/{na}</text>')
    parts.append(f'<text x="{left-6}" y="{top-8}" text-anchor="end" font-size="9" fill="#888">CP 数</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _svg_valid_rate(summary: list[dict]) -> str:
    """Bar: valid% per method (data completeness)."""
    rows = _ordered_summary(summary)
    if not rows:
        return ""
    n = len(rows)
    W, H = 900, 300
    left, right, top, bottom = 60, 30, 30, 60
    plot_w = W - left - right
    plot_h = H - top - bottom
    step = plot_w / n
    bar_w = step * 0.62
    parts: list[str] = []
    for v in (0, 25, 50, 75, 100):
        gy = top + plot_h - v / 100 * plot_h
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{W-right}" y2="{gy:.1f}" stroke="#eee"/>')
        parts.append(f'<text x="{left-6}" y="{gy+3:.1f}" text-anchor="end" font-size="9" fill="#888">{v}%</text>')
    for i, s in enumerate(rows):
        x = left + i * step + (step - bar_w) / 2
        vr = s["mean_valid_rate"] or 0
        h = vr / 100 * plot_h
        fill = "#1b7f3b" if vr >= 95 else ("#f9a825" if vr >= 80 else "#b00020")
        parts.append(f'<rect x="{x:.1f}" y="{top+plot_h-h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{fill}"><title>{s["method"]}: {vr*100:.1f}%</title></rect>')
        cx = x + bar_w / 2
        parts.append(f'<text x="{cx:.1f}" y="{top+plot_h-h-4:.1f}" text-anchor="middle" font-size="10" fill="#333">{vr*100:.0f}%</text>')
        parts.append(f'<text x="{cx:.1f}" y="{H-28:.1f}" text-anchor="middle" font-size="10" fill="#333">{_short(s["method"])}</text>')
    parts.append(f'<text x="{left-6}" y="{top-8}" text-anchor="end" font-size="9" fill="#888">valid%</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _svg_token_cost(summary: list[dict]) -> str:
    """Bar: token cost per method (log scale, since 7M vs 180k spans 1.6 decades)."""
    rows = _ordered_summary(summary)
    if not rows:
        return ""
    n = len(rows)
    W, H = 900, 300
    left, right, top, bottom = 70, 30, 30, 60
    plot_w = W - left - right
    plot_h = H - top - bottom
    step = plot_w / n
    bar_w = step * 0.62
    parts: list[str] = []
    max_tok = max((s["total_tokens"] or 0) for s in rows) or 1
    import math
    log_max = math.log10(max_tok + 1)
    for dec in range(0, int(log_max) + 1):
        gy = top + plot_h - dec / log_max * plot_h if log_max else top + plot_h
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{W-right}" y2="{gy:.1f}" stroke="#eee"/>')
        parts.append(f'<text x="{left-6}" y="{gy+3:.1f}" text-anchor="end" font-size="9" fill="#888">10^{dec}</text>')
    for i, s in enumerate(rows):
        x = left + i * step + (step - bar_w) / 2
        tok = s["total_tokens"] or 0
        h = (math.log10(tok + 1) / log_max * plot_h) if log_max and tok else 0
        fill = "#1565c0" if tok > 0 else "#e0e0e0"
        parts.append(f'<rect x="{x:.1f}" y="{top+plot_h-h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{fill}"><title>{s["method"]}: {tok:,} tokens</title></rect>')
        cx = x + bar_w / 2
        lbl = f"{tok/1000:.0f}k" if tok >= 1000 else (str(tok) if tok else "0")
        parts.append(f'<text x="{cx:.1f}" y="{H-28:.1f}" text-anchor="middle" font-size="10" fill="#333">{_short(s["method"])}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{H-12:.1f}" text-anchor="middle" font-size="9" fill="#888">{lbl}</text>')
    parts.append(f'<text x="{left-6}" y="{top-8}" text-anchor="end" font-size="9" fill="#888">tokens (log)</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _svg_consensus(agreement: dict | None) -> str:
    """Horizontal bars: consensus non-compliant CPs, sorted by flagger count."""
    if not agreement or not agreement.get("cases"):
        return ""
    case = agreement["cases"][0]
    cons = sorted(case.get("consensus_non_compliant", []), key=lambda c: (-c["n"], c["cp_id"]))
    if not cons:
        return '<p class="meta">无共识不合规 CP。</p>'
    n = len(cons)
    W, H = 900, max(220, 36 * n + 40)
    left, right, top, bottom = 90, 120, 20, 30
    plot_w = W - left - right
    row_h = (H - top - bottom) / n
    bar_h = row_h * 0.62
    max_flag = max((c["n"] for c in cons), default=4) or 4
    parts: list[str] = []
    for i, c in enumerate(cons):
        y = top + i * row_h + (row_h - bar_h) / 2
        w = c["n"] / max_flag * plot_w
        fill = {4: "#8a0a0a", 3: "#b00020"}.get(c["n"], "#e57373")
        parts.append(f'<text x="{left-8}" y="{y+bar_h/2+3:.1f}" text-anchor="end" font-size="11" fill="#333">{c["cp_id"]}</text>')
        parts.append(f'<rect x="{left}" y="{y:.1f}" width="{w:.1f}" height="{bar_h:.1f}" fill="{fill}"><title>{c["cp_id"]}: {c["n"]} 方法判 0 ({", ".join(c["flaggers"])})</title></rect>')
        parts.append(f'<text x="{left+w+6:.1f}" y="{y+bar_h/2+3:.1f}" font-size="10" fill="#555">{c["n"]} 方法</text>')
        parts.append(f'<text x="{W-right+6:.1f}" y="{y+bar_h/2+3:.1f}" font-size="9" fill="#888">{", ".join(c["flaggers"])}</text>')
    parts.append(f'<text x="{left}" y="{top-6:.1f}" font-size="9" fill="#888">← 判 0 的方法数 (多=更可信)</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _svg_pairwise_heatmap(agreement: dict | None) -> str:
    """Heatmap: pairwise agreement rate between methods."""
    if not agreement or not agreement.get("cases"):
        return ""
    case = agreement["cases"][0]
    methods = case.get("methods", [])
    pairs = case.get("pairwise_agreement", [])
    if not methods or not pairs:
        return ""
    m = len(methods)
    cell = 46
    left, top = 150, 110
    W = left + m * cell + 20
    H = top + m * cell + 20
    # build lookup
    lut: dict[tuple[str, str], float] = {}
    for p in pairs:
        lut[(p["a"], p["b"])] = p["agreement"]
        lut[(p["b"], p["a"])] = p["agreement"]
    parts: list[str] = []
    for i, mi in enumerate(methods):
        parts.append(f'<text x="{left+i*cell+cell/2:.1f}" y="{top-8:.1f}" text-anchor="middle" font-size="9" fill="#444" transform="rotate(-40 {left+i*cell+cell/2:.1f} {top-8:.1f})">{_short(mi)}</text>')
        parts.append(f'<text x="{left-8:.1f}" y="{top+i*cell+cell/2+3:.1f}" text-anchor="end" font-size="9" fill="#444">{_short(mi)}</text>')
        for j, mj in enumerate(methods):
            if i == j:
                fill, val = "#263238", "—"
            else:
                v = lut.get((mi, mj))
                if v is None:
                    fill, val = "#fafafa", ""
                else:
                    # white -> deep blue
                    r = int(245 - (245 - 21) * v)
                    g = int(245 - (245 - 101) * v)
                    b = int(245 - (245 - 192) * v)
                    fill = f"rgb({r},{g},{b})"
                    val = f"{v*100:.0f}"
            parts.append(f'<rect x="{left+j*cell:.1f}" y="{top+i*cell:.1f}" width="{cell-2}" height="{cell-2}" fill="{fill}"><title>{mi} vs {mj}: {val}%</title></rect>')
            if val:
                tc = "#fff" if (i == j or (lut.get((mi, mj)) or 0) > 0.6) else "#333"
                parts.append(f'<text x="{left+j*cell+cell/2-1:.1f}" y="{top+i*cell+cell/2+3:.1f}" text-anchor="middle" font-size="9" fill="{tc}">{val}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _collect_cp_method_matrix(root: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Per-CP per-method final verdict matrix. Returns (methods, {cp_id: {method: verdict}})."""
    methods = [m for m in _CHART_METHOD_ORDER if (root / m).is_dir()]
    matrix: dict[str, dict[str, str]] = {}
    for m in methods:
        mroot = root / m
        for case_dir in sorted(p for p in mroot.iterdir() if p.is_dir()):
            for track3_dir in sorted(p for p in case_dir.iterdir() if p.is_dir()):
                for ud in _unit_dirs_with_results(track3_dir):
                    try:
                        d = read_json(ud / "result.json")
                    except Exception:
                        continue
                    if not d.get("valid"):
                        continue
                    for v in (d.get("verdicts") or []):
                        cp = v.get("cp_id")
                        if cp:
                            matrix.setdefault(cp, {})[m] = str(v.get("verdict"))
    return methods, matrix


def _collect_agent_stats(root: Path) -> dict:
    """agent_audit module fire + critic flip stats across all cases."""
    mod: Counter = Counter()
    flips: Counter = Counter()
    fired = total = 0
    ag_root = root / "agent_audit"
    if ag_root.is_dir():
        for case_dir in sorted(p for p in ag_root.iterdir() if p.is_dir()):
            for track3_dir in sorted(p for p in case_dir.iterdir() if p.is_dir()):
                for ud in _unit_dirs_with_results(track3_dir):
                    try:
                        d = read_json(ud / "result.json")
                    except Exception:
                        continue
                    if not d.get("valid"):
                        continue
                    total += 1
                    trace = d.get("agent_trace") or {}
                    mods = trace.get("fired_modules") or []
                    if mods:
                        fired += 1
                    for mm in mods:
                        mod[mm.get("module")] += 1
                        if mm.get("module") == "critic":
                            flips[(str(mm.get("verdict_before")), str(mm.get("verdict_after")))] += 1
    return {
        "modules": dict(mod), "fired": fired, "total": total,
        "flips": {f"{k[0]}->{k[1]}": v for k, v in flips.items()},
    }


_VCOLOR = {"1": "#c8e6c9", "0": "#ef9a9a", "N/A": "#e0e0e0", "NOT_APPLICABLE": "#e0e0e0"}


def _svg_cp_method_matrix(methods: list[str], matrix: dict[str, dict[str, str]], agreement: dict | None) -> str:
    """41 CP x N method verdict heatmap. Consensus CPs (>=2 zeros) highlighted in amber."""
    if not matrix:
        return ""
    cps = sorted(matrix.keys(), key=lambda c: int(c[2:]) if c[2:].isdigit() else 999)
    cons_set: set[str] = set()
    if agreement:
        for case in agreement.get("cases", []):
            for c in case.get("consensus_non_compliant", []):
                cons_set.add(c["cp_id"])
    n, m = len(cps), len(methods)
    cell_w, cell_h = 66, 20
    left, top, bottom = 56, 78, 24
    W = left + m * cell_w + 12
    H = top + n * cell_h + bottom
    parts: list[str] = []
    for j, meth in enumerate(methods):
        x = left + j * cell_w + cell_w / 2
        parts.append(f'<text x="{x:.1f}" y="{top-34:.1f}" text-anchor="end" font-size="10" fill="#444" transform="rotate(-40 {x:.1f} {top-34:.1f})">{_short(meth)}</text>')
    parts.append(f'<text x="{left-6}" y="{top-52:.1f}" text-anchor="end" font-size="9" fill="#888">CP</text>')
    for i, cp in enumerate(cps):
        y = top + i * cell_h
        is_cons = cp in cons_set
        if is_cons:
            parts.append(f'<rect x="0" y="{y:.1f}" width="{W}" height="{cell_h}" fill="#fff8e1"/>')
        cp_color = "#b00020" if is_cons else "#333"
        cp_weight = "600" if is_cons else "400"
        parts.append(f'<text x="{left-5}" y="{y+cell_h/2+3:.1f}" text-anchor="end" font-size="9" fill="{cp_color}" font-weight="{cp_weight}">{cp}</text>')
        for j, meth in enumerate(methods):
            x = left + j * cell_w
            v = matrix.get(cp, {}).get(meth)
            fill = _VCOLOR.get(v, "#fafafa") if v else "#fafafa"
            lbl = {"1": "1", "0": "0", "N/A": "N", "NOT_APPLICABLE": "N"}.get(v, "")
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w-2}" height="{cell_h-2}" fill="{fill}" stroke="#fff"><title>{cp} / {meth}: {v or "missing"}</title></rect>')
            if lbl:
                tc = "#1b7f3b" if v == "1" else ("#b00020" if v == "0" else "#666")
                parts.append(f'<text x="{x+cell_w/2-1:.1f}" y="{y+cell_h/2+3:.1f}" text-anchor="middle" font-size="9" fill="{tc}">{lbl}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _svg_agent_chain(agent_stats: dict) -> str:
    """Agent module fire counts + critic flip effect (case-001 actual)."""
    if not agent_stats or agent_stats.get("total", 0) == 0:
        return ""
    mods = agent_stats["modules"]
    flips = agent_stats["flips"]
    fired = agent_stats["fired"]
    total = agent_stats["total"]
    mod_labels = [
        ("critic", "critic (条件3 conflict)"),
        ("verifier", "verifier (条件2/4/5)"),
        ("retrieval_repair", "retrieval_repair (条件1)"),
        ("arbitration", "arbitration (条件6)"),
    ]
    max_v = max((mods.get(k, 0) for k, _ in mod_labels), default=1) or 1
    W, H = 920, 400
    left, right, top, row_h = 240, 70, 44, 32
    plot_w = W - left - right
    parts: list[str] = []
    parts.append(f'<text x="{left}" y="{top-14}" font-size="12" fill="#555">module 触发数(fired CP: {fired}/{total})</text>')
    for i, (k, label) in enumerate(mod_labels):
        y = top + i * row_h
        v = mods.get(k, 0)
        w = v / max_v * plot_w
        fill = "#b00020" if v > 0 else "#eeeeee"
        parts.append(f'<text x="{left-8}" y="{y+18:.1f}" text-anchor="end" font-size="11" fill="#333">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y+4:.1f}" width="{w:.1f}" height="18" fill="{fill}"><title>{k}: {v}</title></rect>')
        parts.append(f'<text x="{left+w+6:.1f}" y="{y+18:.1f}" font-size="11" fill="#444">{v}</text>')
    flip_top = top + len(mod_labels) * row_h + 36
    parts.append(f'<text x="{left}" y="{flip_top-14}" font-size="12" fill="#555">critic 翻转效果(verdict before -&gt; after)</text>')
    flip_labels = [
        ("1->0", "1->0 (合规->不合规)", "#b00020"),
        ("0->1", "0->1 (不合规->合规)", "#1b7f3b"),
        ("1->1", "1->1 (维持合规)", "#cfd8dc"),
        ("0->0", "0->0 (维持不合规)", "#cfd8dc"),
    ]
    max_f = max((flips.get(k, 0) for k, _, _ in flip_labels), default=1) or 1
    for i, (k, label, color) in enumerate(flip_labels):
        y = flip_top + i * row_h
        v = flips.get(k, 0)
        w = v / max_f * plot_w
        parts.append(f'<text x="{left-8}" y="{y+18:.1f}" text-anchor="end" font-size="11" fill="#333">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y+4:.1f}" width="{w:.1f}" height="18" fill="{color}"><title>{label}: {v}</title></rect>')
        parts.append(f'<text x="{left+w+6:.1f}" y="{y+18:.1f}" font-size="11" fill="#444">{v}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">{"".join(parts)}</svg>'


def _render_charts(summary: list[dict], agreement: dict | None, matrix: tuple | None = None, agent_stats: dict | None = None) -> str:
    dist = _svg_distribution(summary)
    valid = _svg_valid_rate(summary)
    cost = _svg_token_cost(summary)
    cons = _svg_consensus(agreement)
    heat = _svg_pairwise_heatmap(agreement)
    matrix_svg = _svg_cp_method_matrix(matrix[0], matrix[1], agreement) if matrix and matrix[1] else ""
    agent_svg = _svg_agent_chain(agent_stats) if agent_stats else ""
    return f"""
<h2>① 方法分布对比 — 合规 / 不合规 / N-A</h2>
<p class="meta">每方法对 41 个 CP 的判定分布。绿=合规,红=不合规,灰=N/A。<b>几乎全绿的方法(如 case_full 40/0/1)存在 blanket-approve 偏差</b> — 倾向于一律放行,区分度低。</p>
{dist}
<div class="legend"><span class="lg" style="background:#1b7f3b"></span>合规(1) <span class="lg" style="background:#b00020"></span>不合规(0) <span class="lg" style="background:#9aa0a6"></span>N/A</div>

<h2>② 数据完整度 — valid%</h2>
<p class="meta">每方法产出的有效判定占比(绿≥95% / 黄80-95% / 红&lt;80%)。低 valid% 说明该方法在部分 CP 上调用失败(本案例主因是配额耗尽,非方法本身缺陷)。</p>
{valid}

<h2>③ 共识不合规 CP — 多方法一致判 0(最高置信发现)</h2>
<p class="meta">无 silver 标注时,<b>多个独立方法同时判不合规</b>的 CP 是最可信的真实问题。条长 = 判 0 的方法数,颜色越深越可信(4 方法=最深红)。这些是 case-001 里最值得人工复核的检查点。</p>
{cons}

<h2>④ 方法间一致率热力图</h2>
<p class="meta">两两方法在同一 CP 上的判定一致率(0-100%)。格子越蓝=越一致。<b>case_full ↔ element_full 高度一致(95%)</b>说明两路 one-shot 互验;而 stage_audit 与多数方法一致率偏低(~50%),因为它更严格、更敢判 0。</p>
{heat}

<h2>⑤ 调用成本 — token 用量(log scale)</h2>
<p class="meta">每方法的 token 消耗(对数刻度,因 checkpoint_full 7M 与 case_full 180k 跨 1.6 个数量级)。成本越高越需要配额预算;blanket-approve 的低成本方法虽省,但区分度不足。</p>
{cost}

<h2>⑥ CP × 方法 判定矩阵(逐 CP 透视)</h2>
<p class="meta">每个 CP 在 7 个方法下的最终判定。绿=合规(1),红=不合规(0),灰=N/A。<b>黄色高亮行 = 共识不合规 CP(≥2 方法判 0)</b>。一眼可见:case_full/element_full 几乎全绿(blanket-approve),而 stage_audit/agent_audit 红格最多(最严格)。</p>
{matrix_svg}
<div class="legend"><span class="lg" style="background:#c8e6c9"></span>合规(1) <span class="lg" style="background:#ef9a9a"></span>不合规(0) <span class="lg" style="background:#e0e0e0"></span>N/A <span class="lg" style="background:#fff8e1;border:1px solid #ffb300"></span>共识 CP</div>

<h2>⑦ Agent 链路触发统计(agent_audit 实测)</h2>
<p class="meta">agent_audit = stage_audit + 6 条件触发的 module。上图:各 module 实际触发次数(<b>case-001 仅 critic 触发</b>,其余 5 module 0 触发)。下图:critic 对 verdict 的翻转效果(<b>3 个 1->0 翻转</b>使 agent 偏向严格)。详见 <code>docs/2026-08-01-method-pipelines.md</code>。</p>
{agent_svg}
"""


def render_html(*, rows: list[dict], summary: list[dict], has_silver: bool, agreement: dict | None = None, matrix: tuple | None = None, agent_stats: dict | None = None) -> str:
    accuracy_note = (
        "" if has_silver
        else "<p class='note'>⚠ 暂无 silver 标注(anomaly_report / 人工标签),accuracy 列留空。"
        "下方 valid% / citation% / N-A / cost 不依赖 ground truth,已可横向对比。</p>"
    )

    def fmt(v, pct=False):
        if v is None:
            return "—"
        if pct:
            return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "—"
        return str(v)

    body_rows = "\n".join(
        f"<tr><td>{r['method']}</td><td>{r['case_id']}</td><td>{r['track3']}</td>"
        f"<td>{r['units']}</td><td>{r['verdicts_total']}</td>"
        f"<td class='c1'>{r['compliant']}</td><td class='c0'>{r['non_compliant']}</td>"
        f"<td class='cna'>{r['not_applicable']}</td>"
        f"<td>{fmt(r['valid_rate'], True)}</td>"
        f"<td>{fmt(r['overall_accuracy'], True) if r['anchored_total'] else '—'}</td>"
        f"<td>{r['anchored_total'] or '—'}</td>"
        f"<td>{fmt(r['citation_validity'], True)}</td>"
        f"<td>{r['calls']}</td><td>{r['total_tokens']}</td></tr>"
        for r in rows
    )
    sum_rows = "\n".join(
        f"<tr><td>{s['method']}</td><td>{s['cases']}</td><td>{s['runs']}</td>"
        f"<td>{s['verdicts_total']}</td>"
        f"<td class='c1'>{s['compliant']}</td><td class='c0'>{s['non_compliant']}</td>"
        f"<td class='cna'>{s['not_applicable']}</td>"
        f"<td>{fmt(s['na_rate'], True)}</td>"
        f"<td>{fmt(s['mean_valid_rate'], True)}</td>"
        f"<td>{fmt(s['mean_citation_validity'], True)}</td>"
        f"<td>{s['total_calls']}</td><td>{s['total_tokens']}</td></tr>"
        for s in summary
    )
    agreement_section = _render_agreement_section(agreement)
    charts = _render_charts(summary, agreement, matrix, agent_stats)
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>FRECA Task2 — 实验看板</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif; margin: 24px; color: #1a1a1a; max-width: 1120px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 16px; margin-top: 32px; color: #333; }}
  h3 {{ font-size: 13px; color: #555; margin: 16px 0 6px; }}
  .meta {{ color: #888; font-size: 12px; margin-bottom: 12px; }}
  .note {{ background: #fff8e1; border-left: 3px solid #ffb300; padding: 8px 12px; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th, td {{ border: 1px solid #e0e0e0; padding: 5px 9px; text-align: right; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  td:nth-child(1), td:nth-child(2), td:nth-child(3), th:nth-child(1), th:nth-child(2), th:nth-child(3) {{ text-align: left; }}
  tr:hover {{ background: #fafafa; }}
  .c1 {{ color: #1b7f3b; font-weight: 600; }}   /* 合规 */
  .c0 {{ color: #b00020; font-weight: 600; }}    /* 不合规 */
  .cna {{ color: #666; }}                         /* N/A */
  code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
  .chart {{ width: 100%; height: auto; background: #fff; border: 1px solid #eee; border-radius: 4px; }}
  .legend {{ margin: 6px 0 22px; font-size: 12px; color: #555; }}
  .lg {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin: 0 4px 0 12px; vertical-align: middle; }}
  .scenario {{ background: #f8fbff; border: 1px solid #d6e4f5; border-radius: 6px; padding: 14px 20px; margin: 14px 0 6px; }}
  .scenario h2 {{ margin-top: 0; color: #1565c0; }}
  .grid {{ display: grid; grid-template-columns: 1.1fr 1fr 1fr; gap: 18px; }}
  .grid h4 {{ margin: 0 0 6px; font-size: 13px; color: #1565c0; }}
  .grid div {{ font-size: 12px; color: #444; line-height: 1.6; }}
  .pill {{ display: inline-block; background: #e3f2fd; color: #0d47a1; border-radius: 10px; padding: 2px 9px; font-size: 11px; margin: 2px 3px; white-space: nowrap; }}
  .ok {{ color: #1b7f3b; font-weight: 600; }} .warn {{ color: #b00020; font-weight: 600; }}
</style></head><body>
<h1>FRECA Task2 — 实验方法对比看板</h1>
<div class="meta">自动生成自 <code>build/experiments</code>。重跑 <code>python scripts/scoreboard.py</code> 刷新。数据:case-001 baseline。</div>
{accuracy_note}

<section class="scenario">
<h2>📌 场景说明</h2>
<div class="grid">
  <div>
    <h4>任务背景</h4>
    FRECA Task2 = <b>出口管制合规审计自动化</b>。<br>
    100 个农场出口案例 × 41 检查点 = 4100 个审计判定。<br>
    依据:澳大利亚《Export Control (Plants and Plant Products) Rules 2021》。<br>
    每案例含 9 条证据轨道(receival register / dispatch dockets / movement log / traceability…),41 个 CP 跨 4 Elements:场所身份 · 产品处理 · 文档 · 追溯。
  </div>
  <div>
    <h4>7 个审计方法(按复杂度↑)</h4>
    <span class="pill">case_full · 整案 1 次</span>
    <span class="pill">element_full · 4 Element</span>
    <span class="pill">checkpoint_full · 41 CP</span>
    <span class="pill">automatic_retrieval · RAG 检索</span>
    <span class="pill">verify_audit · 无条件复查</span>
    <span class="pill">agent_audit · 条件性复查</span>
    <span class="pill">stage_audit · 4 阶段(最严)</span>
    <div style="margin-top:6px;color:#666">one-shot -&gt; 分块 -&gt; 检索 -&gt; 复查 -&gt; 多阶段:复杂度、成本、严格度递增。</div>
  </div>
  <div>
    <h4>当前状态(case-001)</h4>
    <span class="ok">✓ 七方法全部 41/41 完整</span>(valid 100%)<br>
    <b>14 个共识不合规 CP</b>:CP16/CP36(4 方法最高)、CP23/CP30/CP34/CP37(3 方法)<br>
    两套复查策略对比:<b>verify_audit</b>(每 CP 必复查)vs <b>agent_audit</b>(仅低置信触发 verifier)。<br>
    暂无 silver 标注 -&gt; 以<b>跨方法共识</b>作为 ground-truth 代理。
  </div>
</div>
</section>

{charts}

<h2>⑧ 明细表 - 每方法汇总</h2>
<table>
<tr><th>method</th><th>cases</th><th>runs</th><th>verdicts</th>
<th>合规(1)</th><th>不合规(0)</th><th>N/A</th><th>N/A%</th>
<th>mean valid%</th><th>mean cite%</th><th>calls</th><th>tokens</th></tr>
{sum_rows}
</table>
<p class="meta">💡 <b>合规/不合规/N/A 分布</b>直接来自候选自身判定,无需 ground truth —— 若某方法几乎全 1(如 case_full 常见 40/0/1),即 <b>blanket-approve 偏差</b>。</p>

<h2>⑨ 明细表 - 每次运行(method × case × track3)</h2>
<table>
<tr><th>method</th><th>case</th><th>track3</th><th>units</th><th>verdicts</th>
<th>1</th><th>0</th><th>N/A</th><th>valid%</th><th>accuracy</th><th>anchored</th>
<th>cite%</th><th>calls</th><th>tokens</th></tr>
{body_rows}
</table>
{agreement_section}
</body></html>
"""


def _render_agreement_section(agreement: dict | None) -> str:
    """Section ③: cross-method consensus findings (no silver needed)."""
    if not agreement or not agreement.get("cases"):
        return ""
    blocks: list[str] = []
    for case in agreement["cases"]:
        methods = ", ".join(case.get("methods", []))
        pw = " · ".join(
            f"{r['a']} vs {r['b']}: {r['agree']}/{r['common']} ({r['agreement']*100:.0f}%)"
            for r in case.get("pairwise_agreement", [])
        )
        cons = case.get("consensus_non_compliant", [])
        cons_rows = "\n".join(
            f"<tr><td>{c['cp_id']}</td><td class='c0'>{c['n']}</td><td>{', '.join(c['flaggers'])}</td></tr>"
            for c in cons
        ) or "<tr><td colspan='3'>—</td></tr>"
        blocks.append(
            f"<h3>case {case['case']} <span class='meta'>({methods})</span></h3>"
            f"<p class='meta'>两两一致率: {pw or '—'}</p>"
            f"<p class='meta'>共识不合规(≥{agreement.get('min_flaggers',2)} 个方法判 0): <b>{len(cons)}</b> 个</p>"
            "<table><tr><th>CP</th><th>判 0 的方法数</th><th>方法</th></tr>"
            f"{cons_rows}</table>"
        )
    body = "\n".join(blocks)
    return f"""
<h2>⑩ 明细表 - 共识不合规 CP(对应上图③)</h2>
<p class="meta">无 ground truth 时,<b>多个独立方法同时判不合规</b>的 CP 是最可信的真实问题候选。
下表是 ≥{agreement.get('min_flaggers', 2)} 个方法一致判 0 的 CP(由 <code>scripts/agreement.py</code> 生成)。</p>
{body}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("build/experiments"))
    parser.add_argument("--parsed", type=Path, default=Path("build/parsed"))
    parser.add_argument("--anomaly-report", type=Path, default=None)
    parser.add_argument("--human-labels", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=Path("build/experiments/scoreboard.json"))
    parser.add_argument("--output-html", type=Path, default=Path("build/experiments/scoreboard.html"))
    parser.add_argument(
        "--agreement",
        type=Path,
        default=Path("build/experiments/agreement.json"),
        help="agreement.json from scripts/agreement.py (optional; adds consensus section)",
    )
    args = parser.parse_args()

    checkpoints = _load_checkpoints(args.parsed)
    silver = build_silver_reference(
        anomaly_report_path=args.anomaly_report,
        human_labels_path=args.human_labels,
        checkpoints=checkpoints,
    )
    has_silver = bool(silver.entries)

    rows = collect_rows(root=args.root, checkpoints=checkpoints, silver=silver)
    summary = _method_summary(rows)

    agreement_data = None
    if args.agreement and args.agreement.is_file():
        agreement_data = read_json(args.agreement)
    matrix = _collect_cp_method_matrix(args.root)
    agent_stats = _collect_agent_stats(args.root)

    atomic_write_json(
        args.output_json,
        {"has_silver": has_silver, "summary": summary, "rows": rows},
    )
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(
        render_html(rows=rows, summary=summary, has_silver=has_silver, agreement=agreement_data, matrix=matrix, agent_stats=agent_stats),
        encoding="utf-8",
    )

    print(f"scoreboard: {len(rows)} runs across {len(summary)} methods → {args.output_json}")
    print(f"dashboard: {args.output_html}")
    for s in summary:
        print(
            f"  {s['method']:<20} cases={s['cases']} verdicts={s['verdicts_total']} "
            f"1/0/NA={s['compliant']}/{s['non_compliant']}/{s['not_applicable']} "
            f"valid={s['mean_valid_rate']:.1%} cite={s['mean_citation_validity']:.1%} "
            f"calls={s['total_calls']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
