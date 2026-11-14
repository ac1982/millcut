"""End-to-end frozen router vs original automatic/guide1, all in Colab."""
import sys,pathlib,json,subprocess,random,hashlib
sys.path.insert(0,'/content');import routing_core_colab as rc
ROOT=rc.ROOT
cases=json.loads((ROOT/'test-manifest.json').read_text())
frozen=json.loads((ROOT/'frozen-rule.json').read_text())
methods=['default','guide1','router'];seeds=[91,92]
(ROOT/'evaluation-protocol.json').write_text(json.dumps(dict(methods=methods,seeds=seeds,budget_s=10,instances=len(cases),timing='All methods pre-import the same dependencies. Input reading, routing, candidate generation, LP, integer search, output serialization and validation are included in wall_s. Gate time deducted from branch budget; subsolver stopping/validation may cause small overruns.',scope='Frozen geometric route, fresh same-generator cases, unused Berkey 80-item replicate 5, and adversarial height-scrambled cases; not evidence for arbitrary real-world distributions.',witness_access='Solver worker code reads only anonymous items/bins CSV; never generation witnesses or test-manifest labels.'),indent=2))
jobs=[(r['name'],m,s) for r in cases for m in methods for s in seeds];random.Random(81004).shuffle(jobs);rows=[]
for n,m,s in jobs:
    for name,digest in frozen['source_sha256'].items():assert hashlib.sha256(pathlib.Path('/content',name).read_bytes()).hexdigest()==digest
    p=subprocess.run([sys.executable,'/content/routing_core_colab.py',n,m,str(s)],capture_output=True,text=True,timeout=35)
    if p.returncode:raise RuntimeError(p.stderr[-4000:]+p.stdout[-1000:])
    r=json.loads(p.stdout.strip().splitlines()[-1]);case=next(c for c in cases if c['name']==n);r['family']=case['family'];rows.append(r)
    (ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('ROUTING',len(rows),len(jobs),r['family'],n,m,s,r['bins'],flush=True)
summary={}
for control in ('default','guide1'):
    ss={}
    for group in ['all']+sorted({r['family'] for r in rows}):
        rr=[r for r in rows if r['method']=='router' and (group=='all' or r['family']==group)];pairs=[]
        for r in rr:
            b=next(b for b in rows if b['method']==control and b['instance']==r['instance'] and b['seed']==r['seed'])
            pairs.append(dict(instance=r['instance'],seed=r['seed'],reference=b['bins'],router=r['bins'],branch=r['branch'],wall_s=r['wall_s'],gate_s=r['gate_s']))
        ss[group]=dict(pairs=len(pairs),wins=sum(p['router']<p['reference'] for p in pairs),losses=sum(p['router']>p['reference'] for p in pairs),ties=sum(p['router']==p['reference'] for p in pairs),reference_total=sum(p['reference'] for p in pairs),router_total=sum(p['router'] for p in pairs),differences=[p for p in pairs if p['router']!=p['reference']])
    summary[control]=ss
summary['audit']=dict(all_valid=all(r['valid'] for r in rows),max_wall_s=max(r['wall_s'] for r in rows),max_gate_s=max(r['gate_s'] for r in rows),branch_errors=[r for r in rows if 'branch_error' in r],late_over_10_1=[dict(instance=r['instance'],method=r['method'],wall_s=r['wall_s']) for r in rows if r['wall_s']>10.1])
(ROOT/'summary.json').write_text(json.dumps(summary,indent=2));print('ROUTING_COMPLETE',json.dumps(summary),flush=True)
