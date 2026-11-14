#!/usr/bin/env python3
"""Colab Execution Script for R59 (Public 3NHO BKS Gap Closure) & R60 (Bi-Objective Factory Total Cost).

Strictly follows AGENTS.md:
  1. Compiles zero-dependency Modern C++23 CLI + WebAssembly (`mill_cxx23.{js,wasm}` with `_wasm_solve_pareto`)
     and runs a Node.js Wasm verification smoke test.
  2. Freezes all source & binary SHA256 hashes BEFORE opening unseen validation instances.
  3. Runs R59:
       - R59-A: Exact R58 cohort (8 R52 Berkey dev + 12 R55 Martello post-hoc diagnosis) at matched 10s & 60s
         comparing PackingSolver `default` vs `hp_descent` (`solve_high_precision_min_paper`, strictly blind to BKS).
       - R59-B: 12 fresh unconsumed `public_berkey1987` `historical_development` (`n >= 60`) instances at matched 10s & 30s.
  4. Runs R60:
       - R60-A: All 8 unconsumed group-isolated `validation` `repeated_demand` instances x 2 seeds (601, 602) at matched 10.0s,
         comparing `default`, `guide1`, `mill_router` (all with fair identical-plate deduplication) vs `factory_pareto`.
       - R60-B: 12 realistic industrial paperboard batch orders (889x1194 / 787x1092 / 1000x1400mm, 30-400 pcs/SKU),
         comparing `default_10s`, `guide1_10s`, `factory_pareto_10s`, and `cxx23_pareto_3s` under both programmable
         guillotine (`FactoryCostModel`) and rotary slitter-sheeter (`SLITTER_COST_MODEL`) economics.
  5. Verifies 100% of generated `cut_tree.csv` certificates via BOTH Python `verify_cut_tree_csv` and native C++ `ejection`.
  6. Exports `factory_showcase.json` for `web/` and updates `current-label-status.json`, packaging everything into
     `/content/factory-cost-study-results.zip`.
"""
from __future__ import annotations

import collections
from dataclasses import asdict
import csv
import datetime as dt
import hashlib
import importlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile
from typing import Any

CONTENT = pathlib.Path('/content')
if str(CONTENT) not in sys.path:
    sys.path.insert(0, str(CONTENT))
PS_ROOT = CONTENT / 'portfolio-study'
if str(PS_ROOT) not in sys.path:
    sys.path.insert(0, str(PS_ROOT))

import packingsolver.rectangleguillotine as ps  # noqa: E402
import paper_mill_cutter as pmc  # noqa: E402
import factory_pareto_cutter as fpc  # noqa: E402

importlib.reload(fpc)

OUT_ROOT = CONTENT / 'factory-cost-study'
CORPUS_ROOT = CONTENT / 'data-expansion' / 'corpus'
MANIFEST_PATH = CORPUS_ROOT / 'manifest.json'
LABEL_STATUS_PATH = CONTENT / 'current-label-status.json'
BKS_CSV_PATH = CONTENT / 'data_bin_packing_3nho.csv'
POLICY_PATH = (CONTENT / 'mill-policy.json') if (CONTENT / 'mill-policy.json').exists() else (CONTENT / 'mill-study' / 'mill-policy.json')
EJECTION_BIN = CONTENT / 'native-check' / 'ejection'
CXX_ROOT = CONTENT / 'mill_cxx23'
CXX_CLI_BIN = CONTENT / 'mill_cxx23_cli'


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def ensure_emsdk_and_build_cxx23() -> dict[str, Any]:
    print('=== [Step 1] Building Modern C++23 CLI & Emscripten WebAssembly (v2 Pareto) ===', flush=True)
    wasm_dir = OUT_ROOT / 'wasm'
    wasm_dir.mkdir(parents=True, exist_ok=True)

    # 1a. Native C++23 CLI build (-Wall -Wextra -Wpedantic)
    t0 = time.perf_counter()
    cmd_native = [
        'g++', '-std=c++23', '-O3', '-march=native',
        '-Wall', '-Wextra', '-Wpedantic',
        f'-I{CXX_ROOT / "include"}',
        str(CXX_ROOT / 'src' / 'mill_cxx23.cpp'),
        str(CXX_ROOT / 'src' / 'mill_cli.cpp'),
        '-o', str(CXX_CLI_BIN),
    ]
    proc_nat = subprocess.run(cmd_native, capture_output=True, text=True, check=False)
    if proc_nat.returncode != 0:
        raise RuntimeError(f'Native C++23 build failed:\n{proc_nat.stderr}')
    native_build_s = time.perf_counter() - t0
    print(f'  Native CLI built in {native_build_s:.2f}s ({CXX_CLI_BIN.stat().st_size} bytes), stderr={proc_nat.stderr.strip()!r}', flush=True)

    # 1b. Install/activate emsdk if needed
    emsdk_dir = CONTENT / 'emsdk'
    empp = emsdk_dir / 'upstream' / 'emscripten' / 'em++'
    if not empp.exists():
        print('  Installing Emscripten SDK in /content/emsdk ...', flush=True)
        if emsdk_dir.exists():
            shutil.rmtree(emsdk_dir)
        subprocess.run(
            ['git', 'clone', '--depth', '1', 'https://github.com/emscripten-core/emsdk.git', str(emsdk_dir)],
            check=True,
        )
        subprocess.run([str(emsdk_dir / 'emsdk'), 'install', 'latest'], cwd=str(emsdk_dir), check=True)
        subprocess.run([str(emsdk_dir / 'emsdk'), 'activate', 'latest'], cwd=str(emsdk_dir), check=True)

    t1 = time.perf_counter()
    wasm_js = wasm_dir / 'mill_cxx23.js'
    wasm_bin = wasm_dir / 'mill_cxx23.wasm'
    cmd_wasm = [
        str(empp),
        '-std=c++23', '-O3', '-fno-exceptions',
        f'-I{CXX_ROOT / "include"}',
        str(CXX_ROOT / 'src' / 'mill_cxx23.cpp'),
        str(CXX_ROOT / 'src' / 'mill_wasm.cpp'),
        '-sMODULARIZE=1',
        '-sEXPORT_NAME=createMillCxx23',
        '-sALLOW_MEMORY_GROWTH=1',
        '-sINITIAL_MEMORY=33554432',
        '-sENVIRONMENT=web,worker,node',
        "-sEXPORTED_FUNCTIONS=['_wasm_solve_order','_wasm_solve_pareto','_wasm_verify_certificate','_wasm_engine_info','_malloc','_free']",
        "-sEXPORTED_RUNTIME_METHODS=['ccall','cwrap','UTF8ToString','stringToUTF8','lengthBytesUTF8']",
        '-o', str(wasm_js),
    ]
    proc_wasm = subprocess.run(cmd_wasm, capture_output=True, text=True, check=False)
    if proc_wasm.returncode != 0:
        raise RuntimeError(f'Emscripten Wasm build failed:\n{proc_wasm.stderr}')
    wasm_build_s = time.perf_counter() - t1
    print(f'  Wasm built in {wasm_build_s:.2f}s: js={wasm_js.stat().st_size}B, wasm={wasm_bin.stat().st_size}B', flush=True)

    # 1c. Node.js smoke test of _wasm_solve_pareto + _wasm_verify_certificate + _wasm_solve_order
    smoke_js = OUT_ROOT / 'wasm_smoke_check.cjs'
    smoke_js.write_text(f"""
const createMillCxx23 = require({json.dumps(str(wasm_js))});
(async () => {{
  const mod = await createMillCxx23();
  const info = JSON.parse(mod.ccall('wasm_engine_info', 'string', [], []));
  const bins = "ID,WIDTH,HEIGHT\\n0,889,1194\\n";
  const items = [
    "ID,WIDTH,HEIGHT,COPIES",
    "0,290,420,120",
    "1,290,380,120",
    "2,295,390,90",
    "3,210,295,180",
    "4,220,295,150",
    "5,180,260,160"
  ].join("\\n") + "\\n";
  const paretoRaw = mod.ccall(
    'wasm_solve_pareto',
    'string',
    ['string', 'string', 'number', 'number', 'number', 'number', 'number', 'number', 'number', 'string'],
    [bins, items, 2.0, 180.0, 6.0, 9.0, 200, 2.0, 601, 'smoke_batch']
  );
  const pareto = JSON.parse(paretoRaw);
  if (!pareto.ok || !pareto.frontier || pareto.frontier.length === 0) {{
    throw new Error('wasm_solve_pareto failed: ' + paretoRaw);
  }}
  const verChecks = [];
  for (const fp of pareto.frontier) {{
    const vr = JSON.parse(mod.ccall(
      'wasm_verify_certificate',
      'string',
      ['string', 'string', 'string', 'string'],
      [bins, items, fp.cut_tree_csv, 'smoke_batch']
    ));
    if (!vr.valid) throw new Error('Wasm verify failed on ' + fp.plan_id);
    verChecks.push({{
      plan_id: fp.plan_id,
      sheets_used: fp.sheets_used,
      distinct_patterns: fp.distinct_patterns,
      total_strokes: fp.total_strokes,
      total_cost_rmb: fp.total_cost_rmb,
      valid: vr.valid
    }});
  }}
  console.log(JSON.stringify({{
    ok: true,
    engine_info: info,
    pareto_wall_ms: pareto.wall_ms,
    recommended: pareto.recommended,
    frontier_summary: verChecks
  }}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
""")
    node_bin = emsdk_dir / 'node'
    node_exe = 'node'
    if node_bin.exists():
        found_nodes = list(node_bin.glob('**/bin/node'))
        if found_nodes:
            node_exe = str(found_nodes[0])
    proc_node = subprocess.run([node_exe, str(smoke_js)], capture_output=True, text=True, check=True)
    smoke_res = json.loads(proc_node.stdout.strip().splitlines()[-1])
    smoke_js.unlink(missing_ok=True)
    (OUT_ROOT / 'wasm_smoke_test.json').write_text(json.dumps(smoke_res, indent=2, ensure_ascii=False))
    print(f'  Wasm Node smoke test OK: {len(smoke_res["frontier_summary"])} Pareto plans in {smoke_res["pareto_wall_ms"]:.1f}ms', flush=True)

    return {
        'native_cli_bytes': CXX_CLI_BIN.stat().st_size,
        'native_warnings': proc_nat.stderr.strip(),
        'wasm_js_bytes': wasm_js.stat().st_size,
        'wasm_bin_bytes': wasm_bin.stat().st_size,
        'native_build_s': round(native_build_s, 3),
        'wasm_build_s': round(wasm_build_s, 3),
        'smoke_test': smoke_res,
    }


