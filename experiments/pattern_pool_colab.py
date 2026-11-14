"""Colab-only pattern-pool integer covering; quality comparison at 10 seconds."""
import sys,pathlib,json,time,random,math,collections,subprocess,shutil
sys.path.insert(0,'/content');import quality_colab as q
sys.path.insert(0,str(q.pj.ROOT));import packingsolver.rectangleguillotine as ps
from ortools.sat.python import cp_model
OUT=q.pj.ROOT/'pattern-pool';OUT.mkdir(exist_ok=True);q.ps=ps

def subset_width(ids,items,W,rng):
    ids=ids[:];rng.shuffle(ids);states=[1];bits=1;mask=(1<<(W+1))-1
    for i in ids:bits=(bits|(bits<<items[i][0]))&mask;states.append(bits)
    goal=bits.bit_length()-1;chosen=[]
    for j in range(len(ids)-1,-1,-1):
        if (states[j]>>goal)&1:continue
        chosen.append(ids[j]);goal-=items[ids[j]][0]
    assert goal==0
    return chosen

def pattern(items,W,H,rng):
    remaining=set(range(len(items)));y=0;placements=[];rows=[]
    weights=[math.exp(rng.uniform(-1,1)) for _ in items]
    while remaining:
        heights=sorted({items[i][1] for i in remaining if items[i][1]<=H-y})
        if not heights:break
        if len(heights)>10:heights=rng.sample(heights,10)
        rho=rng.choice([.5,.75,.9,1]);candidates=[]
        for h in heights:
            eligible=[i for i in remaining if rho*h<=items[i][1]<=h]
            chosen=subset_width(eligible,items,W,rng)
            if not chosen:continue
            height=max(items[i][1] for i in chosen)
            score=sum(items[i][0]*items[i][1]*weights[i] for i in chosen)/height
            candidates.append((score,chosen,height))
        if not candidates:break
        _,chosen,height=max(candidates,key=lambda c:c[0]);x=0;row=[]
        for i in chosen:
            w,h=items[i];p={'id':i,'x':x,'y':y,'w':w,'h':h};placements.append(p);row.append(p);x+=w;remaining.remove(i)
        rows.append({'y':y,'height':height,'items':row});y+=height
    return {'ids':sorted(p['id'] for p in placements),'placements':placements,'type':'shelf','rows':rows}

def validate_pattern(p,items,W,H):
    assert len(p['ids'])==len(set(p['ids']))
    assert sorted(n['id'] for n in p['placements'])==p['ids']
    for a in p['placements']:
        assert (a['w'],a['h'])==items[a['id']]
        assert 0<=a['x']<a['x']+a['w']<=W and 0<=a['y']<a['y']+a['h']<=H
        for b in p['placements']:
            if a['id']>=b['id']:continue
            assert a['x']+a['w']<=b['x'] or b['x']+b['w']<=a['x'] or a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y']
    if p['type']=='shelf':
        y=0
        for r in p['rows']:
            assert r['y']==y;x=0
            for a in r['items']:
                assert a['x']==x and a['y']==y and a['h']<=r['height'];x+=a['w']
            assert x<=W;y+=r['height']
        assert y<=H


