#!/usr/bin/env bash
set -euo pipefail

V61="${V61:-/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830}"
CORE="${FRECA_PROJECT_ROOT:-/home/MeggieYu/freca/core_v1}"
BASE_OUT="${V642_EVAL_ROOT:-$V61/results/eval20_4cps_v6_4_2_initial}"
REPLAY_DIR="$BASE_OUT/v6_4_2_replay"
mkdir -p "$REPLAY_DIR"

cd "$V61/code"
for shard in "$BASE_OUT"/worker-*; do
  [[ -d "$shard" ]] || continue
  name="$(basename "$shard")"
  PYTHONPATH=. conda run --no-capture-output -n freca-core \
    python replay_full_batch_v6_4_2.py \
      --run-root "$shard" \
      --contracts "$CORE/contracts_v2" \
      --output "$REPLAY_DIR/${name}.json" \
      --markdown "$REPLAY_DIR/${name}.md" \
      --conflicts "$REPLAY_DIR/${name}_conflicts.json"
done

python - "$REPLAY_DIR" <<'PY'
from pathlib import Path
from collections import Counter
import json, sys
root=Path(sys.argv[1])
rows=[]; conflicts=[]
for p in sorted(root.glob('worker-*.json')):
    if p.name.endswith('_conflicts.json'): continue
    d=json.loads(p.read_text())
    rows.extend(d.get('rows',[]))
    cp=p.with_name(p.stem+'_conflicts.json')
    if cp.exists(): conflicts.extend(json.loads(cp.read_text()).get('coordinates',[]))
payload={
 'schema':'freca-v6.4.2-eval20-combined-replay-v1',
 'coordinate_count':len(rows),
 'outcome_counts':dict(Counter(r.get('outcome') for r in rows)),
 'label_counts':dict(Counter(str(r.get('label')) for r in rows)),
 'diagnosis_counts':dict(Counter(r.get('diagnosis') for r in rows)),
 'structural_witness_total':sum(int(r.get('structural_injected',0)) for r in rows),
 'semantic_replay_validation_failures':sum(int(r.get('semantic_replay_failures',0)) for r in rows),
 'conflicting_coordinate_count':len(conflicts),
 'rows':sorted(rows,key=lambda r:(r.get('case',''),r.get('cp',''))),
}
(root/'combined.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
md=['# V6.4.2 eval20 combined replay','',f"- Coordinates: {len(rows)}",f"- Outcomes: `{payload['outcome_counts']}`",f"- Labels: `{payload['label_counts']}`",f"- Structural witnesses: {payload['structural_witness_total']}",f"- Validation failures: {payload['semantic_replay_validation_failures']}",f"- Conflicting coordinates: {len(conflicts)}",'', '| Case | CP | outcome | TB | accepted | structural | diagnosis |','|---|---|---|---:|---:|---:|---|']
for r in payload['rows']:
    md.append(f"| {r.get('case')} | {r.get('cp')} | {r.get('outcome')} | {r.get('truth_bearing',0)} | {r.get('accepted_directions',0)} | {r.get('structural_injected',0)} | {r.get('diagnosis')} |")
(root/'combined.md').write_text('\n'.join(md)+'\n')
print('# V6.4.2 eval20 combined replay')
print('Coordinates:',len(rows))
print('Outcomes:',payload['outcome_counts'])
print('Labels:',payload['label_counts'])
print('Structural witnesses:',payload['structural_witness_total'])
print('Conflicts:',len(conflicts))
print('Saved:',root/'combined.md')
PY
