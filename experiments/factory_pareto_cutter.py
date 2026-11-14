#!/usr/bin/env python3
"""Factory Bi-Objective Guillotine Cutter & Best-Known Precision Engine.

Optimizes the real two-dimensional paper/packaging factory objective under strict
3-Stage NonExact Horizontal Oriented Zero-Kerf guillotine constraints and 100%
exact item demand fulfillment (zero underproduction, zero overproduction):
  1. Material Dimension: Minimize parent sheets consumed (`sheets_used`) and trim waste.
  2. Time / Knife-Setup Dimension: Minimize distinct cutting patterns (`distinct_patterns`)
     and physical stack guillotine blade strokes (`total_strokes`) under clamp stack
     height limit `S_max` (e.g. Polar N115/N137 165mm clamp opening -> ~200 sheets/stack
     for 300gsm coated ivory board).

Also provides `solve_high_precision_min_paper` (target-bin Feasibility descent +
multi-guide tree search + column generation + SVC + colstrip) to close the gap to
published/archived 3NHO Best-Known Solutions (`data_bin_packing_3nho.csv`).
"""
from __future__ import annotations

import collections
from dataclasses import asdict, dataclass
import csv
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

import numpy as np
from ortools.sat.python import cp_model

EXP_DIR = pathlib.Path(__file__).resolve().parent
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))
if '/content' not in sys.path:
    sys.path.insert(0, '/content')

PS_ROOT = pathlib.Path('/content/portfolio-study')
if PS_ROOT.exists() and str(PS_ROOT) not in sys.path:
    sys.path.insert(0, str(PS_ROOT))

import packingsolver.rectangleguillotine as ps  # noqa: E402
import paper_mill_cutter as pmc  # noqa: E402


# ---------------------------------------------------------------------------
# Grounded Industrial Paper & Packaging Mill Cost Model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactoryCostModel:
    """Grounded shop-floor economics for 300gsm coated ivory board (889x1194mm)
    cut on a programmable high-speed guillotine (Polar N115/N137) or slitter-sheeter.
    """
    name: str = 'standard_guillotine_300g_ivory_board'
    # ~4,200 RMB/ton * 0.318 kg/sheet (889x1194mm 300gsm) + sheeting/handling ~= 2.00 RMB/sheet
    sheet_cost_rmb: float = 2.00
    # Machine depreciation + power + skilled operator (~180 RMB/hr = 3.00 RMB/min)
    machine_rate_rmb_per_hour: float = 180.0
    # Program changeover, pile jogging/squaring, first-article check & SKU pallet separation
    # (6.0 min on programmable guillotine; ~15.0 min on rotary slitter-sheeter knife reset)
    setup_minutes_per_pattern: float = 6.0
    # Operational cycle per physical stack through-cut (backgauge travel + clamp/cut + air-table turn)
    stroke_seconds: float = 9.0
    # Polar 115/137 max clamp opening 165mm -> ~200 sheets of 300gsm board per clamp stack
    max_stack_sheets: int = 200

    @property
    def rate_rmb_per_min(self) -> float:
        return self.machine_rate_rmb_per_hour / 60.0

    @property
    def setup_cost_rmb(self) -> float:
        return self.setup_minutes_per_pattern * self.rate_rmb_per_min

    @property
    def stroke_cost_rmb(self) -> float:
        return (self.stroke_seconds / 60.0) * self.rate_rmb_per_min


SLITTER_COST_MODEL = FactoryCostModel(
    name='slitter_sheeter_knife_changeover',
    sheet_cost_rmb=2.00,
    machine_rate_rmb_per_hour=240.0,
    setup_minutes_per_pattern=15.0,
    stroke_seconds=6.0,
    max_stack_sheets=250,
)


# ---------------------------------------------------------------------------
# Canonical Plate Pattern Representation & Cut-Tree Deduplication / Economics
# ---------------------------------------------------------------------------

@dataclass
class PlatePattern:
    """A verified single-sheet 3-Stage NonExact Horizontal cutting pattern."""
    W: int
    H: int
    # Normalized node tuples: (local_id, x, y, w, h, norm_type, cut, local_parent)
    # where norm_type maps -3 -> -1 so reusable-tail marking only depends on final plate order.
    nodes: tuple[tuple[int, int, int, int, int, int, int, int | None], ...]
    item_counts: tuple[int, ...]
    cuts_per_stack: int
    used_area: int
    signature: str
    source: str = 'unknown'


def _parse_raw_plates_from_csv(
    cut_tree_csv: pathlib.Path,
    W: int,
    H: int,
    num_items: int,
    source: str = 'csv',
) -> list[tuple[PlatePattern, int]]:
    with cut_tree_csv.open(newline='') as f:
        raw_rows = list(csv.DictReader(f))
    if not raw_rows:
        return []

    by_plate: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in raw_rows:
        pid = int(r['PLATE_ID'])
        p_str = r.get('PARENT', '')
        by_plate[pid].append({
            'node_id': int(r['NODE_ID']),
            'copies': int(r.get('COPIES', 1) or 1),
            'x': int(r['X']),
            'y': int(r['Y']),
            'w': int(r['WIDTH']),
            'h': int(r['HEIGHT']),
            'type': int(r['TYPE']),
            'cut': int(r['CUT']),
            'parent': None if p_str == '' else int(p_str),
        })

    out: list[tuple[PlatePattern, int]] = []
    for pid in sorted(by_plate.keys()):
        pnodes = [n for n in by_plate[pid] if n['type'] != -4]
        if not pnodes:
            continue
        copies = max(1, int(pnodes[0]['copies']))

        children: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
        roots = []
        for n in pnodes:
            if n['parent'] is None:
                roots.append(n)
            else:
                children[n['parent']].append(n)
        if len(roots) != 1:
            continue

        # Sort children deterministically in geometric order so equivalent trees get identical IDs
        ordered: list[dict[str, Any]] = []
        id_map: dict[int, int] = {}

        def _dfs(u: dict[str, Any]) -> None:
            new_id = len(ordered)
            id_map[u['node_id']] = new_id
            ordered.append(u)
            ch = children.get(u['node_id'], [])
            next_d = u['cut'] + 1
            if next_d % 2 == 1:
                ch_sorted = sorted(ch, key=lambda c: (c['y'], c['x']))
            else:
                ch_sorted = sorted(ch, key=lambda c: (c['x'], c['y']))
            for c in ch_sorted:
                _dfs(c)

        _dfs(roots[0])

        counts = [0] * num_items
        cuts_per_stack = 0
        norm_nodes: list[tuple[int, int, int, int, int, int, int, int | None]] = []

        for u in ordered:
            lid = id_map[u['node_id']]
            lpar = None if u['parent'] is None else id_map[u['parent']]
            ch = children.get(u['node_id'], [])
            ntype = -1 if u['type'] == -3 else u['type']
            if not ch and ntype >= 0 and lpar is not None:
                counts[ntype] += 1
            if ch:
                next_d = u['cut'] + 1
                if next_d % 2 == 1:
                    ch_s = sorted(ch, key=lambda c: c['y'])
                    for idx in range(len(ch_s) - 1):
                        yc = ch_s[idx]['y'] + ch_s[idx]['h']
                        if u['y'] < yc < u['y'] + u['h']:
                            cuts_per_stack += 1
                else:
                    ch_s = sorted(ch, key=lambda c: c['x'])
                    for idx in range(len(ch_s) - 1):
                        xc = ch_s[idx]['x'] + ch_s[idx]['w']
                        if u['x'] < xc < u['x'] + u['w']:
                            cuts_per_stack += 1
            norm_nodes.append((lid, u['x'], u['y'], u['w'], u['h'], ntype, u['cut'], lpar))

        if sum(counts) == 0:
            # Handle single full-sheet item if root was item leaf (rare)
            if len(ordered) == 1 and ordered[0]['type'] >= 0:
                counts[ordered[0]['type']] += 1
            else:
                continue

        used_area = sum(
            u['w'] * u['h']
            for u in ordered
            if not children.get(u['node_id']) and u['type'] >= 0 and u['parent'] is not None
        )
        sig_bytes = repr((W, H, tuple(norm_nodes))).encode('utf-8')
        sig = hashlib.blake2b(sig_bytes, digest_size=16).hexdigest()
        pat = PlatePattern(
            W=W,
            H=H,
            nodes=tuple(norm_nodes),
            item_counts=tuple(counts),
            cuts_per_stack=cuts_per_stack,
            used_area=used_area,
            signature=sig,
            source=source,
        )
        out.append((pat, copies))
    return out


