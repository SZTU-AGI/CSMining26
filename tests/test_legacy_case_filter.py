from pathlib import Path

from case_filter import summarize_manifest
from freca.manifest import build_manifest


def test_legacy_filter_no_longer_excludes_anomalies_or_assigns_na() -> None:
    root = Path(__file__).parents[1] / "extracted" / "SFRE_cases"
    summary = summarize_manifest(build_manifest(root))

    assert summary["total_cases"] == 100
    assert summary["audit_task_cases"] == 100
    assert summary["informational_flags"]["missing_track_1"] == [24, 80]
    assert summary["informational_flags"]["duplicate_re_number"] == [35, 100]
    assert "verdict" not in str(summary).casefold()
