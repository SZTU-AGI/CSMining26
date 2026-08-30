#!/usr/bin/env bash
set -Eeuo pipefail
export PYTHONUNBUFFERED=1
trap 'status=$?; printf "launcher failed (exit=%s) at line %s\\n" "$status" "$LINENO" >&2; exit "$status"' ERR

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-}"
SMOKE_COUNT="${2:-6}"
SMOKE_TAG="${FRECA_SMOKE_TAG:-deepseek_v1}"
ENV_FILE="${FRECA_ENV_FILE:-$SCRIPT_DIR/.env.deepseek}"
V1_ROOT="${FRECA_FULL_V1_ROOT:-$SCRIPT_DIR/results_v2/production_run_v1_full_4100_deepseek_v1}"
V1_SHARD="$V1_ROOT/shard-001"
V2_OUTPUT="${FRECA_FULL_V2_OUTPUT:-$SCRIPT_DIR/results_v2/production_run_v2_full_4100_deepseek_v1_replay}"

if [[ "$MODE" != "preflight" && "$MODE" != "smoke" && "$MODE" != "full" ]]; then
  printf '%s\n' \
    "Usage: bash $0 preflight" \
    "       bash $0 smoke [case_count]" \
    "       FRECA_ALLOW_PAID_FULL_RUN=YES bash $0 full"
  exit 2
fi

run_python() {
  if [[ "${CONDA_DEFAULT_ENV:-}" == "freca-core" ]]; then
    python "$@"
  elif command -v conda >/dev/null 2>&1; then
    conda run --no-capture-output -n freca-core python "$@"
  else
    printf '%s\n' 'Activate the freca-core conda environment first.' >&2
    exit 3
  fi
}

if [[ "$MODE" == "preflight" ]]; then
  run_python production_runner_v1.py \
    --all \
    --run-dir "$V1_SHARD" \
    --dry-run
  printf '%s\n' 'PREFLIGHT PASS: 4100 coordinates staged; API calls: 0.'
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Missing environment file: %s\n' "$ENV_FILE" >&2
  exit 4
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

printf 'launcher start: mode=%s provider=%s\n' "$MODE" "${FRECA_API_PROVIDER:-deepseek}"

export FRECA_API_PROVIDER="${FRECA_API_PROVIDER:-deepseek}"
if [[ "$FRECA_API_PROVIDER" == "zhipu" ]]; then
  : "${ZHIPU_API_KEY:=${ZAI_API_KEY:-}}"
  : "${ZHIPU_API_KEY:?ZHIPU_API_KEY or ZAI_API_KEY is missing from $ENV_FILE}"
  export ZHIPU_API_KEY
  export ZHIPU_BASE_URL="${ZHIPU_BASE_URL:-https://open.bigmodel.cn/api/paas/v4}"
  export FRECA_CONTRACT_MODEL="${FRECA_CONTRACT_MODEL:-glm-4.5-air}"
  export FRECA_ALIGNMENT_MODEL="${FRECA_ALIGNMENT_MODEL:-glm-4.5-air}"
else
  : "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY is missing from $ENV_FILE}"
  export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
  export FRECA_CONTRACT_MODEL="${FRECA_CONTRACT_MODEL:-deepseek-v4-pro}"
  export FRECA_ALIGNMENT_MODEL="${FRECA_ALIGNMENT_MODEL:-deepseek-v4-flash}"
fi

if [[ "$MODE" == "smoke" ]]; then
  if [[ ! "$SMOKE_COUNT" =~ ^[1-9][0-9]*$ ]] || (( SMOKE_COUNT > 20 )); then
    printf '%s\n' 'Smoke case_count must be an integer from 1 to 20.' >&2
    exit 5
  fi
  SMOKE_ROOT="$SCRIPT_DIR/results_v2/production_run_v1_smoke_${SMOKE_COUNT}_${SMOKE_TAG}"
  CASE_ARGS=()
  CP_ARGS=()
  for ((i=1; i<=SMOKE_COUNT; i++)); do
    printf -v case_id 'case-%03d' "$i"
    CASE_ARGS+=(--case "$case_id")
  done
  for ((i=1; i<=41; i++)); do
    CP_ARGS+=(--cp "CP$i")
  done
  run_python production_runner_v1.py \
    "${CASE_ARGS[@]}" \
    "${CP_ARGS[@]}" \
    --run-dir "$SMOKE_ROOT/shard-001" \
    --stop-on-error
  DIGEST="$(find "$SMOKE_ROOT" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
  run_python production_v2_replay_454.py \
    --v1-root "$SMOKE_ROOT" \
    --output-dir "$SCRIPT_DIR/results_v2/production_run_v2_smoke_${SMOKE_COUNT}_${SMOKE_TAG}_replay" \
    --v1-tree-digest "$DIGEST" \
    --expected-v1-tree-digest "$DIGEST"
  run_python production_run_summary_v1.py \
    --run-report "$SMOKE_ROOT/shard-001/run_report.json" \
    --v2-report "$SCRIPT_DIR/results_v2/production_run_v2_smoke_${SMOKE_COUNT}_${SMOKE_TAG}_replay/semantic_reachability_report.json" \
    --output "$SCRIPT_DIR/results_v2/production_run_v2_smoke_${SMOKE_COUNT}_${SMOKE_TAG}_replay/smoke_analysis.json" \
    --expected-coordinates "$((SMOKE_COUNT * 41))"
  printf 'SMOKE COMPLETE: %s cases / %s coordinates. Review smoke_analysis.json before full.\n' \
    "$SMOKE_COUNT" "$((SMOKE_COUNT * 41))"
  exit 0
fi

if [[ "${FRECA_ALLOW_PAID_FULL_RUN:-}" != "YES" ]]; then
  printf '%s\n' \
    'Full run is paid and may issue many model calls.' \
    'Re-run with FRECA_ALLOW_PAID_FULL_RUN=YES after reviewing the smoke output.' >&2
  exit 6
fi

SMOKE_ANALYSIS="$SCRIPT_DIR/results_v2/production_run_v2_smoke_6_deepseek_v1_replay/smoke_analysis.json"
if [[ ! -f "$SMOKE_ANALYSIS" ]] || ! run_python -c \
  "import json; assert json.load(open('$SMOKE_ANALYSIS'))['go_for_full'] is True"; then
  printf '%s\n' 'Full run blocked: the six-case smoke analysis is missing or NO-GO.' >&2
  exit 7
fi

printf '%s\n' \
  'Starting/resuming 4,100-coordinate production run.' \
  "V1/API result root: $V1_ROOT" \
  "V2 zero-API replay output: $V2_OUTPUT"

run_python production_runner_v1.py \
  --all \
  --run-dir "$V1_SHARD" \
  --stop-on-error

DIGEST="$(find "$V1_ROOT" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
run_python production_v2_replay_454.py \
  --v1-root "$V1_ROOT" \
  --output-dir "$V2_OUTPUT" \
  --v1-tree-digest "$DIGEST" \
  --expected-v1-tree-digest "$DIGEST"

printf '%s\n' \
  'FULL RUN AND ZERO-API V2 REPLAY COMPLETE.' \
  "Run report: $V1_SHARD/run_report.json" \
  "V2 report: $V2_OUTPUT/semantic_reachability_report.json"
