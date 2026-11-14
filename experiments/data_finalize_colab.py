"""Re-read all corpus inputs, check held-out status, archive final evidence."""
import ast, collections, csv, hashlib, importlib.metadata, json, pathlib, shutil, subprocess, sys, zipfile
ROOT = pathlib.Path('/content/data-expansion/corpus')
manifest = json.loads((ROOT / 'manifest.json').read_text())
summary = json.loads((ROOT / 'summary.json').read_text())
if 'independent_groups' in summary:
    summary['split_groups'] = summary.pop('independent_groups')
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2))
labels = json.loads((ROOT / 'labels/rows.json').read_text())
label_summary = json.loads((ROOT / 'labels/summary.json').read_text())
assert len(labels) == 36 and label_summary['all_valid']
frozen = json.loads((ROOT / 'frozen-rule.json').read_text())
for name, digest in frozen['source_sha256'].items():
    assert hashlib.sha256(pathlib.Path('/content', name).read_bytes()).hexdigest() == digest
source = pathlib.Path('/content/routing_core_colab.py')
tree, ns = ast.parse(source.read_text()), {}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('features', 'choose')], type_ignores=[]), str(source), 'exec'), ns)
groups, hashes, scaled_hashes, shapes = collections.defaultdict(set), set(), set(), []
for r in manifest:
    item_path = ROOT / 'data' / (r['name'] + '_items.csv')
    bin_path = ROOT / 'data' / (r['name'] + '_bins.csv')
    assert hashlib.sha256(item_path.read_bytes() + b'\0' + bin_path.read_bytes()).hexdigest() == r['input_sha256']
    with item_path.open() as f:
        items = list(csv.DictReader(f))
    with bin_path.open() as f:
        bins = list(csv.DictReader(f))
    assert len(bins) == 1
    W, H = int(bins[0]['WIDTH']), int(bins[0]['HEIGHT'])
    assert (W, H) == (r['width'], r['height'])
    assert [int(it['ID']) for it in items] == list(range(len(items)))
    assert all(0 < int(it['WIDTH']) <= W and 0 < int(it['HEIGHT']) <= H and int(it['COPIES']) > 0 for it in items)
    f = ns['features'](items, W, H)
    assert f == r['features'] and ns['choose'](f) == r['frozen_branch']
    assert r['canonical_sha256'] not in hashes and r['scale_invariant_sha256'] not in scaled_hashes
    hashes.add(r['canonical_sha256'])
    scaled_hashes.add(r['scale_invariant_sha256'])
    groups[r['group_id']].add(r['split'])
    shapes.append((f['items'], W, H))
assert all(len(v) == 1 for v in groups.values())
assert len(manifest) == summary['total_instances'] == 1760
assert collections.Counter(r['split'] for r in manifest) == summary['splits']
by_name = {r['name']: r for r in manifest}
assert all(by_name[r['instance']]['split'] == 'development' for r in labels)
assert all(r['solver_label_status'] == 'not_run' for r in manifest if r['split'] in ('test', 'test_external', 'validation'))
differences = []
for pair in json.loads((ROOT / 'labels/paired-labels.json').read_text()):
    if pair['default'] != pair['router']:
        row = next(r for r in labels if r['instance'] == pair['name'] and r['method'] == 'router')
        differences.append(dict(pair=pair, features=by_name[pair['name']]['features'], router_details=row))
(ROOT / 'labels/difference-details.json').write_text(json.dumps(differences, indent=2))
audit = dict(input_pairs_rechecked=len(manifest), grouped_split_integrity=True, all_input_hashes_match=True,
             frozen_rule_unchanged=True, route_features_rechecked=True, development_labeled_instances=len({r['instance'] for r in labels}),
             paired_solver_runs=len(labels), validation_instances_unrun=summary['splits']['validation'],
             test_instances_unrun=summary['splits']['test'] + summary['splits']['test_external'],
             item_count_range=[min(s[0] for s in shapes), max(s[0] for s in shapes)],
             width_range=[min(s[1] for s in shapes), max(s[1] for s in shapes)], height_range=[min(s[2] for s in shapes), max(s[2] for s in shapes)])
(ROOT / 'corpus-audit.json').write_text(json.dumps(audit, indent=2))
source_names = ['data_inventory_colab.py', 'data_expand_colab.py', 'data_label_colab.py', 'data_label_launch_colab.py', 'data_finalize_colab.py',
                'routing_setup_colab.py', 'routing_core_colab.py', 'strip_lp_colab.py', 'quality_colab.py', 'portfolio_colab.py', 'pattern_pool_colab.py']
for name in source_names:
    p = pathlib.Path('/content', name)
    if not p.exists():
        p = pathlib.Path('/content/data-expansion/history/experiments', name)
    assert p.exists(), name
    shutil.copy(p, ROOT / 'source' / name)
(ROOT / 'source-sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / 'source').glob('*.py'))}, indent=2))
(ROOT / 'environment.json').write_text(json.dumps(dict(python=sys.version, cpu=subprocess.check_output(['lscpu'], text=True),
    versions={p: importlib.metadata.version(p) for p in ['numpy', 'scipy', 'ortools']}, solver_commit='28ac801df472e68736410411d70bfdb15f06e281'), indent=2))
with zipfile.ZipFile('/content/routing-data-v2.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    entries = {}
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and p.name not in ('pilot-running.log', 'running.json'):
            rel = str(p.relative_to(ROOT))
            z.write(p, rel)
            entries[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    z.writestr('artifact-sha256.json', json.dumps(entries, indent=2))
print('DATA_ARCHIVED', json.dumps(audit), 'zip_bytes', pathlib.Path('/content/routing-data-v2.zip').stat().st_size, flush=True)
print('DIFFERENCES', json.dumps(differences), flush=True)
