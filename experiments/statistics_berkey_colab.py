"""Colab-only paired timing uncertainty on the untouched Berkey confirmation set."""
import pathlib,json,shutil
import numpy as np
root=pathlib.Path('/content/portfolio-study/fast-berkey');rows=json.loads((root/'rows.json').read_text());rng=np.random.default_rng(891);report={}
for group in ['all']+sorted({r['distribution'] for r in rows}):
    rr=[r for r in rows if group=='all' or r['distribution']==group];names=sorted({r['instance'] for r in rr})
    a=np.array([[np.mean([r['wall_s'] for r in rr if r['instance']==n and r['method']==m]) for m in ('default','hybrid')] for n in names])
    means=a[rng.integers(0,len(a),size=(10000,len(a)))].mean(1)
    report[group]={'instances':len(a),'speed_ratio':float(a[:,0].mean()/a[:,1].mean()),'ratio_ci95':np.quantile(means[:,0]/means[:,1],[.025,.975]).tolist(),'seconds_saved_ci95':np.quantile(means[:,0]-means[:,1],[.025,.975]).tolist(),'note':'paired bootstrap by instance; two timing repeats averaged; one Colab CPU runtime'}
(root/'statistics.json').write_text(json.dumps(report,indent=2));shutil.make_archive('/content/portfolio-study/fast-berkey-results','zip',root);print(json.dumps(report,indent=2))
