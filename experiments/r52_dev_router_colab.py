"""R52: Stratified 68-instance Development Counterfactual Study & Group-CV Geometric Router Fit.

Strictly uses `development` (60 synthetic instances across all 6 families, 10/family) and
`historical_development` (8 `public_berkey1987` instances). Zero `validation` or `test` instances
are read or touched. Evaluates candidate policies with 5-fold `group_id` cross-validation and
freezes `mill-policy.json` + SHA256 manifest before validation.
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path('/content/mill-study/r52')
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'certificates').mkdir(exist_ok=True)

CORPUS = pathlib.Path('/content/data-expansion/corpus')
R51_ROOT = pathlib.Path('/content/mill-study/r51')
NATIVE_BIN = pathlib.Path('/content/native-check/ejection')


def select_r52_dev_manifest() -> list[dict[str, Any]]:
    manifest = json.loads((CORPUS / 'manifest.json').read_text())

    def _rank(c: dict[str, Any]) -> str:
        return hashlib.sha256(('r52_dev_v1/' + c['name']).encode()).hexdigest()

    # Reuse R51 development instances first (all are strictly split=='development')
    r51_cases = []
    if (R51_ROOT / 'ablation-manifest.json').exists():
        r51_cases = json.loads((R51_ROOT / 'ablation-manifest.json').read_text())
    by_fam_selected: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    seen_names: set[str] = set()
    for c in r51_cases:
        if c['split'] == 'development':
            by_fam_selected[c['family']].append(c)
            seen_names.add(c['name'])

    synth_families = [
        'shelf_same_family',
        'shelf_varied',
        'height_scrambled',
        'independent_random',
        'gate_boundary',
        'repeated_demand',
    ]

    selected: list[dict[str, Any]] = []
    for fam in synth_families:
        cur = list(by_fam_selected[fam])[:10]
        pool = sorted(
            [c for c in manifest if c['split'] == 'development' and c['family'] == fam and c['name'] not in seen_names],
            key=_rank,
        )
        if fam == 'gate_boundary':
            # Ensure coverage of W > 4096, N > 120, and lb > 6 boundary regimes
            wide = [c for c in pool if not c['features']['supported_width']][:3]
            large_n = [c for c in pool if c['features']['items'] > 120 and c not in wide][:2]
            rest = [c for c in pool if c not in wide and c not in large_n]
            ordered_pool = wide + large_n + rest
        else:
            ordered_pool = pool

        for c in ordered_pool:
            if len(cur) >= 10:
                break
            cur.append(c)
            seen_names.add(c['name'])
        assert len(cur) == 10, f'Expected 10 dev cases for {fam}, got {len(cur)}'
        selected.extend(cur)

    # Add 8 historical_development (public_berkey1987) cases spanning diverse classes/sizes
    berkey_pool = sorted(
        [c for c in manifest if c['split'] == 'historical_development'],
        key=_rank,
    )
    selected.extend(berkey_pool[:8])
    assert len(selected) == 68
    assert all(c['split'] in ('development', 'historical_development') for c in selected)
    return selected


def run_dev_labeling() -> list[dict[str, Any]]:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    cases = select_r52_dev_manifest()
    (ROOT / 'dev-manifest.json').write_text(json.dumps(cases, indent=2))

    # Reuse only unmodified native baseline (default/guide1) rows from R51; run all colstrip rows fresh
    cached: dict[tuple[str, str], dict[str, Any]] = {}
    if (R51_ROOT / 'ablation-rows.json').exists():
        for r in json.loads((R51_ROOT / 'ablation-rows.json').read_text()):
            if r['method'] in ('default', 'guide1') and r.get('valid'):
                cached[(r['instance'], r['method'])] = r

    # Also support resuming R52 if partially completed
    rows: list[dict[str, Any]] = []
    if (ROOT / 'dev-rows.json').exists():
        for r in json.loads((ROOT / 'dev-rows.json').read_text()):
            if r.get('valid'):
                cached[(r['instance'], r['method'])] = r

    methods = ['default', 'guide1', 'colstrip']
    data_dir = CORPUS / 'data'
    total = len(cases) * len(methods)
    done = 0

    for c in cases:
        name = c['name']
        b_csv = data_dir / f'{name}_bins.csv'
        i_csv = data_dir / f'{name}_items.csv'
        W, H, items = pmc.read_order_files(b_csv, i_csv)
        feats = pmc.extract_mill_features(W, H, items)

        for m in methods:
            done += 1
            if (name, m) in cached:
                row = dict(cached[(name, m)])
                row['instance'] = name
                row['family'] = c['family']
                row['split'] = c['split']
                row['group_id'] = c['group_id']
                row['features'] = feats
                rows.append(row)
                continue

            proc = subprocess.run(
                [sys.executable, '/content/r52_dev_router_colab.py', 'worker', name, m, '202'],
                capture_output=True, text=True, timeout=45,
            )
            if proc.returncode != 0:
                raise RuntimeError(f'Worker failed on {(name, m)}:\n{proc.stderr[-2500:]}\n{proc.stdout[-1000:]}')
            row = json.loads(proc.stdout.strip().splitlines()[-1])
            row['instance'] = name
            row['family'] = c['family']
            row['split'] = c['split']
            row['group_id'] = c['group_id']
            row['features'] = feats
            rows.append(row)
            (ROOT / 'dev-rows.json').write_text(json.dumps(rows, indent=2))
            print(
                f'R52 [{done}/{total}] {name} ({c["family"]}) {m}: bins={row["bins"]} '
                f'bound={row["bound"]} eng={row.get("winning_engine")} wall={row["wall_s"]:.2f}s',
                flush=True,
            )

    (ROOT / 'dev-rows.json').write_text(json.dumps(rows, indent=2))
    return rows


def _candidate_policies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate interpretable, low-VC-dimension geometric routing policies."""
    cands: list[dict[str, Any]] = [
        {'name': 'default_only', 'kind': 'default_only'},
        {'name': 'guide1_only', 'kind': 'guide1_only'},
        {'name': 'colstrip_only', 'kind': 'colstrip_only'},
    ]

    # Single-condition and two-condition gates sending eligible orders to `colstrip`, else `default` (or `guide1`)
    lb_caps = [6, 8, 10, 12, 16, 25]
    item_caps = [150, 200, 260, 500]
    fill_lows = [0.80, 0.85, 0.88, 0.90, 0.92]
    fill_highs = [0.985, 0.992, 0.997, 1.001]

    for lb_max in lb_caps:
        for n_max in item_caps:
            for fl_lo in fill_lows:
                for fl_hi in fill_highs:
                    for fallback_br in ('default', 'guide1'):
                        cands.append({
                            'name': f'box_lb{lb_max}_n{n_max}_f{fl_lo}_{fl_hi}_else_{fallback_br}',
                            'kind': 'rule_list',
                            'rules': [{
                                'branch': 'colstrip',
                                'conditions': [
                                    {'feature': 'area_lower_bound', 'op': 'le', 'threshold': lb_max},
                                    {'feature': 'items', 'op': 'le', 'threshold': n_max},
                                    {'feature': 'lower_bound_fill', 'op': 'ge', 'threshold': fl_lo},
                                    {'feature': 'lower_bound_fill', 'op': 'le', 'threshold': fl_hi},
                                ],
                            }],
                            'default_branch': fallback_br,
                        })

    # Also test adding one structural feature filter (support / stackability / height_cv / tall_fraction)
    extra_Specs = [
        ('tall_fraction', 'le', [0.35, 0.50, 0.65]),
        ('near_height_width_support_mean', 'ge', [0.35, 0.50, 0.65]),
        ('height_cv', 'le', [0.45, 0.60, 0.75]),
        ('width_cv', 'le', [0.50, 0.65]),
    ]
    for lb_max in (8, 10, 14, 25):
        for fl_lo in (0.85, 0.90):
            for feat, op, thrs in extra_Specs:
                for thr in thrs:
                    cands.append({
                        'name': f'struct_lb{lb_max}_f{fl_lo}_{feat}_{op}_{thr}',
                        'kind': 'rule_list',
                        'rules': [{
                            'branch': 'colstrip',
                            'conditions': [
                                {'feature': 'area_lower_bound', 'op': 'le', 'threshold': lb_max},
                                {'feature': 'items', 'op': 'le', 'threshold': 260},
                                {'feature': 'lower_bound_fill', 'op': 'ge', 'threshold': fl_lo},
                                {'feature': feat, 'op': op, 'threshold': thr},
                            ],
                        }],
                        'default_branch': 'default',
                    })

    # 3-branch policies: colstrip on compact strip-structured regime, guide1 on moderate-lb regime, default on high-lb
    cs_filters = [
        [],
        [{'feature': 'near_height_width_support_ge_one', 'op': 'ge', 'threshold': 0.65}],
        [{'feature': 'near_height_width_support_ge_one', 'op': 'ge', 'threshold': 0.75}],
        [{'feature': 'near_height_width_support_mean', 'op': 'ge', 'threshold': 0.80}],
        [{'feature': 'near_height_width_support_mean', 'op': 'ge', 'threshold': 1.10}],
        [{'feature': 'shape_unique_width_fraction', 'op': 'ge', 'threshold': 0.65}],
        [{'feature': 'shape_unique_width_fraction', 'op': 'ge', 'threshold': 0.72}],
        [{'feature': 'height_cv', 'op': 'ge', 'threshold': 0.22}],
        [{'feature': 'height_cv', 'op': 'le', 'threshold': 0.55}],
        [
            {'feature': 'shape_unique_width_fraction', 'op': 'ge', 'threshold': 0.68},
            {'feature': 'near_height_width_support_mean', 'op': 'ge', 'threshold': 0.75},
        ],
        [
            {'feature': 'shape_unique_width_fraction', 'op': 'ge', 'threshold': 0.68},
            {'feature': 'height_cv', 'op': 'ge', 'threshold': 0.22},
        ],
    ]
    for lb_cs in (6, 8):
        for fl_lo in (0.90, 0.93):
            for fl_hi in (0.982, 0.990):
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
                    # 2-branch (colstrip else default)
                    cands.append({
                        'name': f'cs2_lb{lb_cs}_f{fl_lo}_{fl_hi}_flt{f_idx}_def',
                        'kind': 'rule_list',
                        'rules': [{'branch': 'colstrip', 'conditions': base_cs_conds}],
                        'default_branch': 'default',
                    })
                    # 3-branch (colstrip -> guide1 for lb <= lb_g1 -> default)
                    for lb_g1 in (6, 8, 10, 12):
                        cands.append({
                            'name': f'tri_cs{lb_cs}_f{fl_lo}_{fl_hi}_flt{f_idx}_g1_{lb_g1}_def',
                            'kind': 'rule_list',
                            'rules': [
                                {'branch': 'colstrip', 'conditions': base_cs_conds},
                                {
                                    'branch': 'guide1',
                                    'conditions': [
                                        {'feature': 'area_lower_bound', 'op': 'le', 'threshold': lb_g1},
                                    ],
                                },
                            ],
                            'default_branch': 'default',
                        })

    return cands


