#!/usr/bin/env bash
set -Eeuo pipefail
export PYTHONUNBUFFERED=1
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
set -a
source "${FRECA_ENV_FILE:-$SCRIPT_DIR/.env.deepseek}"
set +a
export FRECA_API_PROVIDER=deepseek
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
export FRECA_CONTRACT_MODEL="${FRECA_CONTRACT_MODEL:-deepseek-v4-pro}"
export FRECA_ALIGNMENT_MODEL="${FRECA_ALIGNMENT_MODEL:-deepseek-v4-flash}"
: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY is missing}"

ROOT="$SCRIPT_DIR/results_v2/production_run_v1_smoke_6_deepseek_v1"
REPLAY="$SCRIPT_DIR/results_v2/production_run_v2_smoke_6_deepseek_v1_replay"
mkdir -p "$ROOT/logs"

run_python() {
  if [[ "${CONDA_DEFAULT_ENV:-}" == "freca-core" ]]; then python "$@"
  else conda run --no-capture-output -n freca-core python "$@"; fi
}

CP_ARGS=()
for ((cp=1; cp<=41; cp++)); do CP_ARGS+=(--cp "CP$cp"); done
PIDS=()
for ((case_no=1; case_no<=6; case_no++)); do
  printf -v case_id 'case-%03d' "$case_no"
  printf -v shard_id 'shard-%03d' "$case_no"
  run_python production_runner_v1.py \
    --case "$case_id" "${CP_ARGS[@]}" \
    --run-dir "$ROOT/$shard_id" --stop-on-error \
    > "$ROOT/logs/$case_id.log" 2>&1 &
  PIDS+=("$!")
  printf 'started %s pid=%s log=%s\n' "$case_id" "$!" "$ROOT/logs/$case_id.log"
done

failed=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then failed=1; fi
done
if (( failed )); then
  printf '%s\n' 'At least one case failed; inspect logs. Full replay not started.' >&2
  exit 2
fi

run_python production_merge_shard_reports.py --root "$ROOT" --output "$ROOT/run_report.json"
DIGEST="$(find "$ROOT" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
run_python production_v2_replay_454.py --v1-root "$ROOT" --output-dir "$REPLAY" \
  --v1-tree-digest "$DIGEST" --expected-v1-tree-digest "$DIGEST"
run_python production_run_summary_v1.py --run-report "$ROOT/run_report.json" \
  --v2-report "$REPLAY/semantic_reachability_report.json" \
  --output "$REPLAY/smoke_analysis.json" --expected-coordinates 246
printf 'parallel DeepSeek smoke complete: %s\n' "$REPLAY/smoke_analysis.json"
