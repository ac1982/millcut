"""Build a grouped, deduplicated routing corpus. Execution: Colab only.

Public geometries are reinterpreted under the common project cutting rules;
published best-known values are deliberately not imported as target labels.
"""
import ast, collections, csv, hashlib, io, json, math, pathlib, random, shutil, sys, zipfile

ROOT = pathlib.Path('/content/data-expansion')
OUT = ROOT / 'corpus'
PUBLIC = ROOT / 'public/rectangle'
HISTORY = ROOT / 'history'
for d in ['data', 'witnesses', 'source', 'labels', 'certificates']:
    (OUT / d).mkdir(parents=True, exist_ok=True)
SEED = 83001
FROZEN = json.loads(pathlib.Path('/content/frozen-rule.json').read_text())
source = HISTORY / 'experiments/routing_core_colab.py'
assert hashlib.sha256(source.read_bytes()).hexdigest() == FROZEN['source_sha256'][source.name]
module = ast.parse(source.read_text())
namespace = {}
exec(compile(ast.Module(body=[n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in ('features', 'choose')], type_ignores=[]), str(source), 'exec'), namespace)
features, choose = namespace['features'], namespace['choose']
shutil.copy(source, OUT / 'source' / source.name)
shutil.copy('/content/frozen-rule.json', OUT / 'frozen-rule.json')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def signatures(items, W, H):
    counts = collections.Counter()
    for it in items:
        counts[int(it['WIDTH']), int(it['HEIGHT'])] += int(it.get('COPIES', 1))
    normalized = sorted((w, h, c) for (w, h), c in counts.items())
    divisor = math.gcd(W, H)
    for w, h, _ in normalized:
        divisor = math.gcd(divisor, math.gcd(w, h))
    return digest([W, H, normalized]), digest([W // divisor, H // divisor, [(w // divisor, h // divisor, c) for w, h, c in normalized]])


def read_case(prefix):
    with pathlib.Path(str(prefix) + '_items.csv').open() as f:
        items = list(csv.DictReader(f))
    with pathlib.Path(str(prefix) + '_bins.csv').open() as f:
        bins = list(csv.DictReader(f))
    assert len(bins) == 1, 'multiple bin types need another benchmark protocol'
    return items, int(bins[0]['WIDTH']), int(bins[0]['HEIGHT'])


history_hashes = set()
for p in HISTORY.glob('results/**/*_items.csv'):
    try:
        old_items, w, h = read_case(str(p).removesuffix('_items.csv'))
        history_hashes.add(signatures(old_items, w, h)[1])
    except (AssertionError, FileNotFoundError):
        continue


def split_group(group):
    bucket = int(digest([SEED, group])[:12], 16) % 10
    return 'development' if bucket < 6 else 'validation' if bucket < 8 else 'test'


def partition(total, n, rng):
    weights = [rng.uniform(.3, 1.7) for _ in range(n)]
    out = [int(total * w / sum(weights)) for w in weights]
    out[-1] += total - sum(out)
    assert min(out) > 0
    return out


def shelf_case(rng, W=1000, H=1000, boards=3, nr=3, nc=5, trim=.1):
    raw, witness = [], []
    for plate in range(boards):
        y = 0
        for rh in partition(H, nr, rng):
            x = 0
            for col, w in enumerate(partition(W, nc, rng)):
                h = rh if col == 0 else max(1, rh - rng.randint(0, max(1, int(rh * trim))))
                witness.append(dict(plate=plate, id=len(raw), x=x, y=y, w=w, h=h, strip_height=rh))
                raw.append(dict(WIDTH=w, HEIGHT=h, COPIES=1))
                x += w
            y += rh
    perm = list(range(len(raw)))
    rng.shuffle(perm)
    remap = {old: new for new, old in enumerate(perm)}
    for it in witness:
        it['id'] = remap[it['id']]
    return [raw[i] for i in perm], witness


def validate_witness(items, W, H, witness):
    seen, by_plate, rows = collections.Counter(), collections.defaultdict(list), collections.defaultdict(list)
    for r in witness:
        i = r['id']
        assert (r['w'], r['h']) == (items[i]['WIDTH'], items[i]['HEIGHT'])
        assert 0 <= r['x'] < r['x'] + r['w'] <= W
        assert 0 <= r['y'] < r['y'] + r['h'] <= H
        assert r['h'] <= r['strip_height']
        seen[i] += 1
        by_plate[r['plate']].append(r)
        rows[r['plate'], r['y'], r['strip_height']].append(r)
    assert seen == collections.Counter({i: int(it.get('COPIES', 1)) for i, it in enumerate(items)})
    for rr in by_plate.values():
        for j, a in enumerate(rr):
            for b in rr[j + 1:]:
                assert a['x'] + a['w'] <= b['x'] or b['x'] + b['w'] <= a['x'] or a['y'] + a['h'] <= b['y'] or b['y'] + b['h'] <= a['y']
    # Constructive H-V-H cut certificate, not merely an overlap test.
    plate_rows = collections.defaultdict(list)
    for (plate, y, rh), rr in rows.items():
        x = 0
        for it in sorted(rr, key=lambda it: it['x']):
            assert it['x'] == x
            x += it['w']
        assert x == W
        plate_rows[plate].append((y, rh))
    for rr in plate_rows.values():
        y = 0
        for yy, rh in sorted(rr):
            assert yy == y
            y += rh
        assert y == H
    return len(by_plate)


cases, duplicates, canonical = [], [], {}
def add(items, W, H, family, group, provenance, witness=None, split=None, extra=None):
    items = [dict(ID=i, WIDTH=int(it['WIDTH']), HEIGHT=int(it['HEIGHT']), COPIES=int(it.get('COPIES', 1))) for i, it in enumerate(items)]
    assert items and W > 0 and H > 0
    assert all(0 < it['WIDTH'] <= W and 0 < it['HEIGHT'] <= H and it['COPIES'] > 0 for it in items)
    exact, scale = signatures(items, W, H)
    if scale in canonical:
        duplicates.append(dict(provenance=provenance, duplicate_of=canonical[scale]['name'], exact_geometry=exact == canonical[scale]['canonical_sha256']))
        return None
    if scale in history_hashes or family == 'public_berkey1987':
        split = 'historical_development'
    split = split or split_group(group)
    name = 'data_' + exact[:16]
    for suffix, fields, records in [('items', ['ID', 'WIDTH', 'HEIGHT', 'COPIES'], items), ('bins', ['ID', 'WIDTH', 'HEIGHT'], [dict(ID=0, WIDTH=W, HEIGHT=H)])]:
        with (OUT / 'data' / f'{name}_{suffix}.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    f = features(items, W, H)
    upper = validate_witness(items, W, H, witness) if witness else None
    known = upper if upper == f['area_lower_bound'] else None
    if witness:
        (OUT / 'witnesses' / f'{name}.json').write_text(json.dumps(witness))
    row = dict(name=name, family=family, group_id=group, split=split, source=provenance, width=W, height=H,
               features=f, frozen_branch=choose(f), canonical_sha256=exact, scale_invariant_sha256=scale,
               input_sha256=hashlib.sha256((OUT / 'data' / f'{name}_items.csv').read_bytes() + b'\0' + (OUT / 'data' / f'{name}_bins.csv').read_bytes()).hexdigest(),
               known_optimum=known, constructive_upper_bound=upper, solver_label_status='not_run', extra=extra)
    cases.append(row)
    canonical[scale] = row
    return row


# The 500 instance rows in the pinned 3nho index. The oriented index additionally
# contains 50 aggregate rows with path-like names, which are not input instances.
# Published scores are not treated as proven optimal labels.
with (PUBLIC / 'data_bin_packing_3nho.csv').open() as f:
    paths = sorted({r['Path'] for r in csv.DictReader(f) if r['Path'] and '/' in r['Path']})
for path in paths:
    items, W, H = read_case(PUBLIC / path)
    fam = path.split('/')[0]
    add(items, W, H, 'public_' + fam, 'public/' + path, dict(kind='public_pinned_repository', path=path), split='test_external' if fam == 'martello1998' else None)

# 200 original-family parents + 200 matched scrambled challenges. Paired
# descendants share a group and can never cross train/validation/test boundaries.
for j in range(200):
    rng = random.Random(SEED * 10000 + j)
    nr, nc = (3, 5) if j < 100 else (5, 6)
    items, witness = shelf_case(rng, nr=nr, nc=nc)
    group = f'shelf_pair/{j}'
    add(items, 1000, 1000, 'shelf_same_family', group, dict(kind='synthetic', seed=SEED * 10000 + j), witness)
    heights = [it['HEIGHT'] for it in items]
    rng.shuffle(heights)
    factor = 2880000 / sum(it['WIDTH'] * h for it, h in zip(items, heights))
    scrambled = [dict(it, HEIGHT=max(1, round(h * factor))) for it, h in zip(items, heights)]
    add(scrambled, 1000, 1000, 'height_scrambled', group, dict(kind='derived_synthetic', parent_group=group))

# Wider parameter coverage, including outside the gate. Do not regenerate an
# instance until it hits the gate: the observed gate coverage is an outcome.
for j in range(240):
    rng = random.Random((SEED + 1) * 10000 + j)
    W, H = rng.choice([800, 1000, 1600]), rng.choice([600, 1000, 1600])
    B, nr, nc, trim = rng.choice([2, 3, 4, 6]), rng.choice([2, 3, 5]), rng.choice([4, 6, 8]), rng.choice([.04, .1, .2, .3])
    items, witness = shelf_case(rng, W, H, B, nr, nc, trim)
    add(items, W, H, 'shelf_varied', f'varied/{j}', dict(kind='synthetic', seed=(SEED + 1) * 10000 + j), witness,
        extra=dict(boards=B, rows=nr, columns=nc, trim=trim))


def random_area_case(seed, N=90, W=1000, H=1000, fill=.96, bound=3, distinct=None):
    rng = random.Random(seed)
    distinct = distinct if distinct is not None else N
    widths = rng.sample(range(max(1, W // 12), W // 2), distinct)
    widths = (widths * ((N + distinct - 1) // distinct))[:N]
    rng.shuffle(widths)
    weights = [rng.uniform(.7, 1.3) for _ in range(N)]
    scale = fill * bound * W * H / sum(w * h for w, h in zip(widths, weights))
    heights = [max(1, round(h * scale)) for h in weights]
    # Correct rounding with a small-width item to make fill endpoints precise.
    i = min(range(N), key=lambda i: widths[i])
    residual = round(fill * bound * W * H) - sum(w * h for w, h in zip(widths, heights))
    heights[i] += round(residual / widths[i])
    assert min(heights) > 0 and max(heights) <= H
    return [dict(WIDTH=w, HEIGHT=h, COPIES=1) for w, h in zip(widths, heights)]

for j in range(200):
    seed = (SEED + 2) * 10000 + j
    items = random_area_case(seed, N=[45, 60, 90, 120][j % 4], fill=[.92, .95, .97, .99][(j // 4) % 4])
    add(items, 1000, 1000, 'independent_random', f'random/{j}', dict(kind='synthetic_without_witness', seed=seed))

# 8 thresholds, 20 independent parents each, 2 sides. All non-target gate
# conditions remain inside their admissible range, including at N=29.
thresholds = [
    ('fill_min', {'fill': .939}, {'fill': .941}),
    ('fill_max', {'fill': .979}, {'fill': .981}),
    ('diversity', {'N': 100, 'distinct': 74}, {'N': 100, 'distinct': 76}),
    ('items_min', {'N': 29}, {'N': 30}),
    ('items_max', {'N': 120}, {'N': 121}),
    ('bound_min', {'bound': 1}, {'bound': 2}),
    ('bound_max', {'bound': 6}, {'bound': 7}),
    ('width_cap', {'W': 4096}, {'W': 4097}),
]
for t, left, right in thresholds:
    for j in range(20):
        seed = (SEED + 3) * 10000 + thresholds.index((t, left, right)) * 100 + j
        for side, change in [('left', left), ('right', right)]:
            kw = dict(N=90, W=1000, H=1000, fill=.96, bound=3)
            kw.update(change)
            items = random_area_case(seed, **kw)
            add(items, kw['W'], kw['H'], 'gate_boundary', f'boundary/{t}/{j}', dict(kind='paired_boundary', seed=seed), extra=dict(threshold=t, side=side, requested=kw))

for j in range(100):
    seed = (SEED + 4) * 10000 + j
    rng = random.Random(seed)
    items = [dict(WIDTH=rng.randint(30, 500), HEIGHT=rng.randint(30, 500), COPIES=rng.randint(2, 5)) for _ in range([10, 20, 30, 40][j % 4])]
    add(items, 1000, 1000, 'repeated_demand', f'copies/{j}', dict(kind='synthetic_without_witness', seed=seed))

# Audits operate on canonical geometry, ignoring IDs, ordering and uniform units.
assert len(cases) == len({c['canonical_sha256'] for c in cases}) == len({c['scale_invariant_sha256'] for c in cases})
groups = collections.defaultdict(set)
for c in cases:
    groups[c['group_id']].add(c['split'])
assert all(len(v) == 1 for v in groups.values())
assert not any(c['scale_invariant_sha256'] in history_hashes for c in cases if c['split'] != 'historical_development')
boundary_pairs = collections.defaultdict(list)
for c in cases:
    if c['family'] == 'gate_boundary':
        boundary_pairs[c['group_id']].append(c)
assert len(boundary_pairs) == 160
assert all(len(rr) == 2 and {r['frozen_branch'] for r in rr} == {'default', 'strips'} for rr in boundary_pairs.values())

by_family = {}
for family in sorted({c['family'] for c in cases}):
    rr = [c for c in cases if c['family'] == family]
    by_family[family] = dict(instances=len(rr), gate_hits=sum(c['frozen_branch'] == 'strips' for c in rr), splits=dict(collections.Counter(c['split'] for c in rr)))
summary = dict(total_instances=len(cases), source_instances=len(paths) + 1260, duplicate_inputs_removed=len(duplicates), families=by_family,
               splits=dict(collections.Counter(c['split'] for c in cases)), split_groups=len(groups), gate_hits=sum(c['frozen_branch'] == 'strips' for c in cases),
               witness_certificates=sum(c['constructive_upper_bound'] is not None for c in cases), proven_optima=sum(c['known_optimum'] is not None for c in cases),
               validation=dict(input_dimensions_and_demand=True, exact_and_uniform_scaling_dedup=True, parent_group_separation=True, historical_exclusion=True, boundary_pairs_straddle_frozen_rule=160))
protocol = dict(seed=SEED, solver_commit='28ac801df472e68736410411d70bfdb15f06e281',
                rules='3-stage NonExact guillotine; horizontal first cut; no rotation; zero kerf; unlimited identical sheets; minimize sheet count',
                provenance='Public inputs copied from pinned PackingSolver repository; published BKS not used as project labels. Synthetic inputs are not industrial orders.',
                historical_policy='All Berkey instances conservatively assigned to historical development. Other historical geometry hashes excluded from fresh partitions.',
                split_policy='All Martello inputs are external holdout. Synthetic parent groups use a fixed hash split 60/20/20 in expectation. Related and boundary variants stay in the same split.',
                route_policy='R43 source and thresholds unchanged. Observing features is allowed; validation/test solver labels remain unopened.',
                training_label_policy='An input or known optimum alone is not an algorithm-choice label. Only paired runs under the same online budget provide such labels.',
                witness_policy='Witnesses stored outside solver input; used only to verify feasibility/optimality, not as solver initialization.',
                pilot_policy='Two development cases per synthetic family, chosen by hash before solving; default/guide1/router all 10 seconds, seed 101. No validation/test runs.',
                generalization='Random synthetic splits assess within-generator variation; Martello is an external-family test. Neither establishes industrial distribution performance.')
(OUT / 'manifest.json').write_text(json.dumps(cases, indent=2))
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
(OUT / 'protocol.json').write_text(json.dumps(protocol, indent=2))
(OUT / 'duplicates.json').write_text(json.dumps(duplicates, indent=2))
pilot = []
for family in sorted({c['family'] for c in cases if not c['family'].startswith('public_')}):
    rr = sorted([c for c in cases if c['family'] == family and c['split'] == 'development'], key=lambda c: digest(['pilot', SEED, c['name']]))
    pilot.extend(c['name'] for c in rr[:2])
assert len(pilot) == 12
(OUT / 'pilot-manifest.json').write_text(json.dumps(pilot, indent=2))
with (OUT / 'features.csv').open('w', newline='') as f:
    fields = ['name', 'family', 'group_id', 'split', 'width', 'height', 'frozen_branch', 'known_optimum'] + list(cases[0]['features'])
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    for c in cases:
        writer.writerow({k: (c['features'][k] if k in c['features'] else c[k]) for k in fields})
print('DATA_EXPANSION', json.dumps(summary), flush=True)
