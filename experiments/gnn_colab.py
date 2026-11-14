"""Colab-only: expanded labels, GNN vs random forest vs fixed policy; fresh held-out tests."""
import sys,pathlib,json,csv,time,random,collections,hashlib,shutil
sys.path.insert(0,'/content')
import portfolio_colab as pj
import numpy as np
import torch
from torch import nn
from sklearn.ensemble import RandomForestRegressor
import joblib
ROOT=pj.ROOT;OUT=ROOT/'learning';OUT.mkdir(exist_ok=True)
torch.set_num_threads(2)
rows=json.loads((ROOT/'results/rows.json').read_text())
all_configs=list(pj.CONFIGS)
# Freeze the candidate set using development data only.
def rank(c):
    rr=[r for r in rows if r['config']==c]
    return (sum(not r['valid'] for r in rr),sum(r['bins'] if r['valid'] else 1000 for r in rr),sum(r['wall_s'] or 100 for r in rr))
candidates=['default']+[c for c in sorted(all_configs,key=rank) if c!='default'][:3]
train_names=[f'Class_{c:02d}.2bp_{n}_{r}' for c in range(1,7) for n in (60,100) for r in (2,3,4,5,6)]
val_names=[f'Class_{c:02d}.2bp_{n}_7' for c in range(1,7) for n in (60,100)]
test_names=[f'Class_{c:02d}.2bp_{n}_{r}' for c in range(1,7) for n in (60,100) for r in (9,10)]
(OUT/'protocol.json').write_text(json.dumps({'candidates':candidates,'training':train_names,'validation':val_names,'test':test_names,'budget_s':2,'test_repeats':2,'training_labels_repeats':1,'selection':'candidates ranked on prior development data; test untouched until all models frozen','gnn':'two message-passing layers, separate width/height compatibility graphs, mean pooling; predicts configuration regret','rf':'200 trees, max depth 4, min leaf 3; fixed hyperparameters','timing':'feature construction and policy inference deducted from solver budget; common process startup and model loading excluded'},indent=2))
seen={}
for split,names in [('train',train_names),('validation',val_names),('test',test_names)]:
    for name in names:
        with (pj.DATA/f'{name}_bins.csv').open() as f:bins=sorted((r['WIDTH'],r['HEIGHT']) for r in csv.DictReader(f))
        with (pj.DATA/f'{name}_items.csv').open() as f:items=sorted((r['WIDTH'],r['HEIGHT'],r.get('COPIES','1')) for r in csv.DictReader(f))
        h=hashlib.sha256(json.dumps([bins,items]).encode()).hexdigest()
        assert h not in seen or seen[h]['split']==split,'duplicate geometry across splits'
        seen[h]={'split':split,'name':name}
(OUT/'geometry-hashes.json').write_text(json.dumps(seen,indent=2))
existing={(r['instance'],r['config']) for r in rows}
jobs=[(n,c) for n in train_names+val_names for c in candidates if (n,c) not in existing]
random.Random(261).shuffle(jobs)
for i,(n,c) in enumerate(jobs):
    row=pj.run_job(n,c,2,f'learning_{n}_{c}');rows.append(row)
    (OUT/'labels.json').write_text(json.dumps(rows,indent=2))
    if i%12==0:print('LABELS',i+1,len(jobs),flush=True)
print('LABELS_COMPLETE',candidates,flush=True)

def tensors(names):
    batch=[];mask=[];stats=[]
    for name in names:
        with (pj.DATA/f'{name}_bins.csv').open() as f:b=next(csv.DictReader(f))
        W,H=float(b['WIDTH']),float(b['HEIGHT'])
        with (pj.DATA/f'{name}_items.csv').open() as f:its=list(csv.DictReader(f))
        a=[]
        for r in its:
            w,h=float(r['WIDTH'])/W,float(r['HEIGHT'])/H
            a.extend([[w,h,w*h,min(w/(h+1e-6),10),len(its)/100]]*int(r.get('COPIES',1)))
        a=np.asarray(a,dtype=np.float32);N=len(a)
        stats.append(np.concatenate([a.mean(0),a.std(0),a.min(0),a.max(0),np.quantile(a,[.25,.5,.75],axis=0).ravel(),[a[:,2].sum()]]))
        batch.append(np.pad(a,((0,100-N),(0,0))));mask.append([1]*N+[0]*(100-N))
    x=torch.tensor(np.array(batch));m=torch.tensor(mask,dtype=torch.float32)
    # Two relations: fit side-by-side, or fit vertically, ignoring other constraints.
    valid=m[:,:,None]*m[:,None,:]
    aw=(x[:,:,0,None]+x[:,None,:,0]<=1).float()*valid
    ah=(x[:,:,1,None]+x[:,None,:,1]<=1).float()*valid
    aw=aw/aw.sum(-1,keepdim=True).clamp_min(1);ah=ah/ah.sum(-1,keepdim=True).clamp_min(1)
    return x,m,aw,ah,np.array(stats,dtype=np.float32)

