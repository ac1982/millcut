"""R54: Post-R53 Diagnostic Refit/Freeze (v2) & Brand-New Unconsumed Validation (24 cases x 2 seeds = 192 runs).

Follows AGENTS.md strictly:
  1. Preserves R51-R53 v1 code, policy, and results untouched; marks all 36 R53 instances as
     reclassified/consumed diagnostic data (never reused as unseen validation).
  2. Fits and freezes `mill-policy.json` (v2) + SHA256 manifest (`fit-report-v2.json`) BEFORE
     opening or running any new validation instance.
  3. Evaluates 24 brand-new unconsumed `validation` instances (4 per family across all 6 synthetic
     families), strictly group-disjoint from `development`, `R50` consumed validation, AND `R53`
     consumed validation, across 2 seeds (411, 412) and 4 methods (`default`, `guide1`,
     `r50_learned_router`, `mill_router`) = 192 sequential 10.0s runs.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import random
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path('/content/mill-study/r54')
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'certificates').mkdir(exist_ok=True)

CORPUS = pathlib.Path('/content/data-expansion/corpus')
R52_ROOT = pathlib.Path('/content/mill-study/r52')
R53_ROOT = pathlib.Path('/content/mill-study/r53')
NATIVE_BIN = pathlib.Path('/content/native-check/ejection')


def fit_and_freeze_v2() -> dict[str, Any]:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    r52_rows = json.loads((R52_ROOT / 'dev-rows.json').read_text())
    r53_rows = json.loads((R53_ROOT / 'validation-rows.json').read_text())
    data_dir = CORPUS / 'data'

    # Reconstruct 68 complete 3-method development records from R52
    by_inst: dict[str, dict[str, Any]] = {}
    for r in r52_rows:
        ent = by_inst.setdefault(r['instance'], {
            'instance': r['instance'],
            'family': r['family'],
            'split': r['split'],
            'group_id': r['group_id'],
            'features': r['features'],
            'bins': {},
        })
        ent['bins'][r['method']] = r['bins']
    dev_records = [v for v in by_inst.values() if len(v['bins']) == 3]

    # Also build diagnostic records from the 36 reclassified R53 instances (now consumed!)
    # using observed default, guide1, and specialist (mill_router/r50 strips) mean bins across seeds 401/402
    r53_by_inst: dict[str, dict[str, Any]] = {}
    for r in r53_rows:
        name = r['instance']
        if name not in r53_by_inst:
            W, H, items = pmc.read_order_files(data_dir / f'{name}_bins.csv', data_dir / f'{name}_items.csv')
            r53_by_inst[name] = {
                'instance': name,
                'family': r['family'],
                'split': 'reclassified_from_r53_validation',
                'group_id': r['group_id'],
                'features': pmc.extract_mill_features(W, H, items),
                'method_seeds': {'default': [], 'guide1': [], 'r50_learned_router': [], 'mill_router': []},
                'r50_branches': [],
                'v1_branches': [],
            }
        r53_by_inst[name]['method_seeds'][r['method']].append(r['bins'])
        if r['method'] == 'r50_learned_router':
            r53_by_inst[name]['r50_branches'].append(r['branch'])
        elif r['method'] == 'mill_router':
            r53_by_inst[name]['v1_branches'].append(r['branch'])

    diag_records: list[dict[str, Any]] = list(dev_records)
    for name, info in r53_by_inst.items():
        d_mean = sum(info['method_seeds']['default']) / 2.0
        g_mean = sum(info['method_seeds']['guide1']) / 2.0
        # Estimate specialist cost from whichever router ran specialist branch, else conservative d_mean
        if 'colstrip' in info['v1_branches'] and 'strips' in info['r50_branches']:
            cs_est = min(
                sum(info['method_seeds']['mill_router']) / 2.0,
                sum(info['method_seeds']['r50_learned_router']) / 2.0,
            )
        elif 'strips' in info['r50_branches']:
            cs_est = sum(info['method_seeds']['r50_learned_router']) / 2.0
        elif 'colstrip' in info['v1_branches']:
            cs_est = sum(info['method_seeds']['mill_router']) / 2.0
        else:
            cs_est = d_mean
        diag_records.append({
            'instance': name,
            'family': info['family'],
            'split': info['split'],
            'group_id': info['group_id'],
            'features': info['features'],
            'bins': {'default': d_mean, 'guide1': g_mean, 'colstrip': cs_est},
        })

    # Candidate 3-branch geometric policies including shape_unique_height_fraction (discovered in R53 diagnosis)
    cs_filters = [
        [{'feature': 'shape_unique_height_fraction', 'op': 'ge', 'threshold': 0.55}],
        [{'feature': 'shape_unique_height_fraction', 'op': 'ge', 'threshold': 0.57}],
        [
            {'feature': 'shape_unique_height_fraction', 'op': 'ge', 'threshold': 0.56},
            {'feature': 'shape_unique_width_fraction', 'op': 'ge', 'threshold': 0.65},
        ],
        [
            {'feature': 'shape_unique_height_fraction', 'op': 'ge', 'threshold': 0.57},
            {'feature': 'shape_unique_width_fraction', 'op': 'ge', 'threshold': 0.70},
        ],
        [{'feature': 'height_cv', 'op': 'ge', 'threshold': 0.22}],
        [{'feature': 'near_height_width_support_ge_one', 'op': 'ge', 'threshold': 0.70}],
    ]
    cands: list[dict[str, Any]] = [
        {'name': 'default_only', 'kind': 'default_only'},
        {'name': 'guide1_only', 'kind': 'guide1_only'},
    ]
    for lb_cs in (6, 8):
        for fl_lo in (0.90, 0.93):
            for fl_hi in (0.985, 0.990):
                for f_idx, extra_conds in enumerate(cs_filters):
                    base_cs_conds = [
                        {'feature': 'area_lower_bound', 'op': 'le', 'threshold': lb_cs},
                        {'feature': 'items', 'op': 'ge', 'threshold': 25},
                        {'feature': 'items', 'op': 'le', 'threshold': 160},
                        {'feature': 'lower_bound_fill', 'op': 'ge', 'threshold': fl_lo},
                        {'feature': 'lower_bound_fill', 'op': 'le', 'threshold': fl_hi},
                        {'feature': 'repeat_fraction', 'op': 'le', 'threshold': 0.20},
                        *extra_conds,
                    ]
                    for lb_g1 in (6, 8, 10):
                        cands.append({
                            'name': f'v2_tri_cs{lb_cs}_f{fl_lo}_{fl_hi}_flt{f_idx}_g1_{lb_g1}_def',
                            'kind': 'rule_list',
                            'rules': [
                                {'branch': 'colstrip', 'conditions': base_cs_conds},
                                {
                                    'branch': 'guide1',
                                    'conditions': [{'feature': 'area_lower_bound', 'op': 'le', 'threshold': lb_g1}],
                                },
                            ],
                            'default_branch': 'default',
                        })

    def _score(recs: list[dict[str, Any]], pol: dict[str, Any]):
        saved_def = 0.0
        loss_def = 0.0
        wins_d = losses_d = strict_w = losses_both = cs_cnt = 0
        tot = 0.0
        for rec in recs:
            br = pmc.evaluate_router_policy(rec['features'], pol)
            if br == 'colstrip':
                cs_cnt += 1
            bp = rec['bins'][br]
            bd = rec['bins']['default']
            bb = min(rec['bins']['default'], rec['bins']['guide1'])
            tot += bp
            if bp < bd - 1e-9:
                wins_d += 1
                saved_def += (bd - bp)
            elif bp > bd + 1e-9:
                losses_d += 1
                loss_def += (bp - bd)
            if bp < bb - 1e-9:
                strict_w += 1
            elif bp > bb + 1e-9:
                losses_both += 1
        reg = (saved_def - 2.5 * loss_def) + 0.30 * strict_w - 0.60 * losses_both
        return reg, {
            'total_bins': round(tot, 3),
            'net_saved_vs_default': round(saved_def - loss_def, 3),
            'wins_vs_default': wins_d,
            'losses_vs_default': losses_d,
            'strict_wins_vs_min_default_guide1': strict_w,
            'losses_vs_min_default_guide1': losses_both,
            'colstrip_selected_count': cs_cnt,
            'regularized_score': round(reg, 4),
        }

    # 5-fold group CV over all 104 development + reclassified-R53 records
    def _fold(gid: str) -> int:
        return int(hashlib.sha256(('cv5_v2/' + gid).encode()).hexdigest()[:8], 16) % 5

    oof_br: dict[str, str] = {}
    fold_names: list[str] = []
    for k in range(5):
        tr = [r for r in diag_records if _fold(r['group_id']) != k]
        va = [r for r in diag_records if _fold(r['group_id']) == k]
        best_p, best_k = None, None
        for pol in cands:
            sc, st = _score(tr, pol)
            key = (sc, -st['losses_vs_default'], -len(pol.get('rules', [{}])[0].get('conditions', [])), st['colstrip_selected_count'])
            if best_k is None or key > best_k:
                best_k, best_p = key, pol
        fold_names.append(best_p['name'])
        for r in va:
            oof_br[r['instance']] = pmc.evaluate_router_policy(r['features'], best_p)

    oof_tot = sum(r['bins'][oof_br[r['instance']]] for r in diag_records)
    def_tot = sum(r['bins']['default'] for r in diag_records)

    best_final_pol, best_final_k, best_final_st = None, None, None
    for pol in cands:
        sc, st = _score(diag_records, pol)
        key = (sc, -st['losses_vs_default'], -len(pol.get('rules', [{}])[0].get('conditions', [])), st['colstrip_selected_count'])
        if best_final_k is None or key > best_final_k:
            best_final_k, best_final_pol, best_final_st = key, pol, st

    policy_path = pathlib.Path('/content/mill-study/mill-policy.json')
    policy_path.write_text(json.dumps(best_final_pol, indent=2))
    (ROOT / 'mill-policy-v2.json').write_text(json.dumps(best_final_pol, indent=2))

    frozen_hashes = {
        'colstrip_gen.cpp': hashlib.sha256(pathlib.Path('/content/colstrip_gen.cpp').read_bytes()).hexdigest(),
        'libcolstrip.so': hashlib.sha256(pathlib.Path('/content/libcolstrip.so').read_bytes()).hexdigest(),
        'paper_mill_cutter.py': hashlib.sha256(pathlib.Path('/content/paper_mill_cutter.py').read_bytes()).hexdigest(),
        'mill-policy.json': hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    }
    fit_rep = {
        'study': 'R54_v2_refit_and_freeze',
        'data_role_change_note': (
            'All 36 R53 validation instances are permanently marked validation_consumed=true '
            'and reclassified into the v2 diagnostic development pool (68 R52 + 36 R53 = 104 instances). '
            'R54 evaluates 24 brand-new unconsumed, group-disjoint validation instances.'
        ),
        'diagnostic_pool_instances': len(diag_records),
        'crossval_5fold_grouped': {
            'default_total_bins': round(def_tot, 3),
            'oof_total_bins': round(oof_tot, 3),
            'oof_net_saved_vs_default': round(def_tot - oof_tot, 3),
            'fold_policies': fold_names,
        },
        'selected_policy_v2': best_final_pol,
        'in_sample_stats': best_final_st,
        'frozen_sha256': frozen_hashes,
    }
    (ROOT / 'fit-report-v2.json').write_text(json.dumps(fit_rep, indent=2))
    print('R54_FREEZE_V2_OK', json.dumps(fit_rep), flush=True)
    return fit_rep


def verify_frozen_artifacts() -> dict[str, str]:
    fit_rep = json.loads((ROOT / 'fit-report-v2.json').read_text())
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


def build_new_unconsumed_validation_manifest() -> list[dict[str, Any]]:
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    label_status = json.loads(pathlib.Path('/content/current-label-status.json').read_text())
    r53_cases = json.loads((R53_ROOT / 'validation-manifest.json').read_text())
    r53_names = {c['name'] for c in r53_cases}
    r53_groups = {c['group_id'] for c in r53_cases}
    dev_groups = {c['group_id'] for c in manifest if c['split'] in ('development', 'historical_development')}
    prior_consumed_groups = {
        c['group_id'] for c in manifest
        if label_status.get(c['name'], {}).get('validation_consumed', False)
    }
    forbidden_groups = dev_groups | prior_consumed_groups | r53_groups

    def _rank(c: dict[str, Any]) -> str:
        return hashlib.sha256(('r54_new_val_v2/' + c['name']).encode()).hexdigest()

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
                and c['name'] not in r53_names
                and not label_status.get(c['name'], {}).get('validation_consumed', False)
                and c['group_id'] not in forbidden_groups
            ],
            key=_rank,
        )
        if fam == 'gate_boundary':
            wide = [c for c in pool if not c['features']['supported_width']][:2]
            rest = [c for c in pool if c not in wide][:2]
            chosen = wide + rest
        else:
            chosen = pool[:4]
        assert len(chosen) == 4, f'Insufficient unconsumed validation for {fam}'
        selected.extend(chosen)

    assert len(selected) == 24
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


def run_validation_v2() -> None:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    if not (ROOT / 'fit-report-v2.json').exists():
        fit_and_freeze_v2()
    frozen = verify_frozen_artifacts()

    cases = build_new_unconsumed_validation_manifest()
    (ROOT / 'validation-v2-manifest.json').write_text(json.dumps(cases, indent=2))
    by_name = {c['name']: c for c in cases}

    methods = ['default', 'guide1', 'r50_learned_router', 'mill_router']
    seeds = [411, 412]
    jobs = [(c['name'], m, s) for s in seeds for c in cases for m in methods]
    random.Random(85402).shuffle(jobs)

    protocol = {
        'study': 'R54_frozen_unconsumed_validation_v2',
        'budget_s': 10.0,
        'instances': len(cases),
        'seeds': seeds,
        'methods': methods,
        'total_runs': len(jobs),
        'frozen_sha256': frozen,
    }
    (ROOT / 'protocol.json').write_text(json.dumps(protocol, indent=2))

    rows: list[dict[str, Any]] = []
    rows_path = ROOT / 'validation-v2-rows.json'
    if rows_path.exists():
        rows = json.loads(rows_path.read_text())
    done_keys = {(r['instance'], r['method'], r['seed']) for r in rows if r.get('valid')}

    for name, m, s in jobs:
        if (name, m, s) in done_keys:
            continue
        verify_frozen_artifacts()
        proc = subprocess.run(
            [sys.executable, '/content/r54_v2_validation_colab.py', 'worker', name, m, str(s)],
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
            f'R54 [{len(rows)}/{len(jobs)}] {name} ({c["family"]}) {m} s={s}: '
            f'bins={row["bins"]} br={row["branch"]} wall={row["wall_s"]:.2f}s',
            flush=True,
        )

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
            wins = ties = losses = sw_wins = sw_losses = same_diffs = 0
            diffs = []
            for s in seeds:
                for c in cases:
                    rc = lookup[(c['name'], cand, s)]
                    rr = lookup[(c['name'], ref, s)]
                    bc, br_ref = rc['bins'], rr['bins']
                    same_br = (rc['branch'] == rr['branch']) or ({rc['branch'], rr['branch']} == {'colstrip', 'strips'})
                    if bc < br_ref:
                        wins += 1
                        if not same_br:
                            sw_wins += 1
                        else:
                            same_diffs += 1
                    elif bc > br_ref:
                        losses += 1
                        if not same_br:
                            sw_losses += 1
                        else:
                            same_diffs += 1
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
            summary['comparisons'][cand][ref] = {
                'net_sheets_saved': m_totals[ref]['total_bins'] - m_totals[cand]['total_bins'],
                'wins': wins,
                'ties': ties,
                'losses': losses,
                'true_branch_switch_wins': sw_wins,
                'true_branch_switch_losses': sw_losses,
                'specialist_search_fluctuations': same_diffs,
                'differences': diffs,
            }

    for fam in sorted({c['family'] for c in cases}):
        fc = [c for c in cases if c['family'] == fam]
        summary['family_totals'][fam] = {
            m: sum(lookup[(c['name'], m, s)]['bins'] for c in fc for s in seeds)
            for m in methods
        }

    (ROOT / 'validation-v2-summary.json').write_text(json.dumps(summary, indent=2))
    print('R54_VALIDATION_V2_COMPLETE', json.dumps(summary), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        print(json.dumps(run_single_worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
    elif len(sys.argv) > 1 and sys.argv[1] == 'freeze':
        fit_and_freeze_v2()
    else:
        run_validation_v2()
