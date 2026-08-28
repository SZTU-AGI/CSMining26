from __future__ import annotations

import json
from pathlib import Path

from freca.method_report import write_gold_html_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_write_gold_html_report_renders_ranked_runs(tmp_path: Path) -> None:
    comparison = tmp_path / "method-comparison" / "gold-v2.json"
    _write_json(
        comparison,
        {
            "runs": [
                {
                    "run_id": "ledger-gold-v1",
                    "agreement_rate": 0.7058823529411765,
                    "coverage": 1.0,
                    "terminal_failure_rate": 0.0,
                    "eligible": True,
                }
            ],
            "winner": {"run_id": "ledger-gold-v1"},
        },
    )
    _write_json(
        tmp_path / "evaluation" / "ledger-gold-v1.json",
        {
            "gold_count": 34,
            "evaluated_count": 34,
            "matched_count": 24,
            "per_cp": {"CP15": {"gold_count": 5, "evaluated_count": 5, "matched_count": 1, "agreement_rate": 0.2}},
        },
    )

    output = write_gold_html_report(build_dir=tmp_path, comparison_path=comparison)
    html = output.read_text(encoding="utf-8")

    assert output == tmp_path / "reports" / "gold-method-selection.html"
    assert "ledger-gold-v1" in html
    assert "70.6%" in html
    assert "Gold 仅用于离线评分" in html
    assert "CP15" in html


def test_report_escapes_untrusted_run_id(tmp_path: Path) -> None:
    comparison = tmp_path / "method-comparison" / "gold-v2.json"
    _write_json(
        comparison,
        {
            "runs": [{"run_id": "<script>alert(1)</script>", "agreement_rate": 1, "coverage": 1, "terminal_failure_rate": 0, "eligible": True}],
            "winner": None,
        },
    )

    output = write_gold_html_report(build_dir=tmp_path, comparison_path=comparison)

    assert "<script>" not in output.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in output.read_text(encoding="utf-8")


def test_report_derives_gold_total_from_evaluations(tmp_path: Path) -> None:
    comparison = tmp_path / "method-comparison" / "gold-v4.json"
    _write_json(
        comparison,
        {
            "runs": [
                {
                    "run_id": "ledger-gold-v4",
                    "agreement_rate": 0.75,
                    "coverage": 1.0,
                    "terminal_failure_rate": 0.0,
                    "eligible": True,
                }
            ],
            "winner": None,
        },
    )
    _write_json(
        tmp_path / "evaluation" / "ledger-gold-v4.json",
        {
            "gold_count": 37,
            "evaluated_count": 37,
            "matched_count": 28,
            "per_cp": {},
        },
    )

    html = write_gold_html_report(
        build_dir=tmp_path, comparison_path=comparison
    ).read_text(encoding="utf-8")

    assert "评测集为 37 条" in html
    assert "evaluated / 37" in html
    assert "/ 34" not in html


def test_report_without_evaluations_shows_placeholder_total(tmp_path: Path) -> None:
    comparison = tmp_path / "method-comparison" / "gold-v4.json"
    _write_json(
        comparison,
        {
            "runs": [
                {
                    "run_id": "ledger-gold-v4",
                    "agreement_rate": 0.75,
                    "coverage": 1.0,
                    "terminal_failure_rate": 0.0,
                    "eligible": True,
                }
            ],
            "winner": None,
        },
    )

    html = write_gold_html_report(
        build_dir=tmp_path, comparison_path=comparison
    ).read_text(encoding="utf-8")

    assert "评测集为 — 条" in html
