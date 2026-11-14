"""Matched fixed-pool diagnostic; candidate construction excluded from 6s solve budget."""
import sys,json,pathlib,random,subprocess,hashlib,shutil
sys.path.insert(0,'/content');import selection_core_colab as a
ROOT=a.ROOT/'screen';ROOT.mkdir(exist_ok=True)
NAMES=['planted_3_3_5_0','planted_3_5_6_1','Class_04.2bp_100_1']

def main():
    pools=[]
    for name in NAMES:
        inst=a.base.q.make(name);items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())];W,H=inst.bin_type(0).w,inst.bin_type(0).h
        for seed in (61,62):
            pats=a.generate(items,W,H,3,seed,attempts=20000);p=ROOT/f'{name}_{seed}-pool.json';p.write_text(json.dumps({'items':items,'W':W,'H':H,'target':3,'pats':pats}));pools.append({'name':name,'seed':seed,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':len(pats)})
    (ROOT/'protocol.json').write_text(json.dumps({'pools':pools,'attempts':20000,'seconds_per_solver':6,'scope':'development diagnosis on identical serialized candidate pools; excludes pool generation, is not end-to-end 10s benchmark','methods':['ranked','full','compatible'],'hypothesis':'select complete integer covers instead of ranking strips independently; compare full integrated model to isolate pruning'},indent=2))
    jobs=[(p['name'],p['seed'],m) for p in pools for m in ('ranked','full','compatible')];random.Random(994).shuffle(jobs);rows=[]
    for n,s,m in jobs:
        p=subprocess.run([sys.executable,__file__,'worker',n,str(s),m],capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError(p.stderr[-3000:])
        row=json.loads(p.stdout.strip().splitlines()[-1]);rows.append(row);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('SCREEN',len(rows),len(jobs),json.dumps(row),flush=True)
    summary={m:{'solved':sum(r['success'] for r in rows if r['method']==m),'runs':6,'mean_seconds':sum(r['wall_s'] for r in rows if r['method']==m)/6} for m in ('ranked','full','compatible')};(ROOT/'summary.json').write_text(json.dumps(summary,indent=2));print('SCREEN_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':
        n,s,m=sys.argv[2],int(sys.argv[3]),sys.argv[4];pool=json.loads((ROOT/f'{n}_{s}-pool.json').read_text());row,cert=a.solve(pool['pats'],pool['items'],pool['W'],pool['H'],3,m,6,s);row.update(instance=n,seed=s)
        if cert is not None:(ROOT/f'{n}_{s}_{m}-certificate.json').write_text(json.dumps(cert))
        print(json.dumps(row))
    else:main()
