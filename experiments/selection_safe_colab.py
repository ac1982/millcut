"""Validate exact reduced-cost pruning on same frozen development pools."""
import sys,json,pathlib,subprocess,hashlib
sys.path.insert(0,'/content');import selection_core_colab as a
ROOT=a.ROOT/'safe';ROOT.mkdir(exist_ok=True)

def main():
    # An expensive alternative covering the same item is safely eliminated.
    pats=[{'ids':[0],'height':4},{'ids':[1],'height':4},{'ids':[0],'height':9}]
    selected,proof=a.safe_subset(pats,2,8,1,a.time.perf_counter()+3);assert len(selected)==2
    (ROOT/'checks.json').write_text(json.dumps({'dominated_alternative_removed':True,'proof':proof},indent=2))
    rows=[]
    for s in (42,61,62,63):
        p=subprocess.run([sys.executable,__file__,'worker',str(s)],capture_output=True,text=True,timeout=20)
        if p.returncode:raise RuntimeError(p.stderr[-3000:])
        r=json.loads(p.stdout.strip().splitlines()[-1]);rows.append(r);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('SAFE',s,r['success'],r['selected_size'],r['wall_s'],flush=True)
    (ROOT/'protocol.json').write_text(json.dumps({'seeds':[42,61,62,63],'pool_source':'../expansion/*-pool.json','seconds_per_solve':6,'scope':'fixed-pool development diagnosis; LP and exact dual repair included in solve budget','source_sha256':hashlib.sha256(pathlib.Path('/content/selection_core_colab.py').read_bytes()).hexdigest()},indent=2))
    summary={'solved':sum(r['success'] for r in rows),'runs':4};(ROOT/'summary.json').write_text(json.dumps(summary,indent=2));print('SAFE_COMPLETE',json.dumps(summary))
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':
        s=int(sys.argv[2]);pool=json.loads((a.ROOT/'expansion'/f'{s}-pool.json').read_text());r,cert=a.solve(pool['pats'],pool['items'],1000,1000,3,'safe',6,s);r['seed']=s
        if cert is not None:(ROOT/f'{s}-certificate.json').write_text(json.dumps(cert))
        print(json.dumps(r))
    else:main()