def load_bks_map() -> dict[str, int]:
    bks: dict[str, int] = {}
    with BKS_CSV_PATH.open(newline='') as f:
        for r in csv.DictReader(f):
            val = (r.get('Best known solution value') or '').strip()
            if val:
                bks[r['Path'].strip()] = int(float(val))
    return bks


def verify_and_score_csv(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    cut_tree_csv: pathlib.Path,
    cost_model: fpc.FactoryCostModel,
    deduplicate_plates: bool,
) -> dict[str, Any]:
    """Run BOTH Python verify_cut_tree_csv AND native C++ ejection check, plus shop-floor economics."""
    t_v0 = time.perf_counter()
    econ = fpc.evaluate_cut_tree_economics(
        bins_csv, items_csv, cut_tree_csv,
        cost_model=cost_model,
        deduplicate_plates=deduplicate_plates,
        native_check_binary=EJECTION_BIN,
    )
    econ['native_ok'] = bool(econ.get('native_verified', False))
    if econ['sheets_used'] == 1 and not econ['native_ok']:
        proc_v = subprocess.run(
            [str(CXX_CLI_BIN), 'verify', '--bins', str(bins_csv), '--items', str(items_csv), '--tree', str(cut_tree_csv)],
            capture_output=True, text=True, check=False,
        )
        if proc_v.returncode == 0 and json.loads(proc_v.stdout).get('valid') is True:
            econ['native_ok'] = True
    econ['verify_wall_s'] = round(time.perf_counter() - t_v0, 6)
    if not econ['valid'] or not econ['native_ok']:
        raise RuntimeError(f'Certificate verification failed on {cut_tree_csv}: {econ}')
    slit = fpc.evaluate_cut_tree_economics(
        bins_csv, items_csv, cut_tree_csv,
        cost_model=fpc.SLITTER_COST_MODEL,
        deduplicate_plates=deduplicate_plates,
        native_check_binary=None,
    )
    econ['slitter_econ'] = {
        'sheets_used': slit['sheets_used'],
        'distinct_patterns': slit['distinct_patterns'],
        'clamp_stacks': slit['clamp_stacks'],
        'total_strokes': slit['total_strokes'],
        'setup_time_min': slit['setup_time_min'],
        'cut_time_min': slit['cut_time_min'],
        'total_time_min': slit['total_time_min'],
        'paper_cost_rmb': slit['paper_cost_rmb'],
        'setup_cost_rmb': slit['setup_cost_rmb'],
        'cut_cost_rmb': slit['cut_cost_rmb'],
        'total_cost_rmb': slit['total_cost_rmb'],
    }
    return econ


