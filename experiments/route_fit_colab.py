"""Fit at most one geometric filter on development labels; never read validation labels."""
import hashlib, json, pathlib, statistics, sys
ROOT = pathlib.Path('/content/route-study')
assert not (ROOT / 'validation-rows.json').exists(), 'do not refit after validation'
sys.path.insert(0, '/content')
import route_features_colab as ft
import routing_core_fixed_colab as rc
cases = json.loads((ROOT / 'development-manifest.json').read_text())
features = json.loads((ROOT / 'development-features.json').read_text())
rows = json.loads((ROOT / 'development-rows.json').read_text())
known = {c['name'] for c in cases}
rows += [r for r in json.loads((ROOT / 'regression-rows.json').read_text()) if r['instance'] in known]
samples = []
for c in cases:
    rr = [r for r in rows if r['instance'] == c['name']]
    costs = {m: statistics.mean(r['bins'] for r in rr if r['method'] == m) for m in ('default', 'guide1', 'strips')}
    assert len({sum(r['method'] == m for r in rr) for m in costs}) == 1
    samples.append(dict(name=c['name'], group_id=c['group_id'], family=c['family'], f=features[c['name']], costs=costs,
                        fold=int(hashlib.sha256(('84005/' + c['group_id']).encode()).hexdigest()[:12], 16) % 5))


def selected(s, p):
    return rc.choose(s['f']) == 'strips' and ft.accept(s['f'], p)


def score(ss, p):
    active = [s for s in ss if selected(s, p)]
    deltas = [min(s['costs']['default'], s['costs']['guide1']) - s['costs']['strips'] for s in active]
    return dict(support=len(active), wins=sum(x > 0 for x in deltas), losses=sum(x < 0 for x in deltas),
                net_savings=sum(deltas), penalized_savings=sum(deltas) - 2 * sum(max(0, -x) for x in deltas))


def fit(ss):
    base = dict(kind='default_only')
    options = [dict(kind='original_gate')]
    inside = [s for s in ss if rc.choose(s['f']) == 'strips']
    if inside:
        for key in sorted(inside[0]['f']):
            if isinstance(inside[0]['f'][key], bool):
                continue
            values = sorted({s['f'][key] for s in inside})
            for a, b in zip(values, values[1:]):
                for direction in ('ge', 'le'):
                    options.append(dict(kind='one_filter', feature=key, threshold=(a + b) / 2, direction=direction))
    best, best_key = base, (0, 0, 0)
    for p in options:
        s = score(ss, p)
        if s['support'] < 5 or s['wins'] < 2 or s['penalized_savings'] <= 0:
            continue
        key = (s['penalized_savings'], s['net_savings'], s['support'])
        if key > best_key:
            best, best_key = p, key
    return best


def outcome(ss, policies):
    pairs = []
    for s, p in zip(ss, policies):
        chosen = 'strips' if selected(s, p) else 'default'
        pairs.append(dict(name=s['name'], family=s['family'], group_id=s['group_id'], branch=chosen, reference=s['costs']['default'], chosen=s['costs'][chosen]))
    return dict(instances=len(pairs), wins=sum(p['chosen'] < p['reference'] for p in pairs), losses=sum(p['chosen'] > p['reference'] for p in pairs), ties=sum(p['chosen'] == p['reference'] for p in pairs), reference_total=sum(p['reference'] for p in pairs), chosen_total=sum(p['chosen'] for p in pairs), pairs=pairs)


folds, predictions = [], {}
for fold in range(5):
    train = [s for s in samples if s['fold'] != fold]
    test = [s for s in samples if s['fold'] == fold]
    if not test:
        continue
    p = fit(train)
    folds.append(dict(fold=fold, train_groups=len({s['group_id'] for s in train}), held_out_groups=len({s['group_id'] for s in test}), policy=p))
    predictions.update({s['name']: p for s in test})
policy = fit(samples)
counterfactual = {}
for group in ['all', 'old_gate_hit', 'old_gate_miss'] + sorted({s['family'] for s in samples}):
    ss = [s for s in samples if group == 'all' or s['family'] == group or (group == 'old_gate_hit' and rc.choose(s['f']) == 'strips') or (group == 'old_gate_miss' and rc.choose(s['f']) != 'strips')]
    counterfactual[group] = {}
    for ref in ('default', 'guide1'):
        counterfactual[group][ref] = dict(instances=len(ss), wins=sum(s['costs']['strips'] < s['costs'][ref] for s in ss), ties=sum(s['costs']['strips'] == s['costs'][ref] for s in ss), losses=sum(s['costs']['strips'] > s['costs'][ref] for s in ss), reference_total=sum(s['costs'][ref] for s in ss), strips_total=sum(s['costs']['strips'] for s in ss), differences=[dict(name=s['name'], reference=s['costs'][ref], strips=s['costs']['strips']) for s in ss if s['costs']['strips'] != s['costs'][ref]])
report = dict(samples=samples, fitted_policy=policy, training_score=score(samples, policy), crossfit_folds=folds,
              counterfactual_by_region=counterfactual,
              crossfit=outcome(samples, [predictions[s['name']] for s in samples]),
              fitted_development=outcome(samples, [policy] * len(samples)),
              original_gate_development=outcome(samples, [dict(kind='original_gate')] * len(samples)),
              caveat='Offline replay of method labels; diagnostic, not end-to-end validation. Known regression cases have two seeds averaged within each case, others one. Source families are absent from fit features.')
(ROOT / 'fit-report.json').write_text(json.dumps(report, indent=2))
(ROOT / 'learned-policy.json').write_text(json.dumps(policy, indent=2))
(ROOT / 'learned-freeze.json').write_text(json.dumps(dict(policy_sha256=hashlib.sha256((ROOT / 'learned-policy.json').read_bytes()).hexdigest(), fit_source_sha256=hashlib.sha256(pathlib.Path('/content/route_fit_colab.py').read_bytes()).hexdigest(), validation_rows_present=(ROOT / 'validation-rows.json').exists()), indent=2))
assert not (ROOT / 'validation-rows.json').exists(), 'do not refit after validation'
print('POLICY_FROZEN', json.dumps(policy), 'SCORE', json.dumps(report['training_score']), flush=True)
print('OFFLINE', json.dumps({k: {a: b for a, b in report[k].items() if a != 'pairs'} for k in ['crossfit', 'fitted_development', 'original_gate_development']}), flush=True)
