"""Paired labels for a prespecified development pilot. Colab only."""
import collections, csv, hashlib, json, pathlib, random, shutil, subprocess, sys

ROOT = pathlib.Path('/content/data-expansion/corpus')
FROZEN = json.loads((ROOT / 'frozen-rule.json').read_text())


def frozen_check():
    for name, expected in FROZEN['source_sha256'].items():
        assert hashlib.sha256(pathlib.Path('/content', name).read_bytes()).hexdigest() == expected, name


def worker(name, method, seed):
    sys.path.insert(0, '/content')
    import routing_core_colab as rc
    frozen_check()
    rc.DATA, rc.OUT = ROOT / 'data', ROOT / 'certificates'
    rc.alg.q.pj.DATA, rc.alg.OUT = rc.DATA, rc.OUT
    return rc.run(name, method, seed, budget=10)


def audit(row):
    name = row['instance']
    path = ROOT / 'certificates' / row['certificate']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row['certificate_sha256']
    with (ROOT / 'data' / f'{name}_items.csv').open() as f:
        items = list(csv.DictReader(f))
    with (ROOT / 'data' / f'{name}_bins.csv').open() as f:
        bt = next(csv.DictReader(f))
    W, H = int(bt['WIDTH']), int(bt['HEIGHT'])
    rects, counts = collections.defaultdict(list), collections.Counter()
    if path.suffix == '.json':
        plates = json.loads(path.read_text())
        bins = len(plates)
        for b, plate in enumerate(plates):
            y = 0
            for strip in plate:
                assert strip['y'] == y
                x = 0
                for it in strip['items']:
                    assert (it['x'], it['y']) == (x, y) and it['h'] <= strip['height']
                    rects[b].append((it['id'], x, y, it['w'], it['h'], 1))
                    x += it['w']
                assert x <= W
                y += strip['height']
            assert y <= H
    else:
        with path.open() as f:
            nodes = list(csv.DictReader(f))
        bins = sum(int(n['COPIES']) for n in nodes if not n['PARENT'])
        for n in nodes:
            if n['PARENT'] and int(n['TYPE']) >= 0:
                rects[int(n['PLATE_ID'])].append(tuple(int(n[k]) for k in ('TYPE', 'X', 'Y', 'WIDTH', 'HEIGHT', 'COPIES')))
    for plate in rects.values():
        for j, (i, x, y, w, h, copies) in enumerate(plate):
            assert (w, h) == (int(items[i]['WIDTH']), int(items[i]['HEIGHT']))
            assert 0 <= x < x + w <= W and 0 <= y < y + h <= H
            counts[i] += copies
            for _, xx, yy, ww, hh, _ in plate[j + 1:]:
                assert x + w <= xx or xx + ww <= x or y + h <= yy or yy + hh <= y
    assert counts == collections.Counter({i: int(it['COPIES']) for i, it in enumerate(items)})
    assert bins == row['bins']


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    by_name = {c['name']: c for c in manifest}
    names = json.loads((ROOT / 'pilot-manifest.json').read_text())
    assert all(by_name[n]['split'] == 'development' for n in names)
    jobs = [(n, m, 101) for n in names for m in ('default', 'guide1', 'router')]
    random.Random(83006).shuffle(jobs)
    rows = []
    for n, m, s in jobs:
        frozen_check()
        p = subprocess.run([sys.executable, '/content/data_label_colab.py', 'worker', n, m, str(s)], capture_output=True, text=True, timeout=45)
        if p.returncode:
            raise RuntimeError(p.stderr[-3000:] + p.stdout[-1000:])
        r = json.loads(p.stdout.strip().splitlines()[-1])
        audit(r)
        r.update(family=by_name[n]['family'], split=by_name[n]['split'], independent_certificate_audit=True)
        assert r['bins'] >= by_name[n]['features']['area_lower_bound']
        rows.append(r)
        (ROOT / 'labels' / 'rows.json').write_text(json.dumps(rows, indent=2))
        print('LABELS', len(rows), len(jobs), n, m, r['bins'], flush=True)
    pairs = []
    for n in names:
        rr = {r['method']: r for r in rows if r['instance'] == n}
        pair = dict(name=n, family=by_name[n]['family'], split=by_name[n]['split'], branch=rr['router']['branch'],
                    default=rr['default']['bins'], guide1=rr['guide1']['bins'], router=rr['router']['bins'],
                    best_observed_methods=[m for m, r in rr.items() if r['bins'] == min(x['bins'] for x in rr.values())])
        pairs.append(pair)
        by_name[n]['solver_label_status'] = 'development_pilot_3_methods_1_seed'
    summary = dict(instances=len(names), runs=len(rows), all_valid=True, independent_certificates_checked=len(rows),
                   maximum_wall_s=max(r['wall_s'] for r in rows), maximum_gate_s=max(r['gate_s'] for r in rows),
                   branch_errors=[r for r in rows if 'branch_error' in r],
                   over_10_1_seconds=[dict(instance=r['instance'], method=r['method'], wall_s=r['wall_s']) for r in rows if r['wall_s'] > 10.1],
                   validation_or_test_runs=0, seed=101, budget_s=10,
                   label_scope='Pilot development outcomes, one seed; not full specialist counterfactuals outside the gate and not a generalization estimate.')
    for ref in ('default', 'guide1'):
        summary[ref] = dict(wins=sum(p['router'] < p[ref] for p in pairs), ties=sum(p['router'] == p[ref] for p in pairs), losses=sum(p['router'] > p[ref] for p in pairs),
                            reference_total=sum(p[ref] for p in pairs), router_total=sum(p['router'] for p in pairs), differences=[p for p in pairs if p['router'] != p[ref]])
    (ROOT / 'labels' / 'summary.json').write_text(json.dumps(summary, indent=2))
    (ROOT / 'labels' / 'paired-labels.json').write_text(json.dumps(pairs, indent=2))
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('LABELS_COMPLETE', json.dumps(summary), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        print(json.dumps(worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
    else:
        main()