def costs(names):
    vals=[]
    for name in names:
        rr=[]
        for c in candidates:
            r=next(r for r in rows if r['instance']==name and r['config']==c)
            rr.append((r['bins'] if r['valid'] else 1000)+.001*(r['wall_s'] or 20))
        vals.append(np.array(rr)-min(rr))
    return torch.tensor(np.array(vals),dtype=torch.float32)

class GNN(nn.Module):
    def __init__(self):
        super().__init__();self.emb=nn.Linear(5,32);self.layers=nn.ModuleList([nn.Linear(96,32) for _ in range(2)]);self.out=nn.Linear(32,len(candidates))
    def forward(self,x,m,aw,ah):
        h=torch.relu(self.emb(x))
        for layer in self.layers:h=torch.relu(layer(torch.cat([h,aw@h,ah@h],dim=-1)))
        return self.out((h*m[:,:,None]).sum(1)/m.sum(1,keepdim=True))

start=time.perf_counter();tr=tensors(train_names);va=tensors(val_names);y=costs(train_names);vy=costs(val_names)
rf=RandomForestRegressor(n_estimators=200,max_depth=4,min_samples_leaf=3,random_state=71,n_jobs=1)
rf.fit(tr[-1],y.numpy());joblib.dump(rf,OUT/'random-forest.joblib')
fixed=candidates[int(y.mean(0).argmin())]
models=[];history=[]
for seed in (41,42,43):
    torch.manual_seed(seed);model=GNN();opt=torch.optim.AdamW(model.parameters(),lr=.002,weight_decay=.01);best=float('inf');state=None
    for epoch in range(200):
        model.train();opt.zero_grad();loss=nn.functional.smooth_l1_loss(model(*tr[:4]),y);loss.backward();opt.step()
        model.eval()
        with torch.no_grad():
            pred=model(*va[:4]);score=vy[torch.arange(len(vy)),pred.argmin(1)].mean().item()
        if score<best:best=score;state={k:v.detach().clone() for k,v in model.state_dict().items()};chosen_epoch=epoch
    model.load_state_dict(state);model.eval();torch.save(state,OUT/f'gnn-{seed}.pt');models.append(model);history.append({'seed':seed,'validation_regret':best,'epoch':chosen_epoch})
training_s=time.perf_counter()-start
# Verify permutation equivariance of graphs and masked pooling.
perm=torch.randperm(100)
with torch.no_grad():
    for model in models:
        original=model(*va[:4]);reordered=model(va[0][:,perm],va[1][:,perm],va[2][:,perm][:,:,perm],va[3][:,perm][:,:,perm])
        assert torch.isfinite(original).all() and torch.allclose(original,reordered,atol=1e-5,rtol=1e-5)
(OUT/'training.json').write_text(json.dumps({'seconds_cpu':training_s,'models':history,'best_fixed':fixed,'permutation_checks':True},indent=2))
print('TRAINING_COMPLETE',training_s,history,fixed,flush=True)

# Freeze model checkpoints before looking at fresh test solution labels.
jobs=[(n,method,r) for n in test_names for method in ('default','fixed','random_forest','gnn') for r in range(2)]
random.Random(362).shuffle(jobs);tests=[]
for i,(name,method,repeat) in enumerate(jobs):
    t=time.perf_counter();choice='default' if method=='default' else fixed
    if method in ('random_forest','gnn'):
        feat=tensors([name])
        if method=='random_forest':idx=int(rf.predict(feat[-1])[0].argmin())
        else:
            with torch.no_grad():idx=int(torch.stack([model(*feat[:4]) for model in models]).mean(0).argmin())
        choice=candidates[idx]
    overhead=time.perf_counter()-t
    row=pj.run_job(name,choice,max(.001,2-overhead),f'test_{name}_{method}_{repeat}')
    row.update(method=method,repeat=repeat,policy_s=overhead)
    if row['wall_s'] is not None:row['wall_s']+=overhead
    tests.append(row);(OUT/'test-rows.json').write_text(json.dumps(tests,indent=2))
    if i%12==0:print('TEST',i+1,len(jobs),flush=True)
summary={}
for method in ('default','fixed','random_forest','gnn'):
    rr=[r for r in tests if r['method']==method];wins=losses=ties=0;details=[]
    for r in rr:
        b=next(b for b in tests if b['method']=='default' and b['instance']==r['instance'] and b['repeat']==r['repeat'])
        if r['valid'] and b['valid']:
            wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins']
            if r['bins']!=b['bins']:details.append({'instance':r['instance'],'repeat':r['repeat'],'default':b['bins'],'candidate':r['bins'],'config':r['config']})
    summary[method]={'valid':sum(r['valid'] for r in rr),'runs':len(rr),'mean_bins_valid':sum(r['bins'] for r in rr if r['valid'])/max(1,sum(r['valid'] for r in rr)),'mean_wall_s':sum(r['wall_s'] or 12 for r in rr)/len(rr),'wins':wins,'ties':ties,'losses':losses,'differences':details}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(ROOT/'learning-results'),'zip',OUT)
# Certificates and mapping files also include newly generated training/test results.
shutil.make_archive(str(ROOT/'portfolio-results'),'zip',ROOT/'results')
print('GNN_COMPLETE',json.dumps(summary),flush=True)
