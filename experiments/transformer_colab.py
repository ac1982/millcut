"""Colab-only exploratory Transformer algorithm selector; run after search_colab.py."""
import pathlib, json, csv, time, collections, random, shutil, sys, hashlib
import numpy as np
import torch
from torch import nn
ROOT=pathlib.Path('/content/search-study')
sys.path.insert(0,str(ROOT))
import packingsolver.rectangleguillotine as ps
DATA=ROOT/'berkey1987'
OUT=ROOT/'transformer'
OUT.mkdir(exist_ok=True)
torch.set_num_threads(2)
configs={'default':{},'guide0':{'use_tree_search':True,'tree_search_guides':[0]},'guide1':{'use_tree_search':True,'tree_search_guides':[1]}}
labels=json.loads((ROOT/'results/rows.json').read_text())
train_names=sorted({r['instance'] for r in labels if r['instance'].endswith(('_2','_3'))})
val_names=sorted({r['instance'] for r in labels if r['instance'].endswith('_4')})
test_names=[f'Class_{c:02d}.2bp_{n}_8' for c in range(1,7) for n in (60,100)]
(OUT/'splits.json').write_text(json.dumps({'train':train_names,'validation':val_names,'test':test_names},indent=2))
fingerprints={}
for split,names in [('train',train_names),('validation',val_names),('test',test_names)]:
    for name in names:
        with (DATA/f'{name}_bins.csv').open() as f:
            bins=sorted((r['WIDTH'],r['HEIGHT']) for r in csv.DictReader(f))
        with (DATA/f'{name}_items.csv').open() as f:
            items=sorted((r['WIDTH'],r['HEIGHT'],r.get('COPIES','1')) for r in csv.DictReader(f))
        digest=hashlib.sha256(json.dumps([bins,items]).encode()).hexdigest()
        assert digest not in fingerprints or fingerprints[digest]['split']==split, 'Geometry leakage across splits'
        fingerprints[digest]={'split':split,'instance':name}
(OUT/'geometry-hashes.json').write_text(json.dumps(fingerprints,indent=2))

def features(names):
    batch=[]; masks=[]
    for name in names:
        with (DATA/f'{name}_bins.csv').open() as f: b=next(csv.DictReader(f))
        W,H=float(b['WIDTH']),float(b['HEIGHT'])
        with (DATA/f'{name}_items.csv').open() as f: items=list(csv.DictReader(f))
        tokens=[]
        for it in items:
            w,h=float(it['WIDTH'])/W,float(it['HEIGHT'])/H
            for _ in range(int(it.get('COPIES',1))): tokens.append([w,h,w*h,w/(h+1e-6),len(items)/100])
        assert len(tokens)<=100
        masks.append([False]*len(tokens)+[True]*(100-len(tokens)))
        batch.append(tokens+[[0]*5]*(100-len(tokens)))
    return torch.tensor(batch,dtype=torch.float32),torch.tensor(masks)

def costs(names):
    out=[]
    for name in names:
        values=[]
        for c in configs:
            rr=[r for r in labels if r['instance']==name and r['config']==c]
            values.append(sum((r['bins'] if r['valid'] else 1000)+0.001*r['wall_s'] for r in rr)/len(rr))
        m=min(values)
        out.append([x-m for x in values])
    return torch.tensor(out,dtype=torch.float32)

class Selector(nn.Module):
    def __init__(self):
        super().__init__()
        self.input=nn.Linear(5,32)
        layer=nn.TransformerEncoderLayer(32,4,64,dropout=0.0,batch_first=True)
        self.encoder=nn.TransformerEncoder(layer,2,enable_nested_tensor=False)
        self.output=nn.Linear(32,3)
    def forward(self,x,mask):
        x=self.encoder(self.input(x),src_key_padding_mask=mask)
        keep=(~mask).unsqueeze(-1)
        return self.output((x*keep).sum(1)/keep.sum(1))

x,mask=features(train_names);y=costs(train_names)
v,vm=features(val_names);vy=costs(val_names)
models=[];hist=[]
training_start=time.perf_counter()
for seed in (11,22,33):
    torch.manual_seed(seed)
    model=Selector();opt=torch.optim.AdamW(model.parameters(),lr=0.002,weight_decay=0.01)
    best=float('inf');best_state=None;best_epoch=0
    for epoch in range(120):
        model.train();opt.zero_grad()
        loss=nn.functional.mse_loss(model(x,mask),y)
        loss.backward();opt.step()
        model.eval()
        with torch.no_grad():
            pred=model(v,vm)
            # Select epochs exclusively using validation regret; no test labels.
            regret=vy[torch.arange(len(vy)),pred.argmin(1)].mean().item()
        if regret<best:
            best=regret;best_epoch=epoch
            best_state={k:t.detach().clone() for k,t in model.state_dict().items()}
    model.load_state_dict(best_state);model.eval()
    torch.save(best_state,OUT/f'selector-{seed}.pt')
    models.append(model);hist.append({'seed':seed,'validation_regret':best,'epoch':best_epoch})
