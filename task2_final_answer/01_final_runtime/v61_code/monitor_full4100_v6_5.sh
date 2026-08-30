#!/usr/bin/env bash
set -euo pipefail
V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
ROOT="${V65_FINAL_RUN_ROOT:-$V61/results/final4100_v6_5_production}"
count=$(find "$ROOT" -path '*/tasks/case-*/CP*/decision.json' -type f 2>/dev/null | wc -l)
na=$(grep -Rhl '"fold_label": "N/A"' "$ROOT"/worker-*/tasks/case-*/CP*/decision.json 2>/dev/null | wc -l || true)
printf 'completed=%s/4100  N/A=%s\n' "$count" "$na"
for log in "$ROOT"/logs/worker-*.log; do
  [[ -f "$log" ]] || continue
  printf '%s: ' "$(basename "$log")"
  tail -n 1 "$log" || true
done
