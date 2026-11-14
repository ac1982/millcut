"""Colab-only frozen strip-LP quality validation on new generated instances."""
import sys,pathlib,json,random,subprocess,shutil,hashlib,importlib.metadata,csv,collections
sys.path.insert(0,'/content');import quality_planted_colab as gen;import strip_lp_colab as alg
ROOT=alg.q.pj.ROOT/'quality-holdout';DATA=ROOT/'data';OUT=ROOT/'results';DATA.mkdir(parents=True,exist_ok=True);OUT.mkdir(exist_ok=True)
gen.ROOT=ROOT;gen.DATA=DATA;gen.OUT=OUT;alg.q.pj.DATA=DATA;alg.OUT=OUT

def main():
    frozen_hash=hashlib.sha256(pathlib.Path('/content/strip_lp_colab.py').read_bytes()).hexdigest()
    manifest=gen.generate(seed=65001,repeats=6)
    old=json.loads((alg.q.pj.ROOT/'planted-quality/protocol.json').read_text())['instances'];oldhashes={r['sha256'] for r in old}
    for row in manifest:
        assert row['sha256'] not in oldhashes
        name=row['name'];new='holdout_'+name
        for suffix in ('items','bins'):(DATA/f'{name}_{suffix}.csv').rename(DATA/f'{new}_{suffix}.csv')
        (ROOT/f'{name}-witness.json').rename(ROOT/f'{new}-witness.json');row['name']=new
    (ROOT/'protocol.json').write_text(json.dumps({'instances':manifest,'generation_seed':65001,'split':'fresh held-out, same generator family; no post-test tuning','algorithm_sha256':frozen_hash,'method':'frozen strip pool + LP reduced-cost ranking + CP-SAT exact cover and bin assignment','budget_s':10,'repeats_seeds':[21,22],'versions':{p:importlib.metadata.version(p) for p in ('ortools','scipy','numpy')},'timing':'includes initial solver, pattern generation, model construction, LP and integer solve; output serialization excluded','witness':'not read by solver; only construction and area prove known optimum 3'},indent=2))
    jobs=[(r['name'],m,s) for r in manifest for m in ('default','strips') for s in (21,22)];random.Random(971).shuffle(jobs);rows=[]
    for i,(n,m,s) in enumerate(jobs):
        assert hashlib.sha256(pathlib.Path('/content/strip_lp_colab.py').read_bytes()).hexdigest()==frozen_hash
        proc=subprocess.run([sys.executable,'/content/quality_holdout_colab.py','worker',n,m,str(s)],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);row['optimum']=3
        with (DATA/f'{n}_items.csv').open() as f:area=sum(int(r['WIDTH'])*int(r['HEIGHT']) for r in csv.DictReader(f))
        row['item_area']=area;row['waste_area']=row['bins']*1000000-area;row['late_over_100ms']=row['wall_s']>10.1
        rows.append(row);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('HOLDOUT',i+1,len(jobs),json.dumps(row),flush=True)
    summary={};diff=[]
    for m in ('default','strips'):
        rr=[r for r in rows if r['method']==m];wins=losses=ties=ontime_wins=0
        for r in rr:
            b=next(b for b in rows if b['instance']==r['instance'] and b['seed']==r['seed'] and b['method']=='default')
            wins+=r['bins']<b['bins'];losses+=r['bins']>b['bins'];ties+=r['bins']==b['bins'];ontime_wins+=r['bins']<b['bins'] and not r['late_over_100ms']
            if m=='strips' and r['bins']!=b['bins']:diff.append({'instance':r['instance'],'seed':r['seed'],'default':b['bins'],'strips':r['bins'],'wall_s':r['wall_s']})
        summary[m]={'runs':len(rr),'total_bins':sum(r['bins'] for r in rr),'total_waste_area':sum(r['waste_area'] for r in rr),'optimal_runs':sum(r['bins']==3 for r in rr),'wins':wins,'losses':losses,'ties':ties,'wins_within_10_1s':ontime_wins,'max_wall_s':max(r['wall_s'] for r in rr),'all_valid':all(r['valid'] for r in rr)}
    summary['differences']=diff
    summary['instances_improved_in_both_repeats']=[r['name'] for r in manifest if all(next(x for x in rows if x['instance']==r['name'] and x['method']=='strips' and x['seed']==s)['bins']<next(x for x in rows if x['instance']==r['name'] and x['method']=='default' and x['seed']==s)['bins'] for s in (21,22))]
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(alg.q.pj.ROOT/'quality-holdout-results'),'zip',ROOT);print('HOLDOUT_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(alg.run(sys.argv[2],sys.argv[3],int(sys.argv[4]),10)))
    else:main()