def write_patterns_cut_tree_csv(
    pattern_copies: list[tuple[PlatePattern, int]],
    out_csv: pathlib.Path,
) -> None:
    """Emit standard PackingSolver-compatible cut_tree.csv with COPIES >= 1 per distinct PLATE_ID."""
    active = [(p, int(c)) for p, c in pattern_copies if int(c) > 0]
    # Place plate with largest top-level residual strip last so TYPE=-3 marks reusable offcut
    def _top_residual(pc: tuple[PlatePattern, int]) -> tuple[int, int]:
        p, c = pc
        res_h = 0
        for lid, x, y, w, h, ntype, cut, lpar in p.nodes:
            if cut == 1 and lpar == 0 and ntype == -1 and y + h == p.H:
                res_h = h
        # Prefer putting low-copy cleanup plate with largest residual strip last
        return (1 if res_h > 0 else 0, res_h - c * 10)

    if len(active) > 1:
        best_last_idx = max(range(len(active)), key=lambda i: _top_residual(active[i]))
        if best_last_idx != len(active) - 1:
            last_item = active.pop(best_last_idx)
            active.append(last_item)

    rows: list[tuple[Any, ...]] = []
    nid_offset = 0
    num_plates = len(active)
    for p_idx, (pat, copies) in enumerate(active):
        is_last = (p_idx == num_plates - 1)
        has_children = {lpar for (_, _, _, _, _, _, _, lpar) in pat.nodes if lpar is not None}
        for lid, x, y, w, h, ntype, cut, lpar in pat.nodes:
            out_type = ntype
            if is_last and cut == 1 and lpar == 0 and ntype == -1 and (lid not in has_children) and (y + h == pat.H):
                out_type = -3
            rows.append((
                p_idx,
                copies,
                nid_offset + lid,
                x,
                y,
                w,
                h,
                out_type,
                cut,
                '' if lpar is None else (nid_offset + lpar),
            ))
        nid_offset += len(pat.nodes)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['PLATE_ID', 'COPIES', 'NODE_ID', 'X', 'Y', 'WIDTH', 'HEIGHT', 'TYPE', 'CUT', 'PARENT'])
        writer.writerows(rows)


def _plate_strips_to_pattern(
    strips: list[dict[str, Any]],
    items: list[dict[str, int]],
    W: int,
    H: int,
    source: str,
) -> PlatePattern | None:
    """Convert an in-memory 3-stage strip/col/item layout directly into a PlatePattern."""
    nodes: list[tuple[int, int, int, int, int, int, int, int | None]] = []

    def add_node(x: int, y: int, w: int, h: int, ntype: int, cut: int, par: int | None) -> int:
        lid = len(nodes)
        nodes.append((lid, x, y, w, h, ntype, cut, par))
        return lid

    root_id = add_node(0, 0, W, H, 0, 0, None)
    M = len(items)
    counts = [0] * M
    used_area = 0

    if (len(strips) == 1 and strips[0]['height'] == H
            and len(strips[0]['cols']) == 1
            and strips[0]['cols'][0]['width'] == W
            and len(strips[0]['cols'][0]['items']) == 1):
        t0 = strips[0]['cols'][0]['items'][0]
        if items[t0]['w'] == W and items[t0]['h'] == H:
            add_node(0, 0, W, H, t0, 1, root_id)
            counts[t0] += 1
            sig = hashlib.blake2b(repr((W, H, tuple(nodes))).encode(), digest_size=16).hexdigest()
            return PlatePattern(W, H, tuple(nodes), tuple(counts), 0, W * H, sig, source)

    y = 0
    for s in strips:
        hs = int(s['height'])
        if hs <= 0 or y + hs > H:
            return None
        strip_id = add_node(0, y, W, hs, -2, 1, root_id)
        x = 0
        for col in s['cols']:
            wc = int(col['width'])
            if wc <= 0 or x + wc > W:
                return None
            c_items = col['items']
            if len(c_items) == 1 and items[c_items[0]]['w'] == wc and items[c_items[0]]['h'] == hs:
                t0 = c_items[0]
                add_node(x, y, wc, hs, t0, 2, strip_id)
                counts[t0] += 1
                used_area += wc * hs
            else:
                col_id = add_node(x, y, wc, hs, -2, 2, strip_id)
                yy = y
                for t_idx in c_items:
                    wit = items[t_idx]['w']
                    hit = items[t_idx]['h']
                    if yy + hit > y + hs or wit > wc:
                        return None
                    if wit == wc:
                        add_node(x, yy, wc, hit, t_idx, 3, col_id)
                    else:
                        sl_id = add_node(x, yy, wc, hit, -2, 3, col_id)
                        add_node(x, yy, wit, hit, t_idx, 4, sl_id)
                        add_node(x + wit, yy, wc - wit, hit, -1, 4, sl_id)
                    counts[t_idx] += 1
                    used_area += wit * hit
                    yy += hit
                if yy < y + hs:
                    add_node(x, yy, wc, (y + hs) - yy, -1, 3, col_id)
            x += wc
        if x < W:
            add_node(x, y, W - x, hs, -1, 2, strip_id)
        y += hs
    if y < H:
        add_node(0, y, W, H - y, -1, 1, root_id)

    if sum(counts) == 0:
        return None

    # Compute exact interior guillotine cuts per stack
    children: dict[int, list[tuple[int, int, int, int, int, int, int, int | None]]] = collections.defaultdict(list)
    for nd in nodes:
        if nd[7] is not None:
            children[nd[7]].append(nd)
    cuts_per_stack = 0
    for nd in nodes:
        ch = children.get(nd[0], [])
        if ch:
            next_d = nd[6] + 1
            if next_d % 2 == 1:
                ch_s = sorted(ch, key=lambda c: c[2])
                for i in range(len(ch_s) - 1):
                    yc = ch_s[i][2] + ch_s[i][4]
                    if nd[2] < yc < nd[2] + nd[4]:
                        cuts_per_stack += 1
            else:
                ch_s = sorted(ch, key=lambda c: c[1])
                for i in range(len(ch_s) - 1):
                    xc = ch_s[i][1] + ch_s[i][3]
                    if nd[1] < xc < nd[1] + nd[3]:
                        cuts_per_stack += 1

    sig = hashlib.blake2b(repr((W, H, tuple(nodes))).encode(), digest_size=16).hexdigest()
    return PlatePattern(W, H, tuple(nodes), tuple(counts), cuts_per_stack, used_area, sig, source)


