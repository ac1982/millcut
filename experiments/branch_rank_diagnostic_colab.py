"""Instance-held-out label diagnostic, not an online solver benchmark. Colab only."""
import sys,json,collections,pathlib
import numpy as np
sys.path.insert(0,'/content');import branch_collect_colab as bc
OUT=bc.OUT
short=json.loads((OUT/'rows.json').read_text())
long=json.loads((OUT/'long-rows.json').read_text()) if (OUT/'long-rows.json').exists() else []

def groups(rows):
    gg=collections.defaultdict(dict)
    for r in rows:
        if r['bins'] is not None:gg[r['state_id']][r['rank']]=r
    return [list(v.values()) for v in gg.values()]

def fit(training):
    gg=groups(training);a=np.asarray([r['features'] for g in gg for r in g],float)
    mean=a.mean(axis=0);scale=a.std(axis=0);scale[scale<1e-8]=1
    pairs=[]
    for g in gg:
        for a in g:
            for b in g:
                if a['bins']<b['bins']:pairs.append((np.asarray(a['features'])-b['features'])/scale)
    w=np.zeros(len(mean));x=np.asarray(pairs)
    if len(pairs):
        # Maximize w.x for a child with a better observed completion. Fixed
        # hyperparameters, no selection on held-out instance outcomes.
        for _ in range(600):
            z=np.clip(x@w,-35,35)
            grad=-(x/(1+np.exp(z))[:,None]).mean(axis=0)+.05*w
            w-=.08*grad
    return mean,scale,w,len(pairs)

def evaluate(rows,models):
    results=[]
    for g in groups(rows):
        name=g[0]['instance'];mean,scale,w,_=models[name]
        strategies={
            'original_guide1':lambda r:(r['rank'],),
            'area_lower_bound':lambda r:(r['branch_area_lower_bound'],r['rank']),
            'absolute_waste':lambda r:(r['child']['waste'],r['rank']),
            'original_guide0':lambda r:(r['child']['waste']/max(1,r['child']['item_area']),r['rank']),
            'learned_linear':lambda r:(-float(((np.asarray(r['features'])-mean)/scale)@w),r['rank']),
        }
        best=min(r['bins'] for r in g)
        chosen={k:min(g,key=fn) for k,fn in strategies.items()}
        results.append(dict(instance=name,state_id=g[0]['state_id'],depth=g[0]['target_depth'],best_observed=best,choices={k:dict(rank=r['rank'],bins=r['bins'],regret=r['bins']-best) for k,r in chosen.items()}))
    summary={}
    for k in ('original_guide1','area_lower_bound','absolute_waste','original_guide0','learned_linear'):
        ss=[r['choices'][k] for r in results]
        summary[k]=dict(states=len(ss),sum_completion_bins=sum(s['bins'] for s in ss),sum_regret=sum(s['regret'] for s in ss),best_choice_states=sum(s['regret']==0 for s in ss))
    return dict(summary=summary,states=results)

models={};saved={}
for name in bc.CASES:
    models[name]=fit([r for r in short if r['instance']!=name])
    mean,scale,w,n=models[name];saved[name]=dict(mean=mean.tolist(),scale=scale.tolist(),weights=w.tolist(),training_strict_pairs=n)
result=dict(protocol='Leave one whole instance out; train only on short-budget feasible outcomes from other instances. Fixed L2=0.05, 600 gradient steps, learning rate=0.08. Evaluate both short and selected longer-budget labels. This is prediction of teacher outcomes over four sampled actions, NOT a packing performance comparison.',models=saved,short=evaluate(short,models),long=evaluate(long,models))
(OUT/'rank-diagnostic.json').write_text(json.dumps(result,indent=2))
print('RANK_DIAGNOSTIC',json.dumps({k:result[k]['summary'] for k in ('short','long')}),flush=True)
