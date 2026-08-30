#!/usr/bin/env python3
"""Merge per-case production reports without changing task artifacts."""
import argparse
import json
from collections import Counter
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--root", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
reports = sorted(a.root.glob("shard-*/run_report.json"))
rows = []
for path in reports:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows.extend(data.get("tasks") or [])
counts = Counter(str(row.get("status")) for row in rows)
out = {
    "schema": "freca-production-merged-shard-report-v1",
    "shard_report_count": len(reports),
    "selected_task_count": 246,
    "status_counts": dict(sorted(counts.items())),
    "tasks": rows,
    "answer_comparator_used": False,
    "human_or_historical_labels_used": False,
}
a.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("merged", len(reports), "shards", len(rows), "tasks", dict(counts))
if len(reports) != 6 or len(rows) != 246 or counts.get("FAILED", 0):
    raise SystemExit(2)
