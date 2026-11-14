"""Run in the prepared Colab runtime; never execute locally."""
import csv, hashlib, json, math, pathlib, time, collections, shutil, platform, subprocess, sys
import packingsolver.rectangleguillotine as ps
import packingsolver
ROOT = pathlib.Path('/content/cut-research')
DATA = ROOT / 'packingsolver/data/rectangle/berkey1987'
OUT = ROOT / 'baseline'
OUT.mkdir(exist_ok=True)
package_dir = pathlib.Path(packingsolver.__file__).parent
shutil.make_archive(str(ROOT / 'compiled-package'), 'zip', package_dir.parent, package_dir.name)
tests = subprocess.run([sys.executable, '-m', 'pytest', str(ROOT / 'packingsolver/python/tests/test_rectangleguillotine.py'), '-q'], capture_output=True, text=True)
(OUT / 'upstream-tests.txt').write_text(tests.stdout + tests.stderr)
print(tests.stdout + tests.stderr, flush=True)
if tests.returncode:
    # Pinned upstream API synchronization issue, unrelated to this protocol.
    expected = "set_bin_type_resource_tolerance: no keyword 'resource_tolerance'"
    if expected not in tests.stdout or '1 failed, 30 passed' not in tests.stdout:
        tests.check_returncode()
    (OUT / 'known-upstream-failure.txt').write_text(expected + '\nNo resource constraints are used in this pilot.\n')
manifest = []
for cls in range(1, 7):
    for size in (20, 60):
        name = f'Class_{cls:02d}.2bp_{size}_1'
        paths = {kind: DATA / f'{name}_{kind}.csv' for kind in ('items', 'bins')}
        manifest.append({'name': name, 'class': cls, 'size': size, 'split': 'development',
                         'sha256': {k: hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}})
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2))
(OUT / 'protocol.json').write_text(json.dumps({'objective': 'BinPacking', 'stages': 3, 'cut_type': 'NonExact', 'first_stage': 'Horizontal', 'rotation': False, 'kerf': 0, 'budgets_seconds': [1, 5], 'repeats': 2, 'note': 'Development pilot, not a held-out test or published benchmark score. Original rectangle instances evaluated under explicit guillotine restrictions. Full-sheet waste includes final-sheet leftover.'}, indent=2))

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

rows=[]
for repeat in range(2):
    for budget in (1, 5):
        for entry in manifest:
            name=entry['name']
            builder=ps.InstanceBuilder()
            builder.read_bin_types(str(DATA / f'{name}_bins.csv'))
            builder.read_item_types(str(DATA / f'{name}_items.csv'))
            builder.set_bin_types_infinite_copies()
            builder.set_objective(ps.Objective.BinPacking)
            builder.set_number_of_stages(3)
            builder.set_cut_type(ps.CutType.NonExact)
            builder.set_first_stage_orientation(ps.CutOrientation.Horizontal)
            builder.set_item_types_oriented(True)
            builder.set_cut_thickness(0)
            inst=builder.build()
            params=ps.OptimizeParameters()
            params.time_limit=budget
            params.verbosity_level=0
            start=time.perf_counter()
            result=ps.optimize(inst, params)
            elapsed=time.perf_counter()-start
            sol=result.solution
            tag=f'{name}_{budget}s_r{repeat}'
            sol.write(str(OUT / f'{tag}.csv'))
            valid=True
            error=''
            try:
                validate(inst, sol)
            except AssertionError as exc:
                valid=False
                error=str(exc)
            bins=sol.number_of_bins()
            area_lb=math.ceil(inst.item_area()/inst.bin_type(0).area())
            rows.append({'instance':name, 'budget_s':budget,'repeat':repeat,'wall_s':elapsed,'valid':valid,'error':error,'bins':bins,'area_lower_bound':area_lb,'solver_lower_bound':result.bin_packing_bound,'full_sheet_waste_fraction':1-inst.item_area()/(bins*inst.bin_type(0).area()) if valid and bins else None})
            (OUT / 'results.json').write_text(json.dumps(rows, indent=2))
            print(json.dumps(rows[-1]), flush=True)
            if len(rows)==1:
                ps.visualize(sol).write_html(str(OUT / 'example-layout.html'), include_plotlyjs=True)
with (OUT / 'results.csv').open('w') as f:
    writer=csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
shutil.copy(ROOT / 'environment.json', OUT)
summary={str(b):{'runs':len(rr := [r for r in rows if r['budget_s']==b]),'valid_runs':sum(r['valid'] for r in rr),'mean_bins_valid':sum(r['bins'] for r in rr if r['valid'])/max(1,sum(r['valid'] for r in rr)), 'mean_wall_s':sum(r['wall_s'] for r in rr)/len(rr)} for b in (1,5)}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
shutil.make_archive(str(ROOT / 'baseline'), 'zip', OUT)
print('BASELINE_COMPLETE', json.dumps(summary), flush=True)
