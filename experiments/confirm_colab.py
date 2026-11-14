"""Colab-only repeated confirmation on development-selected wins."""
import pathlib,sys,json,time,collections,random,shutil
ROOT=pathlib.Path('/content/search-study');sys.path.insert(0,str(ROOT))
import packingsolver.rectangleguillotine as ps
DATA=ROOT/'berkey1987';OUT=ROOT/'confirmation';OUT.mkdir(exist_ok=True)
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

jobs=[(n,c,r) for n in ('Class_02.2bp_60_2','Class_04.2bp_100_3') for c in ('default','guide1') for r in range(5)]
random.Random(25).shuffle(jobs);rows=[]
for name,config,repeat in jobs:
    inst=instance(name);p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=2
    if config=='guide1':p.use_tree_search=True;p.tree_search_guides=[1]
    start=time.perf_counter();out=ps.optimize(inst,p);elapsed=time.perf_counter()-start
    sol=out.solution;validate(inst,sol)
    sol.write(str(OUT/f'{name}_{config}_{repeat}.csv'))
    row={'instance':name,'config':config,'repeat':repeat,'bins':sol.number_of_bins(),'bound':out.bin_packing_bound,'wall_s':elapsed}
    rows.append(row);print(json.dumps(row),flush=True)
    if repeat==0:
        try:ps.visualize(sol).write_html(str(OUT/f'{name}_{config}.html'),include_plotlyjs=True)
        except ImportError:pass
(OUT/'rows.json').write_text(json.dumps(rows,indent=2))
summary={n:{c:[r['bins'] for r in rows if r['instance']==n and r['config']==c] for c in ('default','guide1')} for n in sorted({r['instance'] for r in rows})}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(ROOT/'confirmation'),'zip',OUT)
print('CONFIRMATION_COMPLETE',json.dumps(summary),flush=True)
