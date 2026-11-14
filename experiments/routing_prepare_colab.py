"""Freeze the routing rule before generating any new evaluation instances."""
import sys,pathlib,json,csv,random,hashlib,shutil,collections,importlib.metadata
sys.path.insert(0,'/content');import routing_core_colab as rc
import quality_planted_colab as gen
ROOT=rc.ROOT;DATA=rc.DATA
sources=['routing_core_colab.py','strip_lp_colab.py','quality_colab.py','pattern_pool_colab.py','portfolio_colab.py']
frozen={p:hashlib.sha256(pathlib.Path('/content',p).read_bytes()).hexdigest() for p in sources}
(ROOT/'frozen-rule.json').write_text(json.dumps(dict(source_sha256=frozen,thresholds=dict(width_diversity_min=.75,area_bound_fill_min=.94,area_bound_fill_max=.98,item_count=[30,120],area_lower_bound=[2,6],maximum_width_units=4096,singletons_only=True),selection='Chosen using previous development and previously inspected tests; no new evaluation results used',interpretation='Empirical eligibility hypothesis, not a theorem or causal explanation',budget_s=10,seeds=[91,92],versions={p:importlib.metadata.version(p) for p in ('numpy','scipy','ortools')}),indent=2))

# Raw construction witnesses are outside the solver input directory. Solver
# workers receive anonymous CSV names and never consult these witnesses.
generated=ROOT/'generation';generated.mkdir(exist_ok=True)
gen.ROOT=generated;gen.DATA=generated/'data';gen.DATA.mkdir(exist_ok=True)
manifest=gen.generate(seed=81001,repeats=3)
oldhashes={hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/content/routing-inputs/results').glob('*/data/*_items.csv')}
pending=[]
for r in manifest:
    assert r['sha256'] not in oldhashes
    pending.append(dict(family='new_shelf_family',source=gen.DATA/r['name'],known_optimum=3))
for c in range(1,7):pending.append(dict(family='new_berkey',source=rc.alg.q.pj.ROOT/'berkey1987'/f'Class_{c:02d}.2bp_80_5'))
rng=random.Random(81002)
for j,r in enumerate(manifest):
    with (gen.DATA/f"{r['name']}_items.csv").open() as f:items=list(csv.DictReader(f))
    widths=[int(i['WIDTH']) for i in items];heights=[int(i['HEIGHT']) for i in items];rng.shuffle(heights)
    factor=2880000/sum(w*h for w,h in zip(widths,heights));heights=[max(1,round(h*factor)) for h in heights]
    assert all(h<=1000 for h in heights)
    name=f'scrambled_{j}'
    with (gen.DATA/f'{name}_items.csv').open('w') as f:
        wr=csv.writer(f);wr.writerow(['ID','WIDTH','HEIGHT']);wr.writerows((i,w,h) for i,(w,h) in enumerate(zip(widths,heights)))
    (gen.DATA/f'{name}_bins.csv').write_text('ID,WIDTH,HEIGHT\n0,1000,1000\n')
    pending.append(dict(family='height_scrambled_challenge',source=gen.DATA/name,known_optimum=None))
random.Random(81003).shuffle(pending);cases=[]
for i,r in enumerate(pending):
    name=f'route_case_{i:03d}'
    for suffix in ('items','bins'):shutil.copy(pathlib.Path(str(r['source'])+f'_{suffix}.csv'),DATA/f'{name}_{suffix}.csv')
    # Avoid generator module globals affecting feature reads or future workers.
    rc.alg.q.pj.DATA=DATA
    f=rc.read_features(name)
    cases.append(dict(name=name,family=r['family'],source_name=r['source'].name,known_optimum=r.get('known_optimum'),features=f,branch=rc.choose(f),sha256=hashlib.sha256((DATA/f'{name}_items.csv').read_bytes()+b'\0'+(DATA/f'{name}_bins.csv').read_bytes()).hexdigest()))
assert len({r['sha256'] for r in cases})==len(cases)
# Geometry-order and harmless unit-scaling invariance of the decision.
for r in cases:
    with (DATA/f"{r['name']}_items.csv").open() as f:items=list(csv.DictReader(f))
    with (DATA/f"{r['name']}_bins.csv").open() as f:bt=next(csv.DictReader(f))
    W,H=int(bt['WIDTH']),int(bt['HEIGHT']);rng.shuffle(items)
    assert rc.choose(rc.features(items,W,H))==r['branch']
    scaled=[dict(i,WIDTH=2*int(i['WIDTH']),HEIGHT=2*int(i['HEIGHT'])) for i in items]
    assert rc.choose(rc.features(scaled,2*W,2*H))==r['branch']
(ROOT/'rule-checks.json').write_text(json.dumps(dict(item_order_invariance=True,unit_scaling_within_resource_cap=True,anonymous_case_ids=True,no_duplicate_inputs=True),indent=2))
(ROOT/'test-manifest.json').write_text(json.dumps(cases,indent=2))
print('ROUTE_FROZEN',json.dumps(frozen));print('NEW_TEST_COUNTS',json.dumps({g:dict(total=sum(r['family']==g for r in cases),routed=sum(r['family']==g and r['branch']=='strips' for r in cases)) for g in sorted({r['family'] for r in cases})}),flush=True)
