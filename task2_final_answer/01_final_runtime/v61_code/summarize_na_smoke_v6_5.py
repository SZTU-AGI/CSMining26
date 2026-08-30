#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from collections import Counter,defaultdict
from pathlib import Path

def load(p):
    with open(p,encoding='utf-8') as f:return json.load(f)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--run-root',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--markdown',type=Path,required=True); a=ap.parse_args()
    decisions=[]; apps=[]
    for p in sorted(a.run_root.glob('worker-*/tasks/case-*/CP*/decision.json')):
        d=load(p); decisions.append((p,d))
        rr=Path(d.get('initial_requirement_result',''))
        apath=rr.parent/'applicability_v6_5.json' if rr else None
        app=load(apath) if apath and apath.exists() else ((d.get('fold_decision') or {}).get('v6_5_applicability_evaluation') or {})
        apps.append((d,app))
    labels=Counter(str(d.get('fold_label')) for _,d in decisions)
    outcomes=Counter(str(d.get('common_internal_outcome')) for _,d in decisions)
    scopes=Counter(str(app.get('scope')) for _,app in apps)
    appdec=Counter(str(app.get('decision')) for _,app in apps)
    na=[]
    conditional=[]
    for d,app in apps:
        row={'case':d.get('case_id') or d.get('case_uid'), 'cp':d.get('cp_id'), 'label':str(d.get('fold_label')), 'outcome':d.get('common_internal_outcome'), 'scope':app.get('scope'), 'decision':app.get('decision'), 'trigger':app.get('trigger_text'), 'method':app.get('method'), 'countercheck':app.get('countercheck'), 'non_applicability_evidence':app.get('non_applicability_evidence') or [], 'applicability_evidence':app.get('applicability_evidence') or []}
        if app.get('scope')=='WHOLE_CP_CONDITIONAL': conditional.append(row)
        if str(d.get('fold_label'))=='N/A': na.append(row)
    result={'coordinate_count':len(decisions),'label_counts':dict(labels),'outcome_counts':dict(outcomes),'scope_counts':dict(scopes),'applicability_decision_counts':dict(appdec),'na_count':len(na),'na_rows':na,'whole_cp_conditional_rows':conditional}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# V6.5 N/A smoke summary','',f'- Coordinates: {len(decisions)}',f'- Labels: `{dict(labels)}`',f'- Internal outcomes: `{dict(outcomes)}`',f'- Applicability scopes: `{dict(scopes)}`',f'- Applicability decisions: `{dict(appdec)}`',f'- N/A count: **{len(na)}**',f'- Whole-CP conditional coordinates: **{len(conditional)}**','']
    if na:
        lines += ['## N/A coordinates','']
        for r in na:
            lines += [f"### {r['case']} / {r['cp']}",f"- trigger: `{r['trigger']}`",f"- method: `{r['method']}`",f"- countercheck: `{r['countercheck']}`"]
            for e in r['non_applicability_evidence']:
                lines.append(f"- NON-APP evidence `{e.get('evidence_id')}` :: {e.get('exact_quote')}")
            lines.append('')
    else:
        lines += ['## N/A coordinates','','None in this smoke set. Do not loosen semantics solely to create N/A. Inspect the whole-CP conditional rows below first.','']
    lines += ['## Whole-CP conditional coordinates','']
    for r in conditional:
        lines.append(f"- {r['case']} / {r['cp']}: decision=`{r['decision']}` label=`{r['label']}` method=`{r['method']}` trigger=`{r['trigger']}`")
    a.markdown.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines))
if __name__=='__main__':main()