def evaluate_cut_tree_economics(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    cut_tree_csv: pathlib.Path,
    cost_model: FactoryCostModel = FactoryCostModel(),
    deduplicate_plates: bool = True,
    dedup_out_csv: pathlib.Path | None = None,
    native_check_binary: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Compute exact paper, setup, stack, blade-stroke, time, and RMB cost metrics."""
    W, H, items = pmc.read_order_files(bins_csv, items_csv)
    raw_pcs = _parse_raw_plates_from_csv(cut_tree_csv, W, H, len(items), source='eval')
    raw_distinct_plates = len(raw_pcs)

    if deduplicate_plates and raw_pcs:
        merged: dict[str, tuple[PlatePattern, int]] = {}
        for pat, c in raw_pcs:
            if pat.signature in merged:
                p0, c0 = merged[pat.signature]
                merged[pat.signature] = (p0, c0 + c)
            else:
                merged[pat.signature] = (pat, c)
        pcs = list(merged.values())
        if dedup_out_csv is not None:
            write_patterns_cut_tree_csv(pcs, dedup_out_csv)
            target_verify_csv = dedup_out_csv
        else:
            target_verify_csv = cut_tree_csv
    else:
        pcs = raw_pcs
        target_verify_csv = cut_tree_csv

    ver = pmc.verify_cut_tree_csv(bins_csv, items_csv, target_verify_csv, check_native_binary=native_check_binary)
    s_max = max(1, int(cost_model.max_stack_sheets))

    sheets_used = sum(c for _, c in pcs)
    distinct_patterns = len(pcs)
    total_stacks = sum((c + s_max - 1) // s_max for _, c in pcs)
    total_strokes = sum(((c + s_max - 1) // s_max) * pat.cuts_per_stack for pat, c in pcs)
    unstacked_strokes = sum(c * pat.cuts_per_stack for pat, c in pcs)

    setup_time_min = distinct_patterns * cost_model.setup_minutes_per_pattern
    cut_time_min = total_strokes * (cost_model.stroke_seconds / 60.0)
    total_time_min = setup_time_min + cut_time_min

    paper_cost_rmb = sheets_used * cost_model.sheet_cost_rmb
    setup_cost_rmb = setup_time_min * cost_model.rate_rmb_per_min
    cut_cost_rmb = cut_time_min * cost_model.rate_rmb_per_min
    total_cost_rmb = paper_cost_rmb + setup_cost_rmb + cut_cost_rmb

    return {
        'valid': ver['valid'],
        'native_verified': ver['native_cpp_solution_builder_verified'],
        'sheets_used': sheets_used,
        'raw_distinct_plates': raw_distinct_plates,
        'distinct_patterns': distinct_patterns,
        'clamp_stacks': total_stacks,
        'total_strokes': total_strokes,
        'unstacked_strokes': unstacked_strokes,
        'utilization_pct': ver['utilization_pct'],
        'waste_pct': ver['waste_pct'],
        'waste_area': ver['waste_area'],
        'setup_time_min': round(setup_time_min, 3),
        'cut_time_min': round(cut_time_min, 3),
        'total_time_min': round(total_time_min, 3),
        'paper_cost_rmb': round(paper_cost_rmb, 2),
        'setup_cost_rmb': round(setup_cost_rmb, 2),
        'cut_cost_rmb': round(cut_cost_rmb, 2),
        'total_cost_rmb': round(total_cost_rmb, 2),
        'max_effective_stages': ver['max_effective_stages'],
        'pattern_multiplicities': sorted([c for _, c in pcs], reverse=True),
    }


# ---------------------------------------------------------------------------
# Fast Single-Plate & Subproblem Generators for Pattern Minimization
# ---------------------------------------------------------------------------

def _pack_single_plate_shelf_dp(
    W: int,
    H: int,
    items: list[dict[str, int]],
    avail_copies: list[int],
    item_weights: list[float],
) -> PlatePattern | None:
    """Fast (<2ms) 3-stage NonExact single-plate strip-knapsack packer."""
    cand_heights = sorted({
        items[i]['h'] for i in range(len(items)) if avail_copies[i] > 0 and items[i]['h'] <= H
    }, reverse=True)
    if not cand_heights:
        return None

    rem = list(avail_copies)
    rem_h = H
    strips: list[dict[str, Any]] = []

    while rem_h > 0:
        best_score = -1.0
        best_strip = None

        # Evaluate candidate strip heights
        feasible_hs = [h for h in cand_heights if h <= rem_h]
        if not feasible_hs:
            break
        # Subsample if too many distinct heights
        if len(feasible_hs) > 18:
            step = max(1, len(feasible_hs) // 16)
            feasible_hs = feasible_hs[::step]

        for sh in feasible_hs:
            # Greedy value-density + exact width fill inside strip of height sh
            cols_cand: list[tuple[float, int, list[int]]] = []
            for i, it in enumerate(items):
                if rem[i] <= 0 or it['w'] > W or it['h'] > sh:
                    continue
                # Check vertical self-stacking inside column of height sh
                max_stack = min(rem[i], sh // it['h'], 2)
                for k in range(1, max_stack + 1):
                    val = k * item_weights[i]
                    cols_cand.append((val / it['w'], i, [i] * k))

            if not cols_cand:
                continue
            cols_cand.sort(key=lambda x: (-x[0], -items[x[1]]['w']))

            local_rem = list(rem)
            rem_w = W
            chosen_cols: list[dict[str, Any]] = []
            strip_val = 0.0
            actual_sh = 0
            for _, i_idx, stack_list in cols_cand:
                need = len(stack_list)
                w_it = items[i_idx]['w']
                h_col = need * items[i_idx]['h']
                while local_rem[i_idx] >= need and rem_w >= w_it:
                    chosen_cols.append({'width': w_it, 'height': h_col, 'items': list(stack_list)})
                    local_rem[i_idx] -= need
                    rem_w -= w_it
                    strip_val += need * item_weights[i_idx]
                    if h_col > actual_sh:
                        actual_sh = h_col

            if chosen_cols and actual_sh > 0:
                score = strip_val / (W * actual_sh)
                if score > best_score:
                    best_score = score
                    best_strip = ({'height': actual_sh, 'cols': chosen_cols}, local_rem)

        if best_strip is None:
            break
        s_dict, rem = best_strip
        strips.append(s_dict)
        rem_h -= s_dict['height']

    if not strips:
        return None
    return _plate_strips_to_pattern(strips, items, W, H, source='fast_shelf_knapsack')


def _pack_single_plate_ps_knapsack(
    W: int,
    H: int,
    items: list[dict[str, int]],
    avail_copies: list[int],
    profits: list[float],
    time_limit: float,
    shm_dir: pathlib.Path,
) -> PlatePattern | None:
    """Exact/anytime 3-Stage NonExact single-sheet Knapsack via PackingSolver."""
    b = ps.InstanceBuilder()
    b.add_bin_type(W, H, copies=1)
    b.set_objective(ps.Objective.Knapsack)

    active_to_orig: list[int] = []
    for i, it in enumerate(items):
        c = int(avail_copies[i])
        if c > 0:
            b.add_item_type(it['w'], it['h'], profit=float(max(1.0, profits[i])), copies=c, oriented=True)
            active_to_orig.append(i)
    if not active_to_orig:
        return None

    b.set_number_of_stages(3)
    b.set_cut_type(ps.CutType.NonExact)
    b.set_first_stage_orientation(ps.CutOrientation.Horizontal)
    b.set_cut_thickness(0)
    inst = b.build()

    p = ps.OptimizeParameters()
    p.verbosity_level = 0
    p.time_limit = max(0.01, time_limit)
    p.use_tree_search = True
    p.tree_search_guides = [0, 1]
    out = ps.optimize(inst, p)
    if out.solution.number_of_bins() < 1 or out.solution.number_of_items() < 1:
        return None

    tmp_csv = shm_dir / 'kp_single.csv'
    out.solution.write(str(tmp_csv))
    # Map active item type indices back to original item indices
    with tmp_csv.open(newline='') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        t = int(r['TYPE'])
        if int(r['CUT']) > 0 and t >= 0:
            r['TYPE'] = str(active_to_orig[t])
    mapped_csv = shm_dir / 'kp_mapped.csv'
    with mapped_csv.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    pcs = _parse_raw_plates_from_csv(mapped_csv, W, H, len(items), source='ps_knapsack')
    return pcs[0][0] if pcs else None


def _solve_suborder_binpacking(
    W: int,
    H: int,
    items: list[dict[str, int]],
    sub_copies: list[int],
    time_limit: float,
    shm_dir: pathlib.Path,
) -> list[tuple[PlatePattern, int]]:
    """Solve a reduced/residual exact 3-Stage NonExact BinPacking suborder."""
    active_to_orig: list[int] = []
    b = ps.InstanceBuilder()
    b.add_bin_type(W, H, copies=-1)
    b.set_bin_types_infinite_copies()
    b.set_objective(ps.Objective.BinPacking)
    for i, it in enumerate(items):
        c = int(sub_copies[i])
        if c > 0:
            b.add_item_type(it['w'], it['h'], profit=-1, copies=c, oriented=True)
            active_to_orig.append(i)
    if not active_to_orig:
        return []
    b.set_number_of_stages(3)
    b.set_cut_type(ps.CutType.NonExact)
    b.set_first_stage_orientation(ps.CutOrientation.Horizontal)
    b.set_cut_thickness(0)
    inst = b.build()

    p = ps.OptimizeParameters()
    p.verbosity_level = 0
    p.time_limit = max(0.02, time_limit)
    out = ps.optimize(inst, p)

    if not (out.solution.full() and out.solution.feasible()):
        # Instant FFDH fallback on suborder
        sub_items = [
            {'id': items[i]['id'], 'type_idx': i, 'w': items[i]['w'], 'h': items[i]['h'], 'copies': int(sub_copies[i])}
            for i in active_to_orig
        ]
        ffdh_plates = pmc._build_ffdh_plates(sub_items, W, H)
        res_map: dict[str, tuple[PlatePattern, int]] = {}
        for sp in ffdh_plates:
            pat = _plate_strips_to_pattern(sp, items, W, H, source='suborder_ffdh')
            if pat is not None:
                if pat.signature in res_map:
                    res_map[pat.signature] = (res_map[pat.signature][0], res_map[pat.signature][1] + 1)
                else:
                    res_map[pat.signature] = (pat, 1)
        return list(res_map.values())

    tmp_csv = shm_dir / 'bp_sub.csv'
    out.solution.write(str(tmp_csv))
    with tmp_csv.open(newline='') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        t = int(r['TYPE'])
        if int(r['CUT']) > 0 and t >= 0:
            r['TYPE'] = str(active_to_orig[t])
    mapped_csv = shm_dir / 'bp_mapped.csv'
    with mapped_csv.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)

    raw_pcs = _parse_raw_plates_from_csv(mapped_csv, W, H, len(items), source='ps_sub_bp')
    merged: dict[str, tuple[PlatePattern, int]] = {}
    for pat, c in raw_pcs:
        if pat.signature in merged:
            merged[pat.signature] = (merged[pat.signature][0], merged[pat.signature][1] + c)
        else:
            merged[pat.signature] = (pat, c)
    return list(merged.values())


# ---------------------------------------------------------------------------
# Part A: Best-Known Solution (BKS) Recovery / High-Precision Min-Paper Solver
# ---------------------------------------------------------------------------

def solve_high_precision_min_paper(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    out_csv: pathlib.Path,
    policy: dict[str, Any],
    budget_s: float = 10.0,
    seed: int = 501,
) -> dict[str, Any]:
    """Enhanced sheet-minimizing solver combining frozen mill_router (`colstrip` / `guide1`)
    with target-bin Feasibility descent (`TreeSearch guides 0..5 + ColumnGeneration + SVC`)
    so large multi-bin orders (e.g. Berkey / Martello 3NHO) tighten toward Best-Known Solutions.
    """
    t0 = time.perf_counter()
    deadline = t0 + budget_s - 0.14
    W, H, items = pmc.read_order_files(bins_csv, items_csv)
    features = pmc.extract_mill_features(W, H, items)
    branch = pmc.evaluate_router_policy(features, policy)

    shm_base = pathlib.Path('/dev/shm') if pathlib.Path('/dev/shm').exists() else out_csv.parent
    work_dir = pathlib.Path(tempfile.mkdtemp(prefix='mill_hp_', dir=str(shm_base)))
    try:
        if branch == 'colstrip':
            col_budget = min(budget_s * 0.75, max(4.0, deadline - time.perf_counter() - 0.1)) if budget_s > 15.0 else (deadline - time.perf_counter())
            col_csv = work_dir / 'colstrip.csv'
            res = pmc._solve_colstrip_branch(bins_csv, items_csv, col_csv, seed=seed, deadline=t0 + col_budget)
            best_bins = int(res['bins'])
            best_csv = col_csv
            bound = int(res.get('bound', features['area_lower_bound']))
            winning = f"colstrip:{res.get('winning_engine', 'colstrip')}"
        elif branch == 'guide1':
            inst = pmc.build_ps_instance(bins_csv, items_csv)
            g1_s = min(9.5, max(1.0, (deadline - time.perf_counter()) * (0.45 if budget_s > 15.0 else 0.96)))
            out_g1 = pmc.run_ps_optimize(inst, g1_s, guide=[1])
            bound = max(features['area_lower_bound'], int(out_g1.bin_packing_bound))
            if out_g1.solution.full() and out_g1.solution.feasible():
                best_bins = out_g1.solution.number_of_bins()
                best_csv = work_dir / 'guide1.csv'
                out_g1.solution.write(str(best_csv))
                winning = 'guide1'
            else:
                ffdh = pmc._build_ffdh_plates(items, W, H)
                best_bins = len(ffdh)
                best_csv = work_dir / 'ffdh.csv'
                pmc.write_colstrip_cut_tree_csv(ffdh, items, W, H, best_csv)
                winning = 'ffdh_fallback'
        else:
            # Default large-order branch: initial BinPacking probe + target-bin Feasibility descent!
            inst = pmc.build_ps_instance(bins_csv, items_csv)
            probe_s = min(5.4 if budget_s <= 12.0 else 20.0, max(0.8, (deadline - time.perf_counter()) * 0.54))
            out0 = pmc.run_ps_optimize(inst, probe_s, guide=None)
            bound = max(features['area_lower_bound'], int(out0.bin_packing_bound))
            if out0.solution.full() and out0.solution.feasible():
                best_bins = out0.solution.number_of_bins()
                best_csv = work_dir / 'def0.csv'
                out0.solution.write(str(best_csv))
                winning = 'default_probe'
            else:
                ffdh = pmc._build_ffdh_plates(items, W, H)
                best_bins = len(ffdh)
                best_csv = work_dir / 'ffdh.csv'
                pmc.write_colstrip_cut_tree_csv(ffdh, items, W, H, best_csv)
                winning = 'ffdh_fallback'

        # Target-Bin Feasibility Descent whenever gap to lower bound remains and time > 0.45s
        descent_steps = 0
        while best_bins > bound and (deadline - time.perf_counter()) > 0.45:
            target = best_bins - 1
            rem = deadline - time.perf_counter()
            inst_f = pmc.build_ps_instance(bins_csv, items_csv, target_bins=target)
            pf = ps.OptimizeParameters()
            pf.verbosity_level = 0
            pf.time_limit = max(0.25, rem - 0.08)
            pf.use_tree_search = True
            pf.tree_search_guides = [0, 1, 2, 3, 4, 5]
            pf.use_sequential_single_knapsack = True
            pf.use_sequential_value_correction = True
            pf.use_column_generation = True
            out_f = ps.optimize(inst_f, pf)
            descent_steps += 1
            if out_f.solution.full() and out_f.solution.feasible() and 0 < out_f.solution.number_of_bins() <= target:
                best_bins = out_f.solution.number_of_bins()
                step_csv = work_dir / f'descent_{target}.csv'
                out_f.solution.write(str(step_csv))
                best_csv = step_csv
                winning = f'target_feasibility_descent(step={descent_steps})'
            else:
                break

        # Canonical plate deduplication (merges any identical plates at zero extra sheet cost)
        raw_pcs = _parse_raw_plates_from_csv(best_csv, W, H, len(items), source=winning)
        merged: dict[str, tuple[PlatePattern, int]] = {}
        for pat, c in raw_pcs:
            if pat.signature in merged:
                merged[pat.signature] = (merged[pat.signature][0], merged[pat.signature][1] + c)
            else:
                merged[pat.signature] = (pat, c)
        write_patterns_cut_tree_csv(list(merged.values()), out_csv)
        wall_s = time.perf_counter() - t0
        return {
            'bins': best_bins,
            'distinct_patterns': len(merged),
            'bound': bound,
            'branch': branch,
            'winning_engine': winning,
            'wall_s': round(wall_s, 6),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part B: Bi-Objective Factory Pareto Engine (Min-Paper vs Min-Cost vs Min-Setup)
# ---------------------------------------------------------------------------

def solve_factory_pareto(
    bins_csv: pathlib.Path,
    items_csv: pathlib.Path,
    out_dir: pathlib.Path,
    policy: dict[str, Any],
    cost_model: FactoryCostModel = FactoryCostModel(),
    budget_s: float = 10.0,
    seed: int = 501,
    native_check_binary: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Generate a verified Pareto frontier of shop-floor 3-stage cutting plans trading off
    parent paper sheets (`sheets_used`) against pattern setups (`distinct_patterns`) and
    physical stack cutting strokes (`total_strokes`).
    """
    t0 = time.perf_counter()
    deadline = t0 + budget_s - 0.22
    out_dir.mkdir(parents=True, exist_ok=True)

    W, H, items = pmc.read_order_files(bins_csv, items_csv)
    M = len(items)
    demands = [int(it['copies']) for it in items]
    max_demand = max(demands)
    total_copies = sum(demands)
    sheet_area = W * H

    shm_base = pathlib.Path('/dev/shm') if pathlib.Path('/dev/shm').exists() else out_dir
    work_dir = pathlib.Path(tempfile.mkdtemp(prefix='mill_pareto_', dir=str(shm_base)))

    try:
        # Pool of deduplicated single-sheet patterns keyed by item_counts vector
        # Keep minimum cuts_per_stack when two patterns produce identical item_counts!
        pool_by_counts: dict[tuple[int, ...], PlatePattern] = {}
        trajectories: list[list[tuple[tuple[int, ...], int]]] = []

        def register_pattern(pat: PlatePattern | None) -> tuple[int, ...] | None:
            if pat is None or sum(pat.item_counts) == 0:
                return None
            # Verify pattern does not exceed order demands on any SKU
            if any(pat.item_counts[i] > demands[i] for i in range(M)):
                return None
            key = pat.item_counts
            prev = pool_by_counts.get(key)
            if prev is None or pat.cuts_per_stack < prev.cuts_per_stack:
                pool_by_counts[key] = pat
            return key

        def register_trajectory(pcs: list[tuple[PlatePattern, int]]) -> None:
            if not pcs:
                return
            tot_prod = [0] * M
            traj: list[tuple[tuple[int, ...], int]] = []
            merged_counts: dict[tuple[int, ...], int] = collections.Counter()
            for pat, c in pcs:
                if c <= 0:
                    continue
                k = register_pattern(pat)
                if k is None:
                    return
                merged_counts[k] += int(c)
                for i in range(M):
                    tot_prod[i] += k[i] * int(c)
            if tot_prod == demands:
                for k, c in merged_counts.items():
                    traj.append((k, c))
                trajectories.append(traj)

        # -------------------------------------------------------------------
        # Stage 1 (~42% budget): Strong Min-Paper Anchor
        # -------------------------------------------------------------------
        s1_budget = budget_s * (0.88 if max_demand == 1 else 0.42)
        min_paper_csv = work_dir / 'min_paper_anchor.csv'
        hp_info = solve_high_precision_min_paper(
            bins_csv, items_csv, min_paper_csv, policy=policy, budget_s=s1_budget, seed=seed
        )
        anchor_pcs = _parse_raw_plates_from_csv(min_paper_csv, W, H, M, source='min_paper_anchor')
        register_trajectory(anchor_pcs)

        # -------------------------------------------------------------------
        # Stage 2 (~38% budget): Multi-Multiplicity Batch Peeling & GCD Pricing
        # -------------------------------------------------------------------
        p2_deadline = t0 + budget_s * 0.80
        if max_demand >= 2 and time.perf_counter() < p2_deadline:
            # 2a. Exact GCD scaling if all demands share a common factor > 1
            g = 0
            for d in demands:
                g = math.gcd(g, d)
            if g > 1 and (p2_deadline - time.perf_counter()) > 0.15:
                sub_d = [d // g for d in demands]
                sub_pcs = _solve_suborder_binpacking(
                    W, H, items, sub_d, min(0.8, (p2_deadline - time.perf_counter()) * 0.25), work_dir
                )
                if sub_pcs:
                    register_trajectory([(pat, c * g) for pat, c in sub_pcs])

            # 2b. Single-SKU clean homogeneous grid templates (great low-stroke building blocks)
            for i, it in enumerate(items):
                nx = W // it['w']
                ny = H // it['h']
                cap = min(demands[i], nx * ny)
                if cap >= 1:
                    # Build clean 2-stage grid pattern for c_take copies of item i
                    candidate_takes = {cap}
                    # Add useful divisors of demands[i] <= cap so exact demand clears in 1 setup!
                    for div in range(1, min(demands[i], 64) + 1):
                        if demands[i] % div == 0:
                            q = demands[i] // div
                            if 1 <= q <= cap:
                                candidate_takes.add(q)
                    for c_take in candidate_takes:
                        rem_take = c_take
                        strips_h = []
                        while rem_take > 0:
                            row_n = min(nx, rem_take)
                            strips_h.append({
                                'height': it['h'],
                                'cols': [{'width': it['w'], 'height': it['h'], 'items': [i]} for _ in range(row_n)],
                            })
                            rem_take -= row_n
                        if len(strips_h) <= ny:
                            register_pattern(_plate_strips_to_pattern(strips_h, items, W, H, source='homo_sku'))

            # 2c. Multi-trajectory Batch Peeling across diverse target fill thresholds & weightings
            peel_configs = [
                # (min_fill_ratio, div_bonus, scarcity_bonus, use_ps_knapsack, max_peels)
                (0.82, 0.18, 0.05, True, 12),
                (0.70, 0.25, 0.10, True, 10),
                (0.55, 0.35, 0.15, True, 8),
                (0.40, 0.45, 0.20, False, 6),
                (0.28, 0.50, 0.25, False, 5),
                (0.76, 0.00, 0.30, False, 10),
            ]

            for cfg_idx, (min_fill, div_bonus, scar_bonus, use_ps_kp, max_peels) in enumerate(peel_configs):
                if time.perf_counter() >= p2_deadline - 0.12:
                    break
                rem = list(demands)
                traj_pcs: list[tuple[PlatePattern, int]] = []

                for _step in range(max_peels):
                    if time.perf_counter() >= p2_deadline - 0.10:
                        break
                    max_r = max(rem)
                    rem_area = sum(rem[i] * items[i]['w'] * items[i]['h'] for i in range(M))
                    if max_r < 2 or rem_area <= int(1.35 * sheet_area):
                        break

                    # Candidate batch multiplicities m >= 2 derived from current residual demands
                    pos_r = sorted({r for r in rem if r >= 2}, reverse=True)
                    cand_ms: set[int] = set(pos_r[:8])
                    for r in pos_r[:6]:
                        for div in (2, 3, 4, 5):
                            if r // div >= 2:
                                cand_ms.add(r // div)
                    # Also add target sheets-per-pattern dividers
                    est_sheets = max(1, round(rem_area / sheet_area))
                    for target_k in (1, 2, 3, 4):
                        m_est = est_sheets // target_k
                        if 2 <= m_est <= max_r:
                            cand_ms.add(m_est)
                    for fixed_m in (200, 100, 50, 25, 20, 15, 10, 8, 6, 5, 4, 3, 2):
                        if fixed_m <= max_r:
                            cand_ms.add(fixed_m)

                    # Pick highest multiplicity m that packs at least min_fill * sheet_area
                    best_peel: tuple[float, int, PlatePattern] | None = None
                    sorted_ms = sorted(cand_ms, reverse=True)
                    for m in sorted_ms:
                        avail = [rem[i] // m for i in range(M)]
                        max_avail_area = sum(avail[i] * items[i]['w'] * items[i]['h'] for i in range(M))
                        if max_avail_area < min_fill * sheet_area and m > 2:
                            continue
                        if sum(avail) == 0:
                            continue

                        weights = []
                        for i in range(M):
                            base_a = float(items[i]['w'] * items[i]['h'])
                            db = div_bonus if (avail[i] > 0 and rem[i] % m == 0) else 0.0
                            sb = scar_bonus * (rem[i] / max(1, max_r))
                            weights.append(base_a * (1.0 + db + sb))

                        pat_fast = _pack_single_plate_shelf_dp(W, H, items, avail, weights)
                        if pat_fast is not None:
                            register_pattern(pat_fast)
                            fill_ratio = pat_fast.used_area / sheet_area
                            if fill_ratio >= min_fill or m == 2:
                                # Score favors eliminating huge batch sheets in 1 setup with high fill
                                exact_hits = sum(
                                    1 for i in range(M)
                                    if pat_fast.item_counts[i] > 0 and pat_fast.item_counts[i] * m == rem[i]
                                )
                                score = (fill_ratio ** 1.6) * math.log2(m + 1.0) * (1.0 + 0.08 * exact_hits)
                                if best_peel is None or score > best_peel[0]:
                                    best_peel = (score, m, pat_fast)

                    # Refine winning m with PackingSolver single-bin Knapsack if budget allows
                    if best_peel is not None and use_ps_kp and (p2_deadline - time.perf_counter()) > 0.18:
                        _, m_win, pat_win = best_peel
                        avail_win = [rem[i] // m_win for i in range(M)]
                        profits_win = [
                            float(items[i]['w'] * items[i]['h'])
                            * (1.0 + (div_bonus if rem[i] % m_win == 0 else 0.0))
                            for i in range(M)
                        ]
                        pat_ps = _pack_single_plate_ps_knapsack(
                            W, H, items, avail_win, profits_win,
                            min(0.055, (p2_deadline - time.perf_counter()) * 0.08),
                            work_dir,
                        )
                        if pat_ps is not None:
                            register_pattern(pat_ps)
                            if pat_ps.used_area >= pat_win.used_area:
                                best_peel = (best_peel[0] * 1.02, m_win, pat_ps)

                    if best_peel is None:
                        break
                    _, m_use, pat_use = best_peel
                    # Maximize actual peelable copies of pat_use
                    max_m_actual = min(
                        rem[i] // pat_use.item_counts[i]
                        for i in range(M) if pat_use.item_counts[i] > 0
                    )
                    if max_m_actual < 1:
                        break
                    for i in range(M):
                        rem[i] -= max_m_actual * pat_use.item_counts[i]
                    traj_pcs.append((pat_use, max_m_actual))

                # Clean up remaining items to complete a 100% exact trajectory
                if any(r > 0 for r in rem):
                    g_rem = 0
                    for r in rem:
                        if r > 0:
                            g_rem = math.gcd(g_rem, r)
                    g_rem = max(1, g_rem)
                    sub_rem = [r // g_rem for r in rem]
                    rem_pcs = _solve_suborder_binpacking(
                        W, H, items, sub_rem,
                        min(0.20, max(0.03, (p2_deadline - time.perf_counter()) * 0.20)),
                        work_dir,
                    )
                    for pat_r, c_r in rem_pcs:
                        traj_pcs.append((pat_r, c_r * g_rem))
                register_trajectory(traj_pcs)

        # -------------------------------------------------------------------
        # Stage 3 (~18% budget): Exact Master CSP-S Pareto Frontier (CP-SAT)
        # -------------------------------------------------------------------
        patterns = list(pool_by_counts.values())
        key_to_idx = {pat.item_counts: idx for idx, pat in enumerate(patterns)}
        P = len(patterns)
        s_max = max(1, int(cost_model.max_stack_sheets))

        # Precompute item -> list of (pattern_idx, coeff) and upper bounds U_p
        item_rows: list[list[tuple[int, int]]] = [[] for _ in range(M)]
        u_bounds: list[int] = []
        for p_idx, pat in enumerate(patterns):
            ub = total_copies
            for i in range(M):
                a = pat.item_counts[i]
                if a > 0:
                    ub = min(ub, demands[i] // a)
                    item_rows[i].append((p_idx, a))
            u_bounds.append(max(0, ub))

        def solve_master_cap(max_k: int | None, step_budget_s: float, hint_traj: list[tuple[tuple[int, ...], int]] | None):
            model = cp_model.CpModel()
            y_vars = []
            z_vars = []
            s_vars = []
            for p_idx in range(P):
                ub = u_bounds[p_idx]
                yv = model.NewIntVar(0, ub, f'y_{p_idx}')
                zv = model.NewBoolVar(f'z_{p_idx}')
                max_s = (ub + s_max - 1) // s_max
                sv = model.NewIntVar(0, max_s, f's_{p_idx}')
                if ub == 0:
                    model.Add(zv == 0)
                    model.Add(sv == 0)
                else:
                    model.Add(yv <= ub * zv)
                    model.Add(yv >= zv)
                    model.Add(s_max * sv >= yv)
                    model.Add(sv >= zv)
                y_vars.append(yv)
                z_vars.append(zv)
                s_vars.append(sv)

            for i in range(M):
                model.Add(sum(coeff * y_vars[p_idx] for p_idx, coeff in item_rows[i]) == demands[i])

            if max_k is not None:
                model.Add(sum(z_vars) <= max_k)

            # Lexicographic objective under pattern cap `max_k`:
            # 1st priority: minimize total sheets; 2nd: minimize distinct patterns; 3rd: minimize stack blade strokes
            total_sheets_expr = sum(y_vars)
            total_pats_expr = sum(z_vars)
            total_strokes_expr = sum(patterns[p_idx].cuts_per_stack * s_vars[p_idx] for p_idx in range(P))
            model.Minimize(10_000_000 * total_sheets_expr + 10_000 * total_pats_expr + total_strokes_expr)

            if hint_traj is not None:
                hint_map = {key_to_idx[k]: c for k, c in hint_traj if k in key_to_idx}
                for p_idx in range(P):
                    c = hint_map.get(p_idx, 0)
                    model.AddHint(y_vars[p_idx], c)
                    model.AddHint(z_vars[p_idx], 1 if c > 0 else 0)
                    model.AddHint(s_vars[p_idx], (c + s_max - 1) // s_max if c > 0 else 0)

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = max(0.05, step_budget_s)
            solver.parameters.num_search_workers = 2
            solver.parameters.random_seed = seed
            status = solver.Solve(model)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                sol_pcs = [
                    (patterns[p_idx], int(solver.Value(y_vars[p_idx])))
                    for p_idx in range(P)
                    if solver.Value(y_vars[p_idx]) > 0
                ]
                return sol_pcs, (status == cp_model.INFEASIBLE)
            return None, (status == cp_model.INFEASIBLE)

        candidate_solutions: list[list[tuple[PlatePattern, int]]] = []
        # Seed with all valid trajectories first so we ALWAYS have feasible plans even under tight time
        for tr in trajectories:
            candidate_solutions.append([(patterns[key_to_idx[k]], c) for k, c in tr])

        # Solve Anchor 0: Min-Paper (lexicographically min patterns & strokes)
        rem_master = max(0.10, deadline - time.perf_counter())
        best_min_paper_hint = min(
            trajectories,
            key=lambda tr: (sum(c for _, c in tr), len(tr)),
            default=None,
        )
        sol0, _ = solve_master_cap(None, min(0.65, rem_master * 0.35), best_min_paper_hint)
        if sol0 is not None:
            candidate_solutions.append(sol0)
            cur_max_k = len(sol0) - 1
        else:
            cur_max_k = (len(best_min_paper_hint) - 1) if best_min_paper_hint else 0

        # Epsilon-constraint sweep down on distinct_patterns K = cur_max_k .. 1
        while cur_max_k >= 1 and (deadline - time.perf_counter()) > 0.08:
            feasible_hints = [tr for tr in trajectories if len(tr) <= cur_max_k]
            hint_k = min(feasible_hints, key=lambda tr: sum(c for _, c in tr), default=None)
            step_t = min(0.32, max(0.07, (deadline - time.perf_counter()) * 0.35))
            sol_k, proven_infeas = solve_master_cap(cur_max_k, step_t, hint_k)
            if sol_k is not None:
                candidate_solutions.append(sol_k)
                cur_max_k = len(sol_k) - 1
            elif proven_infeas:
                break
            else:
                # Jump down toward smallest trajectory length if intermediate timed out
                smaller = [len(tr) for tr in trajectories if len(tr) < cur_max_k]
                cur_max_k = max(smaller) if smaller else (cur_max_k - 1)

        # -------------------------------------------------------------------
        # Stage 4: Deduplicate & Filter Non-Dominated Pareto Frontier + Verify
        # -------------------------------------------------------------------
        evaluated_plans: list[dict[str, Any]] = []
        seen_plan_keys: set[tuple[tuple[str, int], ...]] = set()

        for idx, pcs in enumerate(candidate_solutions):
            canon_key = tuple(sorted((p.signature, int(c)) for p, c in pcs if int(c) > 0))
            if not canon_key or canon_key in seen_plan_keys:
                continue
            seen_plan_keys.add(canon_key)
            tmp_plan_csv = work_dir / f'cand_plan_{idx}.csv'
            write_patterns_cut_tree_csv(pcs, tmp_plan_csv)
            econ = evaluate_cut_tree_economics(
                bins_csv, items_csv, tmp_plan_csv,
                cost_model=cost_model,
                deduplicate_plates=False,
                native_check_binary=None,
            )
            evaluated_plans.append({'pcs': pcs, 'econ': econ})

        # Keep Pareto-non-dominated points in (sheets_used, distinct_patterns, total_strokes)
        evaluated_plans.sort(key=lambda x: (
            x['econ']['sheets_used'],
            x['econ']['distinct_patterns'],
            x['econ']['total_strokes'],
            x['econ']['total_cost_rmb'],
        ))

        pareto_raw: list[dict[str, Any]] = []
        for cand in evaluated_plans:
            e = cand['econ']
            dominated = False
            for prev in pareto_raw:
                ep = prev['econ']
                if (ep['sheets_used'] <= e['sheets_used']
                        and ep['distinct_patterns'] <= e['distinct_patterns']
                        and ep['total_strokes'] <= e['total_strokes']
                        and (ep['sheets_used'], ep['distinct_patterns'], ep['total_strokes'])
                        != (e['sheets_used'], e['distinct_patterns'], e['total_strokes'])):
                    dominated = True
                    break
                if (ep['sheets_used'], ep['distinct_patterns'], ep['total_strokes']) == (
                    e['sheets_used'], e['distinct_patterns'], e['total_strokes']
                ):
                    dominated = True
                    break
            if not dominated:
                pareto_raw.append(cand)

        # Write & native-verify every Pareto frontier plan!
        frontier: list[dict[str, Any]] = []
        for p_i, entry in enumerate(pareto_raw):
            plan_id = f'pareto_{p_i}'
            plan_csv = out_dir / f'{plan_id}_cut_tree.csv'
            write_patterns_cut_tree_csv(entry['pcs'], plan_csv)
            econ_verified = evaluate_cut_tree_economics(
                bins_csv, items_csv, plan_csv,
                cost_model=cost_model,
                deduplicate_plates=False,
                native_check_binary=native_check_binary,
            )
            frontier.append({
                'plan_id': plan_id,
                'cut_tree_csv': str(plan_csv),
                **econ_verified,
            })

        wall_s = time.perf_counter() - t0

        # Tag named shop-floor choices
        plan_min_paper = min(frontier, key=lambda x: (x['sheets_used'], x['total_cost_rmb']))
        plan_min_cost = min(frontier, key=lambda x: (x['total_cost_rmb'], x['sheets_used']))
        plan_min_setup = min(frontier, key=lambda x: (x['distinct_patterns'], x['total_time_min'], x['sheets_used']))

        summary = {
            'sheet_width': W,
            'sheet_height': H,
            'sku_count': M,
            'total_pieces': total_copies,
            'max_sku_copies': max_demand,
            'candidate_patterns_generated': P,
            'complete_trajectories_found': len(trajectories),
            'pareto_count': len(frontier),
            'wall_s': round(wall_s, 6),
            'cost_model': asdict(cost_model),
            'recommended_plans': {
                'min_paper': plan_min_paper['plan_id'],
                'min_total_cost': plan_min_cost['plan_id'],
                'min_setup_fastest': plan_min_setup['plan_id'],
            },
            'min_paper': plan_min_paper,
            'min_total_cost': plan_min_cost,
            'min_setup_fastest': plan_min_setup,
            'pareto_frontier': frontier,
            'anchor_info': hp_info,
        }
        (out_dir / 'pareto_summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        return summary
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
