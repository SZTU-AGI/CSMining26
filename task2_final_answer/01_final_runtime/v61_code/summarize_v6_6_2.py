#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from collections import Counter,defaultdict
ap=argparse.ArgumentParser(); ap.add_argument('--run-root',type=Path,required=True); a=ap.parse_args()
counts=Counter(); na=[]; complete=0; gate_candidates=Counter(); gate_calls=0
for p in sorted((a.run_root/'cases').glob('case-*/final_case.json')):
 d=json.loads(p.read_text())
 if d.get('version')!='V6.6.2_HYBRID_FORCED_NA_GATE_1': continue
 complete+=1
 ng=d.get('na_gate') or {}; gate_calls+=int(ng.get('model_calls') or 0)
 for cp in ng.get('candidate_cps') or []: gate_candidates[cp]+=1
 for cp,r in (d.get('verdicts') or {}).items():
  v=r.get('verdict'); counts[v]+=1
  if v=='N/A': na.append((d.get('case_uid'),cp,r.get('reason'),r.get('evidence_ids')))
print(f'Completed V6.6.2 cases: {complete}/100')
print('Coordinates:',sum(counts.values()))
print('Labels:',dict(counts))
print('N/A:',len(na))
print('N/A gate candidate counts:',dict(gate_candidates))
print('N/A gate model calls:',gate_calls)
for row in na[:100]: print('NA',row[0],row[1],'::',row[2],'::',row[3])
