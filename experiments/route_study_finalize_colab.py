"""Audit final certificates, consolidate labels and archive the completed study."""
import ast, collections, csv, hashlib, importlib.metadata, json, math, pathlib, random, shutil, subprocess, sys, zipfile
ROOT = pathlib.Path('/content/route-study')
CORPUS = pathlib.Path('/content/data-expansion/corpus')
assert (ROOT / 'complete.json').exists() and not (ROOT / 'failed.json').exists()
sys.path.insert(0, '/content')
import data_label_colab as audit
import routing_core_fixed_colab as rc
import route_features_colab as ft
audit.ROOT = ROOT
protocol = json.loads((ROOT / 'protocol.json').read_text())
for name, expected in protocol['frozen_source_sha256'].items():
    assert hashlib.sha256(pathlib.Path('/content', name).read_bytes()).hexdigest() == expected
policy = json.loads((ROOT / 'learned-policy.json').read_text())
freeze = json.loads((ROOT / 'learned-freeze.json').read_text())
assert not freeze['validation_rows_present']
assert hashlib.sha256((ROOT / 'learned-policy.json').read_bytes()).hexdigest() == freeze['policy_sha256']
assert hashlib.sha256(pathlib.Path('/content/route_fit_colab.py').read_bytes()).hexdigest() == freeze['fit_source_sha256']
all_rows = []
for phase, expected in [('regression', 10), ('development', 120), ('validation', 80)]:
    rows = json.loads((ROOT / f'{phase}-rows.json').read_text())
    assert len(rows) == expected
    for r in rows:
        audit.audit(r)
        all_rows.append(dict(r, phase=phase, source_study='R48-R50', certificate_base='route-study/certificates'))
cases = {c['name']: c for c in json.loads((CORPUS / 'manifest.json').read_text())}
names = sorted({r['instance'] for r in all_rows})
for n in names:
    paths = [ROOT / 'data' / f'{n}_{suffix}.csv' for suffix in ('items', 'bins')]
    assert hashlib.sha256(paths[0].read_bytes() + b'\0' + paths[1].read_bytes()).hexdigest() == cases[n]['input_sha256']
    with paths[0].open() as f:
        items = list(csv.DictReader(f))
    W, H = cases[n]['width'], cases[n]['height']
    original = ft.extract(items, W, H, rc.features(items, W, H))
    shuffled = list(items)
    random.Random(84006).shuffle(shuffled)
    permuted = ft.extract(shuffled, W, H, rc.features(shuffled, W, H))
    assert all(math.isclose(original[k], permuted[k], rel_tol=1e-10, abs_tol=1e-10) for k in original)
    assert (rc.choose(original) == 'strips' and ft.accept(original, policy)) == (rc.choose(permuted) == 'strips' and ft.accept(permuted, policy))
    if W * 2 <= 4096:
        scaled_items = [dict(it, WIDTH=2 * int(it['WIDTH']), HEIGHT=2 * int(it['HEIGHT'])) for it in items]
        scaled = ft.extract(scaled_items, W * 2, H * 2, rc.features(scaled_items, W * 2, H * 2))
        assert all(math.isclose(original[k], scaled[k], rel_tol=1e-10, abs_tol=1e-10) for k in original)
old_tree = ast.parse(pathlib.Path('/content/routing_core_colab.py').read_text())
new_tree = ast.parse(pathlib.Path('/content/routing_core_fixed_colab.py').read_text())
for fname in ('features', 'choose'):
    assert ast.dump(next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == fname)) == ast.dump(next(n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name == fname))
old_rows = json.loads((CORPUS / 'labels/rows.json').read_text())
consolidated = [dict(r, phase='earlier_pilot', source_study='R47', certificate_base='routing-data-v2/certificates') for r in old_rows] + all_rows
for r in consolidated:
    r['group_id'] = cases[r['instance']]['group_id']
    r['dataset_split'] = cases[r['instance']]['split']
    r['executed_branch'] = r['branch']
    r['run_key'] = hashlib.sha256(json.dumps([r['source_study'], r['phase'], r['instance'], r['method'], r['seed']], separators=(',', ':')).encode()).hexdigest()
assert len({r['run_key'] for r in consolidated}) == len(consolidated)
assert all(r['dataset_split'] not in ('test', 'test_external') for r in consolidated)
(ROOT / 'algorithm-labels.json').write_text(json.dumps(consolidated, indent=2))
overlay = {n: dict(split=cases[n]['split'], label_runs=sum(r['instance'] == n for r in consolidated), methods=sorted({r['method'] for r in consolidated if r['instance'] == n}), prior_studies=sorted({r['source_study'] for r in consolidated if r['instance'] == n}), validation_consumed=cases[n]['split'] == 'validation') for n in sorted({r['instance'] for r in consolidated})}
(ROOT / 'corpus-label-overlay.json').write_text(json.dumps(overlay, indent=2))
validation = json.loads((ROOT / 'validation-summary.json').read_text())
raw_development = {}
for ref in ('default', 'guide1'):
    pairs = []
    for r in all_rows:
        if r['phase'] == 'development' and r['method'] == 'strips':
            b = next(b for b in all_rows if b['phase'] == 'development' and b['instance'] == r['instance'] and b['method'] == ref)
            pairs.append(dict(name=r['instance'], family=r['family'], reference=b['bins'], strips=r['bins']))
    raw_development[ref] = dict(wins=sum(p['strips'] < p['reference'] for p in pairs), ties=sum(p['strips'] == p['reference'] for p in pairs), losses=sum(p['strips'] > p['reference'] for p in pairs), reference_total=sum(p['reference'] for p in pairs), strips_total=sum(p['strips'] for p in pairs), pairs=pairs)
