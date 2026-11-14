"""Colab-only pattern-pool quality tests on known-optimum development instances."""
import sys,pathlib,json,random,subprocess,shutil
sys.path.insert(0,'/content')
variant=sys.argv[1] if len(sys.argv)>1 else 'v1'
if variant=='v2':
    import pattern_pool_v2_colab as v2
    base=v2.base
else:import pattern_pool_colab as base
ROOT=base.q.pj.ROOT/'planted-quality';base.q.pj.DATA=ROOT/'data';base.OUT=ROOT/f'pool-{variant}';base.OUT.mkdir(exist_ok=True)

def main():
    names=[r['name'] for r in json.loads((ROOT/'protocol.json').read_text())['instances']]
    (base.OUT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'variant':variant,'known_optimum':3,'scope':'development only; witnesses never read by solver','repeats':1},indent=2))
    rows=[];jobs=[(n,m) for n in names for m in ('default','pool')];random.Random(832).shuffle(jobs)
    for i,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/pool_planted_colab.py',variant,'worker',n,m,'3'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(base.OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('PLANTED_POOL',variant,i+1,len(jobs),json.dumps(row),flush=True)
    summary={m:{'total_bins':sum(r['bins'] for r in rows if r['method']==m),'optimal_instances':sum(r['bins']==3 for r in rows if r['method']==m),'runs':sum(r['method']==m for r in rows)} for m in ('default','pool')}
    (base.OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(base.q.pj.ROOT/f'planted-pool-{variant}-results'),'zip',base.OUT);print('PLANTED_POOL_COMPLETE',variant,json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[2]=='worker':print(json.dumps(base.run(sys.argv[3],sys.argv[4],int(sys.argv[5]))))
    else:main()
