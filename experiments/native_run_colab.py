"""Colab-only native scoring experiments with exported-certificate validation."""
import sys,pathlib,json,csv,collections,time,subprocess,hashlib,random,shutil
ROOT=pathlib.Path('/content/native-study');DATA=ROOT/'berkey1987';OUT=ROOT/'results';OUT.mkdir(exist_ok=True)
CONFIGS={
 'native01':('native_baseline','0,1',[]),
 'patched01':('native_patched','0,1',[]),
 'alias01':('native_patched','100,101',[]),
 'smooth':('native_patched','0,20',[0,1,0,0,0,0]),
 'dead_small':('native_patched','0,20',[0,1,0,.05,0,0]),
 'dead_large':('native_patched','0,20',[0,1,0,.2,0,0]),
 'fit_small':('native_patched','0,20',[0,1,0,0,.05,0]),
 'fit_large':('native_patched','0,20',[0,1,0,0,.2,0]),
 'progress':('native_patched','0,20',[0,1,0,0,0,.02]),
 'absolute':('native_patched','0,20',[0,0,1,.1,0,0]),
}

def validate(name,cert):
    with (DATA/f'{name}_items.csv').open() as f:items=list(csv.DictReader(f))
    with (DATA/f'{name}_bins.csv').open() as f:b=next(csv.DictReader(f));W,H=int(b['WIDTH']),int(b['HEIGHT'])
    with cert.open() as f:rows=list(csv.DictReader(f))
    counts=collections.Counter();plates=collections.defaultdict(list)
    for r in rows:
        if int(r['TYPE'])<0 or not r['PARENT']:continue
        i=int(r['TYPE']);w,h=int(r['WIDTH']),int(r['HEIGHT']);x,y=int(r['X']),int(r['Y']);assert (w,h)==(int(items[i]['WIDTH']),int(items[i]['HEIGHT']))
        assert 0<=x<x+w<=W and 0<=y<y+h<=H;counts[i]+=int(r['COPIES']);plates[int(r['PLATE_ID'])].append((x,y,w,h))
    for rects in plates.values():
        for i,(x,y,w,h) in enumerate(rects):
            for xx,yy,ww,hh in rects[i+1:]:assert x+w<=xx or xx+ww<=x or y+h<=yy or yy+hh<=y
    assert counts==collections.Counter({i:int(it.get('COPIES',1)) for i,it in enumerate(items)})
    return True

def run(name,config,budget=10,rep=1,mode='anytime',queue=128):
    binary,guides,weights=CONFIGS[config];tag=f'{name}_{config}_{mode}_{rep}';cert=OUT/(tag+'.csv')
    cmd=[str(ROOT/'build'/binary),str(DATA/f'{name}_items.csv'),str(DATA/f'{name}_bins.csv'),str(budget),guides,mode,str(queue),str(cert)]+list(map(str,weights))
    start=time.perf_counter();p=subprocess.run(cmd,capture_output=True,text=True,timeout=budget+10);process_wall=time.perf_counter()-start
    if p.returncode:raise RuntimeError(p.stdout[-1000:]+p.stderr[-2000:])
    row=json.loads(p.stdout.strip().splitlines()[-1]);validate(name,cert)
    row.update(instance=name,config=config,rep=rep,budget_s=budget,mode=mode,process_wall_s=process_wall,certificate_sha256=hashlib.sha256(cert.read_bytes()).hexdigest(),independent_valid=True)
    return row

def parity():
    names=['Class_02.2bp_60_2','Class_03.2bp_100_2','Class_04.2bp_100_1','planted_3_3_5_0'];rows=[]
    for name in names:
        rr=[run(name,c,30,1,'deterministic',128) for c in ('native01','patched01','alias01')];assert len({r['certificate_sha256'] for r in rr})==1,(name,rr);rows+=rr
    (OUT/'parity.json').write_text(json.dumps({'queue':128,'mode':'NotAnytimeDeterministic','all_certificate_hashes_identical':True,'rows':rows},indent=2));print('PARITY_PASS',len(rows),flush=True)

def screen():
    names=['Class_02.2bp_60_2','Class_02.2bp_100_3','Class_03.2bp_100_2','Class_04.2bp_100_1','Class_05.2bp_100_2','planted_3_3_5_0','planted_3_5_6_1']
    configs=['native01','smooth','dead_small','dead_large','fit_small','fit_large','progress','absolute']
    (OUT/'protocol.json').write_text(json.dumps({'instances':names,'configs':{c:CONFIGS[c] for c in configs},'budget_s':3,'repeats':1,'scope':'previously used development cases; direct original tree_search vs direct patched tree_search, not full optimize portfolio','selection':'screen only; confirm promising variants with repeated 10s budgets','correctness':'only ranking changed; feasibility and lower-bound pruning unchanged; three-stage NonExact, first cut horizontal, no rotations, zero kerf','timing':'internal wall includes input build and native search; separate full subprocess wall also reported; certificate writing/independent checks excluded from internal time'},indent=2))
    jobs=[(n,c) for n in names for c in configs];random.Random(1729).shuffle(jobs);rows=[]
    for n,c in jobs:
        r=run(n,c,3);rows.append(r);(OUT/'screen-rows.json').write_text(json.dumps(rows,indent=2));print('NATIVE_SCREEN',len(rows),len(jobs),n,c,r['bins'],flush=True)
    summary={}
    for c in configs:
        rr=[r for r in rows if r['config']==c];diff=[];w=l=t=0
        for r in rr:
            b=next(b for b in rows if b['instance']==r['instance'] and b['config']=='native01');w+=r['bins']<b['bins'];l+=r['bins']>b['bins'];t+=r['bins']==b['bins']
            if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'baseline':b['bins'],'candidate':r['bins']})
        summary[c]={'total_bins':sum(r['bins'] for r in rr),'wins':w,'losses':l,'ties':t,'differences':diff}
    (OUT/'screen-summary.json').write_text(json.dumps(summary,indent=2));print('SCREEN_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='parity':parity()
    else:screen()
