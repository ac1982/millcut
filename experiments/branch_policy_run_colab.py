"""Online development pilot for cross-fitted linear node ranking, Colab only."""
import pathlib,sys,json,subprocess,time,random,zipfile,hashlib
sys.path.insert(0,'/content');import branch_collect_colab as bc
import native_schedule_colab as ns
ROOT=bc.ROOT;OUT=bc.OUT/'online';OUT.mkdir(exist_ok=True)

def run(name,method,rep):
    if method=='full_guide1':
        ns.OUT=OUT;r=ns.run(name,'guide1',rep);r['method']=method;return r
    model='native' if method=='matched_guide1' else str(OUT/f'{name}-model.json')
    cert=OUT/f'{name}_{method}_{rep}.csv'
    cmd=[str(ROOT/'build/branch_policy'),str(bc.nr.DATA/f'{name}_items.csv'),str(bc.nr.DATA/f'{name}_bins.csv'),'10',model,str(cert)]
    start=time.perf_counter();p=subprocess.run(cmd,capture_output=True,text=True,timeout=25)
    if p.returncode:raise RuntimeError(p.stderr[-3000:])
    r=json.loads(p.stdout.strip().splitlines()[-1])
    if r['valid']:bc.nr.validate(name,cert)
    r.update(instance=name,method=method,rep=rep,process_wall_s=time.perf_counter()-start,budget_s=10,certificate=cert.name if r['valid'] else None)
    return r

if len(sys.argv)>1 and sys.argv[1]=='worker':
    print(json.dumps(run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
else:
    with zipfile.ZipFile('/content/compiled-package.zip') as z:z.extractall(ROOT/'python-original')
    models=json.loads((bc.OUT/'rank-diagnostic.json').read_text())['models']
    for name,model in models.items():(OUT/f'{name}-model.json').write_text(json.dumps(model,indent=2))
    protocol=dict(instances=list(bc.CASES),methods=['matched_guide1','learned_linear','full_guide1'],budget_s=10,repeats=1,scope='Development pilot after observing offline diagnostic. Each instance uses a model fitted only on other instances. Not a frozen general-purpose model or fresh held-out benchmark.',timing='Native methods count input, model loading, feature computation, ranking, search and certificate writing. Process launch and independent validation reported in process_wall_s. Full guide1 uses previous end-to-end ten-second wrapper.',matched_control='Same original native branching, same single-search growth factor 2; only node ranking differs.',strong_control='Original full optimizer with guide1; may use multiple search schedules/threads.',binary_sha256=hashlib.sha256((ROOT/'build/branch_policy').read_bytes()).hexdigest())
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2))
    jobs=[(n,m,1) for n in bc.CASES for m in protocol['methods']];random.Random(1738).shuffle(jobs);rows=[]
    for n,m,rep in jobs:
        p=subprocess.run([sys.executable,__file__,'worker',n,m,str(rep)],capture_output=True,text=True,timeout=30)
        if p.returncode:raise RuntimeError(p.stderr[-3000:])
        row=json.loads(p.stdout.strip().splitlines()[-1]);rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('ONLINE',len(rows),len(jobs),n,m,row['bins'],flush=True)
    summary={}
    for control in ('matched_guide1','full_guide1'):
        pairs=[]
        for n in bc.CASES:
            c=next(r for r in rows if r['instance']==n and r['method']==control);r=next(r for r in rows if r['instance']==n and r['method']=='learned_linear')
            pairs.append(dict(instance=n,baseline=c['bins'],learned=r['bins'],valid=r['valid']))
        summary[control]=dict(pairs=pairs,wins=sum(p['valid'] and p['learned']<p['baseline'] for p in pairs),losses=sum(not p['valid'] or p['learned']>p['baseline'] for p in pairs),ties=sum(p['valid'] and p['learned']==p['baseline'] for p in pairs))
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));print('ONLINE_COMPLETE',json.dumps(summary),flush=True)
