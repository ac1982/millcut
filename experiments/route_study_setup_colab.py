"""Restore artifacts and freeze sampling before any new solver labels."""
import pathlib, zipfile, shutil, subprocess, sys, json, hashlib, random, collections
ROOT = pathlib.Path('/content/route-study')
ROOT.mkdir(exist_ok=True)
CORPUS = pathlib.Path('/content/data-expansion/corpus')
with zipfile.ZipFile('/content/routing-data-v2.zip') as z:
    z.extractall(CORPUS)
base = pathlib.Path('/content/portfolio-study')
for name in ['planted-quality', 'routing']:
    (base / name).mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile('/content/compiled-package.zip') as z:
    z.extractall(base)
for name in ['routing_core_colab.py', 'strip_lp_colab.py', 'quality_colab.py', 'portfolio_colab.py', 'pattern_pool_colab.py', 'data_label_colab.py']:
    shutil.copy(CORPUS / 'source' / name, '/content/' + name)
p = subprocess.run([sys.executable, '-m', 'pip', 'install', 'ortools==9.15.6755', 'scipy'], capture_output=True, text=True)
p.check_returncode()
print(p.stdout[-500:])
cases = json.loads((CORPUS / 'manifest.json').read_text())
old = json.loads((CORPUS / 'frozen-rule.json').read_text())
for name, expected in old['source_sha256'].items():
    assert hashlib.sha256(pathlib.Path('/content', name).read_bytes()).hexdigest() == expected
sys.path.insert(0, '/content')
import routing_core_fixed_colab as rc
import route_features_colab as ft
families = ['shelf_same_family', 'height_scrambled', 'independent_random', 'shelf_varied', 'gate_boundary']
known = ['data_5248a5778eb6586f', 'data_59d6eb7717d27fa0']
prior = set(json.loads((CORPUS / 'pilot-manifest.json').read_text()))
def rank(c):
    return hashlib.sha256(('84001/' + c['name']).encode()).hexdigest()
def eligible(c):
    f = c['features']
    return f['singletons'] and f['supported_width'] and 30 <= f['items'] <= 120 and 2 <= f['area_lower_bound'] <= 6
dev, val = [], []
for family in families:
    pool = sorted([c for c in cases if c['family'] == family and eligible(c)], key=rank)
    selected = [c for c in pool if c['name'] in known]
    new = [c for c in pool if c['split'] == 'development' and c['name'] not in prior]
    # Force branch counterfactuals for some misses, without exceeding the
    # specialist's computational range. Sampling does not use solver outcomes.
    miss = [c for c in new if c['frozen_branch'] == 'default'][:2]
    selected += miss
    selected += [c for c in new if c not in selected][:8 - len(selected)]
    assert len(selected) == 8
    dev.extend(selected)
    vv = [c for c in pool if c['split'] == 'validation']
    # Two per family, one eligible and one outside the old gate when available.
    chosen = [c for c in vv if c['frozen_branch'] == 'strips'][:1]
    chosen += [c for c in vv if c['frozen_branch'] == 'default'][:1]
    chosen += [c for c in vv if c not in chosen][:2 - len(chosen)]
    assert len(chosen) == 2
    val.extend(chosen)
assert len(dev) == 40 and len(val) == 10
assert {c['group_id'] for c in dev}.isdisjoint({c['group_id'] for c in val})
for d in ['certificates', 'source', 'data']:
    (ROOT / d).mkdir(exist_ok=True)
frozen = {name: hashlib.sha256(pathlib.Path('/content', name).read_bytes()).hexdigest() for name in list(old['source_sha256']) + ['routing_core_fixed_colab.py', 'route_features_colab.py', 'route_study_colab.py']}
protocol = dict(budget_s=10, development_methods=['default', 'guide1', 'strips'], development_seed=201,
    development_instances=40, fresh_development_instances=38, known_regression_instances=known,
    validation_methods=['default', 'guide1', 'old_router', 'learned_router'], validation_seeds=[301, 302], validation_instances=10,
    fit='Original geometric gate plus at most one extra feature threshold; default elsewhere. Select on development labels only. At least 5 selected samples and 2 strict wins against min(default, guide1); reward total savings minus twice total losses against that reference. Prefer broader support on ties. Zero-action and original gate candidates included.',
    crossfit='Five folds by parent group hash; each held-out fold is predicted by a rule fitted to other folds. Diagnostic only; final rule fitted on all development labels before validation.',
    timing='Preloaded dependencies for every method. Input reading, extra features, routing, solver, output and validation included; no concurrent solves.',
    scope='Geometry feature filtering within original gate; no source family/name/witness features. Forced specialist labels include resource-supported misses. Validation is presampled and not used to revise this candidate; all 459 test instances stay unopened.',
    adapter_fix='Default and guide1 call the native solver directly, avoiding the specialist singleton assertion. Original eligibility and specialist unchanged.',
    frozen_source_sha256=frozen)
(ROOT / 'protocol.json').write_text(json.dumps(protocol, indent=2))
(ROOT / 'development-manifest.json').write_text(json.dumps(dev, indent=2))
(ROOT / 'validation-manifest.json').write_text(json.dumps(val, indent=2))
frows = {c['name']: ft.read(c['name'], CORPUS / 'data', rc.features) for c in dev}
(ROOT / 'development-features.json').write_text(json.dumps(frows, indent=2))
print('STUDY_READY', json.dumps(dict(development=40, fresh=38, validation=10, development_gate_hits=sum(c['frozen_branch'] == 'strips' for c in dev), validation_gate_hits=sum(c['frozen_branch'] == 'strips' for c in val), development_families=dict(collections.Counter(c['family'] for c in dev)))), flush=True)
