"""Colab-only expanded development benchmark and search-guide ablation."""
import pathlib, zipfile, tarfile, sys, json, hashlib, time, csv, collections, random, platform, subprocess, shutil
ROOT=pathlib.Path('/content/search-study')
ROOT.mkdir(exist_ok=True)
with zipfile.ZipFile('/content/compiled-package.zip') as z: z.extractall(ROOT)
with tarfile.open('/content/berkey-data.tar.gz') as t: t.extractall(ROOT, filter='data')
sys.path.insert(0,str(ROOT))
import packingsolver.rectangleguillotine as ps
OUT=ROOT/'results'
OUT.mkdir(exist_ok=True)
DATA=ROOT/'berkey1987'
(OUT/'environment.json').write_text(json.dumps({'python':sys.version,'platform':platform.platform(),'cpu':subprocess.check_output(['lscpu'],text=True),'commit':'28ac801df472e68736410411d70bfdb15f06e281'},indent=2))
configs={'default':{},'guide0':{'use_tree_search':True,'tree_search_guides':[0]},'guide1':{'use_tree_search':True,'tree_search_guides':[1]}}
manifest=[]
for cls in range(1,7):
    for size in (60,100):
        for rep in (2,3,4):
            name=f'Class_{cls:02d}.2bp_{size}_{rep}'
            hashes={k:hashlib.sha256((DATA/f'{name}_{k}.csv').read_bytes()).hexdigest() for k in ('items','bins')}
            manifest.append({'name':name,'split':'development','hashes':hashes})
(OUT/'protocol.json').write_text(json.dumps({'configs':configs,'budget_s':2,'repeats':2,'rules':'3-stage non-exact guillotine; horizontal first cut; no rotation; zero kerf; unlimited sheets; minimize sheets','order_seed':20261112,'reserved_test':'replicates 8,9,10 unused; no generalization claims','validation':'independent item geometry/demand; solver cutting constraints'},indent=2))
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
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

jobs=[(m['name'],c,r) for m in manifest for c in configs for r in range(2)]
random.Random(20261112).shuffle(jobs)
rows=[]
for idx,(name,config,repeat) in enumerate(jobs):
    inst=instance(name)
    p=ps.OptimizeParameters()
    p.verbosity_level=0
    p.time_limit=2
    for k,v in configs[config].items(): setattr(p,k,v)
    start=time.perf_counter()
    out=ps.optimize(inst,p)
    elapsed=time.perf_counter()-start
    sol=out.solution
    valid=True
    error=''
    try: validate(inst,sol)
    except AssertionError as exc: valid=False; error=str(exc)
    tag=f'{name}_{config}_{repeat}'
    sol.write(str(OUT/f'{tag}.csv'))
    row={'instance':name,'config':config,'repeat':repeat,'bins':sol.number_of_bins(),'valid':valid,'error':error,'wall_s':elapsed,'bound':out.bin_packing_bound}
    rows.append(row)
    (OUT/'rows.json').write_text(json.dumps(rows,indent=2))
    print(f'{idx+1}/{len(jobs)} '+json.dumps(row),flush=True)
with (OUT/'rows.csv').open('w') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
by={(r['instance'],r['config'],r['repeat']):r for r in rows}
summary={}
for c in configs:
    rr=[r for r in rows if r['config']==c]
    summary[c]={'valid':sum(r['valid'] for r in rr),'runs':len(rr),'mean_bins':sum(r['bins'] for r in rr if r['valid'])/max(1,sum(r['valid'] for r in rr)),'mean_wall_s':sum(r['wall_s'] for r in rr)/len(rr),'wins':[],'losses':[]}
    for r in rr:
        base=by[r['instance'],'default',r['repeat']]
        if base['valid'] and r['valid'] and r['bins']!=base['bins']:
            summary[c]['wins' if r['bins']<base['bins'] else 'losses'].append({'instance':r['instance'],'repeat':r['repeat'],'baseline':base['bins'],'candidate':r['bins']})
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
shutil.make_archive(str(ROOT/'search-results'),'zip',OUT)
print('SEARCH_COMPLETE '+json.dumps(summary),flush=True)
