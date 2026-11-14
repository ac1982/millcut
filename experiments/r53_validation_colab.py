"""R53: Frozen Unconsumed Validation Study (36 instances x 6 families x 2 seeds = 288 runs).

Strictly verifies frozen R52 SHA256 hashes before every run, asserts every validation instance
is unconsumed in `current-label-status.json` and group-disjoint from development, runs each
query sequentially in an isolated subprocess under a matched 10.0s online budget, and separates
true branch-switch sheet savings from same-branch timed-search variability.
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import random
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path('/content/mill-study/r53')
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'certificates').mkdir(exist_ok=True)

CORPUS = pathlib.Path('/content/data-expansion/corpus')
R52_ROOT = pathlib.Path('/content/mill-study/r52')
NATIVE_BIN = pathlib.Path('/content/native-check/ejection')


def verify_frozen_artifacts() -> dict[str, str]:
    fit_rep = json.loads((R52_ROOT / 'fit-report.json').read_text())
    expected = fit_rep['frozen_sha256']
    paths = {
        'colstrip_gen.cpp': pathlib.Path('/content/colstrip_gen.cpp'),
        'libcolstrip.so': pathlib.Path('/content/libcolstrip.so'),
        'paper_mill_cutter.py': pathlib.Path('/content/paper_mill_cutter.py'),
        'mill-policy.json': pathlib.Path('/content/mill-study/mill-policy.json'),
    }
    for key, p in paths.items():
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        assert actual == expected[key], f'Frozen SHA256 mismatch for {key}: {actual} != {expected[key]}'
    return expected


def build_validation_manifest() -> list[dict[str, Any]]:
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    label_status = json.loads(pathlib.Path('/content/current-label-status.json').read_text())
    dev_groups = {c['group_id'] for c in manifest if c['split'] in ('development', 'historical_development')}
    consumed_groups = {
        c['group_id'] for c in manifest
        if label_status.get(c['name'], {}).get('validation_consumed', False)
    }

    def _rank(c: dict[str, Any]) -> str:
        return hashlib.sha256(('r53_val_v1/' + c['name']).encode()).hexdigest()

    families = [
        'shelf_same_family',
        'shelf_varied',
        'height_scrambled',
        'independent_random',
        'gate_boundary',
        'repeated_demand',
    ]
    selected: list[dict[str, Any]] = []
    for fam in families:
        pool = sorted(
            [
                c for c in manifest
                if c['split'] == 'validation'
                and c['family'] == fam
                and not label_status.get(c['name'], {}).get('validation_consumed', False)
                and c['group_id'] not in dev_groups
                and c['group_id'] not in consumed_groups
            ],
            key=_rank,
        )
        if fam == 'gate_boundary':
            wide = [c for c in pool if not c['features']['supported_width']][:2]
            rest = [c for c in pool if c not in wide][:4]
            chosen = wide + rest
        else:
            chosen = pool[:6]
        assert len(chosen) == 6, f'Insufficient unconsumed validation for {fam}'
        selected.extend(chosen)

    assert len(selected) == 36
    return selected


def run_single_worker(name: str, method: str, seed: int) -> dict[str, Any]:
    sys.path.insert(0, '/content')
    verify_frozen_artifacts()
    data_dir = CORPUS / 'data'
    b_csv = data_dir / f'{name}_bins.csv'
    i_csv = data_dir / f'{name}_items.csv'
    cert_dir = ROOT / 'certificates' / f'{method}_{seed}'
    cert_dir.mkdir(parents=True, exist_ok=True)

    if method == 'r50_learned_router':
        import routing_core_fixed_colab as rc
        import route_features_colab as ft

        rc.DATA = data_dir
        rc.OUT = cert_dir
        rc.alg.q.pj.DATA, rc.alg.OUT = data_dir, cert_dir
        r50_policy = json.loads(pathlib.Path('/content/learned-policy.json').read_text())
        old_choose = rc.choose
        rc.read_features = lambda n: ft.read(n, data_dir, rc.features)
        rc.choose = lambda f: 'strips' if old_choose(f) == 'strips' and ft.accept(f, r50_policy) else 'default'
        r = rc.run(name, 'router', seed, budget=10.0)
        r['method'] = 'r50_learned_router'
        r['winning_engine'] = r['branch']
        r.pop('features', None)
        return r

    import paper_mill_cutter as pmc
    pol = json.loads(pathlib.Path('/content/mill-study/mill-policy.json').read_text())
    out_csv = cert_dir / f'{name}_{method}_{seed}.csv'
    r = pmc.solve_order_online(
        bins_csv=b_csv,
        items_csv=i_csv,
        out_csv=out_csv,
        method=method,
        seed=seed,
        budget_s=10.0,
        policy=pol if method == 'mill_router' else None,
        check_native_binary=NATIVE_BIN,
    )
    r['instance'] = name
    r.pop('features', None)
    return r


def run_validation() -> None:
    frozen = verify_frozen_artifacts()
    cases = build_validation_manifest()
    (ROOT / 'validation-manifest.json').write_text(json.dumps(cases, indent=2))
    by_name = {c['name']: c for c in cases}

    methods = ['default', 'guide1', 'r50_learned_router', 'mill_router']
    seeds = [401, 402]
    jobs = [(c['name'], m, s) for s in seeds for c in cases for m in methods]
    random.Random(85301).shuffle(jobs)

    protocol = {
        'study': 'R53_frozen_unconsumed_validation',
        'budget_s': 10.0,
        'instances': len(cases),
        'seeds': seeds,
        'methods': methods,
        'total_runs': len(jobs),
        'frozen_sha256': frozen,
    }
    (ROOT / 'protocol.json').write_text(json.dumps(protocol, indent=2))

    rows: list[dict[str, Any]] = []
    rows_path = ROOT / 'validation-rows.json'
    if rows_path.exists():
        rows = json.loads(rows_path.read_text())
    done_keys = {(r['instance'], r['method'], r['seed']) for r in rows if r.get('valid')}

    for idx, (name, m, s) in enumerate(jobs, 1):
        if (name, m, s) in done_keys:
            continue
        verify_frozen_artifacts()
        proc = subprocess.run(
            [sys.executable, '/content/r53_validation_colab.py', 'worker', name, m, str(s)],
            capture_output=True, text=True, timeout=45,
        )
        if proc.returncode != 0:
            raise RuntimeError(f'Worker failed on {(name, m, s)}:\n{proc.stderr[-2500:]}\n{proc.stdout[-1000:]}')
        row = json.loads(proc.stdout.strip().splitlines()[-1])
        c = by_name[name]
        row.update(family=c['family'], split=c['split'], group_id=c['group_id'])
        assert row['valid'] and row['bins'] >= c['features']['area_lower_bound']
        rows.append(row)
        rows_path.write_text(json.dumps(rows, indent=2))
        print(
            f'R53 [{len(rows)}/{len(jobs)}] {name} ({c["family"]}) {m} s={s}: '
            f'bins={row["bins"]} br={row["branch"]} wall={row["wall_s"]:.2f}s',
            flush=True,
        )

    # Compute comprehensive validation summary including utilization, waste, and branch-switch decomposition
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc
    data_dir = CORPUS / 'data'
    per_inst_areas = {}
    tot_item_area = 0
    for c in cases:
        W, H, items = pmc.read_order_files(data_dir / f"{c['name']}_bins.csv", data_dir / f"{c['name']}_items.csv")
        ia = sum(it['w'] * it['h'] * it['copies'] for it in items)
        per_inst_areas[c['name']] = (W * H, ia)
        tot_item_area += ia * len(seeds)

    lookup = {(r['instance'], r['method'], r['seed']): r for r in rows}
    m_totals = {}
    for m in methods:
        tot_bins = sum(lookup[(c['name'], m, s)]['bins'] for c in cases for s in seeds)
        tot_sheet_area = sum(
            lookup[(c['name'], m, s)]['bins'] * per_inst_areas[c['name']][0]
            for c in cases for s in seeds
        )
        waste = tot_sheet_area - tot_item_area
        m_totals[m] = {
            'total_bins': tot_bins,
            'aggregate_utilization_pct': round(100.0 * tot_item_area / max(1, tot_sheet_area), 4),
            'aggregate_waste_pct': round(100.0 * waste / max(1, tot_sheet_area), 4),
            'aggregate_waste_area': waste,
            'mean_wall_s': round(
                sum(lookup[(c['name'], m, s)]['wall_s'] for c in cases for s in seeds) / (len(cases) * len(seeds)), 4
            ),
        }

    summary: dict[str, Any] = {
        'instances': len(cases),
        'seeds': seeds,
        'total_runs': len(rows),
        'all_valid': all(r['valid'] for r in rows),
        'max_wall_s': round(max(r['wall_s'] for r in rows), 4),
        'over_10_1s_count': sum(1 for r in rows if r['wall_s'] > 10.1),
        'method_totals': m_totals,
        'comparisons': {},
        'family_totals': {},
    }

    for cand in ('mill_router', 'r50_learned_router', 'guide1'):
        summary['comparisons'][cand] = {}
        for ref in ('default', 'guide1', 'r50_learned_router'):
            if cand == ref:
                continue
            wins = ties = losses = 0
            switch_wins = switch_losses = same_branch_diffs = 0
            diffs = []
            for s in seeds:
                for c in cases:
                    rc = lookup[(c['name'], cand, s)]
                    rr = lookup[(c['name'], ref, s)]
                    bc, br_ref = rc['bins'], rr['bins']
                    same_br = (rc['branch'] == rr['branch'])
                    if bc < br_ref:
                        wins += 1
                        if not same_br:
                            switch_wins += 1
                        else:
                            same_branch_diffs += 1
                    elif bc > br_ref:
                        losses += 1
                        if not same_br:
                            switch_losses += 1
                        else:
                            same_branch_diffs += 1
                    else:
                        ties += 1
                    if bc != br_ref:
                        diffs.append({
                            'instance': c['name'],
                            'family': c['family'],
                            'seed': s,
                            'cand_bins': bc,
                            'ref_bins': br_ref,
                            'cand_branch': rc['branch'],
                            'ref_branch': rr['branch'],
                            'cand_engine': rc.get('winning_engine'),
                        })
            cand_tot = summary['method_totals'][cand]['total_bins']
            ref_tot = summary['method_totals'][ref]['total_bins']
            summary['comparisons'][cand][ref] = {
                'net_sheets_saved': ref_tot - cand_tot,
                'wins': wins,
                'ties': ties,
                'losses': losses,
                'true_branch_switch_wins': switch_wins,
                'true_branch_switch_losses': switch_losses,
                'same_branch_search_fluctuations': same_branch_diffs,
                'differences': diffs,
            }

    for fam in sorted({c['family'] for c in cases}):
        fc = [c for c in cases if c['family'] == fam]
        summary['family_totals'][fam] = {
            m: sum(lookup[(c['name'], m, s)]['bins'] for c in fc for s in seeds)
            for m in methods
        }

    (ROOT / 'validation-summary.json').write_text(json.dumps(summary, indent=2))
    print('R53_VALIDATION_COMPLETE', json.dumps(summary), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        print(json.dumps(run_single_worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
    else:
        run_validation()
