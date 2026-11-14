"""Colab-only compatible-subset selection and shared integrated master."""
import sys,time,json,random,collections,pathlib
sys.path.insert(0,'/content');import anchor_strips_colab as base
from ortools.sat.python import cp_model
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix
from detail_ablation_colab import validate
ROOT=base.q.pj.ROOT/'selection';ROOT.mkdir(exist_ok=True)

def generate(items,W,H,target,seed,attempts=None,deadline=None):
    rng=random.Random(seed);pool={};waste=target*W*H-sum(w*h for w,h in items);i=0
    while (attempts is None or i<attempts) and (deadline is None or time.perf_counter()<deadline):
        i+=1;p=base.candidate(items,W,H,rng,False)
        if p and p['waste']<=waste:pool[p['ids']]=p
    return list(pool.values())

def ranked_subset(pats,N,deadline,with_remainder=False):
    if not pats:return ([],[]) if with_remainder else []
    ri=[];ci=[]
    for j,p in enumerate(pats):
        for i in p['ids']:ri.append(i);ci.append(j)
    A=coo_matrix((np.ones(len(ri)),(ri,ci)),shape=(N,len(pats))).tocsc()
    lp=linprog([p['height'] for p in pats],A_eq=A,b_eq=np.ones(N),bounds=(0,1),method='highs',options={'time_limit':max(.001,min(2,deadline-time.perf_counter()))})
    if lp.x is not None and lp.lower.marginals is not None:
        order=sorted(range(len(pats)),key=lambda j:(lp.lower.marginals[j],-lp.x[j]));selected={j for j in range(len(pats)) if lp.x[j]>1e-7}
    else:order=sorted(range(len(pats)),key=lambda j:pats[j]['waste']/pats[j]['area']);selected=set()
    selected.update(order[:400])
    for i in range(N):selected.update([j for j in order if i in pats[j]['ids']][:8])
    chosen=[pats[j] for j in order if j in selected]
    if with_remainder:return chosen,[pats[j] for j in order if j not in selected]
    return chosen

def master(pats,N,H,target,deadline,seed):
    model=cp_model.CpModel();z={(j,b):model.new_bool_var(f'z{j}_{b}') for j in range(len(pats)) for b in range(target)}
    covers=[[] for _ in range(N)]
    for j,p in enumerate(pats):
        model.add(sum(z[j,b] for b in range(target))<=1)
        for i in p['ids']:covers[i].extend(z[j,b] for b in range(target))
    for vs in covers:model.add(sum(vs)==1)
    loads=[]
    for b in range(target):
        load=sum(p['height']*z[j,b] for j,p in enumerate(pats));model.add(load<=H);loads.append(load)
    for b in range(target-1):model.add(loads[b]>=loads[b+1])
    solver=cp_model.CpSolver();solver.parameters.num_search_workers=2;solver.parameters.random_seed=seed
    solver.parameters.max_time_in_seconds=max(.001,deadline-time.perf_counter())
    if time.perf_counter()>=deadline:return None,'BUILD_TIMEOUT'
    code=solver.solve(model)
    if code not in (cp_model.OPTIMAL,cp_model.FEASIBLE):return None,solver.status_name(code)
    return [[p for j,p in enumerate(pats) if solver.value(z[j,b])] for b in range(target)],solver.status_name(code)

