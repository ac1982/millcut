"""Unified Paper Manufactory Guillotine Cutter, Verifier, Router & Deliverable Generator.

Solves 2D rectangular 3-stage NonExact Horizontal-first oriented zero-kerf guillotine
sheet-minimization problems under a matched online wall-clock budget, and emits complete
executable manufactory deliverables:
  1. cut_tree.csv              (PackingSolver-compatible 3-stage NonExact guillotine tree)
  2. layout.json               (Sheet-by-sheet part coordinates & demand fulfillment)
  3. cutting_plan.json / .md   (Stage-by-stage shop-floor guillotine cutting schedule)
  4. material_summary.json     (Sheet usage, lower bound, utilization %, waste area/%)
  5. verification_certificate.json (Independent recursive guillotine + geometry + demand proof)
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import csv
import hashlib
import json
import math
import os
import pathlib
import statistics
import subprocess
import sys
import time
from typing import Any

import numpy as np
from ortools.sat.python import cp_model
from scipy.optimize import linprog
from scipy.sparse import csc_matrix

PS_ROOT = pathlib.Path('/content/portfolio-study')
if PS_ROOT.exists() and str(PS_ROOT) not in sys.path:
    sys.path.insert(0, str(PS_ROOT))

import packingsolver.rectangleguillotine as ps  # noqa: E402

_LIB_CACHE: ctypes.CDLL | None = None


def load_colstrip_lib(so_path: str | pathlib.Path = '/content/libcolstrip.so') -> ctypes.CDLL:
    global _LIB_CACHE
    if _LIB_CACHE is not None:
        return _LIB_CACHE
    lib = ctypes.CDLL(str(so_path))
    lib.colstrip_create.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_longlong,
        ctypes.c_int,
    ]
    lib.colstrip_create.restype = ctypes.c_void_p

    lib.colstrip_destroy.argtypes = [ctypes.c_void_p]
    lib.colstrip_destroy.restype = None

    lib.colstrip_generate.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_double,
        ctypes.c_uint,
        ctypes.c_int,
    ]
    lib.colstrip_generate.restype = ctypes.c_int

    lib.colstrip_counts.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_longlong),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.colstrip_counts.restype = None

    lib.colstrip_export_lp.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_longlong),
        ctypes.POINTER(ctypes.c_longlong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.colstrip_export_lp.restype = None

    lib.colstrip_get_strip_columns.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
    ]
    lib.colstrip_get_strip_columns.restype = ctypes.c_int

    _LIB_CACHE = lib
    return lib


# ---------------------------------------------------------------------------
# Input Reading & Scale/Order/Split-Invariant Geometric Features
# ---------------------------------------------------------------------------

def read_order_files(bins_csv: pathlib.Path, items_csv: pathlib.Path) -> tuple[int, int, list[dict[str, int]]]:
    with bins_csv.open(newline='') as f:
        bt = next(csv.DictReader(f))
    W, H = int(bt['WIDTH']), int(bt['HEIGHT'])
    items: list[dict[str, int]] = []
    with items_csv.open(newline='') as f:
        for idx, row in enumerate(csv.DictReader(f)):
            items.append({
                'id': int(row['ID']) if 'ID' in row and row['ID'] != '' else idx,
                'type_idx': idx,
                'w': int(row['WIDTH']),
                'h': int(row['HEIGHT']),
                'copies': int(row.get('COPIES', 1) or 1),
            })
    return W, H, items


def extract_mill_features(W: int, H: int, items: list[dict[str, int]]) -> dict[str, Any]:
    """Purely geometric, dimensionless, permutation- and copy-split-invariant features."""
    # Canonicalize by merging identical (w, h) pairs so splitting copies never changes features
    wh_counts: dict[tuple[int, int], int] = collections.Counter()
    for it in items:
        wh_counts[(it['w'], it['h'])] += it['copies']

    total_copies = sum(wh_counts.values())
    distinct_wh = len(wh_counts)
    sheet_area = W * H
    total_area = sum(w * h * c for (w, h), c in wh_counts.items())
    area_lb = max(1, (total_area + sheet_area - 1) // sheet_area)
    lb_fill = total_area / (area_lb * sheet_area)

    # Copy-weighted moments & quantiles
    w_norm_list: list[float] = []
    h_norm_list: list[float] = []
    a_norm_list: list[float] = []
    # Cap expansion at 2000 for speed while preserving exact proportions if huge copies
    scale_rep = 1
    if total_copies > 2000:
        scale_rep = max(1, total_copies // 1000)

    for (w, h), c in sorted(wh_counts.items()):
        rep = max(1, c // scale_rep) if scale_rep > 1 else c
        wn = w / W
        hn = h / H
        w_norm_list.extend([wn] * rep)
        h_norm_list.extend([hn] * rep)
        a_norm_list.extend([wn * hn] * rep)

    def _cv(vals: list[float]) -> float:
        m = statistics.fmean(vals)
        return statistics.pstdev(vals) / m if m > 1e-12 else 0.0

    def _q(vals_sorted: list[float], p: float) -> float:
        return vals_sorted[round((len(vals_sorted) - 1) * p)]

    ws = sorted(w_norm_list)
    hs = sorted(h_norm_list)
    mean_w = statistics.fmean(w_norm_list)
    mean_h = statistics.fmean(h_norm_list)
    q10_w = max(1e-9, _q(ws, 0.10))
    q10_h = max(1e-9, _q(hs, 0.10))
    wh_corr = (
        statistics.correlation(w_norm_list, h_norm_list)
        if len(set(w_norm_list)) > 1 and len(set(h_norm_list)) > 1
        else 0.0
    )

    unique_widths = len({w for (w, _) in wh_counts})
    unique_heights = len({h for (_, h) in wh_counts})

    # Near-height strip width support (weighted by copies)
    wh_list = sorted(wh_counts.items(), key=lambda kv: kv[0][1])
    supports: list[float] = []
    ge_one_copies = 0
    for (w, h), c in wh_list:
        sup_w = sum(ww * cc for (ww, hh), cc in wh_list if 0.90 * h <= hh <= h) / W
        supports.extend([sup_w] * (max(1, c // scale_rep) if scale_rep > 1 else c))
        if sup_w >= 1.0:
            ge_one_copies += c

    # 2-stack column partner compatibility fraction
    stackable_copies = 0
    short_copies = 0
    for (w, h), c in wh_list:
        if h <= 0.70 * H:
            short_copies += c
            has_partner = False
            for (ww, hh), cc in wh_list:
                if h + hh > H:
                    break
                if (w, h) == (ww, hh) and cc < 2:
                    continue
                if min(w, ww) >= 0.85 * max(w, ww):
                    has_partner = True
                    break
            if has_partner:
                stackable_copies += c

    return {
        'items': total_copies,
        'distinct_shapes': distinct_wh,
        'repeat_fraction': 1.0 - (distinct_wh / max(1, total_copies)),
        'area_lower_bound': area_lb,
        'lower_bound_fill': lb_fill,
        'unique_width_fraction': unique_widths / max(1, total_copies),
        'shape_unique_width_fraction': unique_widths / max(1, distinct_wh),
        'unique_height_fraction': unique_heights / max(1, total_copies),
        'shape_unique_height_fraction': unique_heights / max(1, distinct_wh),
        'mean_width': mean_w,
        'mean_height': mean_h,
        'max_width': ws[-1],
        'max_height': hs[-1],
        'width_cv': _cv(w_norm_list),
        'height_cv': _cv(h_norm_list),
        'area_cv': _cv(a_norm_list),
        'width_q90_q10': _q(ws, 0.90) / q10_w,
        'height_q90_q10': _q(hs, 0.90) / q10_h,
        'tall_fraction': sum(1 for v in h_norm_list if v > 0.5) / len(h_norm_list),
        'wide_fraction': sum(1 for v in w_norm_list if v > 0.5) / len(w_norm_list),
        'width_height_correlation': wh_corr,
        'near_height_width_support_mean': statistics.fmean(supports),
        'near_height_width_support_min': min(supports),
        'near_height_width_support_ge_one': ge_one_copies / max(1, total_copies),
        'stack2_partner_fraction': stackable_copies / max(1, total_copies),
        'short_stackable_ratio': stackable_copies / max(1, short_copies) if short_copies > 0 else 0.0,
    }


def evaluate_router_policy(features: dict[str, Any], policy: dict[str, Any]) -> str:
    """Evaluate a frozen JSON routing policy mapping features -> solver branch."""
    kind = policy.get('kind', 'default_only')
    if kind == 'default_only':
        return 'default'
    if kind == 'guide1_only':
        return 'guide1'
    if kind == 'colstrip_only':
        return 'colstrip'
    if kind == 'rule_list':
        for rule in policy['rules']:
            ok = True
            for cond in rule['conditions']:
                val = features[cond['feature']]
                op = cond['op']
                thr = cond['threshold']
                if op == 'ge' and not (val >= thr):
                    ok = False
                    break
                if op == 'le' and not (val <= thr):
                    ok = False
                    break
                if op == 'gt' and not (val > thr):
                    ok = False
                    break
                if op == 'lt' and not (val < thr):
                    ok = False
                    break
            if ok:
                return rule['branch']
        return policy.get('default_branch', 'default')
    raise ValueError(f'Unknown policy kind: {kind}')


# ---------------------------------------------------------------------------
# Native PackingSolver Builder & Wrapper
# ---------------------------------------------------------------------------

def build_ps_instance(bins_csv: pathlib.Path, items_csv: pathlib.Path, target_bins: int | None = None):
    b = ps.InstanceBuilder()
    if target_bins is None:
        b.read_bin_types(str(bins_csv))
        b.set_bin_types_infinite_copies()
        b.set_objective(ps.Objective.BinPacking)
    else:
        with bins_csv.open(newline='') as f:
            bt = next(csv.DictReader(f))
        b.add_bin_type(int(bt['WIDTH']), int(bt['HEIGHT']), copies=target_bins)
        b.set_objective(ps.Objective.Feasibility)
    b.read_item_types(str(items_csv))
    b.set_number_of_stages(3)
    b.set_cut_type(ps.CutType.NonExact)
    b.set_first_stage_orientation(ps.CutOrientation.Horizontal)
    b.set_item_types_oriented(True)
    b.set_cut_thickness(0)
    return b.build()


def run_ps_optimize(inst, time_limit: float, guide: list[int] | None = None):
    p = ps.OptimizeParameters()
    p.verbosity_level = 0
    p.time_limit = max(0.001, time_limit)
    if guide is not None:
        p.use_tree_search = True
        p.tree_search_guides = guide
    return ps.optimize(inst, p)


# ---------------------------------------------------------------------------
# Cut-Tree CSV Emission & Standalone 3-Stage NonExact Verifier
# ---------------------------------------------------------------------------

def write_colstrip_cut_tree_csv(
    plates: list[list[dict[str, Any]]],
    items: list[dict[str, int]],
    W: int,
    H: int,
    out_csv: pathlib.Path,
) -> None:
    """Convert structured colstrip solution into exact PackingSolver cut-tree CSV."""
    rows: list[tuple[Any, ...]] = []
    nid = 0

    def add_row(plate_id: int, copies: int, x: int, y: int, w: int, h: int,
                node_type: int, cut: int, parent: int | str) -> int:
        nonlocal nid
        cur = nid
        nid += 1
        rows.append((plate_id, copies, cur, x, y, w, h, node_type, cut, parent))
        return cur

    num_plates = len(plates)
    for p_idx, strips in enumerate(plates):
        copies = 1
        root_id = add_row(p_idx, copies, 0, 0, W, H, 0, 0, '')

        # Special case: single item equal to entire sheet (W, H)
        if (len(strips) == 1 and strips[0]['height'] == H
                and len(strips[0]['cols']) == 1
                and strips[0]['cols'][0]['width'] == W
                and len(strips[0]['cols'][0]['items']) == 1):
            t0 = strips[0]['cols'][0]['items'][0]
            if items[t0]['w'] == W and items[t0]['h'] == H:
                add_row(p_idx, copies, 0, 0, W, H, t0, 1, root_id)
                continue

        y = 0
        for s in strips:
            hs = s['height']
            strip_id = add_row(p_idx, copies, 0, y, W, hs, -2, 1, root_id)
            x = 0
            for col in s['cols']:
                wc = col['width']
                col_id = add_row(p_idx, copies, x, y, wc, hs, -2, 2, strip_id)
                yy = y
                for t_idx in col['items']:
                    wit = items[t_idx]['w']
                    hit = items[t_idx]['h']
                    if wit == wc:
                        add_row(p_idx, copies, x, yy, wc, hit, t_idx, 3, col_id)
                    else:
                        slice_id = add_row(p_idx, copies, x, yy, wc, hit, -2, 3, col_id)
                        add_row(p_idx, copies, x, yy, wit, hit, t_idx, 4, slice_id)
                        add_row(p_idx, copies, x + wit, yy, wc - wit, hit, -1, 4, slice_id)
                    yy += hit
                if yy < y + hs:
                    add_row(p_idx, copies, x, yy, wc, (y + hs) - yy, -1, 3, col_id)
                x += wc
            if x < W:
                add_row(p_idx, copies, x, y, W - x, hs, -1, 2, strip_id)
            y += hs
        if y < H:
            waste_type = -3 if (p_idx == num_plates - 1) else -1
            add_row(p_idx, copies, 0, y, W, H - y, waste_type, 1, root_id)

    with out_csv.open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['PLATE_ID', 'COPIES', 'NODE_ID', 'X', 'Y', 'WIDTH', 'HEIGHT', 'TYPE', 'CUT', 'PARENT'])
        writer.writerows(rows)


def verify_cut_tree_csv(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    cut_tree_csv: pathlib.Path,
    check_native_binary: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Independent recursive 3-stage NonExact Horizontal-first guillotine + geometry verifier."""
    W, H, items = read_order_files(bins_csv, items_csv)
    expected_copies = {it['type_idx']: it['copies'] for it in items}

    with cut_tree_csv.open(newline='') as f:
        raw_rows = list(csv.DictReader(f))
    if not raw_rows:
        raise AssertionError('Empty cut_tree.csv')

    plates: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in raw_rows:
        p_id = int(r['PLATE_ID'])
        parent_str = r.get('PARENT', '')
        node = {
            'plate_id': p_id,
            'copies': int(r['COPIES']),
            'node_id': int(r['NODE_ID']),
            'x': int(r['X']),
            'y': int(r['Y']),
            'w': int(r['WIDTH']),
            'h': int(r['HEIGHT']),
            'type': int(r['TYPE']),
            'cut': int(r['CUT']),
            'parent': None if parent_str == '' else int(parent_str),
        }
        plates[p_id].append(node)

    produced_copies: collections.Counter[int] = collections.Counter()
    total_bins = 0
    max_effective_stages = 0
    residual_height_last = 0

    sorted_pids = sorted(plates.keys())
    assert sorted_pids == list(range(len(sorted_pids))), 'PLATE_ID must be contiguous 0..K-1'

    for p_id in sorted_pids:
        pnodes = plates[p_id]
        copies = pnodes[0]['copies']
        assert copies >= 1, 'Plate COPIES must be >= 1'
        assert all(n['copies'] == copies for n in pnodes), 'Inconsistent COPIES within plate'
        total_bins += copies

        by_id = {n['node_id']: n for n in pnodes}
        children: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
        roots = []
        for n in pnodes:
            if n['type'] == -4:
                continue
            assert n['w'] > 0 and n['h'] > 0, f"Non-positive rect in node {n['node_id']}"
            assert 0 <= n['x'] and n['x'] + n['w'] <= W, f"X out of bounds in node {n['node_id']}"
            assert 0 <= n['y'] and n['y'] + n['h'] <= H, f"Y out of bounds in node {n['node_id']}"
            if n['parent'] is None:
                roots.append(n)
            else:
                assert n['parent'] in by_id, f"Missing parent {n['parent']} on plate {p_id}"
                children[n['parent']].append(n)

        assert len(roots) == 1, f'Expected 1 root on plate {p_id}, got {len(roots)}'
        root = roots[0]
        assert (root['x'], root['y'], root['w'], root['h'], root['cut']) == (0, 0, W, H, 0), 'Invalid root'

        placed_rects: list[tuple[int, int, int, int]] = []
        max_real_depth = 0

        for n in pnodes:
            nid = n['node_id']
            ch = children[nid]
            d = n['cut']
            if n['parent'] is not None:
                par = by_id[n['parent']]
                assert d == par['cut'] + 1, f'Depth jump at node {nid}'
                if (n['x'], n['y'], n['w'], n['h']) != (par['x'], par['y'], par['w'], par['h']):
                    max_real_depth = max(max_real_depth, d)

            if ch:
                assert n['type'] in (0, -2) or (nid == root['node_id']), f'Internal node {nid} invalid type {n["type"]}'
                # Horizontal first stage: CUT=1,3 partition along Y; CUT=2,4 partition along X
                next_d = d + 1
                if next_d % 2 == 1:
                    # Partition along Y; X and WIDTH must match parent
                    cur_y = n['y']
                    for c in ch:
                        assert c['x'] == n['x'] and c['w'] == n['w'], f'Horizontal cut X mismatch at {c["node_id"]}'
                        assert c['y'] == cur_y, f'Horizontal cut Y gap/overlap at {c["node_id"]}'
                        cur_y += c['h']
                    assert cur_y <= n['y'] + n['h'], f'Horizontal children exceed parent at {nid}'
                else:
                    # Partition along X; Y and HEIGHT must match parent
                    cur_x = n['x']
                    for c in ch:
                        assert c['y'] == n['y'] and c['h'] == n['h'], f'Vertical cut Y mismatch at {c["node_id"]}'
                        assert c['x'] == cur_x, f'Vertical cut X gap/overlap at {c["node_id"]}'
                        cur_x += c['w']
                    assert cur_x <= n['x'] + n['w'], f'Vertical children exceed parent at {nid}'
            else:
                # Leaf node
                if n['type'] >= 0 and nid != root['node_id']:
                    t_idx = n['type']
                    assert 0 <= t_idx < len(items), f'Unknown item type {t_idx}'
                    assert (n['w'], n['h']) == (items[t_idx]['w'], items[t_idx]['h']), (
                        f"Item {t_idx} dim mismatch: got {(n['w'], n['h'])}, expected {(items[t_idx]['w'], items[t_idx]['h'])}"
                    )
                    produced_copies[t_idx] += copies
                    placed_rects.append((n['x'], n['y'], n['w'], n['h']))
                elif n['type'] == -3 and p_id == sorted_pids[-1] and d == 1:
                    residual_height_last = n['h']

        # Pairwise non-overlap check on every plate
        for i, (x1, y1, w1, h1) in enumerate(placed_rects):
            for x2, y2, w2, h2 in placed_rects[i + 1:]:
                assert (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1), (
                    f'Overlapping items on plate {p_id}'
                )

        # PackingSolver 3-stage NonExact staging check (exact mirror of solution.cpp lines 391-451)
        eff_stages = max_real_depth
        if max_real_depth > 0:
            all_non_exact = True
            found = False
            for n in pnodes:
                if n['type'] == -2 and n['cut'] == max_real_depth - 1:
                    found = True
                    ch = [c for c in children[n['node_id']] if c['type'] != -3]
                    item_ch = sum(1 for c in ch if c['type'] >= 0)
                    if not (item_ch == 1 and len(ch) <= 2):
                        all_non_exact = False
                        break
            if found and all_non_exact:
                eff_stages = max_real_depth - 1
        assert eff_stages <= 3, f'Exceeded 3-stage NonExact limit on plate {p_id}: eff_stages={eff_stages}'
        max_effective_stages = max(max_effective_stages, eff_stages)

    assert dict(produced_copies) == {k: v for k, v in expected_copies.items() if v > 0}, (
        f'Demand mismatch: produced={dict(produced_copies)} vs expected={expected_copies}'
    )

    native_checked = False
    if check_native_binary is not None and check_native_binary.exists() and total_bins >= 2:
        tmp_csv = cut_tree_csv.parent / f'.tmp_natcheck_{os.getpid()}.csv'
        tmp_json = cut_tree_csv.parent / f'.tmp_natcheck_{os.getpid()}.json'
        try:
            proc = subprocess.run(
                [str(check_native_binary), str(items_csv), str(bins_csv), str(cut_tree_csv),
                 '0.0', '1', 'monotone', str(tmp_csv), str(tmp_json)],
                capture_output=True, text=True, timeout=15
            )
            if proc.returncode != 0:
                raise AssertionError(f'Native C++ SolutionBuilder check failed: {proc.stderr} {proc.stdout}')
            native_checked = True
        finally:
            tmp_csv.unlink(missing_ok=True)
            tmp_json.unlink(missing_ok=True)

    total_item_area = sum(it['w'] * it['h'] * it['copies'] for it in items)
    total_sheet_area = total_bins * W * H
    waste_area = total_sheet_area - total_item_area

    return {
        'valid': True,
        'sheets_used': total_bins,
        'distinct_plates': len(sorted_pids),
        'max_effective_stages': max_effective_stages,
        'cut_type': 'NonExact',
        'first_stage_orientation': 'Horizontal',
        'oriented_no_rotation': True,
        'kerf_thickness': 0,
        'demand_exact': True,
        'no_overlap': True,
        'within_bounds': True,
        'native_cpp_solution_builder_verified': native_checked,
        'sheet_width': W,
        'sheet_height': H,
        'total_item_area': total_item_area,
        'total_sheet_area': total_sheet_area,
        'waste_area': waste_area,
        'utilization_pct': round(100.0 * total_item_area / total_sheet_area, 4),
        'waste_pct': round(100.0 * waste_area / total_sheet_area, 4),
        'last_plate_residual_height': residual_height_last,
    }


