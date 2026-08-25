"""Render persisted Gold-method evaluations as a portable HTML report."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from freca.state import read_json


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _evaluation(build_dir: Path, run_id: str) -> dict[str, Any]:
    path = build_dir / "evaluation" / f"{run_id}.json"
    return read_json(path) if path.exists() else {}


def _method_rows(build_dir: Path, rows: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for row in rows:
        run_id = str(row.get("run_id", ""))
        evaluation = _evaluation(build_dir, run_id)
        eligible = bool(row.get("eligible"))
        verdict = "达标" if eligible else "仅诊断"
        rendered.append(
            "<tr>"
            f"<td><code>{escape(run_id)}</code></td>"
            f"<td>{_percent(row.get('agreement_rate'))}</td>"
            f"<td>{_percent(row.get('coverage'))}</td>"
            f"<td>{_percent(row.get('terminal_failure_rate'))}</td>"
            f"<td>{evaluation.get('matched_count', '—')}/{evaluation.get('evaluated_count', '—')}</td>"
            f"<td><span class=\"status {'pass' if eligible else 'diagnostic'}\">{verdict}</span></td>"
            "</tr>"
        )
    return "\n".join(rendered)


def _ledger_cp_tables(build_dir: Path, rows: list[dict[str, Any]]) -> str:
    tables: list[str] = []
    for row in rows:
        run_id = str(row.get("run_id", ""))
        if not run_id.startswith("ledger-"):
            continue
        per_cp = _evaluation(build_dir, run_id).get("per_cp", {})
        if not per_cp:
            continue
        cp_rows = "\n".join(
            "<tr>"
            f"<td>{escape(str(cp_id))}</td>"
            f"<td>{values.get('matched_count', '—')}/{values.get('evaluated_count', '—')}</td>"
            f"<td>{_percent(values.get('agreement_rate'))}</td>"
            "</tr>"
            for cp_id, values in sorted(per_cp.items())
        )
        tables.append(
            f"<h3>{escape(run_id)}</h3>"
            "<table><thead><tr><th>CP</th><th>一致 / 已评测</th><th>一致率</th>"
            f"</tr></thead><tbody>{cp_rows}</tbody></table>"
        )
    return "\n".join(tables) or "<p>尚无 Ledger 逐 CP 评测结果。</p>"


def write_gold_html_report(
    *,
    build_dir: Path,
    comparison_path: Path,
    output_path: Path | None = None,
) -> Path:
    """Write a self-contained report from a comparison JSON and evaluations."""

    comparison = read_json(comparison_path)
    rows = list(comparison.get("runs", []))
    winner = comparison.get("winner") or {}
    winner_id = escape(str(winner.get("run_id", "暂无达标方法")))
    output = output_path or build_dir / "reports" / "gold-method-selection.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FRECA Task2 · Gold 方法评测汇报</title>
<style>
:root {{ color-scheme: light; --ink:#16233b; --muted:#62708a; --line:#dbe3ef; --blue:#2457d6; --soft:#edf4ff; --green:#057a55; --amber:#a15500; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:#f5f8fc; color:var(--ink); font:15px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif; }}
main {{ max-width:1100px; margin:auto; padding:44px 24px 64px; }} h1 {{ font-size:32px; margin:0 0 8px; }} h2 {{ margin:36px 0 12px; font-size:21px; }} h3 {{ margin:20px 0 8px; font-size:16px; }}
.lead,.note {{ color:var(--muted); margin:0; }} .hero {{ background:linear-gradient(135deg,#edf4ff,#fff); border:1px solid var(--line); border-radius:16px; padding:28px; }}
.winner {{ margin-top:20px; padding:16px 18px; border-left:4px solid var(--blue); background:#fff; border-radius:8px; }} .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-top:14px; }}
.card {{ background:#fff; border:1px solid var(--line); padding:16px; border-radius:12px; }} .card strong {{ display:block; font-size:21px; color:var(--blue); }} table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:12px; overflow:hidden; }} th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }} th {{ background:#f0f5fc; font-weight:650; }} tr:last-child td {{ border-bottom:0 }} code {{ font-size:13px; }}
.status {{ display:inline-block; border-radius:999px; padding:2px 9px; font-size:12px; font-weight:650; }} .pass {{ color:var(--green); background:#e7f8ef; }} .diagnostic {{ color:var(--amber); background:#fff1df; }}
.callout {{ background:#fff7eb; border:1px solid #f5d7ad; padding:16px; border-radius:10px; }} footer {{ color:var(--muted); margin-top:32px; font-size:13px; }}
</style></head><body><main>
<section class="hero"><p class="lead">FRECA Task2 · 离线实验汇报</p><h1>Gold 方法选择</h1>
<p class="note">评测集为 34 条已确认 case×CP 共识。Gold 仅用于离线评分，未进入模型提示词；本页不代表 369/4,100 项正式结果。</p>
<div class="winner"><strong>当前合格候选：{winner_id}</strong><br>资格门槛：coverage ≥ 90%，终态失败率 ≤ 10%；只有达标方法按一致率竞争。</div></section>
<section><h2>方法排行榜</h2><table><thead><tr><th>运行</th><th>一致率</th><th>覆盖率</th><th>终态失败率</th><th>匹配 / 已评测</th><th>资格</th></tr></thead><tbody>{_method_rows(build_dir, rows)}</tbody></table></section>
<section><h2>Ledger 误差诊断</h2><div class="callout"><strong>v2 重点：</strong>将“主体矛盾、证据不足或无法确认”错误输出为 N/A 的情况规范化为 0；N/A 仅允许模型原始适用性为 NOT_APPLICABLE 且具有法规依据。另行比较全量复核与扩大、去污染证据包。</div>{_ledger_cp_tables(build_dir, rows)}</section>
<section><h2>解释口径</h2><div class="grid"><div class="card"><strong>一致率</strong>matched / evaluated，仅在有最终 verdict 的项上计算。</div><div class="card"><strong>覆盖率</strong>evaluated / 34；BLOCKED、FAILED 仍计入分母。</div><div class="card"><strong>失败率</strong>(BLOCKED + FAILED) / 34，不因缺失输出而抬高结果。</div></div></section>
<footer>生成时间：{datetime.now(timezone.utc).isoformat()} · 来源：{escape(str(comparison_path))}</footer>
</main></body></html>"""
    output.write_text(html, encoding="utf-8")
    return output


__all__ = ["write_gold_html_report"]
