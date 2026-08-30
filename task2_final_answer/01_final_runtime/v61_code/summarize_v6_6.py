#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from collections import Counter,defaultdict
ap=argparse.ArgumentParser(); ap.add_argument('--run-root',type=Path,required=True); a=ap.parse_args()
counts=Counter(); na=[]; complete=0
for p in sorted((a.run_root/'cases').glob('case-*/final_case.json')):
 d=json.loads(p.read_text()); complete+=1
 for cp,r in (d.get('verdicts') or {}).items():
  v=r.get('verdict'); counts[v]+=1
  if v=='N/A': na.append((d.get('case_uid'),cp,r.get('reason'),r.get('evidence_ids')))
print(f'Completed cases: {complete}/100')
print('Coordinates:',sum(counts.values()))
print('Labels:',dict(counts))
print('N/A:',len(na))
for row in na[:80]: print('NA',row[0],row[1],'::',row[2],'::',row[3])
