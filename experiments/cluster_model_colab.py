"""Colab-only quality-first comparison at a matched 10-second budget."""
import sys,pathlib,json,csv,time,random,collections,subprocess,shutil,importlib.metadata
sys.path.insert(0,'/content');import portfolio_colab as pj
sys.path.insert(0,str(pj.ROOT));OUT=pj.ROOT/'quality';OUT.mkdir(parents=True,exist_ok=True)

def configure(b):
    b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_item_types_oriented(True);b.set_cut_thickness(0)

def make(name,target=None):
    b=ps.InstanceBuilder()
    if target is None:b.read_bin_types(str(pj.DATA/f'{name}_bins.csv'));b.set_bin_types_infinite_copies();b.set_objective(ps.Objective.BinPacking)
    else:
        with (pj.DATA/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
        b.add_bin_type(int(bt['WIDTH']),int(bt['HEIGHT']),copies=target);b.set_objective(ps.Objective.Feasibility)
    b.read_item_types(str(pj.DATA/f'{name}_items.csv'));configure(b)
    return b.build()

def optimize(inst,seconds,guide=None):
    p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(.001,seconds)
    if guide is not None:p.use_tree_search=True;p.tree_search_guides=guide
    return ps.optimize(inst,p)

def cp_shelves(inst,target,deadline,seed,diagnostic=None):
    # One shelf has a maximum-height item as its leader. Every item is assigned
    # to one such shelf, shelves are assigned to target sheets.
    from ortools.sat.python import cp_model
    items=[]
    for i in range(inst.number_of_item_types()):
        it=inst.item_type(i)
        for _ in range(it.copies):items.append((i,it.w,it.h))
    items.sort(key=lambda it:(-it[2],-it[1],it[0]));N=len(items);W,H=inst.bin_type(0).w,inst.bin_type(0).h
    model=cp_model.CpModel();a={};z={}
    for i in range(N):
        for j in range(i+1):
            if i==j or (items[i][1]+items[j][1]<=W and 100*items[i][2]>=85*items[j][2]):a[i,j]=model.new_bool_var(f'a{i}_{j}')
        model.add_exactly_one(a[i,j] for j in range(i+1) if (i,j) in a)
    for j in range(N):
        model.add(sum(items[i][1]*a[i,j] for i in range(j,N) if (i,j) in a)<=W*a[j,j])
        for k in range(target):z[j,k]=model.new_bool_var(f'z{j}_{k}')
        model.add(sum(z[j,k] for k in range(target))==a[j,j])
    heights=[]
    for k in range(target):
        load=sum(items[j][2]*z[j,k] for j in range(N));model.add(load<=H);heights.append(load)
    for k in range(target-1):model.add(heights[k]>=heights[k+1])
    if diagnostic is not None:diagnostic(model,a,z,items)
    solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=max(.001,deadline-time.perf_counter());solver.parameters.num_search_workers=2;solver.parameters.random_seed=seed
    if time.perf_counter()>=deadline:return None,'BUILD_BUDGET_EXHAUSTED'
    status=solver.solve(model)
    if status not in (cp_model.FEASIBLE,cp_model.OPTIMAL):return None,solver.status_name(status)
    bins=[]
    for k in range(target):
        y=0;rows=[]
        for j in range(N):
            if not solver.value(z[j,k]):continue
            x=0;placements=[]
            for i in range(j,N):
                if (i,j) in a and solver.value(a[i,j]):
                    orig,w,h=items[i];placements.append({'id':orig,'x':x,'y':y,'w':w,'h':h});x+=w
            rows.append({'y':y,'height':items[j][2],'items':placements});y+=items[j][2]
        if rows:bins.append({'rows':rows})
    # Independent three-stage constructive verification.
    counts=collections.Counter()
    for bin in bins:
        y=0
        for row in bin['rows']:
            assert row['y']==y; x=0
            for it in row['items']:
                orig=inst.item_type(it['id']);assert (it['w'],it['h'])==(orig.w,orig.h)
                assert it['x']==x and it['y']==y and it['h']<=row['height'];x+=it['w'];counts[it['id']]+=1
            assert x<=W;y+=row['height']
        assert y<=H
    assert counts==collections.Counter({i:inst.item_type(i).copies for i in range(inst.number_of_item_types())})
    return bins,solver.status_name(status)

def run(name,method,seed):
    global ps
    import packingsolver.rectangleguillotine as ps
    if method=='cp_shelves':
        from ortools.sat.python import cp_model
    start=time.perf_counter();deadline=start+10;inst=make(name);initial=optimize(inst,(deadline-time.perf_counter()) if method=='default' else 2)
    pj.validate(inst,initial.solution);best=initial;best_bins=best.solution.number_of_bins();initial_bins=best_bins;bound=initial.bin_packing_bound;events=[];shelves=None
    if method=='target' and best_bins>bound:
        while best_bins>bound and time.perf_counter()<deadline-.03:
            sub=make(name,best_bins-1);out=optimize(sub,deadline-time.perf_counter(),[0,1]);ok=out.solution.full() and out.solution.feasible();events.append({'target':best_bins-1,'complete':ok})
            if not ok:break
            pj.validate(sub,out.solution);best=out;best_bins=out.solution.number_of_bins()
    elif method=='portfolio' and best_bins>bound:
        for guide in ([6],[1],[0]):
            if time.perf_counter()>=deadline-.03 or best_bins==bound:break
            seconds=(deadline-time.perf_counter())/(3-len(events));out=optimize(inst,seconds,guide)
            ok=out.solution.full() and out.solution.feasible();events.append({'guide':guide,'complete':ok,'bins':out.solution.number_of_bins() if ok else None})
            if ok and out.solution.number_of_bins()<best_bins:pj.validate(inst,out.solution);best=out;best_bins=out.solution.number_of_bins()
    elif method=='cp_shelves' and best_bins>bound:
        while best_bins>bound and time.perf_counter()<deadline-.03:
            candidate,status=cp_shelves(inst,best_bins-1,deadline,seed);events.append({'target':best_bins-1,'status':status})
            if candidate is None:break
            shelves=candidate;best_bins=len(candidate)
    elapsed=time.perf_counter()-start;tag=f'{name}_{method}_{seed}'
    if shelves is None:pj.validate(inst,best.solution);best.solution.write(str(OUT/f'{tag}.csv'))
    else:(OUT/f'{tag}-shelves.json').write_text(json.dumps(shelves))
    return {'instance':name,'method':method,'seed':seed,'initial_bins':initial_bins,'bins':best_bins,'bound':bound,'valid':True,'wall_s':elapsed,'events':events}

def main():
    pj.setup()
    proc=subprocess.run([sys.executable,'-m','pip','install','ortools'],capture_output=True,text=True)
    if proc.returncode:raise RuntimeError(proc.stderr[-2000:])
    names=json.loads(pathlib.Path('/content/hard-cases.json').read_text())['instances']
    (OUT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'methods':['default','target','portfolio','cp_shelves'],'scope':'14 development cases with unresolved baseline bounds, one run per method for screening','cp_sat_version':importlib.metadata.version('ortools'),'rules':'3-stage non-exact guillotine; horizontal first; no rotation; zero kerf','timing':'includes all instance/model construction, initial solve and subsequent searches; excludes import/process startup and certificate serialization','cp_warning':'shelf model is a restricted feasible subset; INFEASIBLE does not prove full guillotine instance infeasible'},indent=2))
    # Verify shelf model on known feasible and impossible small cases in Colab.
    global ps
    import packingsolver.rectangleguillotine as ps
    b=ps.InstanceBuilder();b.add_bin_type(10,10,copies=4);b.add_item_type(5,5,copies=4,oriented=True);configure(b);tiny=b.build()
    cert,status=cp_shelves(tiny,1,time.perf_counter()+5,1);assert cert is not None and len(cert)==1
    b=ps.InstanceBuilder();b.add_bin_type(10,10,copies=2);b.add_item_type(6,6,copies=2,oriented=True);configure(b);bad=b.build()
    cert,status=cp_shelves(bad,1,time.perf_counter()+5,1);assert cert is None and status=='INFEASIBLE'
    (OUT/'model-checks.json').write_text(json.dumps({'four_squares_one_sheet':True,'two_6x6_do_not_fit_10x10':True}))
    jobs=[(n,m) for n in names for m in ('default','target','portfolio','cp_shelves')];random.Random(215).shuffle(jobs);rows=[]
    for idx,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/quality_colab.py','worker',n,m,'1'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('QUALITY',idx+1,len(jobs),json.dumps(row),flush=True)
    summary={}
    for m in ('default','target','portfolio','cp_shelves'):
        rr=[r for r in rows if r['method']==m];wins=losses=ties=0;diff=[]
        for r in rr:
            b=next(b for b in rows if b['method']=='default' and b['instance']==r['instance']);wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins']
            if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'default':b['bins'],'candidate':r['bins']})
        summary[m]={'runs':len(rr),'total_bins':sum(r['bins'] for r in rr),'wins':wins,'losses':losses,'ties':ties,'differences':diff}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(pj.ROOT/'quality-results'),'zip',OUT);print('QUALITY_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