(ROOT / 'development-counterfactual-summary.json').write_text(json.dumps(raw_development, indent=2))
validation['robust_improvements'] = {}
switch_pairs = []
for r in all_rows:
    if r['phase'] != 'validation' or r['method'] != 'learned_router':
        continue
    old = next(b for b in all_rows if b['phase'] == 'validation' and b['method'] == 'old_router' and b['instance'] == r['instance'] and b['seed'] == r['seed'])
    switch_pairs.append(dict(instance=r['instance'], seed=r['seed'], old_branch=old['branch'], new_branch=r['branch'], switched=old['branch'] != r['branch'], old_bins=old['bins'], new_bins=r['bins']))
validation['branch_switch_analysis'] = {}
for group in ('switched', 'unchanged'):
    pp = [p for p in switch_pairs if p['switched'] == (group == 'switched')]
    validation['branch_switch_analysis'][group] = dict(pairs=len(pp), old_total=sum(p['old_bins'] for p in pp), new_total=sum(p['new_bins'] for p in pp), differences=[p for p in pp if p['old_bins'] != p['new_bins']])
validation['branch_switch_analysis']['interpretation'] = 'Differences when the chosen branch is unchanged are timed-search variability, not evidence that the new filter improves that branch.'
for method in ('old_router', 'learned_router'):
    validation['robust_improvements'][method] = {}
    for ref in ('default', 'guide1', 'old_router'):
        if method == ref:
            continue
        robust = []
        for n in {r['instance'] for r in all_rows if r['phase'] == 'validation'}:
            rr = [r for r in all_rows if r['phase'] == 'validation' and r['instance'] == n]
            if all(next(r['bins'] for r in rr if r['method'] == method and r['seed'] == s) < next(r['bins'] for r in rr if r['method'] == ref and r['seed'] == s) for s in [301, 302]):
                robust.append(n)
        validation['robust_improvements'][method][ref] = sorted(robust)
(ROOT / 'validation-summary.json').write_text(json.dumps(validation, indent=2))
summary = dict(certificates_reaudited=len(all_rows), all_valid=True, instances_this_study=len(names),
               total_consolidated_runs=len(consolidated), total_labeled_instances=len(overlay),
               development_labeled_instances=sum(v['split'] == 'development' for v in overlay.values()),
               validation_used=10, validation_remaining_unrun=272, test_remaining_unrun=459,
               feature_order_and_supported_scale_invariance=True, original_gate_unchanged=True,
               repeated_demand_assertion_fixed=all('branch_error' not in r for r in all_rows if r['family'] == 'repeated_demand'),
               branch_errors=[r for r in all_rows if 'branch_error' in r],
               max_wall_s=max(r['wall_s'] for r in all_rows), max_gate_s=max(r['gate_s'] for r in all_rows),
               timing_note='Common dependency initialization and learned-policy loading occur before each timed query; feature extraction and dispatch are timed.',
               policy=policy)
(ROOT / 'audit-summary.json').write_text(json.dumps(summary, indent=2))
names_to_save = ['routing_core_colab.py', 'routing_core_fixed_colab.py', 'route_features_colab.py', 'route_study_setup_colab.py', 'route_study_colab.py', 'route_fit_colab.py', 'route_study_launch_colab.py', 'route_study_finalize_colab.py', 'strip_lp_colab.py', 'quality_colab.py', 'pattern_pool_colab.py', 'portfolio_colab.py', 'data_label_colab.py']
for n in names_to_save:
    shutil.copy(pathlib.Path('/content', n), ROOT / 'source' / n)
(ROOT / 'source-sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / 'source').glob('*.py'))}, indent=2))
(ROOT / 'environment.json').write_text(json.dumps(dict(python=sys.version, cpu=subprocess.check_output(['lscpu'], text=True), versions={p: importlib.metadata.version(p) for p in ['numpy', 'scipy', 'ortools']}, solver_commit='28ac801df472e68736410411d70bfdb15f06e281'), indent=2))
with zipfile.ZipFile('/content/route-study-results.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    entries = {}
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and p.name not in ('running.log', 'running.json', 'driver.py', 'current-phase.json'):
            rel = str(p.relative_to(ROOT))
            z.write(p, rel)
            entries[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    z.writestr('artifact-sha256.json', json.dumps(entries, indent=2))
print('FINAL_AUDIT', json.dumps(summary), flush=True)
print('FINAL_VALIDATION', json.dumps(validation), flush=True)
print('RAW_DEVELOPMENT', json.dumps({k: {a: b for a, b in v.items() if a != 'pairs'} for k, v in raw_development.items()}), flush=True)
