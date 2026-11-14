"""Colab-only confirmation on previously unused Berkey 40/80-item instances."""
import sys,pathlib,json,csv,hashlib,random,subprocess,shutil
sys.path.insert(0,'/content');import fast_v2_colab as f
f.OUT=f.ROOT/'fast-berkey';f.OUT.mkdir(exist_ok=True)

def main():
    names=[];manifest=[]
    for c in range(1,7):
        for n in (40,80):
            for rep in (9,10):
                name=f'Class_{c:02d}.2bp_{n}_{rep}';names.append(name)
                with (f.pj.DATA/f'{name}_bins.csv').open() as src:b=next(csv.DictReader(src))
                with (f.pj.DATA/f'{name}_items.csv').open() as src:its=list(csv.DictReader(src))
                items=[]
                for r in its:items.extend([[int(r['WIDTH']),int(r['HEIGHT'])]]*int(r.get('COPIES',1)))
                data={'W':int(b['WIDTH']),'H':int(b['HEIGHT']),'items':items,'distribution':f'Class_{c:02d}'}
                (f.OUT/f'{name}-input.json').write_text(json.dumps(data))
                manifest.append({'name':name,'sha256':{k:hashlib.sha256((f.pj.DATA/f'{name}_{k}.csv').read_bytes()).hexdigest() for k in ('items','bins')}})
    (f.OUT/'protocol.json').write_text(json.dumps({'instances':manifest,'method':'frozen fast-v2 algorithm, no changes after synthetic evaluation','budget_s':2,'repeats':2,'scope':'new 40/80-item Berkey instances, replicates 9/10; same guillotine rules as baseline'},indent=2))
    jobs=[(n,m,r) for n in names for m in ('default','hybrid') for r in range(2)];random.Random(184).shuffle(jobs);rows=[]
    for i,(n,m,r) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/fast_berkey_colab.py','worker',n,m,str(r)],capture_output=True,text=True,timeout=15)
        if proc.returncode:raise RuntimeError(proc.stderr[-2000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(f.OUT/'rows.json').write_text(json.dumps(rows,indent=2))
        if i%12==0:print('BERKEY_FAST',i+1,len(jobs),flush=True)
    summary={}
    for group in ['all']+[f'Class_{c:02d}' for c in range(1,7)]:
        rr=[r for r in rows if group=='all' or r['distribution']==group];bb=[r for r in rr if r['method']=='default'];hh=[r for r in rr if r['method']=='hybrid'];wins=losses=ties=0
        for h in hh:
            b=next(b for b in bb if b['instance']==h['instance'] and b['repeat']==h['repeat']);wins+=h['bins']<b['bins'];losses+=h['bins']>b['bins'];ties+=h['bins']==b['bins']
        summary[group]={'pairs':len(hh),'default_bins':sum(b['bins'] for b in bb),'hybrid_bins':sum(h['bins'] for h in hh),'default_mean_s':sum(b['wall_s'] for b in bb)/len(bb),'hybrid_mean_s':sum(h['wall_s'] for h in hh)/len(hh),'wins':wins,'losses':losses,'ties':ties,'certified_early':sum(h['certified_early'] for h in hh)}
    (f.OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(f.ROOT/'fast-berkey-results'),'zip',f.OUT);print('BERKEY_FAST_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(f.run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
