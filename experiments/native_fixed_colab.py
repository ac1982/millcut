"""Offline fixed-search-size native score screening, not a wall-time superiority claim."""
import sys,json,random,pathlib,hashlib
sys.path.insert(0,'/content');import native_run_colab as nr
OUT=nr.ROOT/'fixed-screen';OUT.mkdir(exist_ok=True);nr.OUT=OUT
NAMES=['Class_02.2bp_60_2','Class_02.2bp_100_3','Class_03.2bp_100_2','Class_04.2bp_100_1','Class_05.2bp_100_2','planted_3_3_5_0','planted_3_5_6_1']
CONFIGS=['native01','smooth','dead_small','dead_large','fit_small','fit_large','progress','absolute']
(OUT/'protocol.json').write_text(json.dumps({'instances':NAMES,'configs':{c:nr.CONFIGS[c] for c in CONFIGS},'queue':512,'mode':'NotAnytimeDeterministic','per_run_safety_limit':30,'scope':'offline fixed search-size development comparison; costs differ, no equal-time claim','binary_sha256':hashlib.sha256((nr.ROOT/'build/native_patched').read_bytes()).hexdigest()},indent=2))
jobs=[(n,c) for n in NAMES for c in CONFIGS];random.Random(1731).shuffle(jobs);rows=[]
for n,c in jobs:
    r=nr.run(n,c,30,1,'deterministic',512);rows.append(r);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('FIXED',len(rows),len(jobs),n,c,r['bins'],r['wall_s'],flush=True)
summary={}
for c in CONFIGS:
    rr=[r for r in rows if r['config']==c];diff=[];w=l=t=0
    for r in rr:
        b=next(b for b in rows if b['instance']==r['instance'] and b['config']=='native01');w+=r['bins']<b['bins'];l+=r['bins']>b['bins'];t+=r['bins']==b['bins']
        if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'baseline':b['bins'],'candidate':r['bins']})
    summary[c]={'total_bins':sum(r['bins'] for r in rr),'wins':w,'losses':l,'ties':t,'differences':diff,'total_seconds':sum(r['wall_s'] for r in rr)}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));print('FIXED_COMPLETE',json.dumps(summary),flush=True)
