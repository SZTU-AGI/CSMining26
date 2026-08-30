#!/usr/bin/env bash
set -Eeuo pipefail
V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
RUN="${V66_RUN_ROOT:-$V61/results/final4100_v6_6_deadline}"
WORKERS="${FRECA_FINAL_WORKERS:-4}"
mkdir -p "$RUN/logs" "$RUN/_shards"
python - "$RUN/_shards" "$WORKERS" <<'PY'
from pathlib import Path
import sys
out=Path(sys.argv[1]); n=int(sys.argv[2]); cases=[f'case-{i:03d}' for i in range(1,101)]
q,r=divmod(100,n); pos=0
for i in range(n):
 k=q+(i<r); s=cases[pos:pos+k]; pos+=k
 (out/f'worker-{i+1:03d}.txt').write_text('\n'.join(s)+'\n')
 print(f'worker-{i+1:03d}',len(s),s[0],s[-1])
PY
cd "$CORE"; set -a; source "$CORE/.env.deepseek"; set +a
export FRECA_API_PROVIDER="${FRECA_API_PROVIDER:-deepseek}"
export FRECA_ALIGNMENT_MODEL="${FRECA_ALIGNMENT_MODEL:-deepseek-v4-flash}"
export FRECA_API_MAX_ATTEMPTS="${FRECA_API_MAX_ATTEMPTS:-3}"
pids=(); names=()
for sf in "$RUN/_shards"/worker-*.txt; do
 name="$(basename "$sf" .txt)"; args=()
 while IFS= read -r c; do [[ -n "$c" ]] && args+=(--case "$c"); done < "$sf"
 echo "Launching $name (${#args[@]} cases)"
 (PYTHONPATH="$CORE" conda run --no-capture-output -n freca-core python "$V61/code/fast_final_adjudicator_v6_6.py" --run-root "$RUN" "${args[@]}") >"$RUN/logs/$name.log" 2>&1 &
 pids+=("$!"); names+=("$name")
done
fail=0
for i in "${!pids[@]}"; do if wait "${pids[$i]}"; then echo "[${names[$i]}] COMPLETE"; else echo "[${names[$i]}] FAILED"; tail -n 80 "$RUN/logs/${names[$i]}.log"; fail=1; fi; done
if ((fail)); then echo 'Run is resume-safe; rerun after inspecting failed log.'; exit 1; fi
PYTHONPATH="$CORE" conda run --no-capture-output -n freca-core python "$V61/code/summarize_v6_6.py" --run-root "$RUN"
PYTHONPATH="$CORE" conda run --no-capture-output -n freca-core python "$V61/code/finalize_v6_6_submission.py" --run-root "$RUN"
