"""Inventory pinned public inputs and historical inputs; execute only in Colab."""
import pathlib, tarfile, csv, json, collections
ROOT = pathlib.Path('/content/data-expansion')
ROOT.mkdir(exist_ok=True)
for archive, dest in [('cut-public-data.tar.gz', 'public'), ('cut-seen-inputs.tar.gz', 'history')]:
    with tarfile.open('/content/' + archive) as t:
        t.extractall(ROOT / dest, filter='data')
base = ROOT / 'public/rectangle'
summary = {}
for name in ['data_bin_packing_3nho.csv', 'data_bin_packing_oriented.csv']:
    with (base / name).open() as f:
        rows = list(csv.DictReader(f))
    summary[name] = dict(rows=len(rows), families=dict(collections.Counter(r['Path'].split('/')[0] for r in rows)))
print(json.dumps(summary, indent=2))
(ROOT / 'inventory.json').write_text(json.dumps(summary, indent=2))
