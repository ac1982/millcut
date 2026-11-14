"""Execute in Colab only. Expanded solver portfolio, with subprocess deadlines."""
import pathlib,sys,json,csv,time,random,collections,subprocess,zipfile,tarfile,shutil,platform,hashlib
ROOT=pathlib.Path('/content/portfolio-study');DATA=ROOT/'berkey1987';OUT=ROOT/'results'
CONFIGS={'default':{},'guide1':{'use_tree_search':True,'tree_search_guides':[1]},'tree01':{'use_tree_search':True},'value_correction':{'use_sequential_value_correction':True},'single_knapsack':{'use_sequential_single_knapsack':True},'column_generation':{'use_column_generation':True},'height_order':{'use_tree_search':True,'tree_search_guides':[1]}}

def setup():
    ROOT.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
    with zipfile.ZipFile('/content/compiled-package.zip') as z:z.extractall(ROOT)
    with tarfile.open('/content/berkey-data.tar.gz') as t:t.extractall(ROOT,filter='data')
    (OUT/'environment.json').write_text(json.dumps({'python':sys.version,'cpu':subprocess.check_output(['lscpu'],text=True),'commit':'28ac801df472e68736410411d70bfdb15f06e281'},indent=2))

sys.path.insert(0,str(ROOT))
def validate(inst, sol):
    counts = collections.Counter()
    for bpos in range(sol.number_of_different_bins()):
        b = sol.bin(bpos)
        bt = inst.bin_type(b.bin_type_id)
        nodes = [n for n in b.nodes if n.f != -1 and n.item_type_id >= 0]
        for n in nodes:
            it = inst.item_type(n.item_type_id)
            assert (n.r-n.l, n.t-n.b) == (it.w, it.h), 'item dimensions'
            assert 0 <= n.l < n.r <= bt.w and 0 <= n.b < n.t <= bt.h, 'bounds'
            counts[n.item_type_id] += b.copies
        for i, a in enumerate(nodes):
            for bnode in nodes[i+1:]:
                assert a.r <= bnode.l or bnode.r <= a.l or a.t <= bnode.b or bnode.t <= a.b, 'overlap'
    assert all(counts[i] == inst.item_type(i).copies for i in range(inst.number_of_item_types())), 'demand'
    assert sol.full() and sol.feasible(), 'solver feasibility including cutting constraints'


def instance(name):
    b=ps.InstanceBuilder()
    b.read_bin_types(str(DATA/f'{name}_bins.csv'))
    b.read_item_types(str(DATA/f'{name}_items.csv'))
    b.set_bin_types_infinite_copies()
    b.set_objective(ps.Objective.BinPacking)
    b.set_number_of_stages(3)
    b.set_cut_type(ps.CutType.NonExact)
    b.set_first_stage_orientation(ps.CutOrientation.Horizontal)
    b.set_item_types_oriented(True)
    b.set_cut_thickness(0)
    return b.build()


def solve(name,config,budget,tag):
    global ps
    import packingsolver.rectangleguillotine as ps
    start=time.perf_counter()
    if config=='height_order':
        with (DATA/f'{name}_items.csv').open() as f: records=list(csv.DictReader(f))
        records=sorted(enumerate(records),key=lambda r:(-int(r[1]['HEIGHT']),-int(r[1]['WIDTH'])))
        b=ps.InstanceBuilder();b.read_bin_types(str(DATA/f'{name}_bins.csv'));b.set_bin_types_infinite_copies()
        b.set_objective(ps.Objective.BinPacking);b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_cut_thickness(0)
        for _,r in records:b.add_item_type(int(r['WIDTH']),int(r['HEIGHT']),copies=int(r.get('COPIES',1)),oriented=True)
        inst=b.build()
        (OUT/f'{tag}-item-map.json').write_text(json.dumps([i for i,_ in records]))
    else:inst=instance(name)
    p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(.001,budget-(time.perf_counter()-start))
    for k,v in CONFIGS[config].items():setattr(p,k,v)
    out=ps.optimize(inst,p);wall=time.perf_counter()-start;sol=out.solution
    valid=True;error=''
    try:validate(inst,sol)
    except AssertionError as exc:valid=False;error=str(exc)
    sol.write(str(OUT/f'{tag}.csv'))
    return {'instance':name,'config':config,'budget_s':budget,'bins':sol.number_of_bins(),'bound':out.bin_packing_bound,'valid':valid,'error':error,'wall_s':wall}

def run_job(name,config,budget,tag):
    try:
        proc=subprocess.run([sys.executable,'/content/portfolio_colab.py','worker',name,config,str(budget),tag],capture_output=True,text=True,timeout=budget+10)
        if proc.returncode:raise RuntimeError(proc.stderr[-1500:])
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:return {'instance':name,'config':config,'budget_s':budget,'bins':None,'valid':False,'error':str(exc),'wall_s':None}

def main():
    setup()
    names=[f'Class_{c:02d}.2bp_{n}_{r}' for c in range(1,7) for n in (60,100) for r in (2,3,4)]
    manifest=[{'name':n,'split':'development','sha256':{k:hashlib.sha256((DATA/f'{n}_{k}.csv').read_bytes()).hexdigest() for k in ('bins','items')}} for n in names]
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (OUT/'protocol.json').write_text(json.dumps({'configs':CONFIGS,'budget_s':2,'repeats':1,'timing':'includes instance construction and sorting; excludes process startup and certificate validation','rules':'3-stage non-exact; horizontal first cut; no rotation; zero kerf','fresh_test_reserved':'replicates 9 and 10','oracle_warning':'per-instance best is a hindsight diagnostic; running all candidates consumes multiple budgets'},indent=2))
    jobs=[(n,c) for n in names for c in CONFIGS];random.Random(151).shuffle(jobs);rows=[]
    for i,(n,c) in enumerate(jobs):
        row=run_job(n,c,2,f'{n}_{c}');rows.append(row)
        (OUT/'rows.json').write_text(json.dumps(rows,indent=2))
        print(f'{i+1}/{len(jobs)} '+json.dumps(row),flush=True)
    summary={}
    for c in CONFIGS:
        rr=[r for r in rows if r['config']==c];wins=losses=ties=0
        for r in rr:
            b=next(b for b in rows if b['instance']==r['instance'] and b['config']=='default')
            if r['valid'] and b['valid']:wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins']
        summary[c]={'valid':sum(r['valid'] for r in rr),'wins':wins,'losses':losses,'ties':ties,'total_bins_valid':sum(r['bins'] for r in rr if r['valid'])}
    oracle=[]
    for n in names:
        rr=[r for r in rows if r['instance']==n and r['valid']]
        base=next(r for r in rr if r['config']=='default')
        best=min(rr,key=lambda r:(r['bins'],r['wall_s']))
        oracle.append({'instance':n,'default':base['bins'],'best':best['bins'],'best_config':best['config'],'saved':base['bins']-best['bins']})
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));(OUT/'oracle.json').write_text(json.dumps(oracle,indent=2))
    shutil.make_archive(str(ROOT/'portfolio-results'),'zip',OUT)
    print('PORTFOLIO_COMPLETE',json.dumps(summary),'ORACLE',json.dumps([o for o in oracle if o['saved']]),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(solve(sys.argv[2],sys.argv[3],float(sys.argv[4]),sys.argv[5])))
    else:main()