def run(name,method,seed):
    start=time.perf_counter();deadline=start+10;inst=q.make(name);initial=q.optimize(inst,10 if method=='default' else 2);q.pj.validate(inst,initial.solution)
    items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())]
    assert all(inst.item_type(i).copies==1 for i in range(inst.number_of_item_types()))
    W,H=inst.bin_type(0).w,inst.bin_type(0).h;patterns=[]
    for bp in range(initial.solution.number_of_different_bins()):
        b=initial.solution.bin(bp);assert b.copies==1
        placements=[{'id':n.item_type_id,'x':n.l,'y':n.b,'w':n.r-n.l,'h':n.t-n.b} for n in b.nodes if n.f!=-1 and n.item_type_id>=0]
        nodes=[{'parent':n.f,'depth':n.d,'left':n.l,'right':n.r,'bottom':n.b,'top':n.t,'item':n.item_type_id} for n in b.nodes]
        patterns.append({'ids':sorted(a['id'] for a in placements),'placements':placements,'type':'validated_solver','nodes':nodes})
    base=len(patterns);bound=initial.bin_packing_bound;selected=list(range(base));status='BASELINE';gen_s=0
    if method=='pool' and base>bound:
        rng=random.Random(seed);seen={tuple(p['ids']) for p in patterns};genstart=time.perf_counter()
        while time.perf_counter()<start+6:
            p=pattern(items,W,H,rng)
            key=tuple(p['ids'])
            if key and key not in seen:validate_pattern(p,items,W,H);patterns.append(p);seen.add(key)
        gen_s=time.perf_counter()-genstart
        model=cp_model.CpModel();use=[model.new_bool_var(f'p{i}') for i in range(len(patterns))]
        for i in range(len(items)):model.add(sum(use[j] for j,p in enumerate(patterns) if i in p['ids'])>=1)
        model.add(sum(use)>=bound);model.add(sum(use)<=base);model.minimize(sum(use))
        for j,v in enumerate(use):model.add_hint(v,int(j<base))
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=max(.001,deadline-time.perf_counter());solver.parameters.num_search_workers=2;solver.parameters.random_seed=seed
        if time.perf_counter()<deadline:
            code=solver.solve(model);status=solver.status_name(code)
            if code in (cp_model.FEASIBLE,cp_model.OPTIMAL):selected=[j for j,v in enumerate(use) if solver.value(v)]
    # A selected pattern may cover an item already assigned to another plate.
    # Replace that item by waste: its existing guillotine cuts remain valid.
    assigned=set();plates=[]
    for j in selected:
        p=patterns[j];validate_pattern(p,items,W,H);keep=[i for i in p['ids'] if i not in assigned]
        if not keep:continue
        assigned.update(keep);plates.append({'pattern':p,'keep_item_ids':keep,'removed_items_become_waste':True})
    assert assigned==set(range(len(items)))
    elapsed=time.perf_counter()-start
    (OUT/f'{name}_{method}_{seed}.json').write_text(json.dumps({'plates':plates,'validation':'Each full pattern has a valid constructive or solver-checked cut tree; removed duplicate items become waste; retained items exactly cover demand.'}))
    return {'instance':name,'method':method,'seed':seed,'initial_bins':base,'bins':len(plates),'bound':bound,'patterns':len(patterns),'master_status':status,'generation_s':gen_s,'wall_s':elapsed,'valid':True}

def main():
    names=json.loads(pathlib.Path('/content/hard-cases.json').read_text())['instances'];rows=[]
    (OUT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'methods':['default','pool'],'pool_schedule':'2s default; generate shelf patterns until 6s; remaining time CP-SAT set cover; validation and model construction included','repeats':1,'scope':'development screening; patterns from the baseline plus width-subset-sum shelf construction; keep only complete validated outputs','cut_rules':'unchanged 3-stage non-exact, horizontal first, no rotation, zero kerf; replacing unused items by waste preserves cuts'},indent=2))
    # Exact-width subset reconstruction and independent layout checks.
    rng=random.Random(1);tiny=[(6,4),(4,6),(5,5)]
    picked=subset_width(list(range(3)),tiny,10,rng);assert sum(tiny[i][0] for i in picked)==10
    for seed in range(10):validate_pattern(pattern(tiny,10,10,random.Random(seed)),tiny,10,10)
    jobs=[(n,m) for n in names for m in ('default','pool')];random.Random(761).shuffle(jobs)
    for idx,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/pattern_pool_colab.py','worker',n,m,'1'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('POOL',idx+1,len(jobs),json.dumps(row),flush=True)
    wins=losses=ties=0;diff=[]
    for r in rows:
        if r['method']!='pool':continue
        b=next(b for b in rows if b['instance']==r['instance'] and b['method']=='default');wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins']
        if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'default':b['bins'],'pool':r['bins']})
    summary={'wins':wins,'losses':losses,'ties':ties,'differences':diff,'total_bins':{m:sum(r['bins'] for r in rows if r['method']==m) for m in ('default','pool')}}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(q.pj.ROOT/'pattern-pool-results'),'zip',OUT);print('POOL_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
