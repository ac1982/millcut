"""Fixed-pool paired experiment of infeasibility-triggered pool expansion."""
import sys,pathlib,json,random,subprocess,hashlib
sys.path.insert(0,'/content');import selection_core_colab as a
ROOT=a.ROOT/'expansion';ROOT.mkdir(exist_ok=True);NAME='planted_3_5_6_1';SEEDS=[42,61,62,63]

def main():
    manifest=[]
    for s in SEEDS:
        p=ROOT/f'{s}-pool.json';existing=a.ROOT/'screen'/f'{NAME}_{s}-pool.json'
        if existing.exists():p.write_bytes(existing.read_bytes())
        else:
            inst=a.base.q.make(NAME);items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())];pats=a.generate(items,1000,1000,3,s,attempts=20000);p.write_text(json.dumps({'items':items,'W':1000,'H':1000,'target':3,'pats':pats}))
        manifest.append({'seed':s,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    (ROOT/'protocol.json').write_text(json.dumps({'instance':NAME,'pools':manifest,'seconds_per_solver':6,'scope':'development fixed-pool diagnostic, not total-budget comparison to PackingSolver','single_factor':'after proven INFEASIBLE only, append next 400 strips in unchanged LP rank; reuse remaining deadline','source_sha256':hashlib.sha256(pathlib.Path('/content/selection_core_colab.py').read_bytes()).hexdigest()},indent=2))
    jobs=[(s,m) for s in SEEDS for m in ('ranked','expand')];random.Random(995).shuffle(jobs);rows=[]
    for s,m in jobs:
        p=subprocess.run([sys.executable,__file__,'worker',str(s),m],capture_output=True,text=True,timeout=20)
        if p.returncode:raise RuntimeError(p.stderr[-2000:])
        r=json.loads(p.stdout.strip().splitlines()[-1]);rows.append(r);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('EXPAND',len(rows),len(jobs),json.dumps(r),flush=True)
    summary={m:{'solved':sum(r['success'] for r in rows if r['method']==m),'runs':4} for m in ('ranked','expand')};(ROOT/'summary.json').write_text(json.dumps(summary,indent=2));print('EXPAND_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':
        s,m=int(sys.argv[2]),sys.argv[3];pool=json.loads((ROOT/f'{s}-pool.json').read_text());r,cert=a.solve(pool['pats'],pool['items'],1000,1000,3,m,6,s);r['seed']=s
        if cert is not None:(ROOT/f'{s}_{m}-certificate.json').write_text(json.dumps(cert))
        print(json.dumps(r))
    else:main()
