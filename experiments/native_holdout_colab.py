"""Frozen schedule evaluation on previously unused sizes/replicate; Colab only."""
import sys, pathlib, json, subprocess, random
sys.path.insert(0, '/content')
import native_schedule_colab as ns

OUT = ns.ROOT / 'holdout'
OUT.mkdir(exist_ok=True)
NAMES = [f'Class_{c:02d}.2bp_{n}_1' for c in range(1, 7) for n in (40, 80)]

if len(sys.argv) > 1 and sys.argv[1] == 'worker':
    ns.OUT = OUT
    print(json.dumps(ns.run(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
else:
    protocol = json.loads((ns.ROOT / 'schedule/protocol.json').read_text())
    protocol.update(instances=NAMES, repeats=2, scope='Frozen schedule, new Berkey 40/80-item replicate 1; no tuning on these results')
    (OUT / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    jobs = [(n, m, r) for n in NAMES for m in ('default', 'warm512') for r in (1, 2)]
    random.Random(1733).shuffle(jobs)
    rows = []
    for n, m, r in jobs:
        p = subprocess.run([sys.executable, __file__, 'worker', n, m, str(r)], capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(p.stderr[-3000:])
        row = json.loads(p.stdout.strip().splitlines()[-1])
        rows.append(row)
        (OUT / 'rows.json').write_text(json.dumps(rows, indent=2))
        print('HOLDOUT', len(rows), len(jobs), n, m, r, row['bins'], flush=True)
    pairs = []
    for row in rows:
        if row['method'] != 'warm512':
            continue
        base = next(b for b in rows if b['instance'] == row['instance'] and b['rep'] == row['rep'] and b['method'] == 'default')
        pairs.append(dict(instance=row['instance'], rep=row['rep'], default=base['bins'], candidate=row['bins']))
    summary = dict(pairs=len(pairs), wins=sum(p['candidate'] < p['default'] for p in pairs), losses=sum(p['candidate'] > p['default'] for p in pairs), ties=sum(p['candidate'] == p['default'] for p in pairs), total_default=sum(p['default'] for p in pairs), total_candidate=sum(p['candidate'] for p in pairs), differences=[p for p in pairs if p['candidate'] != p['default']], max_wall_s=max(r['wall_s'] for r in rows))
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('HOLDOUT_COMPLETE', json.dumps(summary), flush=True)
