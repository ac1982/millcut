"""Colab-only mechanism audit; longer diagnostic budgets are not performance results."""
import sys,time,json,pathlib,collections,random
sys.path.insert(0,'/content');import detail_strips_colab as a
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix
from ortools.sat.python import cp_model

def check(pats,N,H,target,seconds):
    start=time.perf_counter();model=cp_model.CpModel();z={(j,b):model.new_bool_var(f'z{j}_{b}') for j in range(len(pats)) for b in range(target)}
    for j in range(len(pats)):model.add(sum(z[j,b] for b in range(target))<=1)
    for i in range(N):model.add(sum(z[j,b] for j,p in enumerate(pats) if i in p['ids'] for b in range(target))==1)
    for b in range(target):model.add(sum(p['height']*z[j,b] for j,p in enumerate(pats))<=H)
    solver=cp_model.CpSolver();solver.parameters.num_search_workers=2;solver.parameters.random_seed=42;solver.parameters.max_time_in_seconds=max(.001,seconds-(time.perf_counter()-start));code=solver.solve(model)
    return {'status':solver.status_name(code),'seconds':time.perf_counter()-start,'columns':len(pats)}

name='planted_3_5_6_1';inst=a.q.make(name);items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())];N=len(items);W,H=1000,1000;waste_budget=3*W*H-sum(w*h for w,h in items);rng=random.Random(42);pool={}
for _ in range(20000):
    p=a.candidate(items,W,H,rng,False)
    if p and p['waste']<=waste_budget:pool[p['ids']]=p
pats=list(pool.values());ri=[];ci=[]
for j,p in enumerate(pats):
    for i in p['ids']:ri.append(i);ci.append(j)
matrix=coo_matrix((np.ones(len(ri)),(ri,ci)),shape=(N,len(pats))).tocsc();lp=linprog([p['height'] for p in pats],A_eq=matrix,b_eq=np.ones(N),bounds=(0,1),method='highs');assert lp.success
order=sorted(range(len(pats)),key=lambda j:(lp.lower.marginals[j],-lp.x[j]));selected={pats[j]['ids']:pats[j] for j in range(len(pats)) if lp.x[j]>1e-7};ranked=[pats[j] for j in order];selected.update({p['ids']:p for p in ranked[:400]})
for i in range(N):
    for p in [p for p in ranked if i in p['ids']][:8]:selected[p['ids']]=p
# Witness loaded AFTER candidate generation and selection; used only to audit coverage.
witness=json.loads(pathlib.Path('/content/planted-witness.json').read_text());groups=collections.defaultdict(list)
for r in witness:groups[r['plate'],r['y']].append(r['id'])
keys=[tuple(sorted(ids)) for ids in groups.values()]
report={'instance':name,'attempts':20000,'seed':42,'full_pool':len(pool),'retained_pool':len(selected),'witness_rows':len(keys),'witness_rows_in_full':sum(k in pool for k in keys),'witness_rows_in_retained':sum(k in selected for k in keys),'restricted_lp_total_height':lp.fun,'scope':'diagnostic only; fixed attempts and 30s CP budgets; witness never supplied to optimization'}
print('POOL_COVERAGE',json.dumps(report),flush=True)
report['retained_master']=check(list(selected.values()),N,H,3,30);print('RETAINED',json.dumps(report['retained_master']),flush=True)
report['full_master']=check(pats,N,H,3,30);print('FULL',json.dumps(report['full_master']),flush=True)
(a.ROOT/'pool-audit.json').write_text(json.dumps(report,indent=2))
