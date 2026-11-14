"""Read compact completed-development evidence in Colab."""
import json,pathlib
r=json.loads(pathlib.Path('/content/route-study/fit-report.json').read_text())
print('COUNTERFACTUAL',json.dumps({g:{m:{k:v for k,v in s.items() if k!='differences'} for m,s in mm.items()} for g,mm in r['counterfactual_by_region'].items()}))
print('FOLDS',json.dumps(r['crossfit_folds']))
print('MISSED_GATE_GAINS',json.dumps(r['counterfactual_by_region']['old_gate_miss']))
print('CROSSFIT_LOSSES',json.dumps([p for p in r['crossfit']['pairs'] if p['chosen']>p['reference']]))