# ---------------------------------------------------------------------------
# Core Upgraded Specialist Branch: colstrip (C++ Pricing + LP + CP-SAT + Fallback)
# ---------------------------------------------------------------------------

def _build_ffdh_plates(items: list[dict[str, int]], W: int, H: int) -> list[list[dict[str, Any]]]:
    """Guaranteed <1ms complete valid 3-stage NonExact Horizontal shelf packing (FFDH)."""
    pieces: list[tuple[int, int, int]] = []
    for it in items:
        for _ in range(it['copies']):
            pieces.append((it['type_idx'], it['w'], it['h']))
    pieces.sort(key=lambda x: (-x[2], -x[1], x[0]))

    strips: list[dict[str, Any]] = []
    for t_idx, w, h in pieces:
        placed = False
        for s in strips:
            if s['rem_w'] >= w:
                s['cols'].append({'width': w, 'height': h, 'items': [t_idx]})
                s['rem_w'] -= w
                placed = True
                break
        if not placed:
            strips.append({
                'height': h,
                'rem_w': W - w,
                'cols': [{'width': w, 'height': h, 'items': [t_idx]}],
            })

    strips.sort(key=lambda s: -s['height'])
    plates: list[dict[str, Any]] = []
    for s in strips:
        clean_s = {'height': s['height'], 'cols': s['cols']}
        placed_b = False
        for p in plates:
            if p['rem_h'] >= s['height']:
                p['strips'].append(clean_s)
                p['rem_h'] -= s['height']
                placed_b = True
                break
        if not placed_b:
            plates.append({'rem_h': H - s['height'], 'strips': [clean_s]})
    return [p['strips'] for p in plates]


