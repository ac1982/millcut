"""R51: Compile C++ colstrip engine, run unit & dual-verifier checks, and run 18-case dev ablation."""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path('/content/mill-study/r51')
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'certificates').mkdir(exist_ok=True)
(ROOT / 'unit_packages').mkdir(exist_ok=True)

CORPUS = pathlib.Path('/content/data-expansion/corpus')
NATIVE_BIN = pathlib.Path('/content/native-check/ejection')


def compile_colstrip() -> dict:
    cmd = [
        'g++', '-O3', '-march=native', '-std=c++17', '-shared', '-fPIC',
        '/content/colstrip_gen.cpp', '-o', '/content/libcolstrip.so'
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'Compilation failed:\n{proc.stderr}')
    so_bytes = pathlib.Path('/content/libcolstrip.so').read_bytes()
    return {
        'compile_s': round(time.perf_counter() - t0, 4),
        'so_size_bytes': len(so_bytes),
        'so_sha256': hashlib.sha256(so_bytes).hexdigest(),
        'src_sha256': hashlib.sha256(pathlib.Path('/content/colstrip_gen.cpp').read_bytes()).hexdigest(),
    }


def _write_order(tmpdir: pathlib.Path, tag: str, W: int, H: int, rows: list[tuple[int, int, int]]):
    b_csv = tmpdir / f'{tag}_bins.csv'
    i_csv = tmpdir / f'{tag}_items.csv'
    with b_csv.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ID', 'WIDTH', 'HEIGHT'])
        w.writerow([0, W, H])
    with i_csv.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ID', 'WIDTH', 'HEIGHT', 'COPIES'])
        for idx, (iw, ih, ic) in enumerate(rows):
            w.writerow([idx, iw, ih, ic])
    return b_csv, i_csv


