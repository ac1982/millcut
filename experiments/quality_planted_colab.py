"""Colab-only quality benchmark with hidden constructive optimum witnesses."""
import sys,pathlib,csv,json,random,math,time,subprocess,shutil,hashlib
sys.path.insert(0,'/content');import quality_colab as q
ROOT=q.pj.ROOT/'planted-quality';DATA=ROOT/'data';OUT=ROOT/'results'
DATA.mkdir(parents=True,exist_ok=True);OUT.mkdir(exist_ok=True)
q.pj.DATA=DATA;q.OUT=OUT

def partition(total,n,rng):
    weights=[rng.uniform(.3,1.7) for _ in range(n)];s=sum(weights);a=[int(total*w/s) for w in weights];a[-1]+=total-sum(a);return a

def generate(seed=55001,repeats=3):
    rng=random.Random(seed);manifest=[]
    for nr,nc in ((3,5),(5,6)):
        for rep in range(repeats):
            name=f'planted_3_{nr}_{nc}_{rep}';raw=[];witness=[]
            for plate in range(3):
                y=0
                for height in partition(1000,nr,rng):
                    x=0;widths=partition(1000,nc,rng)
                    for col,w in enumerate(widths):
                        h=height if col==0 else height-rng.randint(0,max(1,height//10))
                        witness.append({'plate':plate,'id':len(raw),'x':x,'y':y,'w':w,'h':h,'strip_height':height})
                        raw.append((w,h));x+=w
                    assert x==1000;y+=height
                assert y==1000
            perm=list(range(len(raw)));rng.shuffle(perm);mapping={old:new for new,old in enumerate(perm)}
            with (DATA/f'{name}_items.csv').open('w') as f:
                wr=csv.writer(f);wr.writerow(['ID','WIDTH','HEIGHT']);wr.writerows((i,*raw[old]) for i,old in enumerate(perm))
            with (DATA/f'{name}_bins.csv').open('w') as f:f.write('ID,WIDTH,HEIGHT\n0,1000,1000\n')
            for it in witness:it['id']=mapping[it['id']]
            assert len({i['id'] for i in witness})==len(raw)
            for a in witness:
                assert 0<=a['x']<a['x']+a['w']<=1000 and 0<=a['y']<a['y']+a['h']<=1000
                for b in witness:
                    if a['id']>=b['id'] or a['plate']!=b['plate']:continue
                    assert a['x']+a['w']<=b['x'] or b['x']+b['w']<=a['x'] or a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y']
            assert math.ceil(sum(w*h for w,h in raw)/1000000)==3
            (ROOT/f'{name}-witness.json').write_text(json.dumps(witness))
            manifest.append({'name':name,'items':len(raw),'known_optimum':3,'proof':'constructive 3-sheet shelf layout and area lower bound 3','sha256':hashlib.sha256((DATA/f'{name}_items.csv').read_bytes()).hexdigest()})
    return manifest

def main():
    manifest=generate();(ROOT/'protocol.json').write_text(json.dumps({'instances':manifest,'seed':55001,'split':'development calibration','budget_s':10,'methods':['default','cp_shelves','target'],'witness_access':'workers only read items/bins CSV; witness excluded from solver input','scope':'synthetic shelf-generated instances; optimum proven by area and construction; no claim of production representativeness'},indent=2))
    rows=[];jobs=[(m['name'],method) for m in manifest for method in ('default','cp_shelves','target')];random.Random(641).shuffle(jobs)
    for i,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/quality_planted_colab.py','worker',n,m,'1'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);row['optimum']=3;rows.append(row);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('PLANTED',i+1,len(jobs),json.dumps(row),flush=True)
    summary={m:{'total_bins':sum(r['bins'] for r in rows if r['method']==m),'optimal_instances':sum(r['bins']==3 for r in rows if r['method']==m),'runs':sum(r['method']==m for r in rows)} for m in ('default','cp_shelves','target')}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(q.pj.ROOT/'planted-quality-results'),'zip',ROOT);print('PLANTED_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(q.run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