def _solve_colstrip_branch(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    out_csv: pathlib.Path,
    seed: int,
    deadline: float,
    allow_stacks: bool = True,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    lib = load_colstrip_lib()
    W, H, items = read_order_files(bins_csv, items_csv)
    M = len(items)
    sheet_area = W * H
    total_area = sum(it['w'] * it['h'] * it['copies'] for it in items)
    total_pieces = sum(it['copies'] for it in items)
    area_lb = max(1, (total_area + sheet_area - 1) // sheet_area)

    # Stage 0: Instant <1ms FFDH constructive incumbent + Fast Native Probes (keep OutputHandle alive!)
    colstrip_plates: list[list[dict[str, Any]]] | None = _build_ffdh_plates(items, W, H)
    best_bins = len(colstrip_plates)
    winning_engine = 'ffdh_constructive'

    inst = build_ps_instance(bins_csv, items_csv)
    rem0 = max(0.08, deadline - time.perf_counter())
    init_def_s = min(1.15, rem0 * 0.13)
    t_s0 = time.perf_counter()
    best_ps_out = run_ps_optimize(inst, init_def_s, guide=None)
    s0_def_elapsed = time.perf_counter() - t_s0
    ps_overshoot = max(0.0, s0_def_elapsed - init_def_s)
    bound = max(area_lb, int(best_ps_out.bin_packing_bound))

    if best_ps_out.solution.full() and best_ps_out.solution.feasible() and 0 < best_ps_out.solution.number_of_bins() <= best_bins:
        best_bins = best_ps_out.solution.number_of_bins()
        colstrip_plates = None
        winning_engine = 'initial_default'

    # Quick guide1 probe guarantees a tight incumbent on <=8 sheet problems when M <= 125
    # (for M > 125, PackingSolver guide=[1] single pass can exceed short probe limits)
    guide1_fast_safe = (M <= 125 and total_pieces <= 160)
    if guide1_fast_safe and allow_fallback and best_bins > bound and bound <= 8 and (deadline - time.perf_counter()) > 5.0:
        g1_probe_s = min(1.55, max(0.25, (deadline - time.perf_counter()) * 0.17))
        g1_init = run_ps_optimize(inst, g1_probe_s, guide=[1])
        bound = max(bound, int(g1_init.bin_packing_bound))
        if g1_init.solution.full() and g1_init.solution.feasible() and 0 < g1_init.solution.number_of_bins() < best_bins:
            best_ps_out = g1_init
            best_bins = g1_init.solution.number_of_bins()
            colstrip_plates = None
            winning_engine = 'initial_guide1'

    base_bins = best_bins
    diag: dict[str, Any] = {
        'initial_bins': base_bins,
        'bound': bound,
        'generated_strips_r1': 0,
        'generated_strips_r2': 0,
        'cand_cols': 0,
        'lp1_obj': None,
        'lp2_obj': None,
        'retained_strips': 0,
        'cpsat_status': 'NOT_RUN',
        'fallback_ran': False,
        'winning_engine': winning_engine,
    }

    if best_bins <= bound:
        if colstrip_plates is not None:
            write_colstrip_cut_tree_csv(colstrip_plates, items, W, H, out_csv)
        else:
            best_ps_out.solution.write(str(out_csv))
        diag['status'] = f'PROVEN_OPTIMAL_{winning_engine.upper()}'
        return {'bins': best_bins, **diag}

    iw_arr = (ctypes.c_int * M)(*[it['w'] for it in items])
    ih_arr = (ctypes.c_int * M)(*[it['h'] for it in items])
    ic_arr = (ctypes.c_int * M)(*[it['copies'] for it in items])
    copies_np = np.array([it['copies'] for it in items], dtype=np.float64)

    def _run_tier(tier_stacks: bool, r1_budget: float, r2_budget: float, cp_max_budget: float, tier_seed: int) -> str:
        nonlocal best_bins, colstrip_plates
        target = best_bins - 1
        waste_budget = target * sheet_area - total_area
        if target < bound or waste_budget < 0 or (deadline - time.perf_counter()) <= 0.7:
            return 'SKIPPED'

        pool = lib.colstrip_create(
            M, iw_arr, ih_arr, ic_arr, W, H, int(waste_budget), 1 if tier_stacks else 0
        )
        try:
            def _export_and_solve_lp(lp_time_lim: float):
                n_s = ctypes.c_int(0)
                nnz = ctypes.c_longlong(0)
                n_cols = ctypes.c_int(0)
                lib.colstrip_counts(pool, ctypes.byref(n_s), ctypes.byref(nnz), ctypes.byref(n_cols))
                ns_val, nnz_val = n_s.value, nnz.value
                if ns_val == 0 or nnz_val == 0:
                    return None, ns_val, n_cols.value, None, None
                sh = np.empty(ns_val, dtype=np.int32)
                sa = np.empty(ns_val, dtype=np.int64)
                sw = np.empty(ns_val, dtype=np.int64)
                sub = np.empty(ns_val, dtype=np.int32)
                nz_r = np.empty(nnz_val, dtype=np.int32)
                nz_c = np.empty(nnz_val, dtype=np.int32)
                nz_v = np.empty(nnz_val, dtype=np.float64)
                lib.colstrip_export_lp(
                    pool,
                    sh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                    sa.ctypes.data_as(ctypes.POINTER(ctypes.c_longlong)),
                    sw.ctypes.data_as(ctypes.POINTER(ctypes.c_longlong)),
                    sub.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                    nz_r.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                    nz_c.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                    nz_v.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                )
                mat = csc_matrix((nz_v, (nz_r, nz_c)), shape=(M, ns_val))
                bounds = list(zip(np.zeros(ns_val), sub.astype(np.float64)))
                res = linprog(
                    c=sh.astype(np.float64),
                    A_eq=mat,
                    b_eq=copies_np,
                    bounds=bounds,
                    method='highs',
                    options={'time_limit': max(0.05, lp_time_lim)},
                )
                return res, ns_val, n_cols.value, (sh, sa, sw, sub), mat

            t_r1 = min(r1_budget, max(0.08, (deadline - time.perf_counter()) * 0.15))
            lib.colstrip_generate(pool, None, t_r1, tier_seed & 0xFFFFFFFF, 25000)
            lp1, ns1, ncols_cand, _, _ = _export_and_solve_lp(0.30)
            diag['generated_strips_r1'] = ns1
            diag['cand_cols'] = ncols_cand

            if lp1 is not None and lp1.eqlin is not None and lp1.eqlin.marginals is not None:
                diag['lp1_obj'] = round(float(lp1.fun), 3)
                duals_np = np.ascontiguousarray(lp1.eqlin.marginals, dtype=np.float64)
            else:
                duals_np = np.ascontiguousarray([float(it['h']) * 1.5 for it in items], dtype=np.float64)

            t_r2 = min(r2_budget, max(0.08, (deadline - time.perf_counter()) * 0.10))
            lib.colstrip_generate(
                pool,
                duals_np.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                t_r2,
                (tier_seed + 104729) & 0xFFFFFFFF,
                35000,
            )
            lp2, ns2, _, meta_arrays, mat2 = _export_and_solve_lp(0.35)
            diag['generated_strips_r2'] = ns2
            if lp2 is not None and lp2.fun is not None:
                diag['lp2_obj'] = round(float(lp2.fun), 3)

            if lp2 is None or lp2.x is None or lp2.fun > target * H + 1e-4:
                diag['cpsat_status'] = 'LP_LOWER_BOUND_BAILOUT'
                return 'LP_LOWER_BOUND_BAILOUT'

            sh, sa, sw, sub = meta_arrays
            valid_mask = sw <= waste_budget
            x_vals = lp2.x
            if lp2.lower is not None and lp2.lower.marginals is not None:
                red_costs = lp2.lower.marginals
            elif lp2.eqlin is not None and lp2.eqlin.marginals is not None:
                red_costs = sh.astype(np.float64) - mat2.T.dot(lp2.eqlin.marginals)
            else:
                red_costs = sw.astype(np.float64) / np.maximum(1.0, sa.astype(np.float64))

            order_idx = [
                int(j) for j in np.lexsort((-x_vals, red_costs))
                if valid_mask[j]
            ]
            selected_set: dict[int, None] = {}
            for j in range(ns2):
                if valid_mask[j] and x_vals[j] > 1e-7:
                    selected_set[j] = None
            for j in order_idx[:580]:
                selected_set[j] = None

            mat_csr = mat2.tocsr()
            rank_pos = {j: pos for pos, j in enumerate(order_idx)}
            for t in range(M):
                row_cols = mat_csr.indices[mat_csr.indptr[t]:mat_csr.indptr[t + 1]]
                cand_t = [int(j) for j in row_cols if valid_mask[j]]
                cand_t.sort(key=lambda j: rank_pos.get(j, 10**9))
                for j in cand_t[:12]:
                    selected_set[j] = None

            sel_ids = list(selected_set.keys())
            diag['retained_strips'] = len(sel_ids)

            pats_items: list[list[tuple[int, int]]] = []
            for j in sel_ids:
                s_ptr, e_ptr = mat2.indptr[j], mat2.indptr[j + 1]
                pats_items.append([
                    (int(mat2.indices[p]), int(round(mat2.data[p])))
                    for p in range(s_ptr, e_ptr)
                ])

            last_status = 'NOT_RUN'
            while target >= bound and lp2.fun <= target * H + 1e-4:
                cur_waste_budget = target * sheet_area - total_area
                rem_before_cp = deadline - time.perf_counter()
                if rem_before_cp <= 0.25:
                    break
                reserve_s = 1.4 if (allow_fallback and not diag['winning_engine'].startswith('colstrip_cpsat')) else 0.05
                cp_budget = max(0.20, min(cp_max_budget, rem_before_cp - reserve_s))
                if not allow_fallback:
                    cp_budget = max(0.05, rem_before_cp - 0.04)

                model = cp_model.CpModel()
                z = {}
                item_terms: list[list[Any]] = [[] for _ in range(M)]
                bin_terms: list[list[Any]] = [[] for _ in range(target)]
                waste_terms: list[Any] = []

                for p_i, orig_j in enumerate(sel_ids):
                    hj = int(sh[orig_j])
                    wj = int(sw[orig_j])
                    if wj > cur_waste_budget or hj > H:
                        continue
                    ub_global = int(sub[orig_j])
                    ub_bin = min(ub_global, H // hj)
                    if ub_bin <= 0:
                        continue
                    row_vars = []
                    for b in range(target):
                        if ub_bin == 1:
                            v = model.new_bool_var(f'z{p_i}_{b}')
                        else:
                            v = model.new_int_var(0, ub_bin, f'z{p_i}_{b}')
                        z[p_i, b] = v
                        row_vars.append(v)
                        bin_terms[b].append(hj * v)
                        if wj > 0:
                            waste_terms.append(wj * v)
                    if ub_global == 1:
                        model.add_at_most_one(row_vars)
                    else:
                        model.add(sum(row_vars) <= ub_global)

                    for t_id, mult in pats_items[p_i]:
                        s_var = sum(row_vars) if len(row_vars) > 1 else row_vars[0]
                        item_terms[t_id].append(mult * s_var if mult > 1 else s_var)

                if not all(len(item_terms[t]) > 0 for t in range(M) if items[t]['copies'] > 0):
                    last_status = 'UNCOVERED_AFTER_FILTER'
                    diag['cpsat_status'] = last_status
                    break

                for t in range(M):
                    if items[t]['copies'] > 0:
                        model.add(sum(item_terms[t]) == items[t]['copies'])

                loads = []
                for b in range(target):
                    ld = sum(bin_terms[b]) if bin_terms[b] else 0
                    model.add(ld <= H)
                    loads.append(ld)
                for b in range(target - 1):
                    model.add(loads[b] >= loads[b + 1])
                if waste_terms:
                    model.add(sum(waste_terms) <= cur_waste_budget)

                solver = cp_model.CpSolver()
                solver.parameters.num_search_workers = 2
                solver.parameters.random_seed = tier_seed
                solver.parameters.max_time_in_seconds = max(0.05, min(cp_budget, deadline - time.perf_counter() - 0.03))

                code = solver.solve(model)
                last_status = solver.status_name(code)
                diag['cpsat_status'] = last_status

                if code in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                    col_buf = (ctypes.c_int * (6 * 512))()
                    built_plates: list[list[dict[str, Any]]] = []
                    for b in range(target):
                        plate_strips: list[dict[str, Any]] = []
                        for p_i, orig_j in enumerate(sel_ids):
                            if (p_i, b) not in z:
                                continue
                            times = int(solver.value(z[p_i, b]))
                            if times <= 0:
                                continue
                            nc = lib.colstrip_get_strip_columns(pool, orig_j, col_buf, 512)
                            cols_decoded = []
                            for c_i in range(nc):
                                base_off = c_i * 6
                                cw = col_buf[base_off + 0]
                                ch = col_buf[base_off + 1]
                                ni = col_buf[base_off + 2]
                                c_items = [col_buf[base_off + 3 + u] for u in range(ni)]
                                cols_decoded.append({'width': cw, 'height': ch, 'items': c_items})
                            for _ in range(times):
                                plate_strips.append({
                                    'height': int(sh[orig_j]),
                                    'cols': cols_decoded,
                                })
                        if plate_strips:
                            built_plates.append(plate_strips)
                    colstrip_plates = built_plates
                    best_bins = len(built_plates)
                    diag['winning_engine'] = 'colstrip_cpsat_stack' if tier_stacks else 'colstrip_cpsat_shelf'
                    target = best_bins - 1
                else:
                    break
            return last_status
        finally:
            lib.colstrip_destroy(pool)

    # Two-Tier Pricing & Exact Cover Execution:
    # Run C++ pricing + CP-SAT whenever incumbent is within 3 bins of bound (including tight FFDH incumbents);
    # otherwise (large multi-sheet orders far from bound), preserve remaining budget for uninterrupted native search!
    if (best_bins <= bound + 3) or not allow_fallback:
        # Tier 1: Give full CP-SAT time to pure single-layer exact width subset-sum + dual pricing whenever LP_tier1 is feasible!
        t1_status = _run_tier(
            tier_stacks=False,
            r1_budget=0.90,
            r2_budget=0.30,
            cp_max_budget=5.4,
            tier_seed=seed,
        )

        # Tier 2: Only invoke 2/3-stack pricing when Tier 1 fast-exited (LP bailout or fast INFEASIBLE), never stealing Tier-1 CP-SAT budget!
        if allow_stacks and best_bins > bound and (deadline - time.perf_counter()) > 3.2:
            if t1_status in ('LP_LOWER_BOUND_BAILOUT', 'INFEASIBLE', 'UNCOVERED_AFTER_FILTER'):
                _run_tier(
                    tier_stacks=True,
                    r1_budget=0.50,
                    r2_budget=0.35,
                    cp_max_budget=3.2,
                    tier_seed=seed + 7919,
                )

    # Stage 3: Incumbent-Preserving Native Remaining-Budget Fallback
    # Account for measured single-pass C++ timer granularity (`ps_overshoot`) so Stage 3 never exceeds `deadline`.
    rem_fb = deadline - time.perf_counter() - 0.06 - ps_overshoot
    if allow_fallback and best_bins > bound and rem_fb > max(0.25, s0_def_elapsed * 0.65 if ps_overshoot > 1.0 else 0.25):
        diag['fallback_ran'] = True
        if guide1_fast_safe and rem_fb > 3.4 and not diag['winning_engine'].startswith('colstrip_cpsat'):
            g1_time = rem_fb * 0.55
            out_g1 = run_ps_optimize(inst, g1_time, guide=[1])
            if out_g1.solution.full() and out_g1.solution.feasible() and 0 < out_g1.solution.number_of_bins() < best_bins:
                best_ps_out = out_g1
                best_bins = best_ps_out.solution.number_of_bins()
                colstrip_plates = None
                diag['winning_engine'] = 'fallback_guide1'

            rem_def = deadline - time.perf_counter() - 0.06 - ps_overshoot
            if best_bins > bound and rem_def > 0.25:
                out_def = run_ps_optimize(inst, rem_def, guide=None)
                if out_def.solution.full() and out_def.solution.feasible() and 0 < out_def.solution.number_of_bins() < best_bins:
                    best_ps_out = out_def
                    best_bins = best_ps_out.solution.number_of_bins()
                    colstrip_plates = None
                    diag['winning_engine'] = 'fallback_default'
        else:
            guide_choice = [1] if (guide1_fast_safe and bound <= 8) else None
            out_fb = run_ps_optimize(inst, rem_fb, guide=guide_choice)
            if out_fb.solution.full() and out_fb.solution.feasible() and 0 < out_fb.solution.number_of_bins() < best_bins:
                best_ps_out = out_fb
                best_bins = best_ps_out.solution.number_of_bins()
                colstrip_plates = None
                diag['winning_engine'] = 'fallback_guide1' if guide_choice else 'fallback_default'

    if colstrip_plates is not None:
        write_colstrip_cut_tree_csv(colstrip_plates, items, W, H, out_csv)
    else:
        best_ps_out.solution.write(str(out_csv))

    diag['status'] = f"DONE_{diag['winning_engine'].upper()}"
    return {'bins': best_bins, **diag}


# ---------------------------------------------------------------------------
# Unified End-to-End Online Solver Entrypoint (Timed Including Verification)
# ---------------------------------------------------------------------------

def solve_order_online(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    out_csv: pathlib.Path,
    method: str = 'colstrip',
    seed: int = 1,
    budget_s: float = 10.0,
    policy: dict[str, Any] | None = None,
    check_native_binary: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Timed end-to-end online solver: input read + features + routing + solve + CSV + verification."""
    if method in ('colstrip', 'colstrip_nostack', 'colstrip_nofallback', 'mill_router'):
        load_colstrip_lib()  # Preload shared library outside per-query timer (same as ps/ortools)

    t0 = time.perf_counter()
    deadline = t0 + budget_s

    W, H, items = read_order_files(bins_csv, items_csv)
    feats = extract_mill_features(W, H, items)
    if method == 'mill_router':
        if policy is None:
            raise ValueError('policy required when method="mill_router"')
        branch = evaluate_router_policy(feats, policy)
    else:
        branch = method
    gate_s = time.perf_counter() - t0

    # Reserve ~0.12s inside budget for CSV serialization & standalone Python verification;
    # run optional external C++ binary audit right after online wall_s stops (matching R48-R50 protocol).
    solve_deadline = deadline - 0.12

    if branch in ('default', 'guide1'):
        inst = build_ps_instance(bins_csv, items_csv)
        rem = max(0.005, solve_deadline - time.perf_counter())
        out = run_ps_optimize(inst, rem, guide=[1] if branch == 'guide1' else None)
        sheet_area = W * H
        total_area = sum(it['w'] * it['h'] * it['copies'] for it in items)
        area_lb = max(1, (total_area + sheet_area - 1) // sheet_area)
        if out.solution.full() and out.solution.feasible() and out.solution.number_of_bins() > 0:
            out.solution.write(str(out_csv))
            bins_used = out.solution.number_of_bins()
            status_str = 'NATIVE_GUIDE1' if branch == 'guide1' else 'NATIVE_DEFAULT'
            win_eng = branch
        else:
            ffdh_plates = _build_ffdh_plates(items, W, H)
            write_colstrip_cut_tree_csv(ffdh_plates, items, W, H, out_csv)
            bins_used = len(ffdh_plates)
            status_str = f'FFDH_FALLBACK_{branch.upper()}'
            win_eng = 'ffdh_constructive'
        res_info = {
            'bins': bins_used,
            'initial_bins': bins_used,
            'bound': max(area_lb, int(out.bin_packing_bound)),
            'status': status_str,
            'winning_engine': win_eng,
        }
    elif branch in ('colstrip', 'colstrip_nostack', 'colstrip_nofallback'):
        res_info = _solve_colstrip_branch(
            bins_csv=bins_csv,
            items_csv=items_csv,
            out_csv=out_csv,
            seed=seed,
            deadline=solve_deadline,
            allow_stacks=(branch != 'colstrip_nostack'),
            allow_fallback=(branch != 'colstrip_nofallback'),
        )
    else:
        raise ValueError(f'Unsupported branch: {branch}')

    ver = verify_cut_tree_csv(bins_csv, items_csv, out_csv, check_native_binary=None)
    assert ver['sheets_used'] == res_info['bins'], 'Verified bin count mismatch'
    wall_s = time.perf_counter() - t0

    if check_native_binary is not None:
        ver_nat = verify_cut_tree_csv(bins_csv, items_csv, out_csv, check_native_binary=check_native_binary)
        ver['native_cpp_solution_builder_verified'] = ver_nat['native_cpp_solution_builder_verified']

    return {
        **res_info,
        'method': method,
        'branch': branch,
        'seed': seed,
        'budget_s': budget_s,
        'gate_s': round(gate_s, 6),
        'wall_s': round(wall_s, 6),
        'utilization_pct': ver['utilization_pct'],
        'waste_area': ver['waste_area'],
        'waste_pct': ver['waste_pct'],
        'valid': True,
        'certificate': out_csv.name,
        'certificate_sha256': hashlib.sha256(out_csv.read_bytes()).hexdigest(),
        'features': feats,
    }


# ---------------------------------------------------------------------------
# Complete Paper Manufactory Deliverable Package Exporter
# ---------------------------------------------------------------------------

def export_manufactory_package(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    cut_tree_csv: pathlib.Path,
    package_dir: pathlib.Path,
    order_name: str = 'order',
    lower_bound: int | None = None,
    baseline_comparison: dict[str, Any] | None = None,
    check_native_binary: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Generate complete executable Paper Manufactory package from any valid cut_tree.csv."""
    package_dir.mkdir(parents=True, exist_ok=True)
    target_tree_csv = package_dir / 'cut_tree.csv'
    if cut_tree_csv.resolve() != target_tree_csv.resolve():
        target_tree_csv.write_bytes(cut_tree_csv.read_bytes())

    W, H, items = read_order_files(bins_csv, items_csv)
    ver = verify_cut_tree_csv(bins_csv, items_csv, target_tree_csv, check_native_binary=check_native_binary)

    with target_tree_csv.open(newline='') as f:
        raw_rows = list(csv.DictReader(f))

    plates_nodes: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in raw_rows:
        p_str = r.get('PARENT', '')
        plates_nodes[int(r['PLATE_ID'])].append({
            'plate_id': int(r['PLATE_ID']),
            'copies': int(r['COPIES']),
            'node_id': int(r['NODE_ID']),
            'x': int(r['X']),
            'y': int(r['Y']),
            'w': int(r['WIDTH']),
            'h': int(r['HEIGHT']),
            'type': int(r['TYPE']),
            'cut': int(r['CUT']),
            'parent': None if p_str == '' else int(p_str),
        })

    layout_plates = []
    cutting_plates = []
    demand_fulfilled: collections.Counter[int] = collections.Counter()

    for pid in sorted(plates_nodes.keys()):
        pnodes = plates_nodes[pid]
        copies = pnodes[0]['copies']
        children: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
        root = None
        for n in pnodes:
            if n['parent'] is None:
                root = n
            else:
                children[n['parent']].append(n)
        assert root is not None

        placements = []
        for n in pnodes:
            if n['node_id'] != root['node_id'] and not children[n['node_id']] and n['type'] >= 0:
                t_idx = n['type']
                demand_fulfilled[t_idx] += copies
                placements.append({
                    'item_type_index': t_idx,
                    'item_id': items[t_idx]['id'],
                    'x': n['x'],
                    'y': n['y'],
                    'width': n['w'],
                    'height': n['h'],
                    'rotated': False,
                })

        used_area_single = sum(p['width'] * p['height'] for p in placements)
        layout_plates.append({
            'plate_id': pid,
            'copies': copies,
            'sheet_width': W,
            'sheet_height': H,
            'part_count_per_sheet': len(placements),
            'used_area_per_sheet': used_area_single,
            'waste_area_per_sheet': W * H - used_area_single,
            'utilization_pct': round(100.0 * used_area_single / (W * H), 4),
            'placements': placements,
        })

        # Build explicit Stage 1 -> Stage 2 -> Stage 3 (+ NonExact Trim) guillotine cut schedule
        stage1_cuts = []
        strips_schedule = []
        d1_nodes = sorted(children[root['node_id']], key=lambda n: n['y'])
        for s_idx, s_node in enumerate(d1_nodes):
            y_top = s_node['y'] + s_node['h']
            if y_top < H:
                stage1_cuts.append({
                    'cut_order': len(stage1_cuts) + 1,
                    'orientation': 'Horizontal',
                    'y': y_top,
                    'x_from': 0,
                    'x_to': W,
                    'cut_length': W,
                })
            if s_node['type'] in (-1, -3):
                strips_schedule.append({
                    'strip_index': s_idx,
                    'y': s_node['y'],
                    'height': s_node['h'],
                    'role': 'reusable_residual' if s_node['type'] == -3 else 'top_waste_strip',
                    'stage2_vertical_cuts': [],
                    'columns': [],
                })
                continue
            if s_node['type'] >= 0:
                strips_schedule.append({
                    'strip_index': s_idx,
                    'y': s_node['y'],
                    'height': s_node['h'],
                    'role': 'direct_full_width_item',
                    'item_id': items[s_node['type']]['id'],
                    'stage2_vertical_cuts': [],
                    'columns': [],
                })
                continue

            d2_nodes = sorted(children[s_node['node_id']], key=lambda n: n['x'])
            stage2_cuts = []
            cols_schedule = []
            for c_idx, c_node in enumerate(d2_nodes):
                x_right = c_node['x'] + c_node['w']
                if x_right < W:
                    stage2_cuts.append({
                        'cut_order': len(stage2_cuts) + 1,
                        'orientation': 'Vertical',
                        'x': x_right,
                        'y_from': s_node['y'],
                        'y_to': s_node['y'] + s_node['h'],
                        'cut_length': s_node['h'],
                    })
                if c_node['type'] < 0 and not children[c_node['node_id']]:
                    cols_schedule.append({
                        'column_index': c_idx,
                        'x': c_node['x'],
                        'width': c_node['w'],
                        'role': 'right_waste_column',
                        'stage3_horizontal_cuts': [],
                        'slices': [],
                    })
                    continue
                if c_node['type'] >= 0 and not children[c_node['node_id']]:
                    cols_schedule.append({
                        'column_index': c_idx,
                        'x': c_node['x'],
                        'width': c_node['w'],
                        'role': 'direct_column_item',
                        'item_id': items[c_node['type']]['id'],
                        'stage3_horizontal_cuts': [],
                        'slices': [{
                            'y': c_node['y'],
                            'height': c_node['h'],
                            'role': 'item',
                            'item_id': items[c_node['type']]['id'],
                            'width': c_node['w'],
                            'non_exact_width_trim_x': None,
                        }],
                    })
                    continue

                d3_nodes = sorted(children[c_node['node_id']], key=lambda n: n['y'])
                stage3_cuts = []
                slices_schedule = []
                for sl_node in d3_nodes:
                    y_sl_top = sl_node['y'] + sl_node['h']
                    if y_sl_top < s_node['y'] + s_node['h']:
                        stage3_cuts.append({
                            'cut_order': len(stage3_cuts) + 1,
                            'orientation': 'Horizontal',
                            'y': y_sl_top,
                            'x_from': c_node['x'],
                            'x_to': c_node['x'] + c_node['w'],
                            'cut_length': c_node['w'],
                        })
                    if sl_node['type'] >= 0:
                        slices_schedule.append({
                            'y': sl_node['y'],
                            'height': sl_node['h'],
                            'role': 'item',
                            'item_id': items[sl_node['type']]['id'],
                            'width': sl_node['w'],
                            'non_exact_width_trim_x': None,
                        })
                    elif sl_node['type'] == -2:
                        d4_nodes = sorted(children[sl_node['node_id']], key=lambda n: n['x'])
                        item_d4 = next(ch for ch in d4_nodes if ch['type'] >= 0)
                        slices_schedule.append({
                            'y': sl_node['y'],
                            'height': sl_node['h'],
                            'role': 'item_with_nonexact_trim',
                            'item_id': items[item_d4['type']]['id'],
                            'width': item_d4['w'],
                            'non_exact_width_trim_x': item_d4['x'] + item_d4['w'],
                        })
                    else:
                        slices_schedule.append({
                            'y': sl_node['y'],
                            'height': sl_node['h'],
                            'role': 'column_top_waste',
                            'item_id': None,
                            'width': sl_node['w'],
                            'non_exact_width_trim_x': None,
                        })
                cols_schedule.append({
                    'column_index': c_idx,
                    'x': c_node['x'],
                    'width': c_node['w'],
                    'role': 'production_column',
                    'stage3_horizontal_cuts': stage3_cuts,
                    'slices': slices_schedule,
                })

            strips_schedule.append({
                'strip_index': s_idx,
                'y': s_node['y'],
                'height': s_node['h'],
                'role': 'production_strip',
                'stage2_vertical_cuts': stage2_cuts,
                'columns': cols_schedule,
            })

        cutting_plates.append({
            'plate_id': pid,
            'copies': copies,
            'stage1_horizontal_cuts': stage1_cuts,
            'strips': strips_schedule,
        })

    # 1. layout.json
    layout_doc = {
        'order_name': order_name,
        'parent_sheet_spec': {'width': W, 'height': H, 'supply': 'unlimited'},
        'constraints': {
            'stages': 3,
            'cut_type': 'NonExact',
            'first_stage_orientation': 'Horizontal',
            'allow_rotation': False,
            'kerf_thickness': 0,
        },
        'sheets_used': ver['sheets_used'],
        'distinct_plate_patterns': len(layout_plates),
        'demand_fulfillment': [
            {
                'item_type_index': it['type_idx'],
                'item_id': it['id'],
                'width': it['w'],
                'height': it['h'],
                'required_copies': it['copies'],
                'produced_copies': demand_fulfilled[it['type_idx']],
            }
            for it in items
        ],
        'plates': layout_plates,
    }
    (package_dir / 'layout.json').write_text(json.dumps(layout_doc, indent=2))

    # 2. cutting_plan.json & cutting_plan.md
    cutting_doc = {
        'order_name': order_name,
        'machine_protocol': '3-Stage NonExact Guillotine (Stage 1 Horizontal -> Stage 2 Vertical -> Stage 3 Horizontal + NonExact Trim)',
        'kerf_thickness': 0,
        'plates': cutting_plates,
    }
    (package_dir / 'cutting_plan.json').write_text(json.dumps(cutting_doc, indent=2))

    md_lines = [
        f'# 纸厂裁切作业指导书 (Guillotine Cutting Schedule) — `{order_name}`',
        '',
        f'- **原纸规格 (Parent Sheet)**: `{W} × {H}` (零刀缝 `kerf = 0`, 禁止旋转 `oriented = true`)',
        f'- **工艺约束**: 三阶段非精确断头台通刀裁切 (`3-Stage NonExact`, 首刀横向 `Horizontal`)',
        f'- **总用板量**: **{ver["sheets_used"]} 张** | **整单利用率**: **{ver["utilization_pct"]:.2f}%** | **整单废料率**: **{ver["waste_pct"]:.2f}%**',
        '',
    ]
    for cp in cutting_plates:
        pid = cp['plate_id']
        s1_coords = [str(c['y']) for c in cp['stage1_horizontal_cuts']]
        md_lines.append(f'## 板型 #{pid} (重复裁切 `{cp["copies"]}` 张)')
        md_lines.append(f'- **第 1 阶段（整板横向通刀分条 `Y` 坐标）**: `{", ".join(s1_coords) if s1_coords else "无需分条（整板高）"}`')
        for st in cp['strips']:
            if st['role'] != 'production_strip':
                md_lines.append(f'  - 条带 `Y=[{st["y"]}, {st["y"] + st["height"]}]` (高 `{st["height"]}`): `{st["role"]}`')
                continue
            s2_coords = [str(c['x']) for c in st['stage2_vertical_cuts']]
            md_lines.append(
                f'  - **条带 #{st["strip_index"]}** `Y=[{st["y"]}, {st["y"] + st["height"]}]` (高 `{st["height"]}`) — '
                f'第 2 阶段纵向分列 `X` 刀位: `{", ".join(s2_coords) if s2_coords else "整宽单列"}`'
            )
            for col in st['columns']:
                if col['role'] == 'right_waste_column':
                    continue
                parts_desc = []
                for sl in col['slices']:
                    if sl['role'] == 'item':
                        parts_desc.append(f"零件#{sl['item_id']}({sl['width']}×{sl['height']})")
                    elif sl['role'] == 'item_with_nonexact_trim':
                        parts_desc.append(
                            f"零件#{sl['item_id']}({sl['width']}×{sl['height']}, 修边X={sl['non_exact_width_trim_x']})"
                        )
                md_lines.append(
                    f'    - 列 `X=[{col["x"]}, {col["x"] + col["width"]}]` (宽 `{col["width"]}`): ' + ' + '.join(parts_desc)
                )
        md_lines.append('')
    (package_dir / 'cutting_plan.md').write_text('\n'.join(md_lines))

    # 3. material_summary.json
    area_lb = max(1, (ver['total_item_area'] + W * H - 1) // (W * H))
    eff_lb = max(area_lb, lower_bound or area_lb)
    mat_doc: dict[str, Any] = {
        'order_name': order_name,
        'sheet_width': W,
        'sheet_height': H,
        'single_sheet_area': W * H,
        'total_parts_count': sum(it['copies'] for it in items),
        'distinct_item_types': len(items),
        'sheets_used': ver['sheets_used'],
        'mathematical_lower_bound': eff_lb,
        'proven_globally_optimal': ver['sheets_used'] == eff_lb,
        'total_sheet_area': ver['total_sheet_area'],
        'net_item_area': ver['total_item_area'],
        'material_utilization_pct': ver['utilization_pct'],
        'total_waste_area': ver['waste_area'],
        'waste_pct': ver['waste_pct'],
        'last_plate_reusable_residual_height': ver['last_plate_residual_height'],
    }
    if baseline_comparison is not None:
        mat_doc['baseline_comparison'] = baseline_comparison
    (package_dir / 'material_summary.json').write_text(json.dumps(mat_doc, indent=2))

    # 4. verification_certificate.json
    cert_doc = {
        'order_name': order_name,
        **ver,
        'sha256': {
            'bins_csv': hashlib.sha256(bins_csv.read_bytes()).hexdigest(),
            'items_csv': hashlib.sha256(items_csv.read_bytes()).hexdigest(),
            'cut_tree_csv': hashlib.sha256(target_tree_csv.read_bytes()).hexdigest(),
        },
    }
    (package_dir / 'verification_certificate.json').write_text(json.dumps(cert_doc, indent=2))
    return {'material_summary': mat_doc, 'verification': cert_doc}


# ---------------------------------------------------------------------------
# CLI Entrypoint for Paper Manufactory Production Use
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description='Paper Manufactory 3-Stage NonExact Guillotine Cutter')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_cut = sub.add_parser('cut', help='Solve an order and emit full manufactory deliverable package')
    p_cut.add_argument('--bins', type=pathlib.Path, required=True, help='Path to bins.csv')
    p_cut.add_argument('--items', type=pathlib.Path, required=True, help='Path to items.csv')
    p_cut.add_argument('--out-dir', type=pathlib.Path, required=True, help='Output package directory')
    p_cut.add_argument('--method', default='mill_router', choices=['mill_router', 'colstrip', 'default', 'guide1'])
    p_cut.add_argument('--policy', type=pathlib.Path, default=None, help='Router policy JSON path')
    p_cut.add_argument('--budget', type=float, default=10.0, help='Online compute budget in seconds')
    p_cut.add_argument('--seed', type=int, default=1, help='Solver random seed')
    p_cut.add_argument('--order-name', type=str, default='order', help='Order identifier for reports')
    p_cut.add_argument('--native-check', type=pathlib.Path, default=pathlib.Path('/content/native-check/ejection'))

    args = parser.parse_args()
    if args.cmd == 'cut':
        args.out_dir.mkdir(parents=True, exist_ok=True)
        pol = json.loads(args.policy.read_text()) if args.policy else {'kind': 'colstrip_only'}
        tree_csv = args.out_dir / 'cut_tree.csv'
        nat_bin = args.native_check if args.native_check.exists() else None
        res = solve_order_online(
            bins_csv=args.bins,
            items_csv=args.items,
            out_csv=tree_csv,
            method=args.method if args.policy or args.method != 'mill_router' else 'colstrip',
            seed=args.seed,
            budget_s=args.budget,
            policy=pol,
            check_native_binary=nat_bin,
        )
        pkg = export_manufactory_package(
            bins_csv=args.bins,
            items_csv=args.items,
            cut_tree_csv=tree_csv,
            package_dir=args.out_dir,
            order_name=args.order_name,
            lower_bound=res['bound'],
            check_native_binary=nat_bin,
        )
        print(json.dumps({'solve': res, 'package': pkg['material_summary']}, indent=2))


if __name__ == '__main__':
    main()
