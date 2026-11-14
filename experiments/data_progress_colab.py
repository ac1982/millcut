"""Read remote pilot progress without running another solver."""
import pathlib, json, os
root = pathlib.Path('/content/data-expansion/corpus/labels')
if (root / 'summary.json').exists():
    print('COMPLETE', (root / 'summary.json').read_text())
else:
    rows = json.loads((root / 'rows.json').read_text()) if (root / 'rows.json').exists() else []
    print('COMPLETED_RUNS', len(rows), '/36')
    if (root / 'pilot-running.log').exists():
        print((root / 'pilot-running.log').read_text()[-2200:])
