"""Colab-only richer H/V/H patterns with final non-exact width trimming."""
import sys,pathlib,json,random,math,time,subprocess,shutil
sys.path.insert(0,'/content');import pattern_pool_colab as base
old_pattern=base.pattern;old_validate=base.validate_pattern
base.OUT=base.q.pj.ROOT/'pattern-pool-v2';base.OUT.mkdir(exist_ok=True)

def stacked_pattern(items,W,H,rng):
    if rng.random()<.25:return old_pattern(items,W,H,rng)
    remaining=set(range(len(items)));y=0;placements=[];rows=[];weights=[math.exp(rng.uniform(-.7,.7)) for _ in items]
    swapped=[(h,w) for w,h in items]
    while remaining:
        heights={items[i][1] for i in remaining if items[i][1]<=H-y}
        ids=list(remaining)
        for _ in range(10):
            sample=rng.sample(ids,min(len(ids),rng.choice([2,3])));h=sum(items[i][1] for i in sample)
            if h<=H-y:heights.add(h)
        if not heights:break
        candidates=[]
        for height in rng.sample(sorted(heights),min(4,len(heights))):
            left=set(remaining);x=0;columns=[];chosen_all=[]
            while left:
                widths=sorted({items[i][0] for i in left if items[i][0]<=W-x});cols=[]
                for width in rng.sample(widths,min(4,len(widths))):
                    rho=rng.choice([.65,.85,1]);eligible=[i for i in left if rho*width<=items[i][0]<=width and items[i][1]<=height]
                    chosen=base.subset_width(eligible,swapped,height,rng)
                    if not chosen:continue
                    cw=max(items[i][0] for i in chosen);score=sum(items[i][0]*items[i][1]*weights[i] for i in chosen)/cw
                    cols.append((score,cw,chosen))
                if not cols:break
                _,cw,chosen=max(cols,key=lambda c:c[0]);columns.append({'x':x,'width':cw,'ids':chosen});chosen_all.extend(chosen);left.difference_update(chosen);x+=cw
            if not chosen_all:continue
            actual_h=max(sum(items[i][1] for i in c['ids']) for c in columns);score=sum(items[i][0]*items[i][1]*weights[i] for i in chosen_all)/actual_h
            candidates.append((score,actual_h,columns,chosen_all))
        if not candidates:break
        _,height,columns,chosen=max(candidates,key=lambda c:c[0])
        for col in columns:
            cy=y;col['items']=[]
            for i in col.pop('ids'):
                w,h=items[i];p={'id':i,'x':col['x'],'y':cy,'w':w,'h':h};placements.append(p);col['items'].append(p);cy+=h
        rows.append({'y':y,'height':height,'columns':columns});remaining.difference_update(chosen);y+=height
    return {'ids':sorted(p['id'] for p in placements),'placements':placements,'type':'stacked_nonexact','rows':rows}

def validate(p,items,W,H):
    old_validate(p,items,W,H)
    if p['type']!='stacked_nonexact':return
    y=0
    for row in p['rows']:
        assert row['y']==y;x=0
        for col in row['columns']:
            assert col['x']==x;cy=y
            for it in col['items']:
                assert it['x']==x and it['y']==cy and it['w']<=col['width'];cy+=it['h']
            assert cy<=y+row['height'];x+=col['width']
        assert x<=W;y+=row['height']
    assert y<=H
    # H strips -> V columns -> H item segments -> one final V trim per item.
    # The final item/waste trim is permitted by the unchanged NonExact cut type.

base.pattern=stacked_pattern;base.validate_pattern=validate

def main():
    names=json.loads(pathlib.Path('/content/hard-cases.json').read_text())['instances'];rows=[]
    (base.OUT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'repeats':1,'scope':'development screening; same master and schedule as pattern pool v1; 75% stacked patterns, 25% shelf patterns','cut_rule_reference':'packingsolver/doc/internals/rectangleguillotine/column_generation_strips.rst: last-stage stacks may have narrower items with right waste trimmed for non-exact; solution.cpp permits extra item/waste trim','quality_only':True},indent=2))
    tiny=[(6,4),(4,6),(5,5)]
    for seed in range(20):validate(stacked_pattern(tiny,10,10,random.Random(seed)),tiny,10,10)
    jobs=[(n,m) for n in names for m in ('default','pool')];random.Random(771).shuffle(jobs)
    for i,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/pattern_pool_v2_colab.py','worker',n,m,'1'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(base.OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('POOL_V2',i+1,len(jobs),json.dumps(row),flush=True)
    wins=losses=ties=0;diff=[]
    for r in rows:
        if r['method']!='pool':continue
        b=next(b for b in rows if b['instance']==r['instance'] and b['method']=='default');wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins']
        if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'default':b['bins'],'pool':r['bins']})
    summary={'wins':wins,'losses':losses,'ties':ties,'differences':diff,'total_bins':{m:sum(r['bins'] for r in rows if r['method']==m) for m in ('default','pool')}}
    (base.OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(base.q.pj.ROOT/'pattern-pool-v2-results'),'zip',base.OUT);print('POOL_V2_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(base.run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
