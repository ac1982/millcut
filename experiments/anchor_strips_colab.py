"""Colab-only generated-strip exact cover with simultaneous sheet assignment."""
import sys,pathlib,json,csv,time,random,collections,subprocess,shutil,math
sys.path.insert(0,'/content');import quality_colab as q
import pattern_pool_colab as pp
sys.path.insert(0,str(q.pj.ROOT));import packingsolver.rectangleguillotine as ps
from ortools.sat.python import cp_model
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix
ROOT=q.pj.ROOT/'anchor';q.pj.DATA=q.pj.ROOT/'berkey1987';OUT=ROOT/'results';OUT.mkdir(parents=True,exist_ok=True);q.ps=ps

def candidate(items,W,H,rng,anchored):
    heights={h for w,h in items}
    height=rng.choice(sorted(heights));cols=[]
    rho=rng.choice([.7,.85,.95,1]);ids=[i for i,(w,h) in enumerate(items) if rho*height<=h<=height]
    if anchored:
        leader=rng.choice([i for i in ids if items[i][1]==height])
        chosen=[leader]+pp.subset_width([i for i in ids if i!=leader],items,W-items[leader][0],rng)
    else:
        chosen=pp.subset_width(ids,items,W,rng)
    cols=[(items[i][0],[i]) for i in chosen]
    if not cols:return None
    height=max(sum(items[i][1] for i in ids) for w,ids in cols);x=0;nodes=[]
    for w,ids in cols:
        y=0
        for i in ids:
            iw,ih=items[i];nodes.append({'id':i,'x':x,'y':y,'w':iw,'h':ih});y+=ih
        x+=w
    ids=tuple(sorted(n['id'] for n in nodes));area=sum(items[i][0]*items[i][1] for i in ids)
    return {'ids':ids,'height':height,'area':area,'waste':W*height-area,'nodes':nodes}

def run(name,method,seed,budget=10):
    start=time.perf_counter();deadline=start+budget;inst=q.make(name);out=q.optimize(inst,budget if method=='default' else 2);q.pj.validate(inst,out.solution)
    items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())];assert all(inst.item_type(i).copies==1 for i in range(len(items)))
    W,H=inst.bin_type(0).w,inst.bin_type(0).h;N=len(items);base=out.solution.number_of_bins();target=base-1;bound=out.bin_packing_bound;patterns={};status='BASELINE';solution=None;retained=0
    if method in ('shelves','anchored') and base>bound:
        rng=random.Random(seed);heights=sorted({h for w,h in items});totalarea=sum(w*h for w,h in items);waste_budget=target*W*H-totalarea
        generate_until=start+min(4,budget*.4)
        while time.perf_counter()<generate_until:
            p=candidate(items,W,H,rng,method=='anchored')
            if p is None or p['waste']>waste_budget:continue
            key=p['ids']
            if key not in patterns or p['height']<patterns[key]['height']:patterns[key]=p
        # Limit master size; retain efficient strips plus per-item alternatives.
        ranked=list(patterns.values())
        ri=[];ci=[]
        for j,p in enumerate(ranked):
            for i in p['ids']:ri.append(i);ci.append(j)
        matrix=coo_matrix((np.ones(len(ri)),(ri,ci)),shape=(N,len(ranked))).tocsc()
        lp=linprog(np.array([p['height'] for p in ranked],dtype=float),A_eq=matrix,b_eq=np.ones(N),bounds=(0,1),method='highs',options={'time_limit':max(.05,min(2,deadline-time.perf_counter()-2))})
        if lp.x is not None and lp.lower.marginals is not None:
            priorities=sorted(range(len(ranked)),key=lambda j:(lp.lower.marginals[j],-lp.x[j]))
            selected={ranked[j]['ids']:ranked[j] for j in range(len(ranked)) if lp.x[j]>1e-7}
            ranked=[ranked[j] for j in priorities]
        else:
            selected={};ranked.sort(key=lambda p:(p['waste']/p['area'],-p['area']))
        selected.update({p['ids']:p for p in ranked[:400]})
        for i in range(N):
            for p in [p for p in ranked if i in p['ids']][:8]:selected[p['ids']]=p
        pats=list(selected.values());retained=len(pats)
        model=cp_model.CpModel();z={(j,b):model.new_bool_var(f'z{j}_{b}') for j in range(len(pats)) for b in range(target)}
        for j in range(len(pats)):model.add(sum(z[j,b] for b in range(target))<=1)
        for i in range(N):model.add(sum(z[j,b] for j,p in enumerate(pats) if i in p['ids'] for b in range(target))==1)
        loads=[]
        for b in range(target):
            load=sum(p['height']*z[j,b] for j,p in enumerate(pats));model.add(load<=H);loads.append(load)
        for b in range(target-1):model.add(loads[b]>=loads[b+1])
        model.add(sum(p['waste']*z[j,b] for j,p in enumerate(pats) for b in range(target))<=waste_budget)
        solver=cp_model.CpSolver();solver.parameters.num_search_workers=2;solver.parameters.random_seed=seed;solver.parameters.max_time_in_seconds=max(.001,deadline-time.perf_counter())
        if time.perf_counter()<deadline:
            code=solver.solve(model);status=solver.status_name(code)
            if code in (cp_model.FEASIBLE,cp_model.OPTIMAL):
                solution=[];counts=collections.Counter()
                for b in range(target):
                    y=0;strips=[]
                    for j,p in enumerate(pats):
                        if not solver.value(z[j,b]):continue
                        nodes=[dict(n,y=n['y']+y) for n in p['nodes']]
                        for n in nodes:counts[n['id']]+=1
                        strips.append({'y':y,'height':p['height'],'items':nodes});y+=p['height']
                    assert y<=H
                    if strips:solution.append(strips)
                assert counts==collections.Counter(range(N))
    elapsed=time.perf_counter()-start;tag=f'{name}_{method}_{seed}_{budget}'
    if solution is None:out.solution.write(str(OUT/f'{tag}.csv'));bins=base
    else:(OUT/f'{tag}-strips.json').write_text(json.dumps(solution));bins=len(solution)
    return {'instance':name,'method':method,'seed':seed,'budget_s':budget,'initial_bins':base,'bins':bins,'bound':bound,'generated_strips':len(patterns),'retained_strips':retained,'status':status,'wall_s':elapsed,'valid':True}

def main():
    names=[r['name'] for r in json.loads((ROOT/'protocol.json').read_text())['instances']]
    (OUT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'scope':'development planted instances; no witness read by solver','method':'2s baseline; width-subset-sum strip generation until 4s; density/pr-item pruning; exact cover + bin-height assignment in remaining time','repeats':1,'known_optimum':3},indent=2))
    jobs=[(n,m) for n in names for m in ('default','strips')];random.Random(912).shuffle(jobs);rows=[]
    for i,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/strip_lp_colab.py','worker',n,m,'4','10'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('STRIPS',i+1,len(jobs),json.dumps(row),flush=True)
    summary={m:{'total_bins':sum(r['bins'] for r in rows if r['method']==m),'optimal_instances':sum(r['bins']==3 for r in rows if r['method']==m),'runs':sum(r['method']==m for r in rows)} for m in ('default','strips')}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(q.pj.ROOT/'strip-lp-results'),'zip',OUT);print('STRIP_LP_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(run(sys.argv[2],sys.argv[3],int(sys.argv[4]),float(sys.argv[5]))))
    else:main()