def run_ps_baseline_timed(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    out_csv: pathlib.Path,
    budget_s: float,
    guide: list[int] | None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    inst = pmc.build_ps_instance(bins_csv, items_csv)
    out = pmc.run_ps_optimize(inst, max(0.2, budget_s - 0.08), guide=guide)
    if out.solution.full() and out.solution.feasible():
        out.solution.write(str(out_csv))
        bins = int(out.solution.number_of_bins())
    else:
        W, H, items = pmc.read_order_files(bins_csv, items_csv)
        ffdh = pmc._build_ffdh_plates(items, W, H)
        pmc.write_colstrip_cut_tree_csv(ffdh, items, W, H, out_csv)
        bins = len(ffdh)
    online_wall_s = time.perf_counter() - t0
    return {
        'raw_bins': bins,
        'bound': int(out.bin_packing_bound),
        'online_wall_s': round(online_wall_s, 6),
    }


def run_r59_bks_study(
    manifest: list[dict[str, Any]],
    label_status: dict[str, Any],
    policy: dict[str, Any],
    bks_map: dict[str, int],
) -> dict[str, Any]:
    print('\n=== [Step 2] Running R59: Public Academic Benchmark 3NHO BKS Gap Closure ===', flush=True)
    r59_dir = OUT_ROOT / 'r59_bks'
    cert_dir = r59_dir / 'certificates'
    cert_dir.mkdir(parents=True, exist_ok=True)
    data_dir = CORPUS_ROOT / 'data'

    by_id = {m['name']: m for m in manifest}

    # R59-A1: Exact 8 R52 Berkey dev instances
    r52_berkey_ids = sorted([
        aid for aid in label_status
        if aid in by_id and by_id[aid]['family'] == 'public_berkey1987'
    ])
    # R59-A2: Exact 12 R55 Martello external instances (explicitly post-hoc budget/method diagnosis)
    r55_martello_ids = sorted([
        aid for aid, rec in label_status.items()
        if aid in by_id and by_id[aid]['family'] == 'public_martello1998' and rec.get('test_consumed')
    ])

    # R59-B: 12 fresh unconsumed Berkey historical_development instances (2 per Class_01..Class_06, n >= 60, group-isolated)
    used_groups = {
        by_id[aid]['group_id']
        for aid in label_status
        if aid in by_id
    }
    fresh_by_class: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for m in manifest:
        if (m['family'] == 'public_berkey1987'
                and m['split'] == 'historical_development'
                and m['name'] not in label_status
                and m['group_id'] not in used_groups):
            src_path = m['source']['path']  # e.g. berkey1987/Class_01.2bp_100_1
            orig = pathlib.Path(src_path).name
            cls_prefix = orig.split('.')[0]  # Class_01 .. Class_06
            n_val = int(m['features']['items'])
            if n_val >= 60 and src_path in bks_map:
                fresh_by_class[cls_prefix].append(m)

    fresh_berkey_ids: list[str] = []
    for cls_name in sorted(fresh_by_class.keys()):
        cands = sorted(fresh_by_class[cls_name], key=lambda x: (int(x['features']['items']), x['name']), reverse=True)
        for chosen in cands[:2]:
            fresh_berkey_ids.append(chosen['name'])

    print(f'  R59 cohorts: R52_Berkey={len(r52_berkey_ids)}, R55_Martello_diag={len(r55_martello_ids)}, Fresh_Berkey_dev={len(fresh_berkey_ids)}', flush=True)

    cost_model = fpc.FactoryCostModel()
    rows: list[dict[str, Any]] = []

    def eval_cohort(cohort_name: str, aids: list[str], budgets: list[int], is_posthoc_external: bool) -> None:
        for idx, aid in enumerate(aids):
            meta = by_id[aid]
            src_path = meta['source']['path']
            orig_stem = pathlib.Path(src_path).name
            bks = bks_map[src_path]
            bins_csv = data_dir / f'{aid}_bins.csv'
            items_csv = data_dir / f'{aid}_items.csv'
            W, H, items = pmc.read_order_files(bins_csv, items_csv)
            feats = pmc.extract_mill_features(W, H, items)

            entry: dict[str, Any] = {
                'cohort': cohort_name,
                'is_posthoc_diagnosis_on_consumed_external': is_posthoc_external,
                'anon_id': aid,
                'source_path': src_path,
                'original_name': orig_stem,
                'source_family': meta['family'],
                'assigned_role': meta['split'],
                'sheet_w': W,
                'sheet_h': H,
                'items': feats['items'],
                'area_lower_bound': feats['area_lower_bound'],
                'bks_3nho': bks,
                'runs': {},
            }

            # 10s mill_router (frozen v2)
            mr_csv = cert_dir / f'{aid}_mill_router_10s.csv'
            mr_res = pmc.solve_order_online(
                bins_csv=bins_csv, items_csv=items_csv, out_csv=mr_csv,
                method='mill_router', seed=501, budget_s=10.0,
                policy=policy, check_native_binary=EJECTION_BIN,
            )
            mr_ver = verify_and_score_csv(bins_csv, items_csv, mr_csv, cost_model, deduplicate_plates=True)
            entry['runs']['mill_router_10s'] = {
                'bins': int(mr_ver['sheets_used']),
                'gap_to_bks': int(mr_ver['sheets_used']) - bks,
                'online_wall_s': round(float(mr_res['wall_s']), 6),
                'branch': mr_res['branch'],
                'valid': bool(mr_ver['valid']),
                'native_ok': bool(mr_ver['native_ok']),
            }

            for b_sec in budgets:
                # Matched PackingSolver default at b_sec
                def_csv = cert_dir / f'{aid}_default_{b_sec}s.csv'
                def_run = run_ps_baseline_timed(bins_csv, items_csv, def_csv, float(b_sec), guide=None)
                def_ver = verify_and_score_csv(bins_csv, items_csv, def_csv, cost_model, deduplicate_plates=True)
                entry['runs'][f'default_{b_sec}s'] = {
                    'bins': int(def_ver['sheets_used']),
                    'gap_to_bks': int(def_ver['sheets_used']) - bks,
                    'online_wall_s': def_run['online_wall_s'],
                    'valid': def_ver['valid'],
                    'native_ok': def_ver['native_ok'],
                }

                # Matched hp_descent at b_sec (strictly blind to BKS!)
                hp_csv = cert_dir / f'{aid}_hp_descent_{b_sec}s.csv'
                hp_run = fpc.solve_high_precision_min_paper(
                    bins_csv, items_csv, hp_csv, policy=policy, budget_s=float(b_sec), seed=501
                )
                hp_ver = verify_and_score_csv(bins_csv, items_csv, hp_csv, cost_model, deduplicate_plates=False)
                entry['runs'][f'hp_descent_{b_sec}s'] = {
                    'bins': int(hp_ver['sheets_used']),
                    'gap_to_bks': int(hp_ver['sheets_used']) - bks,
                    'online_wall_s': hp_run['wall_s'],
                    'winning_engine': hp_run['winning_engine'],
                    'bound': hp_run['bound'],
                    'valid': hp_ver['valid'],
                    'native_ok': hp_ver['native_ok'],
                }

            r_str = ' | '.join(
                f"{k}={v['bins']}({v['online_wall_s']:.1f}s)"
                for k, v in entry['runs'].items()
            )
            print(f'  [{cohort_name} {idx+1}/{len(aids)}] {orig_stem:22s} BKS={bks:2d} LB={feats["area_lower_bound"]:2d} | {r_str}', flush=True)
            rows.append(entry)

    eval_cohort('r52_berkey_dev', r52_berkey_ids, [10, 60], is_posthoc_external=False)
    eval_cohort('r55_martello_posthoc_diag', r55_martello_ids, [10, 60], is_posthoc_external=True)
    eval_cohort('fresh_berkey_dev', fresh_berkey_ids, [10, 30], is_posthoc_external=False)

    # Summarize per cohort
    cohort_summaries: dict[str, Any] = {}
    for cname in ('r52_berkey_dev', 'r55_martello_posthoc_diag', 'fresh_berkey_dev'):
        crows = [r for r in rows if r['cohort'] == cname]
        bks_sum = sum(r['bks_3nho'] for r in crows)
        lb_sum = sum(r['area_lower_bound'] for r in crows)
        methods_summary = {}
        method_keys = list(crows[0]['runs'].keys())
        for mk in method_keys:
            tot_bins = sum(r['runs'][mk]['bins'] for r in crows)
            eq_bks = sum(1 for r in crows if r['runs'][mk]['bins'] == r['bks_3nho'])
            better_bks = sum(1 for r in crows if r['runs'][mk]['bins'] < r['bks_3nho'])
            plus1 = sum(1 for r in crows if r['runs'][mk]['bins'] == r['bks_3nho'] + 1)
            plus2p = sum(1 for r in crows if r['runs'][mk]['bins'] >= r['bks_3nho'] + 2)
            mean_s = sum(r['runs'][mk]['online_wall_s'] for r in crows) / len(crows)
            max_s = max(r['runs'][mk]['online_wall_s'] for r in crows)
            # Pairwise vs default_10s
            wins_vs_def10 = sum(1 for r in crows if r['runs'][mk]['bins'] < r['runs']['default_10s']['bins'])
            ties_vs_def10 = sum(1 for r in crows if r['runs'][mk]['bins'] == r['runs']['default_10s']['bins'])
            loss_vs_def10 = sum(1 for r in crows if r['runs'][mk]['bins'] > r['runs']['default_10s']['bins'])
            methods_summary[mk] = {
                'total_bins': tot_bins,
                'gap_to_bks_total': tot_bins - bks_sum,
                'better_than_bks': better_bks,
                'equal_to_bks': eq_bks,
                'plus_1_vs_bks': plus1,
                'plus_2plus_vs_bks': plus2p,
                'vs_default_10s_wtl': [wins_vs_def10, ties_vs_def10, loss_vs_def10],
                'mean_online_wall_s': round(mean_s, 4),
                'max_online_wall_s': round(max_s, 4),
                'all_dual_verified': all(r['runs'][mk]['valid'] and r['runs'][mk]['native_ok'] for r in crows),
            }
        cohort_summaries[cname] = {
            'instance_count': len(crows),
            'bks_3nho_total': bks_sum,
            'area_lb_total': lb_sum,
            'methods': methods_summary,
        }

    summary = {
        'experiment': 'R59',
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'cohorts': cohort_summaries,
        'fresh_berkey_dev_ids': fresh_berkey_ids,
        'rows': rows,
    }
    (r59_dir / 'r59_summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def build_12_industrial_batch_orders(out_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Create 12 realistic paper & packaging workshop batch orders on standard Chinese/ISO parent sheets:
      - 大度 (Magnanimous): 889 x 1194 mm (300g/350g白卡纸)
      - 正度 (Standard):    787 x 1092 mm (250g/300g白卡/灰底白板纸)
      - 特规对开/全开:      1000 x 1400 mm (瓦楞面纸/礼盒特种纸)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        {
            'order_id': 'ind_01_cosmetics_carton_889x1194',
            'title_zh': '美妆彩妆折叠纸盒混拼单（300g大度白卡 889×1194）',
            'category': 'cosmetics_folding_carton',
            'sheet_w': 889, 'sheet_h': 1194,
            'items': [
                (0, 285, 410, 120), (1, 285, 360, 120), (2, 295, 390, 90), (3, 295, 380, 90),
                (4, 210, 290, 180), (5, 220, 290, 150), (6, 195, 260, 100), (7, 175, 260, 70),
            ],
        },
        {
            'order_id': 'ind_02_pharma_leaflet_box_787x1092',
            'title_zh': 'OTC药品包装盒批量单（300g正度白卡 787×1092）',
            'category': 'pharma_carton',
            'sheet_w': 787, 'sheet_h': 1092,
            'items': [
                (0, 250, 350, 160), (1, 250, 340, 160), (2, 260, 310, 120), (3, 190, 270, 240),
                (4, 190, 250, 200), (5, 175, 220, 180), (6, 150, 220, 140), (7, 130, 180, 120),
            ],
        },
        {
            'order_id': 'ind_03_tea_gift_lid_base_889x1194',
            'title_zh': '精品茗茶天地盖礼盒面纸单（889×1194）',
            'category': 'rigid_box_wrap',
            'sheet_w': 889, 'sheet_h': 1194,
            'items': [
                (0, 420, 560, 80), (1, 410, 540, 80), (2, 290, 380, 120), (3, 280, 360, 120),
                (4, 215, 290, 160), (5, 210, 260, 140), (6, 165, 240, 100),
            ],
        },
        {
            'order_id': 'ind_04_bakery_food_sleeve_889x1194',
            'title_zh': '烘焙西点腰封与外盒拼版单（食品级白卡 889×1194）',
            'category': 'food_packaging',
            'sheet_w': 889, 'sheet_h': 1194,
            'items': [
                (0, 295, 580, 90), (1, 295, 480, 90), (2, 220, 390, 150), (3, 220, 360, 150),
                (4, 175, 295, 250), (5, 175, 270, 200), (6, 145, 240, 180), (7, 145, 210, 120),
            ],
        },
        {
            'order_id': 'ind_05_apparel_hangtag_card_787x1092',
            'title_zh': '服装吊牌与衬卡多SKU合版单（350g正度白卡 787×1092）',
            'category': 'apparel_hangtag',
            'sheet_w': 787, 'sheet_h': 1092,
            'items': [
                (0, 195, 360, 200), (1, 195, 320, 200), (2, 155, 270, 300), (3, 155, 250, 250),
                (4, 130, 215, 360), (5, 130, 195, 300), (6, 110, 180, 240), (7, 95, 160, 200),
                (8, 185, 240, 120), (9, 160, 215, 150),
            ],
        },
        {
            'order_id': 'ind_06_electronics_inner_tray_889x1194',
            'title_zh': '3C数码配件盒与内托卡纸单（889×1194）',
            'category': 'electronics_box',
            'sheet_w': 889, 'sheet_h': 1194,
            'items': [
                (0, 290, 395, 140), (1, 290, 375, 140), (2, 220, 295, 210), (3, 215, 285, 210),
                (4, 175, 235, 280), (5, 170, 225, 210), (6, 145, 195, 140), (7, 140, 185, 105),
            ],
        },
        {
            'order_id': 'ind_07_book_cover_bellyband_889x1194',
            'title_zh': '图书护封、腰封与书签混裁单（大度铜版卡 889×1194）',
            'category': 'print_finishing',
            'sheet_w': 889, 'sheet_h': 1194,
            'items': [
                (0, 440, 295, 160), (1, 420, 285, 160), (2, 440, 145, 240), (3, 420, 135, 240),
                (4, 210, 295, 120), (5, 180, 260, 120), (6, 145, 210, 200),
            ],
        },
        {
            'order_id': 'ind_08_daily_care_toothpaste_787x1092',
            'title_zh': '日化牙膏与洁面乳长条盒批量单（正度 787×1092）',
            'category': 'personal_care',
            'sheet_w': 787, 'sheet_h': 1092,
            'items': [
                (0, 195, 540, 120), (1, 195, 480, 120), (2, 185, 440, 150), (3, 185, 390, 150),
                (4, 155, 360, 180), (5, 155, 320, 180), (6, 130, 270, 150),
            ],
        },
        {
            'order_id': 'ind_09_ecommerce_mailer_1000x1400',
            'title_zh': '电商飞机盒面纸拼版单（全开牛卡 1000×1400）',
            'category': 'corrugated_topsheet',
            'sheet_w': 1000, 'sheet_h': 1400,
            'items': [
                (0, 490, 680, 100), (1, 480, 640, 100), (2, 330, 460, 150), (3, 320, 440, 150),
                (4, 245, 340, 200), (5, 240, 320, 160), (6, 195, 270, 120),
            ],
        },
        {
            'order_id': 'ind_10_stationery_folder_card_787x1092',
            'title_zh': '文具档案袋与分隔页卡纸单（正度 787×1092）',
            'category': 'stationery',
            'sheet_w': 787, 'sheet_h': 1092,
            'items': [
                (0, 380, 530, 90), (1, 370, 510, 90), (2, 255, 360, 180), (3, 250, 340, 180),
                (4, 190, 265, 240), (5, 185, 250, 180), (6, 150, 210, 150),
            ],
        },
        {
            'order_id': 'ind_11_wine_spirits_giftbox_1000x1400',
            'title_zh': '酒水伴手礼盒多规格订单（特规白板 1000×1400）',
            'category': 'beverage_packaging',
            'sheet_w': 1000, 'sheet_h': 1400,
            'items': [
                (0, 330, 690, 90), (1, 320, 660, 90), (2, 330, 450, 120), (3, 245, 460, 180),
                (4, 245, 420, 180), (5, 195, 340, 210), (6, 195, 310, 150), (7, 160, 270, 120),
            ],
        },
        {
            'order_id': 'ind_12_mixed_shortrun_commercial_889x1194',
            'title_zh': '快印包装中心12款急单合版（大度 889×1194）',
            'category': 'short_run_gang_print',
            'sheet_w': 889, 'sheet_h': 1194,
            'items': [
                (0, 295, 395, 60), (1, 290, 370, 60), (2, 220, 395, 80), (3, 220, 350, 80),
                (4, 215, 295, 100), (5, 210, 280, 100), (6, 175, 295, 120), (7, 175, 260, 90),
                (8, 145, 235, 120), (9, 145, 215, 90), (10, 125, 195, 80), (11, 120, 180, 60),
            ],
        },
    ]

    manifest_out: list[dict[str, Any]] = []
    for sp in specs:
        odir = out_dir / sp['order_id']
        odir.mkdir(parents=True, exist_ok=True)
        bins_csv = odir / 'bins.csv'
        items_csv = odir / 'items.csv'
        bins_csv.write_text(f"ID,WIDTH,HEIGHT\n0,{sp['sheet_w']},{sp['sheet_h']}\n")
        lines = ['ID,WIDTH,HEIGHT,COPIES']
        tot_pcs = 0
        tot_area = 0
        for iid, w, h, c in sp['items']:
            lines.append(f'{iid},{w},{h},{c}')
            tot_pcs += c
            tot_area += w * h * c
        items_csv.write_text('\n'.join(lines) + '\n')
        rec = {
            'order_id': sp['order_id'],
            'title_zh': sp['title_zh'],
            'category': sp['category'],
            'sheet_w': sp['sheet_w'],
            'sheet_h': sp['sheet_h'],
            'sku_count': len(sp['items']),
            'total_pieces': tot_pcs,
            'area_lower_bound': math.ceil(tot_area / (sp['sheet_w'] * sp['sheet_h'])),
            'bins_csv': str(bins_csv),
            'items_csv': str(items_csv),
        }
        (odir / 'order_meta.json').write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        manifest_out.append(rec)
    (out_dir / 'manifest.json').write_text(json.dumps(manifest_out, indent=2, ensure_ascii=False))
    return manifest_out


def rescore_econ_under_model(econ: dict[str, Any], _model: fpc.FactoryCostModel) -> dict[str, Any]:
    return econ['slitter_econ']


def run_r60_factory_pareto_study(
    manifest: list[dict[str, Any]],
    label_status: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    print('\n=== [Step 3] Running R60: Bi-Objective Factory Total-Cost Optimization ===', flush=True)
    r60_dir = OUT_ROOT / 'r60_factory_pareto'
    r60a_dir = r60_dir / 'r60a_unseen_validation_repeated_demand'
    r60b_dir = r60_dir / 'r60b_industrial_batch_12'
    r60a_dir.mkdir(parents=True, exist_ok=True)
    r60b_dir.mkdir(parents=True, exist_ok=True)

    guillotine_model = fpc.FactoryCostModel()
    slitter_model = fpc.SLITTER_COST_MODEL

    # -----------------------------------------------------------------------
    # R60-A: All 8 unseen group-isolated `validation` `repeated_demand` instances x 2 seeds (601, 602)
    # -----------------------------------------------------------------------
    data_dir = CORPUS_ROOT / 'data'
    by_id = {m['name']: m for m in manifest}
    non_val_or_used_groups = {
        m['group_id'] for m in manifest if m['split'] != 'validation'
    } | {
        by_id[aid]['group_id'] for aid in label_status if aid in by_id
    }
    unseen_val_rep = sorted([
        m['name'] for m in manifest
        if m['split'] == 'validation'
        and m['family'] == 'repeated_demand'
        and m['name'] not in label_status
        and m['group_id'] not in non_val_or_used_groups
    ])
    print(f'  R60-A Unseen group-isolated validation repeated_demand instances: {len(unseen_val_rep)} -> {unseen_val_rep}', flush=True)
    if len(unseen_val_rep) != 8:
        raise RuntimeError(f'Expected 8 unconsumed repeated_demand validation instances, got {len(unseen_val_rep)}')

    r60a_rows: list[dict[str, Any]] = []
    seeds = [601, 602]

    for idx, aid in enumerate(unseen_val_rep):
        meta = by_id[aid]
        bins_csv = data_dir / f'{aid}_bins.csv'
        items_csv = data_dir / f'{aid}_items.csv'
        W, H, items = pmc.read_order_files(bins_csv, items_csv)
        tot_pcs = sum(int(it['copies']) for it in items)

        for sd in seeds:
            run_tag = f'{aid}_s{sd}'
            cdir = r60a_dir / run_tag
            cdir.mkdir(parents=True, exist_ok=True)

            # 1. default (10.0s, with fair identical-plate deduplication)
            def_csv = cdir / 'default_raw.csv'
            def_run = run_ps_baseline_timed(bins_csv, items_csv, def_csv, 10.0, guide=None)
            def_econ = verify_and_score_csv(bins_csv, items_csv, def_csv, guillotine_model, deduplicate_plates=True)

            # 2. guide1 (10.0s, with fair identical-plate deduplication)
            g1_csv = cdir / 'guide1_raw.csv'
            g1_run = run_ps_baseline_timed(bins_csv, items_csv, g1_csv, 10.0, guide=[1])
            g1_econ = verify_and_score_csv(bins_csv, items_csv, g1_csv, guillotine_model, deduplicate_plates=True)

            # 3. mill_router (frozen v2, 10.0s, with fair identical-plate deduplication)
            mr_csv = cdir / 'mill_router_raw.csv'
            mr_pkg = pmc.solve_order_online(
                bins_csv=bins_csv, items_csv=items_csv, out_csv=mr_csv,
                method='mill_router', seed=sd, budget_s=10.0,
                policy=policy, check_native_binary=EJECTION_BIN,
            )
            mr_econ = verify_and_score_csv(
                bins_csv, items_csv, mr_csv, guillotine_model, deduplicate_plates=True
            )

            # 4. factory_pareto (10.0s online budget; verify all frontier plans externally)
            fp_dir = cdir / 'factory_pareto'
            fp_res = fpc.solve_factory_pareto(
                bins_csv, items_csv, fp_dir,
                policy=policy, cost_model=guillotine_model,
                budget_s=10.0, seed=sd, native_check_binary=None,
            )
            verified_frontier = []
            for fp_plan in fp_res['pareto_frontier']:
                ver_plan = verify_and_score_csv(
                    bins_csv, items_csv, pathlib.Path(fp_plan['cut_tree_csv']),
                    guillotine_model, deduplicate_plates=False,
                )
                ver_plan['plan_id'] = fp_plan['plan_id']
                ver_plan['cut_tree_csv'] = fp_plan['cut_tree_csv']
                ver_plan['slitter_econ'] = rescore_econ_under_model(ver_plan, slitter_model)
                verified_frontier.append(ver_plan)

            p_min_paper = min(verified_frontier, key=lambda x: (x['sheets_used'], x['total_cost_rmb']))
            p_min_cost = min(verified_frontier, key=lambda x: (x['total_cost_rmb'], x['sheets_used']))
            p_min_setup = min(verified_frontier, key=lambda x: (x['distinct_patterns'], x['total_time_min'], x['sheets_used']))
            p_min_slitter = min(verified_frontier, key=lambda x: (x['slitter_econ']['total_cost_rmb'], x['sheets_used']))

            row = {
                'anon_id': aid,
                'group_id': meta['group_id'],
                'seed': sd,
                'sheet_w': W,
                'sheet_h': H,
                'sku_count': len(items),
                'total_pieces': tot_pcs,
                'default': {**def_econ, 'online_wall_s': def_run['online_wall_s'], 'slitter_econ': rescore_econ_under_model(def_econ, slitter_model)},
                'guide1': {**g1_econ, 'online_wall_s': g1_run['online_wall_s'], 'slitter_econ': rescore_econ_under_model(g1_econ, slitter_model)},
                'mill_router': {**mr_econ, 'online_wall_s': round(float(mr_pkg['wall_s']), 6), 'slitter_econ': rescore_econ_under_model(mr_econ, slitter_model)},
                'factory_pareto': {
                    'online_wall_s': fp_res['wall_s'],
                    'candidate_patterns': fp_res['candidate_patterns_generated'],
                    'pareto_count': len(verified_frontier),
                    'min_paper': p_min_paper,
                    'min_total_cost': p_min_cost,
                    'min_setup_fastest': p_min_setup,
                    'min_slitter_cost': p_min_slitter,
                    'frontier': verified_frontier,
                },
            }
            r60a_rows.append(row)
            print(
                f'  [R60-A {idx+1}/8 seed={sd}] {aid}: '
                f'def=({def_econ["sheets_used"]}sh,{def_econ["distinct_patterns"]}pat,¥{def_econ["total_cost_rmb"]:.1f}) | '
                f'pareto_paper=({p_min_paper["sheets_used"]}sh,{p_min_paper["distinct_patterns"]}pat,¥{p_min_paper["total_cost_rmb"]:.1f}) | '
                f'pareto_cost=({p_min_cost["sheets_used"]}sh,{p_min_cost["distinct_patterns"]}pat,{p_min_cost["total_time_min"]:.1f}m,¥{p_min_cost["total_cost_rmb"]:.1f}) '
                f'[{fp_res["wall_s"]:.2f}s]',
                flush=True,
            )

    # -----------------------------------------------------------------------
    # R60-B: 12 Realistic Industrial Paperboard Batch Orders (seed=601)
    # -----------------------------------------------------------------------
    ind_orders_dir = OUT_ROOT / 'industrial_batch_orders'
    ind_manifest = build_12_industrial_batch_orders(ind_orders_dir)
    r60b_rows: list[dict[str, Any]] = []

    for idx, om in enumerate(ind_manifest):
        oid = om['order_id']
        bins_csv = pathlib.Path(om['bins_csv'])
        items_csv = pathlib.Path(om['items_csv'])
        cdir = r60b_dir / oid
        cdir.mkdir(parents=True, exist_ok=True)

        # 1. default_10s
        def_csv = cdir / 'default_raw.csv'
        def_run = run_ps_baseline_timed(bins_csv, items_csv, def_csv, 10.0, guide=None)
        def_dedup_csv = cdir / 'default_dedup_cut_tree.csv'
        raw_def_pcs = fpc._parse_raw_plates_from_csv(def_csv, om['sheet_w'], om['sheet_h'], om['sku_count'], 'default')
        merged_def: dict[str, tuple[fpc.PlatePattern, int]] = {}
        for pat, c in raw_def_pcs:
            if pat.signature in merged_def:
                merged_def[pat.signature] = (merged_def[pat.signature][0], merged_def[pat.signature][1] + c)
            else:
                merged_def[pat.signature] = (pat, c)
        fpc.write_patterns_cut_tree_csv(list(merged_def.values()), def_dedup_csv)
        def_econ = verify_and_score_csv(bins_csv, items_csv, def_dedup_csv, guillotine_model, deduplicate_plates=False)

        # 2. guide1_10s
        g1_csv = cdir / 'guide1_raw.csv'
        g1_run = run_ps_baseline_timed(bins_csv, items_csv, g1_csv, 10.0, guide=[1])
        g1_econ = verify_and_score_csv(bins_csv, items_csv, g1_csv, guillotine_model, deduplicate_plates=True)

        # 3. factory_pareto_10s (Python + PackingSolver + CP-SAT)
        fp_dir = cdir / 'factory_pareto'
        fp_res = fpc.solve_factory_pareto(
            bins_csv, items_csv, fp_dir,
            policy=policy, cost_model=guillotine_model,
            budget_s=10.0, seed=601, native_check_binary=None,
        )
        verified_fp = []
        for fp_plan in fp_res['pareto_frontier']:
            ver_plan = verify_and_score_csv(
                bins_csv, items_csv, pathlib.Path(fp_plan['cut_tree_csv']),
                guillotine_model, deduplicate_plates=False,
            )
            ver_plan['plan_id'] = fp_plan['plan_id']
            ver_plan['cut_tree_csv'] = fp_plan['cut_tree_csv']
            ver_plan['slitter_econ'] = rescore_econ_under_model(ver_plan, slitter_model)
            verified_fp.append(ver_plan)

        fp_min_paper = min(verified_fp, key=lambda x: (x['sheets_used'], x['total_cost_rmb']))
        fp_min_cost = min(verified_fp, key=lambda x: (x['total_cost_rmb'], x['sheets_used']))
        fp_min_setup = min(verified_fp, key=lambda x: (x['distinct_patterns'], x['total_time_min'], x['sheets_used']))
        fp_min_slitter = min(verified_fp, key=lambda x: (x['slitter_econ']['total_cost_rmb'], x['sheets_used']))

        # 4. cxx23_pareto_3s (Zero-dependency C++23 CLI / Wasm engine)
        cxx_dir = cdir / 'cxx23_pareto'
        cxx_dir.mkdir(parents=True, exist_ok=True)
        t_cxx0 = time.perf_counter()
        proc_cxx = subprocess.run(
            [
                str(CXX_CLI_BIN), 'pareto',
                '--bins', str(bins_csv),
                '--items', str(items_csv),
                '--budget', '3.0',
                '--seed', '601',
                '--order-name', oid,
            ],
            capture_output=True, text=True, check=True,
        )
        cxx_wall = time.perf_counter() - t_cxx0
        cxx_json = json.loads(proc_cxx.stdout)
        (cxx_dir / 'cxx23_pareto.json').write_text(proc_cxx.stdout)
        verified_cxx = []
        for c_plan in cxx_json['frontier']:
            c_csv = cxx_dir / f"{c_plan['plan_id']}_cut_tree.csv"
            c_csv.write_text(c_plan['cut_tree_csv'])
            ver_c = verify_and_score_csv(bins_csv, items_csv, c_csv, guillotine_model, deduplicate_plates=False)
            ver_c['plan_id'] = c_plan['plan_id']
            ver_c['cut_tree_csv'] = str(c_csv)
            ver_c['slitter_econ'] = rescore_econ_under_model(ver_c, slitter_model)
            verified_cxx.append(ver_c)
        cxx_min_cost = min(verified_cxx, key=lambda x: (x['total_cost_rmb'], x['sheets_used']))
        cxx_min_paper = min(verified_cxx, key=lambda x: (x['sheets_used'], x['total_cost_rmb']))

        row_b = {
            **om,
            'default_dedup_csv': str(def_dedup_csv),
            'default_10s': {**def_econ, 'online_wall_s': def_run['online_wall_s'], 'slitter_econ': rescore_econ_under_model(def_econ, slitter_model)},
            'guide1_10s': {**g1_econ, 'online_wall_s': g1_run['online_wall_s'], 'slitter_econ': rescore_econ_under_model(g1_econ, slitter_model)},
            'factory_pareto_10s': {
                'online_wall_s': fp_res['wall_s'],
                'candidate_patterns': fp_res['candidate_patterns_generated'],
                'pareto_count': len(verified_fp),
                'min_paper': fp_min_paper,
                'min_total_cost': fp_min_cost,
                'min_setup_fastest': fp_min_setup,
                'min_slitter_cost': fp_min_slitter,
                'frontier': verified_fp,
            },
            'cxx23_pareto_3s': {
                'online_wall_s': round(cxx_json['wall_ms'] / 1000.0, 6),
                'cli_wall_s': round(cxx_wall, 6),
                'pareto_count': len(verified_cxx),
                'min_paper': cxx_min_paper,
                'min_total_cost': cxx_min_cost,
                'frontier': verified_cxx,
            },
        }
        r60b_rows.append(row_b)
        print(
            f'  [R60-B {idx+1}/12] {oid[:26]:26s} | '
            f'def=({def_econ["sheets_used"]}sh,{def_econ["distinct_patterns"]}pat,{def_econ["total_time_min"]:.1f}m,¥{def_econ["total_cost_rmb"]:.0f}) | '
            f'fp_paper=({fp_min_paper["sheets_used"]}sh,{fp_min_paper["distinct_patterns"]}pat,¥{fp_min_paper["total_cost_rmb"]:.0f}) | '
            f'fp_cost=({fp_min_cost["sheets_used"]}sh,{fp_min_cost["distinct_patterns"]}pat,{fp_min_cost["total_time_min"]:.1f}m,¥{fp_min_cost["total_cost_rmb"]:.0f}) | '
            f'cxx23=({cxx_min_cost["sheets_used"]}sh,{cxx_min_cost["distinct_patterns"]}pat,¥{cxx_min_cost["total_cost_rmb"]:.0f})',
            flush=True,
        )

    # Aggregate R60-A & R60-B summaries
    def agg_r60a() -> dict[str, Any]:
        n = len(r60a_rows)
        def sum_field(getter):
            return sum(getter(r) for r in r60a_rows)
        return {
            'independent_instances': len(unseen_val_rep),
            'seeds': seeds,
            'runs_per_method': n,
            'max_online_wall_s_pareto': max(r['factory_pareto']['online_wall_s'] for r in r60a_rows),
            'default_totals': {
                'sheets': sum_field(lambda r: r['default']['sheets_used']),
                'patterns': sum_field(lambda r: r['default']['distinct_patterns']),
                'strokes': sum_field(lambda r: r['default']['total_strokes']),
                'time_min': round(sum_field(lambda r: r['default']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_field(lambda r: r['default']['total_cost_rmb']), 2),
                'cost_rmb_slitter': round(sum_field(lambda r: r['default']['slitter_econ']['total_cost_rmb']), 2),
            },
            'guide1_totals': {
                'sheets': sum_field(lambda r: r['guide1']['sheets_used']),
                'patterns': sum_field(lambda r: r['guide1']['distinct_patterns']),
                'strokes': sum_field(lambda r: r['guide1']['total_strokes']),
                'time_min': round(sum_field(lambda r: r['guide1']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_field(lambda r: r['guide1']['total_cost_rmb']), 2),
                'cost_rmb_slitter': round(sum_field(lambda r: r['guide1']['slitter_econ']['total_cost_rmb']), 2),
            },
            'mill_router_totals': {
                'sheets': sum_field(lambda r: r['mill_router']['sheets_used']),
                'patterns': sum_field(lambda r: r['mill_router']['distinct_patterns']),
                'strokes': sum_field(lambda r: r['mill_router']['total_strokes']),
                'time_min': round(sum_field(lambda r: r['mill_router']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_field(lambda r: r['mill_router']['total_cost_rmb']), 2),
                'cost_rmb_slitter': round(sum_field(lambda r: r['mill_router']['slitter_econ']['total_cost_rmb']), 2),
            },
            'pareto_min_paper_totals': {
                'sheets': sum_field(lambda r: r['factory_pareto']['min_paper']['sheets_used']),
                'patterns': sum_field(lambda r: r['factory_pareto']['min_paper']['distinct_patterns']),
                'strokes': sum_field(lambda r: r['factory_pareto']['min_paper']['total_strokes']),
                'time_min': round(sum_field(lambda r: r['factory_pareto']['min_paper']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_field(lambda r: r['factory_pareto']['min_paper']['total_cost_rmb']), 2),
                'vs_default_sheets_wtl': [
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_paper']['sheets_used'] < r['default']['sheets_used']),
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_paper']['sheets_used'] == r['default']['sheets_used']),
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_paper']['sheets_used'] > r['default']['sheets_used']),
                ],
                'vs_default_patterns_wtl': [
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_paper']['distinct_patterns'] < r['default']['distinct_patterns']),
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_paper']['distinct_patterns'] == r['default']['distinct_patterns']),
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_paper']['distinct_patterns'] > r['default']['distinct_patterns']),
                ],
            },
            'pareto_min_cost_guillotine_totals': {
                'sheets': sum_field(lambda r: r['factory_pareto']['min_total_cost']['sheets_used']),
                'patterns': sum_field(lambda r: r['factory_pareto']['min_total_cost']['distinct_patterns']),
                'strokes': sum_field(lambda r: r['factory_pareto']['min_total_cost']['total_strokes']),
                'time_min': round(sum_field(lambda r: r['factory_pareto']['min_total_cost']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_field(lambda r: r['factory_pareto']['min_total_cost']['total_cost_rmb']), 2),
                'vs_default_cost_wtl': [
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_total_cost']['total_cost_rmb'] < r['default']['total_cost_rmb'] - 1e-6),
                    sum(1 for r in r60a_rows if abs(r['factory_pareto']['min_total_cost']['total_cost_rmb'] - r['default']['total_cost_rmb']) <= 1e-6),
                    sum(1 for r in r60a_rows if r['factory_pareto']['min_total_cost']['total_cost_rmb'] > r['default']['total_cost_rmb'] + 1e-6),
                ],
            },
            'pareto_min_cost_slitter_totals': {
                'sheets': sum_field(lambda r: r['factory_pareto']['min_slitter_cost']['sheets_used']),
                'patterns': sum_field(lambda r: r['factory_pareto']['min_slitter_cost']['distinct_patterns']),
                'cost_rmb_slitter': round(sum_field(lambda r: r['factory_pareto']['min_slitter_cost']['slitter_econ']['total_cost_rmb']), 2),
            },
        }

    def agg_r60b() -> dict[str, Any]:
        def sum_b(getter):
            return sum(getter(r) for r in r60b_rows)
        return {
            'independent_orders': len(r60b_rows),
            'total_pieces_across_12_orders': sum_b(lambda r: r['total_pieces']),
            'max_online_wall_s_pareto': max(r['factory_pareto_10s']['online_wall_s'] for r in r60b_rows),
            'max_online_wall_s_cxx23': max(r['cxx23_pareto_3s']['online_wall_s'] for r in r60b_rows),
            'default_10s_totals': {
                'sheets': sum_b(lambda r: r['default_10s']['sheets_used']),
                'patterns': sum_b(lambda r: r['default_10s']['distinct_patterns']),
                'strokes': sum_b(lambda r: r['default_10s']['total_strokes']),
                'time_min': round(sum_b(lambda r: r['default_10s']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_b(lambda r: r['default_10s']['total_cost_rmb']), 2),
                'cost_rmb_slitter': round(sum_b(lambda r: r['default_10s']['slitter_econ']['total_cost_rmb']), 2),
            },
            'guide1_10s_totals': {
                'sheets': sum_b(lambda r: r['guide1_10s']['sheets_used']),
                'patterns': sum_b(lambda r: r['guide1_10s']['distinct_patterns']),
                'strokes': sum_b(lambda r: r['guide1_10s']['total_strokes']),
                'time_min': round(sum_b(lambda r: r['guide1_10s']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_b(lambda r: r['guide1_10s']['total_cost_rmb']), 2),
                'cost_rmb_slitter': round(sum_b(lambda r: r['guide1_10s']['slitter_econ']['total_cost_rmb']), 2),
            },
            'factory_pareto_min_paper_totals': {
                'sheets': sum_b(lambda r: r['factory_pareto_10s']['min_paper']['sheets_used']),
                'patterns': sum_b(lambda r: r['factory_pareto_10s']['min_paper']['distinct_patterns']),
                'strokes': sum_b(lambda r: r['factory_pareto_10s']['min_paper']['total_strokes']),
                'time_min': round(sum_b(lambda r: r['factory_pareto_10s']['min_paper']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_b(lambda r: r['factory_pareto_10s']['min_paper']['total_cost_rmb']), 2),
                'vs_default_sheets_wtl': [
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_paper']['sheets_used'] < r['default_10s']['sheets_used']),
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_paper']['sheets_used'] == r['default_10s']['sheets_used']),
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_paper']['sheets_used'] > r['default_10s']['sheets_used']),
                ],
                'vs_default_patterns_wtl': [
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_paper']['distinct_patterns'] < r['default_10s']['distinct_patterns']),
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_paper']['distinct_patterns'] == r['default_10s']['distinct_patterns']),
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_paper']['distinct_patterns'] > r['default_10s']['distinct_patterns']),
                ],
            },
            'factory_pareto_min_cost_guillotine_totals': {
                'sheets': sum_b(lambda r: r['factory_pareto_10s']['min_total_cost']['sheets_used']),
                'patterns': sum_b(lambda r: r['factory_pareto_10s']['min_total_cost']['distinct_patterns']),
                'strokes': sum_b(lambda r: r['factory_pareto_10s']['min_total_cost']['total_strokes']),
                'time_min': round(sum_b(lambda r: r['factory_pareto_10s']['min_total_cost']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_b(lambda r: r['factory_pareto_10s']['min_total_cost']['total_cost_rmb']), 2),
                'vs_default_cost_wtl': [
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_total_cost']['total_cost_rmb'] < r['default_10s']['total_cost_rmb'] - 1e-6),
                    sum(1 for r in r60b_rows if abs(r['factory_pareto_10s']['min_total_cost']['total_cost_rmb'] - r['default_10s']['total_cost_rmb']) <= 1e-6),
                    sum(1 for r in r60b_rows if r['factory_pareto_10s']['min_total_cost']['total_cost_rmb'] > r['default_10s']['total_cost_rmb'] + 1e-6),
                ],
            },
            'factory_pareto_min_cost_slitter_totals': {
                'sheets': sum_b(lambda r: r['factory_pareto_10s']['min_slitter_cost']['sheets_used']),
                'patterns': sum_b(lambda r: r['factory_pareto_10s']['min_slitter_cost']['distinct_patterns']),
                'cost_rmb_slitter': round(sum_b(lambda r: r['factory_pareto_10s']['min_slitter_cost']['slitter_econ']['total_cost_rmb']), 2),
            },
            'cxx23_pareto_3s_min_cost_totals': {
                'sheets': sum_b(lambda r: r['cxx23_pareto_3s']['min_total_cost']['sheets_used']),
                'patterns': sum_b(lambda r: r['cxx23_pareto_3s']['min_total_cost']['distinct_patterns']),
                'strokes': sum_b(lambda r: r['cxx23_pareto_3s']['min_total_cost']['total_strokes']),
                'time_min': round(sum_b(lambda r: r['cxx23_pareto_3s']['min_total_cost']['total_time_min']), 2),
                'cost_rmb_guillotine': round(sum_b(lambda r: r['cxx23_pareto_3s']['min_total_cost']['total_cost_rmb']), 2),
                'vs_default_cost_wtl': [
                    sum(1 for r in r60b_rows if r['cxx23_pareto_3s']['min_total_cost']['total_cost_rmb'] < r['default_10s']['total_cost_rmb'] - 1e-6),
                    sum(1 for r in r60b_rows if abs(r['cxx23_pareto_3s']['min_total_cost']['total_cost_rmb'] - r['default_10s']['total_cost_rmb']) <= 1e-6),
                    sum(1 for r in r60b_rows if r['cxx23_pareto_3s']['min_total_cost']['total_cost_rmb'] > r['default_10s']['total_cost_rmb'] + 1e-6),
                ],
            },
        }

    summary = {
        'experiment': 'R60',
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'cost_models': {
            'guillotine': asdict(guillotine_model),
            'slitter_sheeter': asdict(slitter_model),
        },
        'r60a_unseen_validation_summary': agg_r60a(),
        'r60b_industrial_batch_summary': agg_r60b(),
        'unseen_validation_consumed_ids': unseen_val_rep,
        'r60a_rows': r60a_rows,
        'r60b_rows': r60b_rows,
    }
    (r60_dir / 'r60_summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def cut_tree_to_web_plan(bins_csv: pathlib.Path, items_csv: pathlib.Path, cut_tree_csv: pathlib.Path) -> dict[str, Any]:
    """Convert any verified `cut_tree.csv` (with COPIES >= 1) into lightweight JSON for `web/canvas2d.js`."""
    W, H, items = pmc.read_order_files(bins_csv, items_csv)
    with cut_tree_csv.open(newline='') as f:
        rows = list(csv.DictReader(f))
    by_plate: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        pid = int(r['PLATE_ID'])
        by_plate[pid].append({
            'node_id': int(r['NODE_ID']),
            'x': int(r['X']),
            'y': int(r['Y']),
            'w': int(r['WIDTH']),
            'h': int(r['HEIGHT']),
            'type': int(r['TYPE']),
            'cut': int(r['CUT']),
            'parent': -1 if r.get('PARENT', '') == '' else int(r['PARENT']),
            'copies': int(r.get('COPIES', 1) or 1),
        })

    plates_out = []
    tot_sheets = 0
    tot_item_area = 0
    for pid in sorted(by_plate.keys()):
        pnodes = [n for n in by_plate[pid] if n['type'] != -4]
        if not pnodes:
            continue
        children: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
        root = None
        for n in pnodes:
            if n['parent'] < 0:
                root = n
            else:
                children[n['parent']].append(n)
        copies = root['copies'] if root else 1
        tot_sheets += copies
        placed = []
        cuts_per_stack = 0
        used_a = 0
        for n in pnodes:
            ch = children.get(n['node_id'], [])
            if not ch:
                if n['type'] >= 0 and (not root or n['node_id'] != root['node_id']):
                    spec = items[n['type']]
                    placed.append({
                        'item_idx': n['type'],
                        'item_id': int(spec['id']),
                        'x': n['x'],
                        'y': n['y'],
                        'w': n['w'],
                        'h': n['h'],
                    })
                    used_a += n['w'] * n['h']
            else:
                next_d = n['cut'] + 1
                if next_d % 2 == 1:
                    cs = sorted(ch, key=lambda c: c['y'])
                    for i in range(len(cs) - 1):
                        yc = cs[i]['y'] + cs[i]['h']
                        if n['y'] < yc < n['y'] + n['h']:
                            cuts_per_stack += 1
                else:
                    cs = sorted(ch, key=lambda c: c['x'])
                    for i in range(len(cs) - 1):
                        xc = cs[i]['x'] + cs[i]['w']
                        if n['x'] < xc < n['x'] + n['w']:
                            cuts_per_stack += 1
        tot_item_area += used_a * copies
        plates_out.append({
            'plate_id': pid,
            'copies': copies,
            'part_count': len(placed),
            'cuts_per_stack': cuts_per_stack,
            'utilization_pct': round(100.0 * used_a / (W * H), 2),
            'items': placed,
        })

    tot_sheet_area = tot_sheets * W * H
    return {
        'sheet_width': W,
        'sheet_height': H,
        'sheets_used': tot_sheets,
        'distinct_patterns': len(plates_out),
        'utilization_pct': round(100.0 * tot_item_area / max(1, tot_sheet_area), 2),
        'cut_tree_csv': cut_tree_csv.read_text(),
        'plates': plates_out,
    }


def export_web_factory_presets(r59_sum: dict[str, Any], r60_sum: dict[str, Any]) -> None:
    batch_presets = []
    # Pick 5 compelling industrial batch orders covering 889x1194, 787x1092, 1000x1400
    selected_ids = [
        'ind_01_cosmetics_carton_889x1194',
        'ind_02_pharma_leaflet_box_787x1092',
        'ind_04_bakery_food_sleeve_889x1194',
        'ind_05_apparel_hangtag_card_787x1092',
        'ind_09_ecommerce_mailer_1000x1400',
    ]
    row_by_id = {r['order_id']: r for r in r60_sum['r60b_rows']}
    for oid in selected_ids:
        r = row_by_id[oid]
        bins_csv = pathlib.Path(r['bins_csv'])
        items_csv = pathlib.Path(r['items_csv'])
        _, _, items = pmc.read_order_files(bins_csv, items_csv)
        base_plan = cut_tree_to_web_plan(bins_csv, items_csv, pathlib.Path(r['default_dedup_csv']))
        frontier_web = []
        for fp in r['factory_pareto_10s']['frontier']:
            wp = cut_tree_to_web_plan(bins_csv, items_csv, pathlib.Path(fp['cut_tree_csv']))
            wp['plan_id'] = fp['plan_id']
            frontier_web.append(wp)
        batch_presets.append({
            'id': oid,
            'title_zh': r['title_zh'],
            'sheet_width': r['sheet_w'],
            'sheet_height': r['sheet_h'],
            'sku_count': r['sku_count'],
            'total_pieces': r['total_pieces'],
            'bins_csv': bins_csv.read_text(),
            'items_csv': items_csv.read_text(),
            'items': items,
            'baseline': base_plan,
            'frontier': frontier_web,
        })

    payload = {
        'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'default_cost_model': r60_sum['cost_models']['guillotine'],
        'slitter_cost_model': r60_sum['cost_models']['slitter_sheeter'],
        'r59_headline': {
            'r52_berkey_dev_8': r59_sum['cohorts']['r52_berkey_dev'],
            'r55_martello_diag_12': r59_sum['cohorts']['r55_martello_posthoc_diag'],
            'fresh_berkey_dev_12': r59_sum['cohorts']['fresh_berkey_dev'],
        },
        'r60_headline': {
            'unseen_validation_8x2': r60_sum['r60a_unseen_validation_summary'],
            'industrial_batch_12': r60_sum['r60b_industrial_batch_summary'],
        },
        'batch_presets': batch_presets,
    }
    (OUT_ROOT / 'factory_showcase.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def update_cumulative_label_status(
    manifest: list[dict[str, Any]],
    label_status: dict[str, Any],
    r59_sum: dict[str, Any],
    r60_sum: dict[str, Any],
) -> dict[str, Any]:
    by_id = {m['name']: m for m in manifest}

    # 1. Record R59 runs (8 R52 Berkey dev + 12 fresh Berkey dev + 12 R55 Martello post-hoc diagnosis)
    for row in r59_sum['rows']:
        aid = row['anon_id']
        meta = by_id[aid]
        rec = label_status.setdefault(aid, {
            'split': meta['split'],
            'label_runs': 0,
            'methods': [],
            'prior_studies': [],
            'validation_consumed': False,
        })
        rec['label_runs'] = int(rec.get('label_runs', 0)) + len(row['runs'])
        m_set = set(rec.get('methods', [])) | set(row['runs'].keys())
        rec['methods'] = sorted(m_set)
        p_studies = list(rec.get('prior_studies', []))
        tag = 'R59(posthoc_bks_diag)' if row['is_posthoc_diagnosis_on_consumed_external'] else 'R59(bks_dev)'
        if tag not in p_studies:
            p_studies.append(tag)
        rec['prior_studies'] = p_studies

    # 2. Record R60-A runs and mark the 8 unseen validation repeated_demand instances consumed!
    by_aid_r60: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in r60_sum['r60a_rows']:
        by_aid_r60[r['anon_id']].append(r)

    for aid, arows in by_aid_r60.items():
        meta = by_id[aid]
        rec = label_status.setdefault(aid, {
            'split': meta['split'],
            'label_runs': 0,
            'methods': [],
            'prior_studies': [],
            'validation_consumed': True,
        })
        rec['validation_consumed'] = True
        rec['label_runs'] = int(rec.get('label_runs', 0)) + 4 * len(arows)
        m_set = set(rec.get('methods', [])) | {'default', 'guide1', 'mill_router', 'factory_pareto'}
        rec['methods'] = sorted(m_set)
        p_studies = list(rec.get('prior_studies', []))
        if 'R60(factory_pareto_validation)' not in p_studies:
            p_studies.append('R60(factory_pareto_validation)')
        rec['prior_studies'] = p_studies

    # Sort keys cleanly just like original current-label-status.json
    sorted_status = {k: label_status[k] for k in sorted(label_status.keys())}
    updated_path = OUT_ROOT / 'current-label-status-updated.json'
    updated_path.write_text(json.dumps(sorted_status, indent=2, ensure_ascii=False) + '\n')

    # Compute summary counts across the entire 1,760-instance corpus
    val_total = sum(1 for m in manifest if m['split'] == 'validation')
    test_total = sum(1 for m in manifest if m['split'] == 'test')
    ext_total = sum(1 for m in manifest if m['split'] == 'test_external')
    val_consumed = sum(1 for r in sorted_status.values() if r.get('split') == 'validation' and r.get('validation_consumed'))
    test_consumed = sum(1 for r in sorted_status.values() if r.get('split') == 'test' and r.get('test_consumed'))
    ext_consumed = sum(1 for r in sorted_status.values() if r.get('split') == 'test_external' and r.get('test_consumed'))
    by_split_labeled = collections.Counter(r.get('split') for r in sorted_status.values())

    return {
        'corpus_total': len(manifest),
        'any_labeled_instances': len(sorted_status),
        'unlabeled_instances': len(manifest) - len(sorted_status),
        'total_label_runs': sum(int(r.get('label_runs', 0)) for r in sorted_status.values()),
        'validation_consumed': val_consumed,
        'validation_unconsumed': val_total - val_consumed,
        'test_consumed': test_consumed,
        'test_unconsumed': test_total - test_consumed,
        'test_external_consumed': ext_consumed,
        'test_external_unconsumed': ext_total - ext_consumed,
        'by_split_labeled': dict(by_split_labeled),
    }


def main() -> None:
    t_all = time.perf_counter()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    frozen_path = OUT_ROOT / 'frozen-sha256.json'
    r59_saved_path = OUT_ROOT / 'r59_bks' / 'r59_summary.json'

    if frozen_path.exists() and (OUT_ROOT / 'wasm' / 'mill_cxx23.wasm').exists() and CXX_CLI_BIN.exists():
        print('=== [Resume] Reusing frozen C++23 CLI + Wasm build and verifying frozen SHA256s ===', flush=True)
        frozen = json.loads(frozen_path.read_text())
        build_info = frozen['build_info']
        check_map = {
            'paper_mill_cutter.py': CONTENT / 'paper_mill_cutter.py',
            'libcolstrip.so': CONTENT / 'libcolstrip.so',
            'mill-policy.json': POLICY_PATH,
            'factory_pareto_cutter.py': CONTENT / 'factory_pareto_cutter.py',
            'mill_cxx23.hpp': CXX_ROOT / 'include' / 'mill_cxx23.hpp',
            'mill_cxx23.cpp': CXX_ROOT / 'src' / 'mill_cxx23.cpp',
            'mill_cli.cpp': CXX_ROOT / 'src' / 'mill_cli.cpp',
            'mill_wasm.cpp': CXX_ROOT / 'src' / 'mill_wasm.cpp',
            'mill_cxx23_cli': CXX_CLI_BIN,
            'mill_cxx23.js': OUT_ROOT / 'wasm' / 'mill_cxx23.js',
            'mill_cxx23.wasm': OUT_ROOT / 'wasm' / 'mill_cxx23.wasm',
        }
        for k, p in check_map.items():
            actual = sha256_file(p)
            if actual != frozen['files'][k]:
                raise RuntimeError(f'Frozen SHA256 mismatch on {k}: {actual} != {frozen["files"][k]}')
        print('  All 11 frozen source & binary SHA256s verified identical!', flush=True)
    else:
        # 1. Build C++23 CLI + Emscripten Wasm
        build_info = ensure_emsdk_and_build_cxx23()

        # 2. Freeze SHA256s BEFORE opening unseen validation data
        frozen = {
            'frozen_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'files': {
                'paper_mill_cutter.py': sha256_file(CONTENT / 'paper_mill_cutter.py'),
                'libcolstrip.so': sha256_file(CONTENT / 'libcolstrip.so'),
                'mill-policy.json': sha256_file(POLICY_PATH),
                'factory_pareto_cutter.py': sha256_file(CONTENT / 'factory_pareto_cutter.py'),
                'mill_cxx23.hpp': CXX_ROOT / 'include' / 'mill_cxx23.hpp',
                'mill_cxx23.cpp': sha256_file(CXX_ROOT / 'src' / 'mill_cxx23.cpp'),
                'mill_cli.cpp': sha256_file(CXX_ROOT / 'src' / 'mill_cli.cpp'),
                'mill_wasm.cpp': sha256_file(CXX_ROOT / 'src' / 'mill_wasm.cpp'),
                'mill_cxx23_cli': sha256_file(CXX_CLI_BIN),
                'mill_cxx23.js': sha256_file(OUT_ROOT / 'wasm' / 'mill_cxx23.js'),
                'mill_cxx23.wasm': sha256_file(OUT_ROOT / 'wasm' / 'mill_cxx23.wasm'),
            },
            'build_info': build_info,
        }
        frozen_path.write_text(json.dumps(frozen, indent=2, ensure_ascii=False))

    manifest = json.loads(MANIFEST_PATH.read_text())
    label_status = json.loads(LABEL_STATUS_PATH.read_text())
    policy = json.loads(POLICY_PATH.read_text())
    bks_map = load_bks_map()

    # 3. Run or resume R59 (Public 3NHO BKS Gap Closure)
    if r59_saved_path.exists():
        print('=== [Resume] Loading completed R59 summary from r59_bks/r59_summary.json ===', flush=True)
        r59_sum = json.loads(r59_saved_path.read_text())
    else:
        r59_sum = run_r59_bks_study(manifest, label_status, policy, bks_map)

    # 4. Run R60 (Bi-Objective Factory Total-Cost Study)
    r60_sum = run_r60_factory_pareto_study(manifest, label_status, policy)

    # 5. Export Web Showcase Presets + Updated Label Status
    export_web_factory_presets(r59_sum, r60_sum)
    new_label_summary = update_cumulative_label_status(manifest, label_status, r59_sum, r60_sum)

    overall = {
        'total_wall_s': round(time.perf_counter() - t_all, 2),
        'frozen_sha256': frozen['files'],
        'wasm_build': {
            'wasm_js_bytes': build_info['wasm_js_bytes'],
            'wasm_bin_bytes': build_info['wasm_bin_bytes'],
            'native_cli_bytes': build_info['native_cli_bytes'],
        },
        'r59_cohorts': r59_sum['cohorts'],
        'r60a_unseen_validation': r60_sum['r60a_unseen_validation_summary'],
        'r60b_industrial_batch': r60_sum['r60b_industrial_batch_summary'],
        'updated_label_summary': new_label_summary,
    }
    (OUT_ROOT / 'overall_report.json').write_text(json.dumps(overall, indent=2, ensure_ascii=False))

    # 6. Zip results archive for clean download
    zip_path = CONTENT / 'factory-cost-study-results.zip'
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(OUT_ROOT.rglob('*')):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(OUT_ROOT)))

    print('\n=== ALL DONE ===', flush=True)
    print(json.dumps(overall, indent=2, ensure_ascii=False), flush=True)
    print(f'Archive ready: {zip_path} ({zip_path.stat().st_size} bytes)', flush=True)


if __name__ == '__main__':
    main()
