"""Colab-only model correctness diagnostic. Witnesses are used ONLY here, not in benchmarks."""
import sys,pathlib,json,time,importlib
sys.path.insert(0,'/content');import quality_colab as q;import cluster_model_colab as cm
importlib.reload(q);importlib.reload(cm)
sys.path.insert(0,str(q.pj.ROOT));import packingsolver.rectangleguillotine as ps
ROOT=q.pj.ROOT/'planted-quality';q.pj.DATA=ROOT/'data';q.ps=ps;cm.ps=ps
results=[]
for entry in json.loads((ROOT/'protocol.json').read_text())['instances']:
    name=entry['name'];witness=json.loads((ROOT/f'{name}-witness.json').read_text());inst=q.make(name)
    def fix(model,a,z,items):
        groups={}
        for p in witness:groups.setdefault((p['plate'],p['y']),[]).append(p['id'])
        index={it[0]:j for j,it in enumerate(items)};leader={};leader_bin={}
        for (plate,y),ids in groups.items():
            j=min(index[i] for i in ids);leader_bin[j]=plate
            for i in ids:leader[i]=j
        for i,it in enumerate(items):assert (i,leader[it[0]]) in a
        for (i,j),var in a.items():model.add(var==int(leader[items[i][0]]==j))
        for (j,k),var in z.items():model.add(var==int(j in leader_bin and leader_bin[j]==k))
    for mod in (q,cm):
        bins,status=mod.cp_shelves(inst,3,time.perf_counter()+5,1,diagnostic=fix)
        assert bins is not None and len(bins)==3,(name,mod.__name__,status)
        results.append({'instance':name,'model':mod.__name__,'known_witness_accepted':True})
(ROOT/'model-correctness.json').write_text(json.dumps({'warning':'Fixed known-answer assignments; correctness diagnostic only, excluded from performance results.','checks':results},indent=2))
print('MODEL_CORRECTNESS_PASS',len(results))
