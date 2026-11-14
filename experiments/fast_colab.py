"""Colab-only certified shelf construction + default-solver fallback."""
import sys,pathlib,json,time,random,math,collections,subprocess,shutil,hashlib
sys.path.insert(0,'/content');import portfolio_colab as pj
sys.path.insert(0,str(pj.ROOT));import packingsolver.rectangleguillotine as ps
ROOT=pj.ROOT;OUT=ROOT/'fast';OUT.mkdir(exist_ok=True)

def lower_bound(items,W,H):
    return max(math.ceil(sum(w*h for w,h in items)/(W*H)),math.ceil(sum(h for w,h in items if 2*w>W)/H),math.ceil(sum(w for w,h in items if 2*h>H)/W),sum(2*w>W and 2*h>H for w,h in items))

def shelf(items,W,H,order):
    bins=[]
    for i in order:
        w,h=items[i];options=[]
        for bi,b in enumerate(bins):
            for ri,r in enumerate(b['rows']):
                if h<=r['height'] and r['used_width']+w<=W:
                    options.append(((r['height']-h)*w+(W-r['used_width']-w)*r['height'],bi,ri))
        if options:
            _,bi,ri=min(options);r=bins[bi]['rows'][ri]
        else:
            choices=[(H-b['used_height']-h,bi) for bi,b in enumerate(bins) if b['used_height']+h<=H]
            if choices:_,bi=min(choices)
            else:bi=len(bins);bins.append({'used_height':0,'rows':[]})
            b=bins[bi];r={'y':b['used_height'],'height':h,'used_width':0,'items':[]};b['used_height']+=h;b['rows'].append(r)
        r['items'].append({'id':i,'x':r['used_width'],'y':r['y'],'w':w,'h':h});r['used_width']+=w
    return bins

def verify_shelf(items,W,H,bins):
    counts=collections.Counter()
    for b in bins:
        y=0
        for row in b['rows']:
            assert row['y']==y and row['height']>0
            x=0
            for it in row['items']:
                assert (it['w'],it['h'])==tuple(items[it['id']])
                assert it['x']==x and it['y']==y and 0<it['h']<=row['height']
                x+=it['w'];counts[it['id']]+=1
            assert x==row['used_width'] and x<=W
            y+=row['height']
        assert y==b['used_height'] and y<=H
    assert counts==collections.Counter(range(len(items)))
    # Constructive certificate: horizontal strip cuts, vertical item columns,
    # then horizontal trims inside each column: at most three stages.


def run(name,method,rep):
    data=json.loads((OUT/f'{name}-input.json').read_text());items=data['items'];W=data['W'];H=data['H']
    start=time.perf_counter();lb=lower_bound(items,W,H);best=None;certified=False
    if method=='hybrid':
        keys=[lambda t:t[1],lambda t:t[0],lambda t:t[0]*t[1],lambda t:max(t),lambda t:min(t),lambda t:t[0]/t[1]]
        orders=[sorted(range(len(items)),key=lambda i:(key(items[i]),items[i][0]*items[i][1]),reverse=True) for key in keys]
        rng=random.Random(123)
        for _ in range(10):o=list(range(len(items)));rng.shuffle(o);orders.append(o)
        for order in orders:
            candidate=shelf(items,W,H,order)
            if best is None or len(candidate)<len(best):best=candidate
            if len(best)==lb:certified=True;break
            if time.perf_counter()-start>.08:break
        verify_shelf(items,W,H,best)
    solver_used=False;solver_bins=None;solution_path=None
    if not certified:
        solver_used=True;b=ps.InstanceBuilder();b.add_bin_type(W,H,copies=len(items));b.set_objective(ps.Objective.BinPacking);b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_cut_thickness(0)
        for w,h in items:b.add_item_type(w,h,oriented=True)
        inst=b.build();p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(.001,2-(time.perf_counter()-start));out=ps.optimize(inst,p)
        if out.solution.full() and out.solution.feasible():
            pj.validate(inst,out.solution);solver_bins=out.solution.number_of_bins()
        if best is None and solver_bins is None:raise RuntimeError('No complete solution')
    elapsed=time.perf_counter()-start
    if best is not None and (solver_bins is None or len(best)<=solver_bins):
        n=len(best);certificate={'type':'three-stage shelf','plates':best}
    else:
        n=solver_bins;path=OUT/f'{name}_{method}_{rep}.csv';out.solution.write(str(path));certificate={'type':'solver','file':path.name}
    (OUT/f'{name}_{method}_{rep}-certificate.json').write_text(json.dumps(certificate))
    return {'instance':name,'distribution':data['distribution'],'method':method,'repeat':rep,'bins':n,'bound':lb,'wall_s':elapsed,'certified_early':certified,'solver_used':solver_used,'valid':True}

