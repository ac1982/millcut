"""R48 adapter fix; R43 eligibility and the specialist source remain unchanged.

Eligible: singleton items, 30..120 pieces, width diversity >= .75,
area-bound fill in [.94,.98], area lower bound 2..6, width <=4096.
Otherwise delegate to the original automatic optimizer. No filename features.
"""
import sys,pathlib,json,csv,time,math,collections,hashlib
sys.path.insert(0,'/content');import strip_lp_colab as alg
ROOT=alg.q.pj.ROOT/'routing';DATA=ROOT/'data';OUT=ROOT/'results'
DATA.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
alg.q.pj.DATA=DATA;alg.OUT=OUT

def features(items,W,H):
    N=sum(int(i.get('COPIES',1)) for i in items)
    area=sum(int(i['WIDTH'])*int(i['HEIGHT'])*int(i.get('COPIES',1)) for i in items)
    lb=(area+W*H-1)//(W*H)
    return dict(items=N,unique_width_fraction=len({int(i['WIDTH']) for i in items})/N,lower_bound_fill=area/(lb*W*H),area_lower_bound=lb,singletons=all(int(i.get('COPIES',1))==1 for i in items),supported_width=0<W<=4096)

def choose(f):
    return 'strips' if (f['singletons'] and f['supported_width'] and 30<=f['items']<=120 and f['unique_width_fraction']>=.75 and .94<=f['lower_bound_fill']<=.98 and 2<=f['area_lower_bound']<=6) else 'default'

def read_features(name):
    with (DATA/f'{name}_items.csv').open() as f:items=list(csv.DictReader(f))
    with (DATA/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
    return features(items,int(bt['WIDTH']),int(bt['HEIGHT']))

def validate_strips(name,certificate):
    with (DATA/f'{name}_items.csv').open() as f:items=list(csv.DictReader(f))
    with (DATA/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
    W,H=int(bt['WIDTH']),int(bt['HEIGHT']);counts=collections.Counter();bins=json.loads(certificate.read_text())
    for plate in bins:
        y=0;rects=[]
        for strip in plate:
            assert strip['y']==y;x=0
            for it in strip['items']:
                i=it['id'];w,h=int(items[i]['WIDTH']),int(items[i]['HEIGHT'])
                assert (it['x'],it['y'],it['w'],it['h'])==(x,y,w,h)
                assert 0<w and 0<h<=strip['height'];counts[i]+=1;rects.append((x,y,w,h));x+=w
            assert x<=W;y+=strip['height']
        assert y<=H
        for a,(x,y,w,h) in enumerate(rects):
            for xx,yy,ww,hh in rects[a+1:]:assert x+w<=xx or xx+ww<=x or y+h<=yy or yy+hh<=y
    assert counts==collections.Counter({i:int(it.get('COPIES',1)) for i,it in enumerate(items)})
    return len(bins)

def run(name,method,seed,budget=10):
    start=time.perf_counter();f=read_features(name) if method=='router' else None
    branch=choose(f) if f else method;gate_seconds=time.perf_counter()-start
    original_optimize=alg.q.optimize;captured={}
    def capture(inst,seconds,guide=None):
        result=original_optimize(inst,seconds,guide)
        captured.update(instance=inst,output=result)
        return result
    alg.q.optimize=capture
    try:
        if branch in ('default','guide1'):
            inst=alg.q.make(name);out=original_optimize(inst,max(.001,budget-(time.perf_counter()-start)),[1] if branch=='guide1' else None);alg.q.pj.validate(inst,out.solution)
            cert=OUT/f'{name}_{method}_{seed}.csv';out.solution.write(str(cert))
            r=dict(bins=out.solution.number_of_bins(),initial_bins=out.solution.number_of_bins(),bound=out.bin_packing_bound,status='ORIGINAL_GUIDE1' if branch=='guide1' else 'ORIGINAL_AUTOMATIC',certificate=cert.name)
        else:
            # Preserve the validated original LP branch. Its initial incumbent
            # is retained even if the specialist cannot improve it.
            remaining=max(.001,budget-(time.perf_counter()-start))
            try:
                r=alg.run(name,branch,seed,remaining)
                tag=f'{name}_{branch}_{seed}_{remaining}'
                strip=OUT/f'{tag}-strips.json';cert=strip if strip.exists() else OUT/f'{tag}.csv'
                if strip.exists():assert validate_strips(name,strip)==r['bins']
                r['certificate']=cert.name
            except Exception as exc:
                if not captured:raise
                out=captured['output'];inst=captured['instance'];alg.q.pj.validate(inst,out.solution)
                cert=OUT/f'{name}_{method}_{seed}-fallback.csv';out.solution.write(str(cert))
                r=dict(bins=out.solution.number_of_bins(),initial_bins=out.solution.number_of_bins(),status='BRANCH_ERROR_KEEP_VALID_INCUMBENT',branch_error=repr(exc),certificate=cert.name)
        r.update(instance=name,method=method,seed=seed,branch=branch,features=f,gate_s=gate_seconds,budget_s=budget,wall_s=time.perf_counter()-start,valid=True)
        r['certificate_sha256']=hashlib.sha256((OUT/r['certificate']).read_bytes()).hexdigest()
        return r
    finally:alg.q.optimize=original_optimize

if __name__=='__main__':print(json.dumps(run(sys.argv[1],sys.argv[2],int(sys.argv[3]))))
