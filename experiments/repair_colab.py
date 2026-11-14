"""Colab-only large-neighborhood repair: replace k existing plates by at most k-1."""
import sys,pathlib,json,time,random,collections,itertools,shutil
sys.path.insert(0,'/content');import portfolio_colab as pj
sys.path.insert(0,str(pj.ROOT));import packingsolver.rectangleguillotine as ps
pj.ps=ps
ROOT=pj.ROOT;OUT=ROOT/'repair';OUT.mkdir(exist_ok=True)

def plates(sol,mapping):
    result=[]
    for bpos in range(sol.number_of_different_bins()):
        b=sol.bin(bpos)
        items=[mapping[n.item_type_id] for n in b.nodes if n.f!=-1 and n.item_type_id>=0]
        nodes=[{'parent':n.f,'depth':n.d,'left':n.l,'right':n.r,'bottom':n.b,'top':n.t,'item':mapping[n.item_type_id] if n.f!=-1 and n.item_type_id>=0 else n.item_type_id} for n in b.nodes]
        for _ in range(b.copies):result.append({'items':items,'nodes':nodes})
    return result

def check_global(inst,pp):
    counts=collections.Counter(i for p in pp for i in p['items'])
    assert counts==collections.Counter({i:inst.item_type(i).copies for i in range(inst.number_of_item_types())})
    for p in pp:
        ns=[n for n in p['nodes'] if n['parent']!=-1 and n['item']>=0]
        for a in ns:
            it=inst.item_type(a['item'])
            assert a['right']-a['left']==it.w and a['top']-a['bottom']==it.h
            assert 0<=a['left']<a['right']<=inst.bin_type(0).w and 0<=a['bottom']<a['top']<=inst.bin_type(0).h
        for i,a in enumerate(ns):
            for b in ns[i+1:]:assert a['right']<=b['left'] or b['right']<=a['left'] or a['top']<=b['bottom'] or b['top']<=a['bottom']


def run(name,method,repeat):
    start=time.perf_counter();deadline=start+10;inst=pj.instance(name)
    p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=2 if method=='repair' else max(.001,deadline-time.perf_counter())
    initial=ps.optimize(inst,p);pj.validate(inst,initial.solution)
    pp=plates(initial.solution,list(range(inst.number_of_item_types())))
    initial_bins=len(pp);bound=initial.bin_packing_bound;attempts=success=0
    rng=random.Random(800+repeat);seen=set();binarea=inst.bin_type(0).area()
    if method=='repair':
        while time.perf_counter()<deadline-.03 and len(pp)>bound:
            areas=[sum(inst.item_type(i).area() for i in p['items']) for p in pp]
            order=sorted(range(len(pp)),key=lambda j:areas[j]);groups=[]
            for k in range(2,min(7,len(pp)+1)):
                groups.append(tuple(sorted(order[:k])))
                for _ in range(15):groups.append(tuple(sorted(rng.sample(range(len(pp)),k))))
            groups=[g for g in dict.fromkeys(groups) if sum(areas[i] for i in g)<=(len(g)-1)*binarea and g not in seen]
            if not groups:break
            # Prioritize smaller neighborhoods and greater combined free area.
            groups.sort(key=lambda g:(len(g),sum(areas[i] for i in g)))
            g=groups[0];seen.add(g);counts=collections.Counter(i for idx in g for i in pp[idx]['items']);mapping=sorted(counts)
            b=ps.InstanceBuilder();b.set_objective(ps.Objective.BinPacking)
            bt=inst.bin_type(0);b.add_bin_type(bt.w,bt.h,copies=len(g)-1)
            b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_cut_thickness(0)
            for i in mapping:
                it=inst.item_type(i);b.add_item_type(it.w,it.h,copies=counts[i],oriented=True)
            sub=b.build();params=ps.OptimizeParameters();params.verbosity_level=0;params.use_tree_search=True;params.tree_search_guides=[[1],[0],[0,1]][attempts%3]
            params.time_limit=max(.001,min(.5,deadline-time.perf_counter()));attempts+=1
            out=ps.optimize(sub,params)
            if out.solution.full() and out.solution.feasible() and out.solution.number_of_bins()<len(g):
                pj.validate(sub,out.solution)
                new=plates(out.solution,mapping)
                pp=[p for idx,p in enumerate(pp) if idx not in g]+new;success+=1;seen=set()
    elapsed=time.perf_counter()-start;check_global(inst,pp)
    (OUT/f'{name}_{method}_{repeat}.json').write_text(json.dumps({'plates':pp,'note':'Each plate comes from a solver-validated three-stage subsolution; global counts and geometry independently checked.'}))
    return {'instance':name,'method':method,'repeat':repeat,'initial_bins':initial_bins,'bins':len(pp),'bound':bound,'wall_s':elapsed,'attempts':attempts,'successful_repairs':success,'valid':True}

rows=json.loads((ROOT/'results/rows.json').read_text())
names=sorted({r['instance'] for r in rows if r['config']=='default' and r['valid'] and r['bins']>r['bound']})
(OUT/'protocol.json').write_text(json.dumps({'instances':names,'selection':'development cases whose default 2-second result exceeded its bound','methods':{'default':'10 seconds default','repair':'2 seconds default + up to 8 seconds neighborhood repairs'},'repeats':2,'neighborhood':'2 to 6 plates, area filter, at most k-1 replacement plates; up to 0.5s per subproblem','selection_warning':'development-only experiment, not fresh test'},indent=2))
jobs=[(n,m,r) for n in names for m in ('default','repair') for r in range(2)];random.Random(381).shuffle(jobs);results=[]
for idx,(n,m,r) in enumerate(jobs):
    row=run(n,m,r);results.append(row);(OUT/'rows.json').write_text(json.dumps(results,indent=2));print('REPAIR',idx+1,len(jobs),json.dumps(row),flush=True)
summary={}
for m in ('default','repair'):
    rr=[r for r in results if r['method']==m];wins=losses=ties=0;diff=[]
    for r in rr:
        b=next(b for b in results if b['method']=='default' and b['instance']==r['instance'] and b['repeat']==r['repeat'])
        wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins']
        if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'repeat':r['repeat'],'default':b['bins'],'repair':r['bins']})
    summary[m]={'runs':len(rr),'total_bins':sum(r['bins'] for r in rr),'mean_wall_s':sum(r['wall_s'] for r in rr)/len(rr),'wins':wins,'losses':losses,'ties':ties,'successful_repairs':sum(r['successful_repairs'] for r in rr),'differences':diff}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(ROOT/'repair-results'),'zip',OUT)
print('REPAIR_COMPLETE',json.dumps(summary),flush=True)
