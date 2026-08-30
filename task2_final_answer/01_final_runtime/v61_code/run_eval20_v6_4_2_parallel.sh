#!/usr/bin/env bash
set -euo pipefail

V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
CASE_FILE="${V642_CASE_FILE:-$V61/code/eval20_cases_v6_3.txt}"
BASE_OUT="${V642_EVAL_ROOT:-$V61/results/eval20_4cps_v6_4_2_initial}"
WORKERS="${FRECA_EVAL_WORKERS:-4}"

if [[ ! -f "$CASE_FILE" ]]; then
  echo "Missing case file: $CASE_FILE" >&2
  exit 2
fi
if ! [[ "$WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "FRECA_EVAL_WORKERS must be a positive integer" >&2
  exit 2
fi

mkdir -p "$BASE_OUT/_case_shards" "$BASE_OUT/logs"
rm -f "$BASE_OUT/_case_shards"/worker-*.txt

python - "$CASE_FILE" "$BASE_OUT/_case_shards" "$WORKERS" <<'PY'
from pathlib import Path
import sys
case_file=Path(sys.argv[1]); out=Path(sys.argv[2]); n=int(sys.argv[3])
cases=[x.strip() for x in case_file.read_text().splitlines() if x.strip()]
if not cases:
    raise SystemExit("case file is empty")
if n > len(cases):
    n=len(cases)
# Contiguous, near-even partitioning keeps shard reports easy to inspect.
q,r=divmod(len(cases),n)
pos=0
for i in range(n):
    k=q+(1 if i<r else 0)
    shard=cases[pos:pos+k]; pos += k
    (out/f"worker-{i+1:03d}.txt").write_text("\n".join(shard)+"\n")
print(f"Prepared {n} workers for {len(cases)} cases")
for i in range(n):
    p=out/f"worker-{i+1:03d}.txt"
    print(p.name, p.read_text().strip().replace("\n", " "))
PY

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

pids=()
names=()
for shard_file in "$BASE_OUT/_case_shards"/worker-*.txt; do
  worker="$(basename "$shard_file" .txt)"
  run_dir="$BASE_OUT/$worker"
  log="$BASE_OUT/logs/$worker.log"
  case_args=()
  while IFS= read -r c; do
    [[ -n "$c" ]] && case_args+=(--case "$c")
  done < "$shard_file"

  echo "Launching $worker -> ${case_args[*]}"
  (
    PYTHONPATH="$CORE" conda run --no-capture-output -n freca-core \
      python "$CORE/production_runner_v6_4_2.py" \
        --manifest "$CORE/results_v2/logical_case_manifest_v1.json" \
        --contract-dir "$CORE/contracts_v2" \
        --repair-policy "$V61/config/production_repair_policy_v1.json" \
        --run-dir "$run_dir" \
        "${case_args[@]}" \
        --cp CP1 --cp CP12 --cp CP26 --cp CP35 \
        --no-repair \
        --stop-on-error
  ) >"$log" 2>&1 &
  pids+=("$!")
  names+=("$worker")
done

fail=0
for i in "${!pids[@]}"; do
  pid="${pids[$i]}"; name="${names[$i]}"
  if wait "$pid"; then
    echo "[$name] COMPLETE"
  else
    echo "[$name] FAILED -- see $BASE_OUT/logs/$name.log" >&2
    tail -n 40 "$BASE_OUT/logs/$name.log" >&2 || true
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo "One or more workers failed. The runner is resume-safe; rerun this script after fixing the error." >&2
  exit 1
fi

echo
echo "All parallel eval workers completed."
echo "Run root: $BASE_OUT"
echo "Logs:     $BASE_OUT/logs"
echo "Next: bash $V61/code/replay_eval20_v6_4_2.sh"
