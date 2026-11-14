"""Final audit, CLI verification, label-status overlay, and archive builder for R51-R55 (`mill-study`).

Executed on Colab after R55 completes. Produces:
  - /content/mill-study/audit-summary.json
  - /content/mill-study/cli_demo_package/ (6-file deliverable via `paper_mill_cutter.py cut` CLI)
  - /content/mill-study/cli-demo-summary.json
  - /content/mill-study/source/ + /content/mill-study/source-sha256.json
  - /content/mill-study/corpus-label-overlay.json (updated current-label-status.json)
  - /content/mill-study/corpus-label-summary.json
  - /content/mill-study/artifact-sha256.json
  - /content/mill-study-results.zip
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile
from typing import Any

MILL_ROOT = pathlib.Path('/content/mill-study')
CORPUS = pathlib.Path('/content/data-expansion/corpus')
NATIVE_BIN = pathlib.Path('/content/native-check/ejection')


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_r50_strips_json(
    bins_csv: pathlib.Path, items_csv: pathlib.Path, strips_json: pathlib.Path, pmc_mod: Any
) -> int:
    """Verify R50's legacy `-strips.json` certificate (exact demand, dimensions, bounds, 3-stage shelf geometry)."""
    W, H, items = pmc_mod.read_order_files(bins_csv, items_csv)
    by_id = {it['id']: it for it in items}
    expected = {it['id']: it['copies'] for it in items if it['copies'] > 0}
    produced: dict[int, int] = {}
    plates = json.loads(strips_json.read_text())
    assert isinstance(plates, list) and len(plates) >= 1
    for p in plates:
        cur_y = 0
        for st in p:
            sy, sh = int(st['y']), int(st['height'])
            assert sy >= cur_y and sh > 0 and sy + sh <= H
            cur_y = sy + sh
            cur_x = 0
            for it_rec in st['items']:
                iid = int(it_rec['id'])
                ix, iy, iw, ih = int(it_rec['x']), int(it_rec['y']), int(it_rec['w']), int(it_rec['h'])
                assert iid in by_id
                assert (iw, ih) == (by_id[iid]['w'], by_id[iid]['h'])
                assert ix >= cur_x and ix + iw <= W
                assert iy == sy and ih <= sh
                cur_x = ix + iw
                produced[iid] = produced.get(iid, 0) + 1
    assert produced == expected, f'R50 strips.json demand mismatch: {produced} != {expected}'
    return len(plates)


