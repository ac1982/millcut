"""Colab-only correctness checks and instance-cluster bootstrap for fresh fast-v2 results."""
import sys,pathlib,json,random,shutil
import numpy as np
sys.path.insert(0,'/content');import fast_v2_colab as f
root=f.OUT
checks=[]
for items,expected in [([[50,50]]*4,1),([[51,50]]*2,1),([[50,51]]*2,1),([[51,51]]*2,2)]:
    assert f.lower_bound(items,100,100)==expected
checks.append('half-width/height lower-bound boundary cases')
rng=random.Random(661)
for _ in range(100):
    items=[];remaining_h=100
    while remaining_h>0:
        h=rng.randint(1,remaining_h);remaining_h-=h;remaining_w=100
        while remaining_w>0:
            w=rng.randint(1,remaining_w);remaining_w-=w;items.append([w,rng.randint(1,h)])
    assert f.lower_bound(items,100,100)<=1
checks.append('100 independently constructed one-sheet cut layouts respect all lower bounds')
(root/'checks.json').write_text(json.dumps(checks,indent=2))
rows=json.loads((root/'rows.json').read_text());report={};rngnp=np.random.default_rng(871)
for group in ['all','excluding_large']+sorted({r['distribution'] for r in rows}):
    rr=[r for r in rows if group=='all' or (group=='excluding_large' and r['distribution']!='large') or r['distribution']==group]
    names=sorted({r['instance'] for r in rr});pairs=[]
    for n in names:
        pairs.append([np.mean([r['wall_s'] for r in rr if r['instance']==n and r['method']==m]) for m in ('default','hybrid')])
    a=np.array(pairs);ix=rngnp.integers(0,len(a),size=(10000,len(a)));boot=a[ix].mean(1)
    speed=boot[:,0]/boot[:,1];delta=boot[:,0]-boot[:,1]
    report[group]={'instances':len(a),'default_mean_s':float(a[:,0].mean()),'hybrid_mean_s':float(a[:,1].mean()),'speed_ratio':float(a[:,0].mean()/a[:,1].mean()),'ratio_ci95':np.quantile(speed,[.025,.975]).tolist(),'seconds_saved_ci95':np.quantile(delta,[.025,.975]).tolist(),'note':'paired bootstrap by instance, averages its two timing repeats; same runtime only; synthetic distribution scope'}
(root/'statistics.json').write_text(json.dumps(report,indent=2));shutil.make_archive(str(f.ROOT/'fast-v2-results'),'zip',root)
print(json.dumps(report,indent=2))
