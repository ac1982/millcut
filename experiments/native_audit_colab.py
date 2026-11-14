"""Stronger development comparator and evidence archive; execute in Colab."""
import sys, pathlib, json, subprocess, zipfile, hashlib, shutil
sys.path.insert(0, '/content')
import native_schedule_colab as ns
ROOT = ns.ROOT
OUT = ROOT / 'strong-comparator'
OUT.mkdir(exist_ok=True)
if len(sys.argv) > 1 and sys.argv[1] == 'worker':
    ns.OUT = OUT
    print(json.dumps(ns.run('Class_02.2bp_100_3', 'guide1', int(sys.argv[2]))))
else:
    rows = []
    for rep in (1, 2, 3):
        p = subprocess.run([sys.executable, __file__, 'worker', str(rep)], capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(p.stderr[-3000:])
        row = json.loads(p.stdout.strip().splitlines()[-1]); rows.append(row)
        print('GUIDE1', rep, row['bins'], row['wall_s'], flush=True)
    (OUT / 'rows.json').write_text(json.dumps(rows, indent=2))
    (OUT / 'protocol.json').write_text(json.dumps(dict(scope='Development winner only, original full optimizer restricted to guide1', budget_s=10, repeats=3), indent=2))
    audit = {}
    for name in ('schedule', 'holdout'):
        data = json.loads((ROOT / name / 'rows.json').read_text())
        audit[name] = dict(runs=len(data), max_wall_s=max(r['wall_s'] for r in data), over_10_1=[dict(instance=r['instance'], method=r['method'], wall_s=r['wall_s']) for r in data if r['wall_s'] > 10.1], all_valid=all(r['valid'] for r in data))
    (ROOT / 'timing-audit.json').write_text(json.dumps(audit, indent=2))
    print('TIMING_AUDIT', json.dumps(audit), flush=True)
    source = ROOT / 'source-snapshot'
    source.mkdir(exist_ok=True)
    shutil.copytree(ROOT / 'build-src', source / 'build-src', dirs_exist_ok=True)
    for p in pathlib.Path('/content').glob('native_*colab.py'):
        shutil.copy(p, source / p.name)
    if pathlib.Path('/content/native_patch_v2_colab.py').exists():
        shutil.copy('/content/native_patch_v2_colab.py', source / 'native_patch_colab.py')
    binaries = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ('build/native_baseline', 'build/native_patched', 'v1/native_patched') if (ROOT / name).exists()}
    (source / 'binary-sha256.json').write_text(json.dumps(binaries, indent=2))
    for name in ('tree_search.hpp', 'tree_search.cpp'):
        diff = subprocess.run(['diff', '-u', str(ROOT / 'pristine/src/rectangleguillotine' / name), str(ROOT / 'patched/src/rectangleguillotine' / name)], capture_output=True, text=True)
        (source / (name + '.patch')).write_text(diff.stdout)
    paths = ['environment.json', 'v2-parity.json', 'timing-audit.json', 'v1/results', 'v1/scaling-diagnostic.json', 'fixed-screen', 'schedule', 'holdout', 'strong-comparator', 'source-snapshot']
    hashes = {}
    with zipfile.ZipFile('/content/native-results.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for name in paths:
            path = ROOT / name
            files = sorted(path.rglob('*')) if path.is_dir() else [path]
            for p in files:
                if p.is_file():
                    rel = str(p.relative_to(ROOT)); z.write(p, rel); hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        z.writestr('artifact-sha256.json', json.dumps(hashes, indent=2))
    with zipfile.ZipFile('/content/native-binaries.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for name in binaries:
            p = ROOT / name
            z.write(p, name)
    print('ARCHIVED', len(hashes), flush=True)