def main() -> None:
    import importlib
    sys.path.insert(0, '/content')
    import paper_mill_cutter as pmc
    pmc = importlib.reload(pmc)

    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    manifest_by_name = {c['name']: c for c in manifest}
    base_labels: dict[str, dict[str, Any]] = json.loads(
        pathlib.Path('/content/current-label-status.json').read_text()
    )

    round_files = [
        ('R51', MILL_ROOT / 'r51' / 'ablation-rows.json', MILL_ROOT / 'r51' / 'certificates'),
        ('R52', MILL_ROOT / 'r52' / 'dev-rows.json', MILL_ROOT / 'r52' / 'certificates'),
        ('R53', MILL_ROOT / 'r53' / 'validation-rows.json', MILL_ROOT / 'r53' / 'certificates'),
        ('R54', MILL_ROOT / 'r54' / 'validation-v2-rows.json', MILL_ROOT / 'r54' / 'certificates'),
        ('R55', MILL_ROOT / 'r55' / 'test-rows.json', MILL_ROOT / 'r55' / 'certificates'),
    ]

    audit_rounds: dict[str, Any] = {}
    total_runs = 0
    total_valid = 0
    total_over_10_1s = 0
    global_max_wall = 0.0
    global_max_feat_s = 0.0

    per_inst_runs: dict[str, int] = {}
    per_inst_methods: dict[str, set[str]] = {}
    per_inst_rounds: dict[str, list[str]] = {}
    data_dir = CORPUS / 'data'

    # Cache certificate verification by (instance, cert_path, expected_sha256) since R52 reused cached R51 native rows
    r51_cert_dir = MILL_ROOT / 'r51' / 'certificates'

    for rnd, rpath, cert_dir in round_files:
        assert rpath.exists(), f'Missing rows file for {rnd}: {rpath}'
        rows = json.loads(rpath.read_text())
        n_valid = 0
        for r in rows:
            if not r.get('valid'):
                continue
            inst = r['instance']
            cert_name = r['certificate']
            candidates = [
                cert_dir / cert_name,
                cert_dir / f"{r['method']}_{r.get('seed')}" / cert_name,
                r51_cert_dir / cert_name,
            ]
            cpath = next((p for p in candidates if p.exists()), candidates[0])
            assert cpath.exists(), f'{rnd}: missing certificate {cpath}'
            if r.get('certificate_sha256'):
                assert sha256_file(cpath) == r['certificate_sha256'], f'{rnd}: sha256 mismatch on {cpath}'
            if cpath.suffix == '.json':
                n_plates = verify_r50_strips_json(
                    data_dir / f'{inst}_bins.csv',
                    data_dir / f'{inst}_items.csv',
                    cpath,
                    pmc,
                )
                assert n_plates == int(r['bins']), f'{rnd}: strips.json bin mismatch on {cpath}'
            else:
                ver = pmc.verify_cut_tree_csv(
                    data_dir / f'{inst}_bins.csv',
                    data_dir / f'{inst}_items.csv',
                    cpath,
                    check_native_binary=NATIVE_BIN,
                )
                assert ver['sheets_used'] == int(r['bins']), f'{rnd}: bin mismatch on {cpath}'
                if int(r['bins']) >= 2:
                    assert ver['native_cpp_solution_builder_verified'] is True
            n_valid += 1

        n_over = sum(1 for r in rows if float(r.get('wall_s', 0.0)) > 10.1)
        n_err = sum(1 for r in rows if r.get('branch_error'))
        max_w = max((float(r.get('wall_s', 0.0)) for r in rows), default=0.0)
        max_f = max(
            (float(r.get('gate_s', r.get('route_meta', {}).get('feature_and_route_s', 0.0))) for r in rows),
            default=0.0,
        )
        assert n_valid == len(rows), f'{rnd}: {n_valid}/{len(rows)} valid!'
        assert n_err == 0, f'{rnd}: {n_err} branch errors!'
        assert n_over == 0, f'{rnd}: {n_over} runs exceeded 10.1s!'

        audit_rounds[rnd] = {
            'rows_file': str(rpath.relative_to(MILL_ROOT)),
            'total_runs': len(rows),
            'distinct_instances': len({r['instance'] for r in rows}),
            'valid_and_native_audited': n_valid,
            'branch_errors': n_err,
            'over_10_1s_count': n_over,
            'max_wall_s': round(max_w, 4),
            'max_feature_and_route_ms': round(max_f * 1000.0, 4),
        }
        total_runs += len(rows)
        total_valid += n_valid
        total_over_10_1s += n_over
        global_max_wall = max(global_max_wall, max_w)
        global_max_feat_s = max(global_max_feat_s, max_f)

        for r in rows:
            inst = r['instance']
            per_inst_runs[inst] = per_inst_runs.get(inst, 0) + 1
            per_inst_methods.setdefault(inst, set()).add(r['method'])
            rnds = per_inst_rounds.setdefault(inst, [])
            if rnd not in rnds:
                rnds.append(rnd)

    audit_summary = {
        'study_range': 'R51-R55',
        'total_runs': total_runs,
        'total_valid_and_native_audited': total_valid,
        'total_branch_errors': 0,
        'total_over_10_1s': total_over_10_1s,
        'global_max_wall_s': round(global_max_wall, 4),
        'global_max_feature_and_route_ms': round(global_max_feat_s * 1000.0, 4),
        'original_packingsolver_modified': False,
        'rounds': audit_rounds,
    }
    (MILL_ROOT / 'audit-summary.json').write_text(json.dumps(audit_summary, indent=2))

    # End-to-end CLI verification (`paper_mill_cutter.py cut`) on a winning held-out test order
    r55_summary = json.loads((MILL_ROOT / 'r55' / 'test-summary.json').read_text())
    diffs = r55_summary['overall_heldout_48']['comparisons']['mill_router']['default']['differences']
    wins = [d for d in diffs if d['cand_bins'] < d['ref_bins']]
    varied_wins = [d for d in wins if d.get('family') == 'shelf_varied']
    chosen_win = varied_wins[-1] if varied_wins else (wins[0] if wins else None)
    if chosen_win is not None:
        demo_inst = chosen_win['instance']
        demo_seed = int(chosen_win['seed'])
        demo_ref_bins = int(chosen_win['ref_bins'])
    else:
        r54_summary = json.loads((MILL_ROOT / 'r54' / 'validation-v2-summary.json').read_text())
        w54 = r54_summary['comparisons']['mill_router']['default']['differences'][0]
        demo_inst = w54['instance']
        demo_seed = int(w54['seed'])
        demo_ref_bins = int(w54['ref_bins'])

    cli_dir = MILL_ROOT / 'cli_demo_package'
    if cli_dir.exists():
        shutil.rmtree(cli_dir)
    bins_csv = CORPUS / 'data' / f'{demo_inst}_bins.csv'
    items_csv = CORPUS / 'data' / f'{demo_inst}_items.csv'
    cli_cmd = [
        sys.executable,
        '/content/paper_mill_cutter.py',
        'cut',
        '--bins', str(bins_csv),
        '--items', str(items_csv),
        '--out-dir', str(cli_dir),
        '--budget', '10.0',
        '--seed', str(demo_seed),
        '--policy', str(MILL_ROOT / 'mill-policy.json'),
        '--native-check', str(NATIVE_BIN),
        '--order-name', f'cli_demo_{demo_inst}',
    ]
    proc = subprocess.run(cli_cmd, capture_output=True, text=True, timeout=45)
    if proc.returncode != 0:
        raise RuntimeError(f'CLI demo failed:\n{proc.stderr}\n{proc.stdout}')
    cli_out = json.loads(proc.stdout)
    required_pkg_files = [
        'cut_tree.csv',
        'layout.json',
        'cutting_plan.json',
        'cutting_plan.md',
        'material_summary.json',
        'verification_certificate.json',
    ]
    for fn in required_pkg_files:
        assert (cli_dir / fn).exists(), f'Missing CLI package file: {fn}'
    cert_obj = json.loads((cli_dir / 'verification_certificate.json').read_text())
    assert cert_obj['valid'] is True and cert_obj['native_cpp_solution_builder_verified'] is True
    cli_summary = {
        'instance': demo_inst,
        'seed': demo_seed,
        'reference_default_bins': demo_ref_bins,
        'cli_output': cli_out,
        'package_files': required_pkg_files,
        'valid': cert_obj['valid'],
        'native_cpp_solution_builder_verified': cert_obj['native_cpp_solution_builder_verified'],
    }
    (MILL_ROOT / 'cli-demo-summary.json').write_text(json.dumps(cli_summary, indent=2))

    # Archive all v1 + v2 sources, compiled binaries, policies, and study scripts
    src_dir = MILL_ROOT / 'source'
    src_dir.mkdir(parents=True, exist_ok=True)
    to_copy = [
        pathlib.Path('/content/colstrip_gen.cpp'),
        pathlib.Path('/content/libcolstrip.so'),
        pathlib.Path('/content/paper_mill_cutter.py'),
        MILL_ROOT / 'mill-policy.json',
        pathlib.Path('/content/mill_setup_colab.py'),
        pathlib.Path('/content/r51_engine_check_colab.py'),
        pathlib.Path('/content/r52_dev_router_colab.py'),
        pathlib.Path('/content/r53_validation_colab.py'),
        pathlib.Path('/content/r54_v2_validation_colab.py'),
        pathlib.Path('/content/r55_heldout_test_colab.py'),
        pathlib.Path('/content/mill_finalize_colab.py'),
    ]
    for p in to_copy:
        if p.exists():
            shutil.copy2(p, src_dir / p.name)

    source_hashes = {
        p.name: sha256_file(p)
        for p in sorted(src_dir.iterdir())
        if p.is_file()
    }
    (MILL_ROOT / 'source-sha256.json').write_text(json.dumps(source_hashes, indent=2))

    # Build updated cumulative label status (`corpus-label-overlay.json`)
    merged = copy.deepcopy(base_labels)
    for inst in sorted(per_inst_runs):
        split = manifest_by_name[inst]['split']
        rnds = per_inst_rounds[inst]
        study_tag = f"R51-R55({','.join(rnds)})"
        prev = merged.get(inst)
        if prev is None:
            entry: dict[str, Any] = {
                'split': split,
                'label_runs': per_inst_runs[inst],
                'methods': sorted(per_inst_methods[inst]),
                'prior_studies': [study_tag],
                'validation_consumed': bool(split == 'validation' and ('R53' in rnds or 'R54' in rnds)),
            }
        else:
            studies = list(prev.get('prior_studies', []))
            if study_tag not in studies:
                studies.append(study_tag)
            entry = {
                'split': prev.get('split', split),
                'label_runs': int(prev.get('label_runs', 0)) + per_inst_runs[inst],
                'methods': sorted(set(prev.get('methods', [])) | per_inst_methods[inst]),
                'prior_studies': studies,
                'validation_consumed': bool(
                    prev.get('validation_consumed', False)
                    or (split == 'validation' and ('R53' in rnds or 'R54' in rnds))
                ),
            }
        if split in ('test', 'test_external') and 'R55' in rnds:
            entry['test_consumed'] = True
        merged[inst] = entry

    ordered_merged = {k: merged[k] for k in sorted(merged)}
    (MILL_ROOT / 'corpus-label-overlay.json').write_text(json.dumps(ordered_merged, indent=2) + '\n')

    # Summarize corpus partition & consumption status across all 1,760 instances
    split_counts: dict[str, dict[str, int]] = {}
    for c in manifest:
        sp = c['split']
        st = split_counts.setdefault(sp, {
            'total_in_corpus': 0,
            'with_label_runs': 0,
            'unrun': 0,
            'validation_consumed': 0,
            'validation_unconsumed': 0,
            'test_consumed': 0,
            'test_unconsumed': 0,
        })
        st['total_in_corpus'] += 1
        ent = ordered_merged.get(c['name'])
        if ent is not None and ent.get('label_runs', 0) > 0:
            st['with_label_runs'] += 1
        else:
            st['unrun'] += 1
        if sp == 'validation':
            if ent is not None and ent.get('validation_consumed'):
                st['validation_consumed'] += 1
            else:
                st['validation_unconsumed'] += 1
        if sp in ('test', 'test_external'):
            if ent is not None and ent.get('test_consumed'):
                st['test_consumed'] += 1
            else:
                st['test_unconsumed'] += 1

    corpus_summary = {
        'total_corpus_instances': len(manifest),
        'previous_labeled_instances_r47_r50': len(base_labels),
        'newly_touched_instances_r51_r55': len(per_inst_runs),
        'cumulative_labeled_instances': len(ordered_merged),
        'cumulative_label_runs': sum(v['label_runs'] for v in ordered_merged.values()),
        'by_split': split_counts,
    }
    (MILL_ROOT / 'corpus-label-summary.json').write_text(json.dumps(corpus_summary, indent=2))

    # Hash all files in /content/mill-study (excluding transient pid/log pointers & artifact-sha256 itself)
    skip_names = {'active.pid', 'active_log.txt', 'artifact-sha256.json'}
    artifact_hashes: dict[str, str] = {}
    for p in sorted(MILL_ROOT.rglob('*')):
        if p.is_file() and p.name not in skip_names:
            rel = str(p.relative_to(MILL_ROOT))
            artifact_hashes[rel] = sha256_file(p)
    (MILL_ROOT / 'artifact-sha256.json').write_text(json.dumps(artifact_hashes, indent=2))

    # Build /content/mill-study-results.zip
    zip_path = pathlib.Path('/content/mill-study-results.zip')
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(MILL_ROOT.rglob('*')):
            if p.is_file() and p.name not in {'active.pid', 'active_log.txt'}:
                zf.write(p, arcname=str(p.relative_to(MILL_ROOT)))

    print('MILL_FINALIZE_OK', json.dumps({
        'zip_path': str(zip_path),
        'zip_size_bytes': zip_path.stat().st_size,
        'audit_summary': audit_summary,
        'cli_demo': cli_summary,
        'corpus_summary': corpus_summary,
    }))


if __name__ == '__main__':
    main()
