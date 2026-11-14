"""Sequential regression, counterfactual labels and frozen validation, Colab only."""
import collections, hashlib, json, pathlib, random, subprocess, sys
ROOT = pathlib.Path('/content/route-study')
CORPUS = pathlib.Path('/content/data-expansion/corpus')


def check_frozen():
    p = json.loads((ROOT / 'protocol.json').read_text())
    for name, digest in p['frozen_source_sha256'].items():
        assert hashlib.sha256(pathlib.Path('/content', name).read_bytes()).hexdigest() == digest, name


def worker(name, method, seed):
    import routing_core_fixed_colab as rc
    import route_features_colab as ft
    check_frozen()
    rc.DATA = CORPUS / 'data'
    rc.OUT = ROOT / 'certificates' / f'{method}_{seed}'
    rc.OUT.mkdir(parents=True, exist_ok=True)
    rc.alg.q.pj.DATA, rc.alg.OUT = rc.DATA, rc.OUT
    internal = 'router' if method in ('old_router', 'learned_router') else method
    if method == 'learned_router':
        frozen = json.loads((ROOT / 'learned-freeze.json').read_text())
        assert hashlib.sha256((ROOT / 'learned-policy.json').read_bytes()).hexdigest() == frozen['policy_sha256']
        policy = json.loads((ROOT / 'learned-policy.json').read_text())
        old_choose = rc.choose
        rc.read_features = lambda n: ft.read(n, rc.DATA, rc.features)
        rc.choose = lambda f: 'strips' if old_choose(f) == 'strips' and ft.accept(f, policy) else 'default'
    r = rc.run(name, internal, seed, budget=10)
    r['method'] = method
    r['certificate'] = str(pathlib.Path(f'{method}_{seed}') / r['certificate'])
    return r


def run(phase):
    import data_label_colab as auditor
    auditor.ROOT = ROOT
    cases = json.loads((CORPUS / 'manifest.json').read_text())
    by_name = {c['name']: c for c in cases}
    if phase == 'regression':
        copies = ['data_c4bd292e1e4fa8b2', 'data_e16ccfd6e72b357e']
        known = ['data_5248a5778eb6586f', 'data_59d6eb7717d27fa0']
        jobs = [(n, m, 202) for n in copies for m in ('default', 'old_router')]
        jobs += [(n, m, 202) for n in known for m in ('default', 'guide1', 'strips')]
    else:
        manifest = json.loads((ROOT / f'{phase}-manifest.json').read_text())
        assert all(c['split'] == ('development' if phase == 'development' else 'validation') for c in manifest)
        methods, seeds = (['default', 'guide1', 'strips'], [201]) if phase == 'development' else (['default', 'guide1', 'old_router', 'learned_router'], [301, 302])
        jobs = [(c['name'], m, s) for c in manifest for m in methods for s in seeds]
    random.Random({'regression': 84002, 'development': 84003, 'validation': 84004}[phase]).shuffle(jobs)
    rows = []
    # Resume only exact completed jobs, with certificates re-audited.
    path = ROOT / f'{phase}-rows.json'
    if path.exists():
        rows = json.loads(path.read_text())
        for r in rows:
            auditor.audit(r)
    done = {(r['instance'], r['method'], r['seed']) for r in rows}
    for n, m, s in jobs:
        if (n, m, s) in done:
            continue
        check_frozen()
        for suffix in ('items', 'bins'):
            src = CORPUS / 'data' / f'{n}_{suffix}.csv'
            dst = ROOT / 'data' / src.name
            if not dst.exists():
                dst.write_bytes(src.read_bytes())
        p = subprocess.run([sys.executable, '/content/route_study_colab.py', 'worker', n, m, str(s)], capture_output=True, text=True, timeout=45)
        if p.returncode:
            raise RuntimeError(p.stderr[-3500:] + p.stdout[-1000:])
        r = json.loads(p.stdout.strip().splitlines()[-1])
        auditor.audit(r)
        r.update(family=by_name[n]['family'], split=by_name[n]['split'], group_id=by_name[n]['group_id'], independent_certificate_audit=True)
        assert r['bins'] >= by_name[n]['features']['area_lower_bound']
        if by_name[n]['family'] == 'repeated_demand':
            assert 'branch_error' not in r and r['status'] == 'ORIGINAL_AUTOMATIC'
        rows.append(r)
        path.write_text(json.dumps(rows, indent=2))
        print(phase.upper(), len(rows), len(jobs), n, m, s, r['bins'], flush=True)
    summary = dict(runs=len(rows), instances=len({r['instance'] for r in rows}), all_valid=True,
                   max_wall_s=max(r['wall_s'] for r in rows), branch_errors=[r for r in rows if 'branch_error' in r],
                   over_10_1=[r for r in rows if r['wall_s'] > 10.1])
    if phase == 'validation':
        for candidate in ('old_router', 'learned_router'):
            summary[candidate] = {}
            for ref in ('default', 'guide1', 'old_router'):
                if candidate == ref:
                    continue
                pairs = []
                for r in rows:
                    if r['method'] != candidate:
                        continue
                    b = next(b for b in rows if b['method'] == ref and b['instance'] == r['instance'] and b['seed'] == r['seed'])
                    pairs.append(dict(instance=r['instance'], seed=r['seed'], reference=b['bins'], candidate=r['bins'], branch=r['branch'], family=r['family']))
                summary[candidate][ref] = dict(wins=sum(p['candidate'] < p['reference'] for p in pairs), ties=sum(p['candidate'] == p['reference'] for p in pairs), losses=sum(p['candidate'] > p['reference'] for p in pairs), reference_total=sum(p['reference'] for p in pairs), candidate_total=sum(p['candidate'] for p in pairs), differences=[p for p in pairs if p['candidate'] != p['reference']])
    (ROOT / f'{phase}-summary.json').write_text(json.dumps(summary, indent=2))
    print(phase.upper() + '_COMPLETE', json.dumps(summary), flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'worker':
        print(json.dumps(worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
    else:
        run(sys.argv[1])