def _score_policy(records: list[dict[str, Any]], pol: dict[str, Any], eval_fn) -> tuple[float, dict[str, Any]]:
    """Score policy: maximize net sheets saved vs default minus 2x regressions vs default,
    plus bonus for strict wins vs min(default, guide1)."""
    saved_vs_def = 0
    loss_bins_vs_def = 0
    wins_def = ties_def = losses_def = 0
    strict_wins_both = 0
    losses_vs_both = 0
    total_bins = 0
    colstrip_selected = 0

    for rec in records:
        br = eval_fn(rec['features'], pol)
        if br == 'colstrip':
            colstrip_selected += 1
        b_pol = rec['bins'][br]
        b_def = rec['bins']['default']
        b_best_nat = min(rec['bins']['default'], rec['bins']['guide1'])
        total_bins += b_pol

        if b_pol < b_def:
            wins_def += 1
            saved_vs_def += (b_def - b_pol)
        elif b_pol > b_def:
            losses_def += 1
            loss_bins_vs_def += (b_pol - b_def)
        else:
            ties_def += 1

        if b_pol < b_best_nat:
            strict_wins_both += 1
        elif b_pol > b_best_nat:
            losses_vs_both += 1

    # Penalize regressions vs default heavily (2.5x per lost sheet) and reward beating both baselines
    reg_score = (
        (saved_vs_def - 2.5 * loss_bins_vs_def)
        + 0.25 * strict_wins_both
        - 0.50 * losses_vs_both
    )
    stats = {
        'total_bins': total_bins,
        'net_sheets_saved_vs_default': saved_vs_def - loss_bins_vs_def,
        'wins_vs_default': wins_def,
        'ties_vs_default': ties_def,
        'losses_vs_default': losses_def,
        'strict_wins_vs_min_default_guide1': strict_wins_both,
        'losses_vs_min_default_guide1': losses_vs_both,
        'colstrip_selected_count': colstrip_selected,
        'regularized_score': round(reg_score, 4),
    }
    return reg_score, stats


