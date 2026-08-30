#!/usr/bin/env bash
set -Eeuo pipefail

V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
BASE_OUT="${V65_FINAL_RUN_ROOT:-$V61/results/final4100_v6_5_production}"
WORKERS="${FRECA_FINAL_WORKERS:-4}"

if ! [[ "$WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "FRECA_FINAL_WORKERS must be a positive integer" >&2
  exit 2
fi
if (( WORKERS > 100 )); then WORKERS=100; fi

mkdir -p "$BASE_OUT/_case_shards" "$BASE_OUT/logs"
rm -f "$BASE_OUT/_case_shards"/worker-*.txt

python - "$BASE_OUT/_case_shards" "$WORKERS" <<'PY'
from pathlib import Path
import sys
out=Path(sys.argv[1]); n=int(sys.argv[2])
cases=[f"case-{i:03d}" for i in range(1,101)]
q,r=divmod(len(cases),n); pos=0
for i in range(n):
    k=q+(1 if i<r else 0)
    shard=cases[pos:pos+k]; pos+=k
    (out/f"worker-{i+1:03d}.txt").write_text("\n".join(shard)+"\n")
print(f"Prepared {n} parallel workers for 100 cases / 4100 coordinates")
for p in sorted(out.glob('worker-*.txt')):
    rows=[x for x in p.read_text().splitlines() if x.strip()]
    print(p.name, len(rows), rows[0], '..', rows[-1])
PY

cd "$CORE"
set -a
source "$CORE/.env.deepseek"
set +a
export FRECA_PROJECT_ROOT="$CORE"
export FRECA_TASK_ROOT="${FRECA_TASK_ROOT:-/home/MeggieYu/freca/Task2}"
export FRECA_ENABLE_NA_COUNTERCHECK=1
export FRECA_API_PROVIDER="${FRECA_API_PROVIDER:-deepseek}"
export FRECA_ALIGNMENT_MODEL="${FRECA_ALIGNMENT_MODEL:-deepseek-v4-flash}"
export FRECA_APPLICABILITY_MODEL="${FRECA_APPLICABILITY_MODEL:-$FRECA_ALIGNMENT_MODEL}"
export FRECA_API_MAX_ATTEMPTS="${FRECA_API_MAX_ATTEMPTS:-3}"
export FRECA_REFERENCE_CORE_SRC="${FRECA_REFERENCE_CORE_SRC:-$V61/testing/reference_core/freca_reference_core_20260828/src}"

cp_args=()
for i in $(seq 1 41); do cp_args+=(--cp "CP$i"); done

pids=(); names=()
for shard_file in "$BASE_OUT/_case_shards"/worker-*.txt; do
  worker="$(basename "$shard_file" .txt)"
  run_dir="$BASE_OUT/$worker"
  log="$BASE_OUT/logs/$worker.log"
  case_args=()
  while IFS= read -r c; do [[ -n "$c" ]] && case_args+=(--case "$c"); done < "$shard_file"
  echo "Launching $worker: ${#case_args[@]} cases x 41 CPs"
  (
    PYTHONPATH="$CORE" conda run --no-capture-output -n freca-core \
      python "$CORE/production_runner_v6_5_final.py" \
        --manifest "$CORE/results_v2/logical_case_manifest_v1.json" \
        --contract-dir "$CORE/contracts_v2" \
        --repair-policy "$V61/config/production_repair_policy_v1.json" \
        --run-dir "$run_dir" \
        "${case_args[@]}" \
        "${cp_args[@]}" \
        --no-repair \
        --stop-on-error
  ) >"$log" 2>&1 &
  pids+=("$!"); names+=("$worker")
done

fail=0
for i in "${!pids[@]}"; do
  pid="${pids[$i]}"; name="${names[$i]}"
  if wait "$pid"; then
    echo "[$name] COMPLETE"
  else
    echo "[$name] FAILED -- see $BASE_OUT/logs/$name.log" >&2
    tail -n 60 "$BASE_OUT/logs/$name.log" >&2 || true
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo "At least one worker failed. The run is resume-safe: rerun this script after fixing the error." >&2
  exit 1
fi

count=$(find "$BASE_OUT" -path '*/tasks/case-*/CP*/decision.json' -type f | wc -l)
echo "Completed decision files: $count / 4100"
if [[ "$count" -ne 4100 ]]; then
  echo "FULL RUN INCOMPLETE; do not build submission yet." >&2
  exit 3
fi

echo "Full V6.5 production complete: $BASE_OUT"
echo "Next: python $V61/code/finalize_v6_5_submission.py --run-root '$BASE_OUT'"
