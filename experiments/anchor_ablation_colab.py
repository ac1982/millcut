"""Colab-only single-decision refinement: force an item at chosen shelf height."""
import sys,json,random,subprocess,hashlib,pathlib,shutil,collections
sys.path.insert(0,'/content');import anchor_strips_colab as alg
from detail_ablation_colab import validate
ROOT=alg.ROOT;NAMES=['planted_3_3_5_0','planted_3_5_6_1'];SEEDS=[51,52,53,54,55]

def worker(n,m,s):
    r=alg.run(n,m,s,10);p=alg.OUT/f'{n}_{m}_{s}_10-strips.json'
    if p.exists():
        inst=alg.q.make(n);items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())];validate(json.loads(p.read_text()),items,inst.bin_type(0).w,inst.bin_type(0).h);r['independent_certificate_valid']=True
    return r

def main():
    (ROOT/'protocol.json').write_text(json.dumps({'instances':NAMES,'seeds':SEEDS,'budget_s':10,'split':'previously used synthetic development cases only','single_factor':'force one max-height item into each candidate shelf, then same width subset-sum, LP ranking and CP-SAT master','source_sha256':hashlib.sha256(pathlib.Path('/content/anchor_strips_colab.py').read_bytes()).hexdigest()},indent=2))
    jobs=[(n,m,s) for n in NAMES for m in ('shelves','anchored') for s in SEEDS];random.Random(902).shuffle(jobs);rows=[]
    for n,m,s in jobs:
        proc=subprocess.run([sys.executable,__file__,'worker',n,m,str(s)],capture_output=True,text=True,timeout=30)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        r=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(r);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('ANCHOR',len(rows),len(jobs),json.dumps(r),flush=True)
    summary={};diff=[]
    for n in NAMES:
        ss={m:[r['bins'] for r in sorted(rows,key=lambda r:r['seed']) if r['instance']==n and r['method']==m] for m in ('shelves','anchored')};ss['wins']=sum(a<b for a,b in zip(ss['anchored'],ss['shelves']));ss['losses']=sum(a>b for a,b in zip(ss['anchored'],ss['shelves']));summary[n]=ss
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(alg.q.pj.ROOT/'anchor-results'),'zip',ROOT);print('ANCHOR_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(worker(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
