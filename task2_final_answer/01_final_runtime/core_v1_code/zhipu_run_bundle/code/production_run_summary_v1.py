#!/usr/bin/env python3
"""Fail-closed smoke summary; no API calls."""
import argparse
import json
from collections import Counter
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--run-report", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--v2-report", type=Path, required=True)
p.add_argument("--expected-coordinates", type=int, required=True)
a = p.parse_args()
d = json.loads(a.run_report.read_text(encoding="utf-8"))
v2 = json.loads(a.v2_report.read_text(encoding="utf-8"))
rows = d.get("tasks") or []
statuses = Counter(str(x.get("status")) for x in rows)
labels = Counter(str(x.get("fold_label")) for x in rows if x.get("fold_label") is not None)
fallbacks = sum(x.get("benchmark_fallback") is True for x in rows)
failed = statuses.get("FAILED", 0)
complete = sum(statuses[x] for x in ("COMPLETED", "SKIPPED_COMPLETE"))
v2_rows = v2.get("coordinate_summaries") or []
outcomes = Counter(str(x.get("v2_internal_outcome")) for x in v2_rows)
substantive = sum(v for k, v in outcomes.items() if k not in {"UNKNOWN", "SYSTEM_BLOCK", "REPLAY_INCOMPATIBLE"})
execution_ok = len(rows) == a.expected_coordinates and complete == len(rows) and failed == 0
replay_ok = len(v2_rows) == a.expected_coordinates and not outcomes.get("SYSTEM_BLOCK") and not outcomes.get("REPLAY_INCOMPATIBLE")
report = {
    "schema": "freca-production-smoke-analysis-v1",
    "expected_coordinate_count": a.expected_coordinates,
    "observed_coordinate_count": len(rows),
    "complete_coordinate_count": complete,
    "failed_coordinate_count": failed,
    "status_counts": dict(sorted(statuses.items())),
    "fold_label_counts": dict(sorted(labels.items())),
    "benchmark_fallback_count": fallbacks,
    "benchmark_fallback_rate": fallbacks / len(rows) if rows else None,
    "v2_internal_outcome_counts": dict(sorted(outcomes.items())),
    "v2_substantive_coordinate_count": substantive,
    "execution_health_pass": execution_ok and replay_ok,
    "semantic_reachability_pass": substantive > 0,
    "go_for_full": execution_ok and replay_ok and substantive > 0,
    "warning": "GO establishes execution health and nonzero semantic reachability, not accuracy.",
}
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
if not report["go_for_full"]:
    raise SystemExit(2)
