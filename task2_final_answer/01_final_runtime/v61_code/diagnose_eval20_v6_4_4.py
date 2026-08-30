#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from collections import Counter, defaultdict

import production_runner_v2
import proof_standard_v1_1 as proof_v1
import semantic_replay_v6_1
import structured_witness_v6_3


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))

def save(v, p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def alignment_lookup(rr):
    out={}
    for row in rr.get('alignments',[]) or []:
        keys={proof_v1.alignment_id(row), str(row.get('fact_candidate_id') or ''), str(row.get('evidence_id') or ''), str(row.get('alignment_evidence_id') or '')}
        for k in keys:
            if k: out[k]=row
    return out

def basis_rows(rr, report, direction):
    lookup=alignment_lookup(rr)
    pr=report.get('support_proof' if direction=='SUPPORT' else 'attack_proof') or {}
    rows=[]
    for aid in pr.get('basis_artifact_ids',[]) or []:
        if str(aid) in lookup: rows.append(lookup[str(aid)])
    if not rows:
        evid={str(x) for x in (pr.get('basis_evidence_ids',[]) or [])}
        rows=[r for r in rr.get('alignments',[]) or [] if str(r.get('evidence_id')) in evid]
    return rows

def compact(row):
    q=str(row.get('exact_quote') or row.get('quote') or row.get('semantic_context') or '').strip()
    parent=str(row.get('parent_evidence_id') or row.get('evidence_id') or '')
    return {
      'relation':row.get('relation'), 'reason_code':row.get('reason_code'),
      'evidence_id':row.get('evidence_id'), 'parent_evidence_id':parent,
      'source_id':row.get('source_id') or parent.split(':',1)[0],
      'fact_candidate_id':row.get('fact_candidate_id'), 'quote':q,
      'truth_bearing':row.get('argument_truth_bearing'),
      'channel':row.get('argument_admission_channel'),
      'evidence_nature':row.get('evidence_nature'),
      'structural':bool(row.get('structural_witness_key')),
      'structural_witness_key':row.get('structural_witness_key'),
      'temporal_relation':row.get('temporal_relation'),
      'reliability_status':(row.get('information_reliability') or {}).get('status'),
    }

def report_diag(rr, rep):
    out={'requirement_id':rep.get('requirement_id'),'raw_state':rep.get('raw_state'),'accepted_state':rep.get('accepted_state')}
    for d in ('SUPPORT','ATTACK'):
        pr=rep.get('support_proof' if d=='SUPPORT' else 'attack_proof') or {}
        out[d.lower()]={
          'accepted':pr.get('accepted_direction') is True,
          'status':pr.get('status'),
          'failures':list(pr.get('failure_codes') or pr.get('failures') or []),
          'temporal':pr.get('temporal_status') or pr.get('temporal_result') or pr.get('temporal'),
          'reliability':pr.get('reliability_status') or pr.get('information_reliability_status') or pr.get('reliability'),
          'basis':[compact(x) for x in basis_rows(rr,rep,d)],
        }
    return out

def discover_coords(base: Path):
    out=[]
    for p in sorted(base.glob('worker-*/tasks/case-*/CP*/initial/requirement_result.json')):
        rel=p.relative_to(base)
        parts=rel.parts
        # worker/tasks/case/cp/initial/file
        if len(parts)>=6:
            out.append((parts[0],parts[2],parts[3],p))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--eval-root',type=Path,required=True)
    ap.add_argument('--contracts',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--markdown',type=Path,required=True)
    args=ap.parse_args()

    unknowns=[]; conflicts=[]; all_rows=[]
    for worker,case,cp,rr_path in discover_coords(args.eval_root):
        chunks_path=args.eval_root/worker/'cases'/case/'evidence_chunks.json'
        contract_path=args.contracts/f'{cp}.json'
        if not (chunks_path.exists() and contract_path.exists()):
            continue
        rr0=load(rr_path); chunks=load(chunks_path); contract=load(contract_path)
        replayed,replay_audit=semantic_replay_v6_1.replay_requirement_result(rr0)
        enriched,struct_audit=structured_witness_v6_3.enrich_requirement_result(replayed,chunks)
        root=production_runner_v2.build_layer7_v2(requirement_result=enriched,contract=contract)
        summary=semantic_replay_v6_1.summarize_layer7(root)
        bundle,fold=production_runner_v2.build_outcome_and_fold(root,contract)
        outcome=bundle.get('common_internal_outcome')
        item={
          'worker':worker,'case':case,'cp':cp,'outcome':outcome,
          'label':fold.get('label'),'truth_bearing':summary.get('direct_truth_bearing_count',0),
          'accepted_directions':summary.get('accepted_direction_count',0),
          'proof_failure_counts':summary.get('proof_failure_counts',{}),
          'structural_injected':struct_audit.get('injected_count',0),
          'structural_rows':struct_audit.get('injected',[]),
          'validation_failures':replay_audit.get('validation_failure_count',0),
          'requirements':[report_diag(enriched,r) for r in (root.get('proof',root).get('requirement_reports',[]) or [])],
        }
        all_rows.append(item)
        if outcome=='UNKNOWN': unknowns.append(item)
        if outcome=='CONFLICTING': conflicts.append(item)

    conflict_by_cp=Counter(x['cp'] for x in conflicts)
    unknown_by_cp=Counter(x['cp'] for x in unknowns)
    conflict_reason=Counter()
    conflict_struct=Counter()
    conflict_sources=Counter()
    for x in conflicts:
        for req in x['requirements']:
            s=req['support']; a=req['attack']
            if s['accepted'] and a['accepted']:
                conflict_reason[(x['cp'],req['requirement_id'])]+=1
                for b in s['basis']+a['basis']:
                    if b.get('structural_witness_key'):
                        conflict_struct[b['structural_witness_key']]+=1
                    if b.get('source_id'):
                        conflict_sources[b['source_id']]+=1

    payload={
      'schema':'freca-v6.4.4-eval20-diagnostic-v1',
      'coordinate_count':len(all_rows),
      'outcome_counts':dict(Counter(x['outcome'] for x in all_rows)),
      'unknown_count':len(unknowns),'unknown_by_cp':dict(unknown_by_cp),
      'conflict_count':len(conflicts),'conflict_by_cp':dict(conflict_by_cp),
      'conflict_requirement_counts':{f'{k[0]}/{k[1]}':v for k,v in conflict_reason.items()},
      'conflict_structural_witness_counts':dict(conflict_struct),
      'unknowns':unknowns,
      'conflicts':conflicts,
    }
    save(payload,args.output)

    md=['# V6.4.4 eval20 zero-API diagnostic','',f"- Coordinates: {len(all_rows)}",f"- Outcomes: `{payload['outcome_counts']}`",f"- UNKNOWN: {len(unknowns)} `{dict(unknown_by_cp)}`",f"- CONFLICTING: {len(conflicts)} `{dict(conflict_by_cp)}`",'']
    md += ['## UNKNOWN coordinates','']
    for x in unknowns:
        md += [f"### {x['case']} / {x['cp']}",f"- TB={x['truth_bearing']} accepted={x['accepted_directions']} structural={x['structural_injected']}",f"- Proof failures: `{x['proof_failure_counts']}`"]
        for req in x['requirements']:
            md.append(f"- {req['requirement_id']} raw={req['raw_state']} accepted={req['accepted_state']}")
            for d in ('support','attack'):
                p=req[d]
                md.append(f"  - {d.upper()} accepted={p['accepted']} failures=`{p['failures']}` temporal={p['temporal']} reliability={p['reliability']}")
                for b in p['basis'][:6]:
                    q=re.sub(r'\s+',' ',b['quote']).strip()
                    if len(q)>260:q=q[:257]+'...'
                    md.append(f"    - {b['reason_code']} structural={b['structural']} nature={b['evidence_nature']} channel={b['channel']} :: {q}")
        if x['structural_rows']:
            md.append('  - Structural injected:')
            for s in x['structural_rows'][:8]:
                md.append(f"    - `{s}`")
        md.append('')

    md += ['## Conflict distribution','',f"- By CP: `{dict(conflict_by_cp)}`",f"- By requirement: `{payload['conflict_requirement_counts']}`",f"- Structural witness keys: `{dict(conflict_struct.most_common())}`",'']
    md += ['## Conflicting coordinates (accepted basis)','']
    for x in conflicts:
        md += [f"### {x['case']} / {x['cp']}"]
        for req in x['requirements']:
            s=req['support']; a=req['attack']
            if not (s['accepted'] and a['accepted']): continue
            md.append(f"- {req['requirement_id']} accepted BOTH")
            for label,p in [('SUPPORT',s),('ATTACK',a)]:
                for b in p['basis'][:4]:
                    q=re.sub(r'\s+',' ',b['quote']).strip()
                    if len(q)>220:q=q[:217]+'...'
                    md.append(f"  - {label} `{b['reason_code']}` structural={b['structural']} :: {q}")
        md.append('')
    args.markdown.parent.mkdir(parents=True,exist_ok=True)
    args.markdown.write_text('\n'.join(md)+'\n',encoding='utf-8')
    print('# V6.4.4 eval20 zero-API diagnostic')
    print('Coordinates:',len(all_rows))
    print('Outcomes:',payload['outcome_counts'])
    print('UNKNOWN:',len(unknowns),dict(unknown_by_cp))
    print('CONFLICTING:',len(conflicts),dict(conflict_by_cp))
    print('Saved:',args.markdown)
    return 0
if __name__=='__main__': raise SystemExit(main())
