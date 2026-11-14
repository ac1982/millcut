"""R55: Final Held-Out Test Study (`test` + `test_external` Martello1998) & Manufactory Packages.

Strictly uses the frozen R54 v2 code & policy SHA256 hashes without any post-validation edits.
Evaluates 48 held-out test instances (36 synthetic `test` across all 6 families + 12 external
`public_martello1998` instances) across 2 seeds (501, 502) under matched 10.0s online budget,
and exports full 6-file Paper Manufactory Deliverable Packages on representative orders.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import random
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path('/content/mill-study/r55')
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'certificates').mkdir(exist_ok=True)
(ROOT / 'manufactory_packages').mkdir(exist_ok=True)

CORPUS = pathlib.Path('/content/data-expansion/corpus')
R54_ROOT = pathlib.Path('/content/mill-study/r54')
NATIVE_BIN = pathlib.Path('/content/native-check/ejection')


def verify_frozen_artifacts() -> dict[str, str]:
    fit_rep = json.loads((R54_ROOT / 'fit-report-v2.json').read_text())
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


def build_heldout_test_manifest() -> list[dict[str, Any]]:
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    label_status = json.loads(pathlib.Path('/content/current-label-status.json').read_text())
    non_test_groups = {
        c['group_id'] for c in manifest
        if c['split'] not in ('test', 'test_external')
    }

    def _rank(c: dict[str, Any]) -> str:
        return hashlib.sha256(('r54_heldout_v1/' + c['name']).encode()).hexdigest()

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
                if c['split'] == 'test'
                and c['family'] == fam
                and c['name'] not in label_status
                and c['group_id'] not in non_test_groups
            ],
            key=_rank,
        )
        if fam == 'gate_boundary':
            wide = [c for c in pool if not c['features']['supported_width']][:2]
            rest = [c for c in pool if c not in wide][:4]
            chosen = wide + rest
        else:
            chosen = pool[:6]
        assert len(chosen) == 6, f'Insufficient test instances for {fam}'
        selected.extend(chosen)

    ext_pool = sorted(
        [
            c for c in manifest
            if c['split'] == 'test_external'
            and c['name'] not in label_status
            and c['group_id'] not in non_test_groups
        ],
        key=_rank,
    )
    selected.extend(ext_pool[:12])
    assert len(selected) == 48
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


def run_heldout_test() -> None:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    frozen = verify_frozen_artifacts()
    cases = build_heldout_test_manifest()
    (ROOT / 'test-manifest.json').write_text(json.dumps(cases, indent=2))
    by_name = {c['name']: c for c in cases}

    methods = ['default', 'guide1', 'r50_learned_router', 'mill_router']
    seeds = [501, 502]
    jobs = [(c['name'], m, s) for s in seeds for c in cases for m in methods]
    random.Random(95501).shuffle(jobs)

    protocol = {
        'study': 'R55_frozen_heldout_test_and_external_martello1998',
        'budget_s': 10.0,
        'instances': len(cases),
        'synthetic_test_instances': 36,
        'external_martello1998_instances': 12,
        'seeds': seeds,
        'methods': methods,
        'total_runs': len(jobs),
        'frozen_sha256': frozen,
    }
    (ROOT / 'protocol.json').write_text(json.dumps(protocol, indent=2))

    rows: list[dict[str, Any]] = []
    rows_path = ROOT / 'test-rows.json'
    if rows_path.exists():
        rows = json.loads(rows_path.read_text())
    done_keys = {(r['instance'], r['method'], r['seed']) for r in rows if r.get('valid')}

    for name, m, s in jobs:
        if (name, m, s) in done_keys:
            continue
        verify_frozen_artifacts()
        proc = subprocess.run(
            [sys.executable, '/content/r55_heldout_test_colab.py', 'worker', name, m, str(s)],
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
            f'R55 [{len(rows)}/{len(jobs)}] {name} ({c["family"]}) {m} s={s}: '
            f'bins={row["bins"]} br={row["branch"]} wall={row["wall_s"]:.2f}s',
            flush=True,
        )

    lookup = {(r['instance'], r['method'], r['seed']): r for r in rows}

    def _summarize_subset(sub_cases: list[dict[str, Any]]) -> dict[str, Any]:
        tot_item_area = 0
        data_dir = CORPUS / 'data'
        per_inst_areas = {}
        for c in sub_cases:
            W, H, items = pmc.read_order_files(data_dir / f"{c['name']}_bins.csv", data_dir / f"{c['name']}_items.csv")
            ia = sum(it['w'] * it['h'] * it['copies'] for it in items)
            per_inst_areas[c['name']] = (W * H, ia)
            tot_item_area += ia * len(seeds)

        m_stats = {}
        for m in methods:
            tot_bins = sum(lookup[(c['name'], m, s)]['bins'] for c in sub_cases for s in seeds)
            tot_sheet_area = sum(
                lookup[(c['name'], m, s)]['bins'] * per_inst_areas[c['name']][0]
                for c in sub_cases for s in seeds
            )
            waste = tot_sheet_area - tot_item_area
            m_stats[m] = {
                'total_bins': tot_bins,
                'aggregate_utilization_pct': round(100.0 * tot_item_area / max(1, tot_sheet_area), 4),
                'aggregate_waste_pct': round(100.0 * waste / max(1, tot_sheet_area), 4),
                'aggregate_waste_area': waste,
                'mean_wall_s': round(
                    sum(lookup[(c['name'], m, s)]['wall_s'] for c in sub_cases for s in seeds)
                    / (len(sub_cases) * len(seeds)), 4
                ),
            }

        comps = {}
        for cand in ('mill_router', 'r50_learned_router', 'guide1'):
            comps[cand] = {}
            for ref in ('default', 'guide1', 'r50_learned_router'):
                if cand == ref:
                    continue
                wins = ties = losses = sw_wins = sw_losses = same_flips = 0
                diffs = []
                for s in seeds:
                    for c in sub_cases:
                        rc = lookup[(c['name'], cand, s)]
                        rr = lookup[(c['name'], ref, s)]
                        bc, br_ref = rc['bins'], rr['bins']
                        same_br = (rc['branch'] == rr['branch']) or ({rc['branch'], rr['branch']} == {'colstrip', 'strips'})
                        if bc < br_ref:
                            wins += 1
                            if not same_br:
                                sw_wins += 1
                            else:
                                same_flips += 1
                        elif bc > br_ref:
                            losses += 1
                            if not same_br:
                                sw_losses += 1
                            else:
                                same_flips += 1
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
                comps[cand][ref] = {
                    'net_sheets_saved': m_stats[ref]['total_bins'] - m_stats[cand]['total_bins'],
                    'wins': wins,
                    'ties': ties,
                    'losses': losses,
                    'true_branch_switch_wins': sw_wins,
                    'true_branch_switch_losses': sw_losses,
                    'specialist_search_fluctuations': same_flips,
                    'differences': diffs,
                }
        return {'instances': len(sub_cases), 'runs_per_method': len(sub_cases) * len(seeds), 'methods': m_stats, 'comparisons': comps}

    synth_cases = [c for c in cases if c['split'] == 'test']
    ext_cases = [c for c in cases if c['split'] == 'test_external']
    fam_totals = {}
    for fam in sorted({c['family'] for c in cases}):
        fc = [c for c in cases if c['family'] == fam]
        fam_totals[fam] = {
            m: sum(lookup[(c['name'], m, s)]['bins'] for c in fc for s in seeds)
            for m in methods
        }

    summary = {
        'all_valid': all(r['valid'] for r in rows),
        'max_wall_s': round(max(r['wall_s'] for r in rows), 4),
        'over_10_1s_count': sum(1 for r in rows if r['wall_s'] > 10.1),
        'overall_heldout_48': _summarize_subset(cases),
        'synthetic_test_36': _summarize_subset(synth_cases),
        'external_martello1998_12': _summarize_subset(ext_cases),
        'family_totals': fam_totals,
    }

    exported_packages = []
    win_candidates = [
        c for c in cases
        if lookup[(c['name'], 'mill_router', 501)]['bins'] < lookup[(c['name'], 'default', 501)]['bins']
    ]
    pkg_targets = win_candidates[:3] + ext_cases[:1]
    seen_pkg = set()
    for c in pkg_targets:
        if c['name'] in seen_pkg:
            continue
        seen_pkg.add(c['name'])
        rm = lookup[(c['name'], 'mill_router', 501)]
        rd = lookup[(c['name'], 'default', 501)]
        rg = lookup[(c['name'], 'guide1', 501)]
        cert_src = ROOT / 'certificates' / 'mill_router_501' / rm['certificate']
        pkg_dir = ROOT / 'manufactory_packages' / c['name']
        pkg_out = pmc.export_manufactory_package(
            bins_csv=CORPUS / 'data' / f"{c['name']}_bins.csv",
            items_csv=CORPUS / 'data' / f"{c['name']}_items.csv",
            cut_tree_csv=cert_src,
            package_dir=pkg_dir,
            order_name=c['name'],
            lower_bound=rm.get('bound'),
            baseline_comparison={
                'default_10s_sheets': rd['bins'],
                'guide1_10s_sheets': rg['bins'],
                'mill_router_10s_sheets': rm['bins'],
                'sheets_saved_vs_default': rd['bins'] - rm['bins'],
                'sheets_saved_vs_guide1': rg['bins'] - rm['bins'],
            },
            check_native_binary=NATIVE_BIN,
        )
        exported_packages.append(pkg_out['material_summary'])

    summary['exported_manufactory_packages'] = exported_packages
    (ROOT / 'test-summary.json').write_text(json.dumps(summary, indent=2))
    print('R55_HELDOUT_TEST_COMPLETE', json.dumps(summary), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        print(json.dumps(run_single_worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
    else:
        run_heldout_test()
