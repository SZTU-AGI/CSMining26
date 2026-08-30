#!/usr/bin/env bash
set -euo pipefail

V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
RUN_OUT="${V64_EVAL_RUN:-$V61/results/eval20_4cps_v6_4_initial/shard-001}"
CASE_FILE="${V64_CASE_FILE:-$V61/code/eval20_cases_v6_3.txt}"

cd "$CORE"
set -a
source "$CORE/.env.deepseek"
set +a
export FRECA_PROJECT_ROOT="$CORE"
export FRECA_ENABLE_NA_COUNTERCHECK=0
export FRECA_API_PROVIDER=deepseek
export FRECA_ALIGNMENT_MODEL=deepseek-v4-flash
export FRECA_API_MAX_ATTEMPTS="${FRECA_API_MAX_ATTEMPTS:-3}"
export FRECA_REFERENCE_CORE_SRC="${FRECA_REFERENCE_CORE_SRC:-$V61/testing/reference_core/freca_reference_core_20260828/src}"

case_args=()
while IFS= read -r c; do
  [[ -n "$c" ]] && case_args+=(--case "$c")
done < "$CASE_FILE"

python production_runner_v6_4.py \
  --manifest "$CORE/results_v2/logical_case_manifest_v1.json" \
  --contract-dir "$CORE/contracts_v2" \
  --repair-policy "$V61/config/production_repair_policy_v1.json" \
  --run-dir "$RUN_OUT" \
  "${case_args[@]}" \
  --cp CP1 --cp CP12 --cp CP26 --cp CP35 \
  --no-repair \
  --stop-on-error

echo "Initial V6.4 eval completed: $RUN_OUT"
echo "Next: run replay_full_batch_v6_4.py on this shard before any repair."