fixed_idx=int(costs(train_names).mean(0).argmin())
fixed=list(configs)[fixed_idx]
training_seconds=time.perf_counter()-training_start
(OUT/'training.json').write_text(json.dumps({'training_seconds_cpu':training_seconds,'seeds':hist,'best_fixed_from_training':fixed,'architecture':'2-layer permutation-invariant Transformer; 32 hidden; 4 heads; masked mean pooling; regression of configuration regret','caveat':'24 training instances and 12 validation instances: exploratory only'},indent=2))
print('TRAINED',training_seconds,hist,fixed,flush=True)
def validate(inst, sol):
    counts = collections.Counter()
    for bpos in range(sol.number_of_different_bins()):
        b = sol.bin(bpos)
        bt = inst.bin_type(b.bin_type_id)
        nodes = [n for n in b.nodes if n.f != -1 and n.item_type_id >= 0]
        for n in nodes:
            it = inst.item_type(n.item_type_id)
            assert (n.r-n.l, n.t-n.b) == (it.w, it.h), 'item dimensions'
            assert 0 <= n.l < n.r <= bt.w and 0 <= n.b < n.t <= bt.h, 'bounds'
            counts[n.item_type_id] += b.copies
        for i, a in enumerate(nodes):
            for bnode in nodes[i+1:]:
                assert a.r <= bnode.l or bnode.r <= a.l or a.t <= bnode.b or bnode.t <= a.b, 'overlap'
    assert all(counts[i] == inst.item_type(i).copies for i in range(inst.number_of_item_types())), 'demand'
    assert sol.full() and sol.feasible(), 'solver feasibility including cutting constraints'


def instance(name):
    b=ps.InstanceBuilder()
    b.read_bin_types(str(DATA/f'{name}_bins.csv'))
    b.read_item_types(str(DATA/f'{name}_items.csv'))
    b.set_bin_types_infinite_copies()
    b.set_objective(ps.Objective.BinPacking)
    b.set_number_of_stages(3)
    b.set_cut_type(ps.CutType.NonExact)
    b.set_first_stage_orientation(ps.CutOrientation.Horizontal)
    b.set_item_types_oriented(True)
    b.set_cut_thickness(0)
    return b.build()

rows=[]
jobs=[(n,method,rep) for n in test_names for method in ('default','best_fixed','transformer') for rep in range(2)]
random.Random(20261113).shuffle(jobs)
for idx,(name,method,rep) in enumerate(jobs):
    inst=instance(name)
    start=time.perf_counter()
    config='default' if method=='default' else fixed
    inference=0.0
    if method=='transformer':
        f,fm=features([name])
        with torch.no_grad(): scores=torch.stack([m(f,fm) for m in models]).mean(0)
        config=list(configs)[int(scores.argmin())]
        inference=time.perf_counter()-start
    p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(0.001,2-inference)
    for k,value in configs[config].items():setattr(p,k,value)
    out=ps.optimize(inst,p)
    elapsed=time.perf_counter()-start
    sol=out.solution
    valid=True;error=''
    try:validate(inst,sol)
    except AssertionError as exc:valid=False;error=str(exc)
    sol.write(str(OUT/f'{name}_{method}_{rep}.csv'))
    row={'instance':name,'method':method,'config':config,'repeat':rep,'bins':sol.number_of_bins(),'valid':valid,'error':error,'wall_s':elapsed,'inference_s':inference,'bound':out.bin_packing_bound}
    rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2))
    print(f'{idx+1}/{len(jobs)} '+json.dumps(row),flush=True)
summary={}
for method in ('default','best_fixed','transformer'):
    rr=[r for r in rows if r['method']==method]
    wins=losses=ties=0
    for r in rr:
        base=next(b for b in rows if b['method']=='default' and b['instance']==r['instance'] and b['repeat']==r['repeat'])
        if r['valid'] and base['valid']:
            wins+=r['bins']<base['bins'];losses+=r['bins']>base['bins'];ties+=r['bins']==base['bins']
    summary[method]={'valid':sum(r['valid'] for r in rr),'runs':len(rr),'mean_bins':sum(r['bins'] for r in rr if r['valid'])/max(1,sum(r['valid'] for r in rr)),'mean_wall_s':sum(r['wall_s'] for r in rr)/len(rr),'wins':wins,'losses':losses,'ties':ties}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
shutil.make_archive(str(ROOT/'transformer-results'),'zip',OUT)
print('TRANSFORMER_COMPLETE '+json.dumps(summary),flush=True)
