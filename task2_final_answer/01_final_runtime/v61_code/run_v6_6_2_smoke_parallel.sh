#!/usr/bin/env bash
set -Eeuo pipefail
V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
RUN="${V662_RUN_ROOT:-$V61/results/final4100_v6_6_deadline}"
mkdir -p "$RUN/logs"
cd "$CORE"; set -a; source "$CORE/.env.deepseek"; set +a
export FRECA_API_PROVIDER="${FRECA_API_PROVIDER:-deepseek}"
export FRECA_ALIGNMENT_MODEL="${FRECA_ALIGNMENT_MODEL:-deepseek-v4-flash}"
export FRECA_API_MAX_ATTEMPTS="${FRECA_API_MAX_ATTEMPTS:-3}"
cases=(case-001 case-020 case-058 case-098); pids=()
for c in "${cases[@]}"; do
 echo "Launching V6.6.2 smoke $c"
 (PYTHONPATH="$CORE:$V61/code" conda run --no-capture-output -n freca-core python "$V61/code/fast_final_adjudicator_v6_6_2.py" --run-root "$RUN" --case "$c") >"$RUN/logs/smoke-v662-$c.log" 2>&1 & pids+=("$!")
done
fail=0; for p in "${pids[@]}"; do wait "$p" || fail=1; done
if ((fail)); then echo 'V6.6.2 smoke worker failed'; tail -n 100 "$RUN"/logs/smoke-v662-*.log; exit 1; fi
PYTHONPATH="$CORE:$V61/code" conda run --no-capture-output -n freca-core python "$V61/code/summarize_v6_6_2.py" --run-root "$RUN"