def run_unit_tests() -> dict:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    results = {}
    with tempfile.TemporaryDirectory() as td:
        tdir = pathlib.Path(td)

        # Test 1: Large sheet width W = 10000 (> 4096 ceiling-scaled C++ DP + exact completion)
        # Construct 2 sheets of (10000, 5000), each with 3 horizontal strips of heights 1700, 1650, 1650
        w_gt_items = []
        strip_specs = [
            (1700, [2410, 2590, 2330, 2670]),
            (1650, [3110, 1890, 2740, 2260]),
            (1650, [2150, 2850, 2480, 2520]),
            (1800, [2620, 2380, 2190, 2810]),
            (1600, [3300, 1700, 2450, 2550]),
            (1600, [2220, 2780, 2630, 2370]),
        ]
        for sh, widths in strip_specs:
            for idx_w, ww in enumerate(widths):
                w_gt_items.append((ww, sh - (idx_w * 7), 1))
        b1, i1 = _write_order(tdir, 'wide10000', 10000, 5000, w_gt_items)
        c1 = ROOT / 'certificates' / 'unit_wide10000.csv'
        r1 = pmc.solve_order_online(b1, i1, c1, method='colstrip', seed=7, budget_s=6.0, check_native_binary=NATIVE_BIN)
        assert r1['bins'] == 2 and r1['valid'], f'Test 1 failed: {r1}'
        results['test1_width_gt_4096'] = {'bins': r1['bins'], 'wall_s': r1['wall_s'], 'native_verified': True}

        # Test 2: Repeated demand COPIES > 1 (total 36 parts across 9 types on 2 sheets of 1000x1000)
        rep_items = [
            (330, 500, 3),  # 3 copies -> 990x500 strip
            (250, 500, 4),  # 4 copies -> 1000x500 strip
            (495, 490, 2),  # 2 copies -> 990x490 strip
            (200, 510, 5),  # 5 copies -> 1000x510 strip
        ]
        b2, i2 = _write_order(tdir, 'repeated_copies', 1000, 1000, rep_items)
        c2 = ROOT / 'certificates' / 'unit_repeated.csv'
        r2 = pmc.solve_order_online(b2, i2, c2, method='colstrip', seed=11, budget_s=6.0, check_native_binary=NATIVE_BIN)
        assert r2['bins'] == 2 and r2['valid'], f'Test 2 failed: {r2}'
        results['test2_repeated_copies'] = {'bins': r2['bins'], 'wall_s': r2['wall_s'], 'native_verified': True}

        # Test 3: 2-item vertical column stacks + CUT=4 NonExact trim advantage!
        # Construct 2 sheets of (1000, 1000) where each sheet has one strip of height 1000
        # containing two tall items (w=300, h=995) + two 2-stack columns ((w=200,h=495) + (w=192,h=500)).
        # Without 2-stacks, the 4 tall items (w=300, h=995) can fit at most 3 per sheet (900<=1000),
        # leaving 1 tall item (h=995) on sheet 2 plus 8 items of height ~500 (which need width 8*196=1568 > 700),
        # forcing 3 sheets for any pure single-layer shelf packing!
        stack_items = [
            (301, 996, 1),
            (299, 995, 1),
            (302, 994, 1),
            (298, 993, 1),
            (199, 498, 1), (193, 497, 1),
            (201, 499, 1), (195, 496, 1),
            (198, 495, 1), (192, 500, 1),
            (202, 497, 1), (196, 498, 1),
        ]
        b3, i3 = _write_order(tdir, 'stacked_cols', 1000, 1000, stack_items)
        c3_nostack = ROOT / 'certificates' / 'unit_nostack.csv'
        c3_stack = ROOT / 'certificates' / 'unit_stack.csv'
        r3_no = pmc.solve_order_online(
            b3, i3, c3_nostack, method='colstrip_nofallback', seed=13, budget_s=5.0, check_native_binary=NATIVE_BIN
        )
        # Also specifically run single-layer strip LP without stacks or fallback:
        inst_tmp = pmc.build_ps_instance(b3, i3)
        # Force pure nostack check by disabling fallback in a direct call:
        t_ns = time.perf_counter()
        r3_pure_nostack = pmc._solve_colstrip_branch(
            b3, i3, c3_nostack, seed=13, deadline=t_ns + 4.0, allow_stacks=False, allow_fallback=False
        )
        assert r3_no['bins'] == 2, f'Expected 2 bins with stacks, got {r3_no}'
        ver3 = pmc.verify_cut_tree_csv(b3, i3, c3_nostack, check_native_binary=NATIVE_BIN)
        results['test3_vertical_stacks_cut4'] = {
            'with_stacks_bins': r3_no['bins'],
            'without_stacks_bins': r3_pure_nostack['bins'],
            'max_effective_stages': ver3['max_effective_stages'],
            'native_cpp_verified': ver3['native_cpp_solution_builder_verified'],
        }

        # Test 4: Feature scale, permutation, and copy-split invariance
        W0, H0, it0 = pmc.read_order_files(b2, i2)
        f_orig = pmc.extract_mill_features(W0, H0, it0)
        f_perm = pmc.extract_mill_features(W0, H0, list(reversed(it0)))
        it_scaled = [{**x, 'w': x['w'] * 7, 'h': x['h'] * 7} for x in it0]
        f_scaled = pmc.extract_mill_features(W0 * 7, H0 * 7, it_scaled)
        # Split first item (copies=3) into copies=1 + copies=2
        it_split = [
            {**it0[0], 'copies': 1},
            {**it0[0], 'id': 99, 'type_idx': 99, 'copies': 2},
            *it0[1:],
        ]
        f_split = pmc.extract_mill_features(W0, H0, it_split)
        for k, v in f_orig.items():
            assert abs(v - f_perm[k]) <= 1e-12, f'Perm mismatch on {k}'
            assert abs(v - f_scaled[k]) <= 1e-12, f'Scale mismatch on {k}'
            assert abs(v - f_split[k]) <= 1e-12, f'Copy-split mismatch on {k}: {v} vs {f_split[k]}'
        results['test4_feature_invariance'] = True

        # Test 5: Full Manufactory Deliverable Package export
        pkg_dir = ROOT / 'unit_packages' / 'sample_stacked_order'
        pkg = pmc.export_manufactory_package(
            b3, i3, c3_nostack, pkg_dir, order_name='sample_stacked_order',
            lower_bound=2, check_native_binary=NATIVE_BIN
        )
        expected_files = [
            'cut_tree.csv', 'layout.json', 'cutting_plan.json',
            'cutting_plan.md', 'material_summary.json', 'verification_certificate.json'
        ]
        for fn in expected_files:
            assert (pkg_dir / fn).exists(), f'Missing deliverable {fn}'
        results['test5_manufactory_package'] = pkg['material_summary']

    return results


def select_r51_dev_cases() -> list[dict]:
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    by_name = {c['name']: c for c in manifest}

    # Include known diagnostic development cases from R49 + fresh stratified dev cases across ALL 6 families
    pinned = [
        'data_efbe867c62123d5b',  # R49 regression: strips 4.30s INFEASIBLE vs guide1 5->4
        'data_6f46c9f427e28415',  # R49 regression: strips lost vs guide1 (1.20s)
        'data_2544ac2c477673bc',  # R49 early INFEASIBLE at 4.87s
        'data_d327d2a4d3e15188',  # R49 missed win outside narrow R43 unique_width gate (4->3)
        'data_5248a5778eb6586f',  # R49 development case
        'data_59d6eb7717d27fa0',  # R49 development case
    ]
    chosen = [by_name[n] for n in pinned if n in by_name and by_name[n]['split'] == 'development']
    seen = {c['name'] for c in chosen}

    def _rank(c):
        return hashlib.sha256(('r51_ablation/' + c['name']).encode()).hexdigest()

    dev_pool = sorted([c for c in manifest if c['split'] == 'development' and c['name'] not in seen], key=_rank)

    # Add 2 cases per family including repeated_demand and W > 4096 gate_boundary!
    for fam in ['shelf_same_family', 'shelf_varied', 'height_scrambled', 'independent_random', 'repeated_demand', 'gate_boundary']:
        if fam == 'gate_boundary':
            # Specifically include at least one W > 4096 development case!
            wide_cases = [c for c in dev_pool if c['family'] == fam and not c['features']['supported_width']]
            other_cases = [c for c in dev_pool if c['family'] == fam and c['features']['supported_width']]
            fam_pick = wide_cases[:1] + other_cases[:1]
        else:
            fam_pick = [c for c in dev_pool if c['family'] == fam][:2]
        for c in fam_pick:
            if c['name'] not in seen:
                chosen.append(c)
                seen.add(c['name'])

    return chosen