def fit_and_freeze_router(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc

    by_inst: dict[str, dict[str, Any]] = {}
    for r in rows:
        ent = by_inst.setdefault(r['instance'], {
            'instance': r['instance'],
            'family': r['family'],
            'split': r['split'],
            'group_id': r['group_id'],
            'features': r['features'],
            'bins': {},
            'wall_s': {},
        })
        ent['bins'][r['method']] = r['bins']
        ent['wall_s'][r['method']] = r['wall_s']

    records = [v for v in by_inst.values() if len(v['bins']) == 3]
    cands = _candidate_policies(records)

    # 5-fold cross-validation grouped by parent group_id hash
    def _fold(gid: str) -> int:
        return int(hashlib.sha256(('cv5/' + gid).encode()).hexdigest()[:8], 16) % 5

    oof_chosen_branch: dict[str, str] = {}
    fold_selected_names: list[str] = []
    for k in range(5):
        train_recs = [r for r in records if _fold(r['group_id']) != k]
        val_recs = [r for r in records if _fold(r['group_id']) == k]
        best_pol = None
        best_key = None
        for pol in cands:
            sc, st = _score_policy(train_recs, pol, pmc.evaluate_router_policy)
            # Tie-break: higher regularized score -> fewer losses vs default -> broader colstrip coverage -> simpler policy
            complexity = len(pol.get('rules', []))
            key = (sc, -st['losses_vs_default'], st['colstrip_selected_count'], -complexity)
            if best_key is None or key > best_key:
                best_key = key
                best_pol = pol
        fold_selected_names.append(best_pol['name'])
        for r in val_recs:
            oof_chosen_branch[r['instance']] = pmc.evaluate_router_policy(r['features'], best_pol)

    # Evaluate out-of-fold CV performance
    oof_bins = sum(r['bins'][oof_chosen_branch[r['instance']]] for r in records)
    def_bins = sum(r['bins']['default'] for r in records)
    g1_bins = sum(r['bins']['guide1'] for r in records)
    cs_bins = sum(r['bins']['colstrip'] for r in records)
    oof_wins_def = sum(1 for r in records if r['bins'][oof_chosen_branch[r['instance']]] < r['bins']['default'])
    oof_losses_def = sum(1 for r in records if r['bins'][oof_chosen_branch[r['instance']]] > r['bins']['default'])
    oof_strict_wins_both = sum(
        1 for r in records
        if r['bins'][oof_chosen_branch[r['instance']]] < min(r['bins']['default'], r['bins']['guide1'])
    )

    # Fit final frozen policy on all 68 development records
    best_final_pol = None
    best_final_key = None
    best_final_stats = None
    for pol in cands:
        sc, st = _score_policy(records, pol, pmc.evaluate_router_policy)
        complexity = len(pol.get('rules', []))
        key = (sc, -st['losses_vs_default'], st['colstrip_selected_count'], -complexity)
        if best_final_key is None or key > best_final_key:
            best_final_key = key
            best_final_pol = pol
            best_final_stats = st

    # Family breakdown on development
    fam_breakdown = {}
    for fam in sorted({r['family'] for r in records}):
        frecs = [r for r in records if r['family'] == fam]
        fam_breakdown[fam] = {
            'count': len(frecs),
            'default_bins': sum(r['bins']['default'] for r in frecs),
            'guide1_bins': sum(r['bins']['guide1'] for r in frecs),
            'colstrip_bins': sum(r['bins']['colstrip'] for r in frecs),
            'mill_router_bins': sum(
                r['bins'][pmc.evaluate_router_policy(r['features'], best_final_pol)]
                for r in frecs
            ),
        }

    policy_path = pathlib.Path('/content/mill-study/mill-policy.json')
    policy_path.write_text(json.dumps(best_final_pol, indent=2))
    (ROOT / 'mill-policy.json').write_text(json.dumps(best_final_pol, indent=2))

    frozen_hashes = {
        'colstrip_gen.cpp': hashlib.sha256(pathlib.Path('/content/colstrip_gen.cpp').read_bytes()).hexdigest(),
        'libcolstrip.so': hashlib.sha256(pathlib.Path('/content/libcolstrip.so').read_bytes()).hexdigest(),
        'paper_mill_cutter.py': hashlib.sha256(pathlib.Path('/content/paper_mill_cutter.py').read_bytes()).hexdigest(),
        'mill-policy.json': hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    }

    report = {
        'development_instances': len(records),
        'method_totals': {
            'default_bins': def_bins,
            'guide1_bins': g1_bins,
            'colstrip_bins': cs_bins,
        },
        'crossval_5fold_grouped': {
            'oof_total_bins': oof_bins,
            'oof_net_saved_vs_default': def_bins - oof_bins,
            'oof_wins_vs_default': oof_wins_def,
            'oof_losses_vs_default': oof_losses_def,
            'oof_strict_wins_vs_min_default_guide1': oof_strict_wins_both,
            'fold_policies': fold_selected_names,
        },
        'selected_policy': best_final_pol,
        'in_sample_dev_stats': best_final_stats,
        'family_breakdown': fam_breakdown,
        'frozen_sha256': frozen_hashes,
    }
    (ROOT / 'fit-report.json').write_text(json.dumps(report, indent=2))
    print('R52_FIT_COMPLETE', json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode == 'worker':
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
    else:
        if mode in ('label', 'all'):
            rows = run_dev_labeling()
        else:
            rows = json.loads((ROOT / 'dev-rows.json').read_text())
        if mode in ('fit', 'all'):
            fit_and_freeze_router(rows)
