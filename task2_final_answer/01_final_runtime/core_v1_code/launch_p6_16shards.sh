#!/usr/bin/env bash
set -u

ROOT="/home/MeggieYu/freca/core_v1"
BASE="$ROOT/results_v2/production_run_v1_shards"
SHARDS=16

cd "$ROOT"

mkdir -p "$BASE/logs"
mkdir -p "$BASE/pids"

CP_ARGS=()
for cp in $(seq 1 41); do
    CP_ARGS+=(--cp "CP${cp}")
done

echo "============================================================"
echo "FRECA P6 — 16 SHARD PRODUCTION"
echo "============================================================"
echo "Root:   $ROOT"
echo "Output: $BASE"
echo "Shards: $SHARDS"
echo

for shard in $(seq 0 $((SHARDS - 1))); do
    CASE_ARGS=()

    for serial in $(seq 1 100); do
        if (( (serial - 1) % SHARDS == shard )); then
            printf -v case_id "case-%03d" "$serial"
            CASE_ARGS+=(--case "$case_id")
        fi
    done

    printf -v shard_id "%02d" "$shard"

    RUN_DIR="$BASE/shard-$shard_id"
    LOG="$BASE/logs/shard-$shard_id.log"
    PIDFILE="$BASE/pids/shard-$shard_id.pid"

    echo "Launching shard-$shard_id: ${#CASE_ARGS[@]} cases × 41 CP"

    python -u production_runner_v1.py \
        "${CASE_ARGS[@]}" \
        "${CP_ARGS[@]}" \
        --run-dir "$RUN_DIR" \
        > "$LOG" 2>&1 &

    echo $! > "$PIDFILE"
done

echo
echo "All $SHARDS shards launched."
echo "Waiting for workers..."
wait

echo
echo "============================================================"
echo "ALL SHARD PROCESSES EXITED"
echo "============================================================"
