#!/usr/bin/env python3
from pathlib import Path
import argparse, json, re

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--core',type=Path,required=True)
    args=ap.parse_args()
    core=args.core
    manifest=core/'results_v2'/'logical_case_manifest_v1.json'
    print('# Benchmark/gold probe')
    if manifest.exists():
        try:
            d=json.loads(manifest.read_text(encoding='utf-8'))
            print('Manifest:',manifest)
            print('Top-level keys:',sorted(d.keys()))
            cases=d.get('cases') or []
            if cases:
                print('Case keys:',sorted(cases[0].keys()))
                labelish=[k for k in cases[0].keys() if re.search(r'label|gold|truth|answer|target|decision|expected',k,re.I)]
                print('Case label-like keys:',labelish)
        except Exception as e: print('Manifest read error:',repr(e))
    else: print('Manifest missing:',manifest)
    pats=re.compile(r'(gold|label|ground|truth|answer|target|expected|submission|decision)',re.I)
    roots=[core/'results_v2',core/'config',core/'contracts_v2',core/'testing']
    found=[]
    for root in roots:
        if not root.exists(): continue
        for p in root.rglob('*'):
            try:
                if p.is_file() and pats.search(p.name) and p.stat().st_size<50_000_000:
                    found.append(p)
            except OSError: pass
    print('\nCandidate files:')
    for p in sorted(set(found))[:300]: print(p)
if __name__=='__main__': main()