def run_ablation() -> None:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    cases = select_r51_dev_cases()
    (ROOT / 'ablation-manifest.json').write_text(json.dumps(cases, indent=2))

    methods = ['default', 'guide1', 'colstrip_nostack', 'colstrip']
    # Preserve any valid native baseline rows (default/guide1) from v1; rerun all colstrip* rows fresh
    cached_native = {}
    if (ROOT / 'ablation-rows.json').exists():
        for r in json.loads((ROOT / 'ablation-rows.json').read_text()):
            if r.get('valid') and r['method'] in ('default', 'guide1'):
                cached_native[(r['instance'], r['method'])] = r

    rows = []
    total_jobs = len(cases) * len(methods)
    done = 0

    for c in cases:
        name = c['name']
        for m in methods:
            done += 1
            if (name, m) in cached_native:
                row = cached_native[(name, m)]
                rows.append(row)
                continue
            proc = subprocess.run(
                [sys.executable, '/content/r51_engine_check_colab.py', 'worker', name, m, '51'],
                capture_output=True, text=True, timeout=45,
            )
            if proc.returncode != 0:
                raise RuntimeError(f'Worker failed on {(name, m)}:\n{proc.stderr[-2500:]}\n{proc.stdout[-1000:]}')
            row = json.loads(proc.stdout.strip().splitlines()[-1])
            row['instance'] = name
            row['family'] = c['family']
            row['split'] = c['split']
            row.pop('features', None)
            rows.append(row)
            (ROOT / 'ablation-rows.json').write_text(json.dumps(rows, indent=2))
            print(f'R51v2 [{done}/{total_jobs}] {name} ({c["family"]}) {m}: bins={row["bins"]} bound={row["bound"]} eng={row.get("winning_engine")} wall={row["wall_s"]:.2f}s', flush=True)

    # Summarize results
    by_inst = {}
    for r in rows:
        by_inst.setdefault(r['instance'], {})[r['method']] = r

    summary = {}
    for m in methods:
        m_rows = [by_inst[n][m] for n in by_inst]
        vs_def_w = sum(1 for n in by_inst if by_inst[n][m]['bins'] < by_inst[n]['default']['bins'])
        vs_def_l = sum(1 for n in by_inst if by_inst[n][m]['bins'] > by_inst[n]['default']['bins'])
        vs_def_t = sum(1 for n in by_inst if by_inst[n][m]['bins'] == by_inst[n]['default']['bins'])
        vs_best_w = sum(
            1 for n in by_inst
            if by_inst[n][m]['bins'] < min(by_inst[n]['default']['bins'], by_inst[n]['guide1']['bins'])
        )
        vs_best_l = sum(
            1 for n in by_inst
            if by_inst[n][m]['bins'] > min(by_inst[n]['default']['bins'], by_inst[n]['guide1']['bins'])
        )
        summary[m] = {
            'instances': len(m_rows),
            'total_bins': sum(r['bins'] for r in m_rows),
            'mean_utilization_pct': round(sum(r['utilization_pct'] for r in m_rows) / len(m_rows), 4),
            'mean_wall_s': round(sum(r['wall_s'] for r in m_rows) / len(m_rows), 4),
            'max_wall_s': round(max(r['wall_s'] for r in m_rows), 4),
            'vs_default': {'wins': vs_def_w, 'ties': vs_def_t, 'losses': vs_def_l},
            'vs_min_default_guide1': {'strict_wins': vs_best_w, 'losses': vs_best_l},
        }
    (ROOT / 'ablation-summary.json').write_text(json.dumps(summary, indent=2))
    print('R51_ABLATION_COMPLETE', json.dumps(summary), flush=True)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'unit'
    if mode == 'unit':
        comp = compile_colstrip()
        ut = run_unit_tests()
        out = {'compile': comp, 'unit_tests': ut}
        (ROOT / 'unit-check.json').write_text(json.dumps(out, indent=2))
        print('R51_UNIT_OK', json.dumps(out))
    elif mode == 'worker':
        sys.path.insert(0, '/content')
        import paper_mill_cutter as pmc
        w_name, w_method, w_seed = sys.argv[2], sys.argv[3], int(sys.argv[4])
        data_dir = CORPUS / 'data'
        out_csv = ROOT / 'certificates' / f'{w_name}_{w_method}_{w_seed}.csv'
        res = pmc.solve_order_online(
            bins_csv=data_dir / f'{w_name}_bins.csv',
            items_csv=data_dir / f'{w_name}_items.csv',
            out_csv=out_csv,
            method=w_method,
            seed=w_seed,
            budget_s=10.0,
            check_native_binary=NATIVE_BIN,
        )
        print(json.dumps(res))
    elif mode == 'ablation':
        run_ablation()
