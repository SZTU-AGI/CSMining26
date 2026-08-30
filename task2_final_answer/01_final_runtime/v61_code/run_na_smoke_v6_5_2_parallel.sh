#!/usr/bin/env bash
set -Eeuo pipefail

V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
RUNNER="$V61/code/production_runner_v6_5_final.py"
BASE_OUT="${V65_NA_SMOKE_ROOT:-$V61/results/na_smoke_v6_5_2}"
WORKERS="${FRECA_NA_SMOKE_WORKERS:-4}"
CASE_STRING="${FRECA_NA_SMOKE_CASES:-case-001 case-020 case-058 case-098}"
read -r -a CASES <<< "$CASE_STRING"

# Fresh smoke by default so a previous broken invocation cannot contaminate results.
if [[ "${FRECA_NA_SMOKE_RESUME:-0}" != "1" ]]; then
  rm -rf "$BASE_OUT"
fi
mkdir -p "$BASE_OUT/_case_shards" "$BASE_OUT/logs"

# Hard preflight: fail before spawning any worker.
required=(
  "$RUNNER"
  "$CORE/results_v2/logical_case_manifest_v1.json"
  "$CORE/contracts_v2/CP1.json"
  "$CORE/contracts_v2/CP41.json"
  "$V61/config/production_repair_policy_v1.json"
  "$V61/code/summarize_na_smoke_v6_5.py"
  "$CORE/.env.deepseek"
)
for p in "${required[@]}"; do
  if [[ ! -f "$p" ]]; then
    echo "PRECHECK FAILED: missing $p" >&2
    echo "Re-run: bash /home/MeggieYu/freca/v6_5_2_na_smoke_fixed/install_v6_5_2.sh" >&2
    exit 2
  fi
done

if (( ${#CASES[@]} == 0 )); then echo "No smoke cases selected" >&2; exit 2; fi
if (( WORKERS > ${#CASES[@]} )); then WORKERS=${#CASES[@]}; fi
if (( WORKERS < 1 )); then WORKERS=1; fi

rm -f "$BASE_OUT/_case_shards"/worker-*.txt
python - "$BASE_OUT/_case_shards" "$WORKERS" "${CASES[@]}" <<'PY'
from pathlib import Path
import sys
out=Path(sys.argv[1]); n=int(sys.argv[2]); cases=sys.argv[3:]
q,r=divmod(len(cases),n); pos=0
for i in range(n):
    k=q+(1 if i<r else 0); shard=cases[pos:pos+k]; pos+=k
    (out/f"worker-{i+1:03d}.txt").write_text("\n".join(shard)+"\n")
print(f"NA smoke V6.5.2: {len(cases)} cases x 41 CP = {len(cases)*41} coordinates; workers={n}")
for p in sorted(out.glob('worker-*.txt')):
    print(p.name, p.read_text().strip().replace('\n', ' '))
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

# Build arguments as a bash array; never concatenate option strings.
cp_args=()
for i in $(seq 1 41); do
  cp_args+=(--cp "CP$i")
done
if (( ${#cp_args[@]} != 82 )); then
  echo "PRECHECK FAILED: CP argument array length=${#cp_args[@]}, expected 82" >&2
  exit 2
fi

pids=(); names=()
for shard_file in "$BASE_OUT/_case_shards"/worker-*.txt; do
  worker="$(basename "$shard_file" .txt)"
  run_dir="$BASE_OUT/$worker"
  log="$BASE_OUT/logs/$worker.log"
  case_args=()
  while IFS= read -r c; do
    [[ -n "$c" ]] && case_args+=(--case "$c")
  done < "$shard_file"
  echo "Launching $worker: ${case_args[*]} ; CP1..CP41"
  (
    PYTHONPATH="$CORE:$V61/code" conda run --no-capture-output -n freca-core \
      python "$RUNNER" \
        --manifest "$CORE/results_v2/logical_case_manifest_v1.json" \
        --contract-dir "$CORE/contracts_v2" \
        --repair-policy "$V61/config/production_repair_policy_v1.json" \
        --run-dir "$run_dir" \
        "${case_args[@]}" \
        "${cp_args[@]}" \
        --no-repair --stop-on-error
  ) >"$log" 2>&1 &
  pids+=("$!"); names+=("$worker")
done

fail=0
for i in "${!pids[@]}"; do
  if wait "${pids[$i]}"; then
    echo "[${names[$i]}] COMPLETE"
  else
    echo "[${names[$i]}] FAILED" >&2
    tail -n 100 "$BASE_OUT/logs/${names[$i]}.log" >&2 || true
    fail=1
  fi
done
(( fail == 0 )) || exit 1

python "$V61/code/summarize_na_smoke_v6_5.py" \
  --run-root "$BASE_OUT" \
  --output "$BASE_OUT/na_smoke_summary.json" \
  --markdown "$BASE_OUT/na_smoke_summary.md"
cat "$BASE_OUT/na_smoke_summary.md"
