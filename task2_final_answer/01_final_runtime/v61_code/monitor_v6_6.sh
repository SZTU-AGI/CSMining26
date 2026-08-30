#!/usr/bin/env bash
set -Eeuo pipefail
V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
RUN="${V66_RUN_ROOT:-$V61/results/final4100_v6_6_deadline}"
cases=$(find "$RUN/cases" -path '*/final_case.json' -type f 2>/dev/null | wc -l)
elems=$(find "$RUN/cases" -path '*/elements/Element-*.json' -type f 2>/dev/null | wc -l)
echo "Completed cases: $cases / 100"
echo "Completed element calls: $elems / 400"
for f in "$RUN"/logs/worker-*.log; do [[ -f "$f" ]] || continue; echo "--- $(basename "$f") ---"; tail -n 5 "$f"; done
