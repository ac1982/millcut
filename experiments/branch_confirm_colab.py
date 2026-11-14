"""Longer teacher budget and label stability audit; run only in Colab."""
import sys,json,random,pathlib
sys.path.insert(0,'/content');import branch_collect_colab as bc
OUT=bc.OUT
short=json.loads((OUT/'rows.json').read_text())
summary=bc.summarize(short)
# At most one informative state per instance. Prefer a state where rank 0
# missed a better completion, then an early state. Selection is diagnostic.
selected=[]
for name in bc.CASES:
    gg=[g for g in summary['groups'] if g['instance']==name and g['observed_best']<g['observed_worst']]
    if gg:selected.append(sorted(gg,key=lambda g:(not g['better_than_rank0'],g['depth']))[0])
(OUT/'confirmation-protocol.json').write_text(json.dumps(dict(selected=selected,budget_s=10,selection='At most one informative state per instance, prefer rank0 misses then smallest depth',scope='Selected label stability audit, not unbiased performance evaluation'),indent=2))
jobs=[]
for g in selected:
    for r in sorted({r['rank'] for r in short if r['state_id']==g['state_id']}):
        jobs.append((g['instance'],g['depth'],r))
random.Random(1737).shuffle(jobs);rows=[]
for n,d,r in jobs:
    row=bc.solve(n,d,r,10,'long')
    before=next(x for x in short if x['instance']==n and x['target_depth']==d and x['rank']==r)
    assert row['state_id']==before['state_id'] and row['child']==before['child']
    rows.append(row);(OUT/'long-rows.json').write_text(json.dumps(rows,indent=2))
    print('LONG',len(rows),len(jobs),n,d,r,before['bins'],'->',row['bins'],flush=True)
long_summary=bc.summarize(rows)
audit=[]
for g in long_summary['groups']:
    old=next(x for x in selected if x['state_id']==g['state_id'])
    pairs=[]
    for a,aa in g['ranks'].items():
        for b,bb in g['ranks'].items():
            oa,ob=old['ranks'][a],old['ranks'][b]
            if a>=b or any(v is None for v in (aa,bb,oa,ob)):continue
            oldsign=(oa>ob)-(oa<ob);newsign=(aa>bb)-(aa<bb)
            pairs.append(dict(a=a,b=b,old=[oa,ob],new=[aa,bb],old_strict=oldsign!=0,reversed=oldsign*newsign<0,collapsed=oldsign!=0 and newsign==0,new_strict=oldsign==0 and newsign!=0))
    audit.append(dict(instance=g['instance'],depth=g['depth'],old=old['ranks'],new=g['ranks'],pairs=pairs))
result=dict(summary=long_summary,stability=audit,old_strict_pairs=sum(p['old_strict'] for a in audit for p in a['pairs']),reversed_pairs=sum(p['reversed'] for a in audit for p in a['pairs']),collapsed_pairs=sum(p['collapsed'] for a in audit for p in a['pairs']),new_strict_pairs=sum(p['new_strict'] for a in audit for p in a['pairs']))
(OUT/'confirmation-summary.json').write_text(json.dumps(result,indent=2));print('LONG_COMPLETE',json.dumps(result),flush=True)