def safe_subset(pats,N,H,target,deadline):
    if not pats:return [],{'status':'EMPTY'}
    ri=[];ci=[]
    for j,p in enumerate(pats):
        for i in p['ids']:ri.append(i);ci.append(j)
    A=coo_matrix((np.ones(len(ri)),(ri,ci)),shape=(N,len(pats))).tocsc()
    lp=linprog([p['height'] for p in pats],A_eq=A,b_eq=np.ones(N),bounds=(0,None),method='highs',options={'time_limit':max(.001,min(2,deadline-time.perf_counter()))})
    if not lp.success:return pats,{'status':'LP_UNAVAILABLE'}
    # Integer dual repair makes every pool-column constraint exact. For any
    # exact cover, sum reduced costs <= target*H - dual bound. Each cost >=0.
    scale=1000000;prices=[int(np.floor(float(v)*scale)) for v in lp.eqlin.marginals]
    sums=[sum(prices[i] for i in p['ids']) for p in pats]
    divisor=max([scale]+[(s+p['height']-1)//p['height'] for s,p in zip(sums,pats)])
    costs=[p['height']*divisor-s for p,s in zip(pats,sums)];assert min(costs)>=0
    gap=target*H*divisor-sum(prices)
    selected=[p for p,c in zip(pats,costs) if c<=gap]
    return selected,{'status':'EXACT_DUAL_FILTER','retained':len(selected),'dual_height_bound':sum(prices)/divisor,'gap':gap/divisor,'prices':prices,'divisor':divisor,'rule':'keep integer reduced cost <= integer capacity-minus-dual gap; no feasible target cover in this pool is removed'}

def compatible(pats,N,H,target,deadline,seed):
    # Select WHOLE exact covers first; partition only the selected strips.
    model=cp_model.CpModel();x=[model.new_bool_var(f'x{j}') for j in range(len(pats))];covers=[[] for _ in range(N)]
    for j,p in enumerate(pats):
        for i in p['ids']:covers[i].append(x[j])
    for vs in covers:model.add(sum(vs)==1)
    model.add(sum(p['height']*x[j] for j,p in enumerate(pats))<=target*H)
    model.minimize(sum(p['height']*x[j] for j,p in enumerate(pats)))
    events=[]
    for attempt in range(8):
        if time.perf_counter()>=deadline-.03:break
        solver=cp_model.CpSolver();solver.parameters.num_search_workers=2;solver.parameters.random_seed=seed+attempt;solver.parameters.stop_after_first_solution=True
        solver.parameters.max_time_in_seconds=max(.001,min(2,(deadline-time.perf_counter())*.65))
        code=solver.solve(model);event={'cover':solver.status_name(code)};events.append(event)
        if code==cp_model.INFEASIBLE:break
        if code not in (cp_model.OPTIMAL,cp_model.FEASIBLE):continue
        chosen=[j for j in range(len(pats)) if solver.value(x[j])];event['strips']=len(chosen);event['height']=sum(pats[j]['height'] for j in chosen)
        layout,status=master([pats[j] for j in chosen],N,H,target,min(deadline,time.perf_counter()+.5),seed+attempt);event['partition']=status
        if layout is not None:return layout,'FEASIBLE',events
        # This heuristic excludes only this exact selected cover; no global
        # infeasibility claim is made if a partition attempt times out.
        model.add(sum(x[j] for j in chosen)<=len(chosen)-1)
    return None,'NO_SOLUTION',events

def solve(pats,items,W,H,target,method,seconds,seed):
    items=[tuple(item) for item in items]
    start=time.perf_counter();deadline=start+seconds;events=[];used=pats
    if method=='expand':
        used,remainder=ranked_subset(pats,len(items),deadline,True)
        while True:
            layout,status=master(used,len(items),H,target,deadline,seed)
            events.append({'columns':len(used),'status':status})
            if layout is not None or status!='INFEASIBLE' or not remainder or time.perf_counter()>=deadline:break
            used.extend(remainder[:400]);remainder=remainder[400:]
    elif method=='safe':
        used,proof=safe_subset(pats,len(items),H,target,deadline);events.append(proof)
        layout,status=master(used,len(items),H,target,deadline,seed)
    elif method=='compatible':layout,status,events=compatible(used,len(items),H,target,deadline,seed)
    else:
        if method=='ranked':used=ranked_subset(pats,len(items),deadline)
        layout,status=master(used,len(items),H,target,deadline,seed)
    elapsed=time.perf_counter()-start;cert=None
    if layout is not None:
        cert=[]
        for sheet in layout:
            y=0;strips=[]
            for p in sheet:
                strips.append({'y':y,'height':p['height'],'items':[dict(n,y=n['y']+y) for n in p['nodes']]});y+=p['height']
            if strips:cert.append(strips)
        validate(cert,items,W,H)
    return {'method':method,'success':cert is not None,'status':status,'wall_s':elapsed,'pool_size':len(pats),'selected_size':len(used),'events':events},cert