def main():
    names=[];manifest=[];rng=random.Random(930001)
    for dist in ('small','uniform','wide','tall','mixed','large'):
        for n in (60,100):
            for rep in range(2):
                items=[]
                for i in range(n):
                    if dist=='small':w,h=rng.randint(5,35),rng.randint(5,35)
                    elif dist=='wide':w,h=rng.randint(51,100),rng.randint(5,35)
                    elif dist=='tall':w,h=rng.randint(5,35),rng.randint(51,100)
                    elif dist=='large':w,h=rng.randint(51,100),rng.randint(51,100)
                    elif dist=='mixed':w,h=(rng.randint(5,25),rng.randint(5,25)) if i%2 else (rng.randint(40,90),rng.randint(40,90))
                    else:w,h=rng.randint(5,100),rng.randint(5,100)
                    items.append([w,h])
                name=f'{dist}_{n}_{rep}';names.append(name);data={'distribution':dist,'W':100,'H':100,'items':items}
                path=OUT/f'{name}-input.json';path.write_text(json.dumps(data));manifest.append({'name':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (OUT/'protocol.json').write_text(json.dumps({'seed':930001,'instances':manifest,'budget_s':2,'repeats':2,'method':'best-fit shelves from six deterministic and ten shuffled orders, <=80ms construction; early exit only when valid lower bound reached; otherwise default solver with remaining time; keep best feasible result','lower_bounds':['area','sum heights of width>W/2 items','sum widths of height>H/2 items','count both dimensions >half'],'scope':'new synthetic distributions; no tuning on these outcomes; large is deliberately trivial control; report separately','timing':'algorithm input already in memory; includes construction, bounds, fallback and validation; excludes model/library import and certificate serialization'},indent=2))
    # Direct tests of the new certificate validator, executed only in Colab.
    good=shelf([[6,4],[4,6]],10,10,[1,0]);verify_shelf([[6,4],[4,6]],10,10,good)
    import copy
    bad=copy.deepcopy(good);bad[0]['rows'][0]['items'][0]['x']=1
    try:verify_shelf([[6,4],[4,6]],10,10,bad)
    except AssertionError:pass
    else:raise AssertionError('validator accepted malformed cut plan')
    jobs=[(n,m,r) for n in names for m in ('default','hybrid') for r in range(2)];random.Random(281).shuffle(jobs);rows=[]
    for i,(n,m,r) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/fast_colab.py','worker',n,m,str(r)],capture_output=True,text=True,timeout=15)
        if proc.returncode:raise RuntimeError(proc.stderr[-2000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2))
        if i%12==0:print('FAST',i+1,len(jobs),flush=True)
    summary={}
    for dist in ['all']+list(dict.fromkeys(r['distribution'] for r in rows)):
        rr=[r for r in rows if dist=='all' or r['distribution']==dist];bb=[r for r in rr if r['method']=='default'];hh=[r for r in rr if r['method']=='hybrid'];wins=losses=ties=0
        for h in hh:
            b=next(b for b in bb if b['instance']==h['instance'] and b['repeat']==h['repeat']);wins+=h['bins']<b['bins'];losses+=h['bins']>b['bins'];ties+=h['bins']==b['bins']
        summary[dist]={'pairs':len(hh),'default_bins':sum(b['bins'] for b in bb),'hybrid_bins':sum(h['bins'] for h in hh),'default_mean_s':sum(b['wall_s'] for b in bb)/len(bb),'hybrid_mean_s':sum(h['wall_s'] for h in hh)/len(hh),'wins':wins,'losses':losses,'ties':ties,'certified_early':sum(h['certified_early'] for h in hh)}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(ROOT/'fast-results'),'zip',OUT);print('FAST_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
