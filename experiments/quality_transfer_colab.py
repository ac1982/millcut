"""Frozen strip-LP transfer to six unused Berkey instances; Colab only."""
import sys,json,random,subprocess,hashlib,pathlib,shutil
sys.path.insert(0,'/content')
import strip_lp_colab as alg
ROOT=alg.q.pj.ROOT/'quality-transfer';ROOT.mkdir(exist_ok=True)
alg.q.pj.DATA=alg.q.pj.ROOT/'berkey1987';alg.OUT=ROOT/'results';alg.OUT.mkdir(exist_ok=True)

def main():
    names=[f'Class_{i:02d}.2bp_100_1' for i in range(1,7)]
    source=pathlib.Path('/content/strip_lp_colab.py');frozen=hashlib.sha256(source.read_bytes()).hexdigest()
    holdout=json.loads((alg.q.pj.ROOT/'quality-holdout/protocol.json').read_text());assert frozen==holdout['algorithm_sha256']
    (ROOT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'seeds':[31,32],'algorithm_sha256':frozen,'scope':'previously unused Berkey 100-item replicate 1; transfer without tuning'},indent=2))
    jobs=[(n,m,s) for n in names for m in ('default','strips') for s in (31,32)];random.Random(972).shuffle(jobs);rows=[]
    for n,m,s in jobs:
        assert hashlib.sha256(source.read_bytes()).hexdigest()==frozen
        proc=subprocess.run([sys.executable,__file__,'worker',n,m,str(s)],capture_output=True,text=True,timeout=30)
        if proc.returncode:row={'instance':n,'method':m,'seed':s,'error':proc.stderr[-2000:]}
        else:row=json.loads(proc.stdout.strip().splitlines()[-1])
        rows.append(row);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('TRANSFER',len(rows),24,json.dumps(row),flush=True)
    summary={}
    for m in ('default','strips'):
        rr=[r for r in rows if r['method']==m and 'error' not in r];w=l=t=0
        for r in rr:
            b=next(b for b in rows if b['instance']==r['instance'] and b['seed']==r['seed'] and b['method']=='default')
            w+=r['bins']<b['bins'];l+=r['bins']>b['bins'];t+=r['bins']==b['bins']
        summary[m]={'completed':len(rr),'errors':12-len(rr),'total_bins':sum(r['bins'] for r in rr),'wins':w,'losses':l,'ties':t,'all_valid':all(r['valid'] for r in rr)}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(alg.q.pj.ROOT/'quality-transfer-results'),'zip',ROOT);print('TRANSFER_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(alg.run(sys.argv[2],sys.argv[3],int(sys.argv[4]),10)))
    else:main()
