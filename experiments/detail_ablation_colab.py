"""Colab-only controlled development ablation: add equal-width stacks, unchanged LP/master."""
import sys,pathlib,json,random,subprocess,shutil,hashlib,collections,time
sys.path.insert(0,'/content');import detail_strips_colab as alg
ROOT=alg.ROOT
NAMES=['Class_04.2bp_100_1','Class_02.2bp_100_3','Class_03.2bp_100_2','Class_05.2bp_100_2','planted_3_3_5_0','planted_3_5_6_1']

def validate(layout,items,W,H):
    counts=collections.Counter()
    for sheet in layout:
        end_y=0;allnodes=[]
        for strip in sheet:
            assert strip['y']==end_y;end_y+=strip['height'];assert end_y<=H
            columns=collections.defaultdict(list)
            for p in strip['items']:
                i=p['id'];assert (p['w'],p['h'])==items[i];counts[i]+=1
                assert 0<=p['x']<p['x']+p['w']<=W
                assert strip['y']<=p['y']<p['y']+p['h']<=end_y
                columns[p['x']].append(p);allnodes.append(p)
            end_x=0
            for x,parts in sorted(columns.items()):
                assert x>=end_x;assert len({p['w'] for p in parts})==1;end_x=x+parts[0]['w'];y=strip['y']
                for p in sorted(parts,key=lambda p:p['y']):assert p['y']==y;y+=p['h']
                assert y<=end_y
        for j,a in enumerate(allnodes):
            for b in allnodes[j+1:]:assert a['x']+a['w']<=b['x'] or b['x']+b['w']<=a['x'] or a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y']
    assert counts==collections.Counter(range(len(items)))

def worker(n,m,s):
    row=alg.run(n,m,s,10)
    cert=alg.OUT/f'{n}_{m}_{s}_10-strips.json'
    if cert.exists():
        inst=alg.q.make(n);items=[(inst.item_type(i).w,inst.item_type(i).h) for i in range(inst.number_of_item_types())]
        validate(json.loads(cert.read_text()),items,inst.bin_type(0).w,inst.bin_type(0).h)
        row['independent_certificate_valid']=True
    return row

def main():
    # Validate both structural branches using a tiny instance whose stack fills a sheet.
    items=[(5,6),(5,4),(5,10)];found=False
    for s in range(100):
        p=alg.candidate(items,10,10,random.Random(s),True)
        if p and len(p['ids'])==3:
            validate([[{'y':0,'height':p['height'],'items':p['nodes']}]],items,10,10);found=True
    assert found
    frozen=hashlib.sha256(pathlib.Path('/content/detail_strips_colab.py').read_bytes()).hexdigest()
    (ROOT/'protocol.json').write_text(json.dumps({'instances':NAMES,'split':'all previously used development/regression cases; no claim of held-out evaluation','methods':['default','shelves','stacked'],'seeds':[41,42],'budget_s':10,'source_sha256':frozen,'single_factor':'50% of candidate attempts allow equal-width vertical stacks; LP ranking, master and schedule unchanged','timing':'input loading, initial solver, candidate generation, LP/master and in-run checks; imports and independent exported-certificate checks excluded','microcheck':'equal-width 6+4 stack beside height-10 item fits 10x10; independently verified','diagnostic_warning':'native reference certificate used only for diagnosis, never for candidate generation'},indent=2))
    jobs=[(n,m,s) for n in NAMES for m in ('default','shelves','stacked') for s in (41,42)];random.Random(901).shuffle(jobs);rows=[]
    for n,m,s in jobs:
        proc=subprocess.run([sys.executable,__file__,'worker',n,m,str(s)],capture_output=True,text=True,timeout=30)
        if proc.returncode:row={'instance':n,'method':m,'seed':s,'error':proc.stderr[-2500:]}
        else:row=json.loads(proc.stdout.strip().splitlines()[-1])
        rows.append(row);(ROOT/'rows.json').write_text(json.dumps(rows,indent=2));print('ABLATION',len(rows),len(jobs),json.dumps(row),flush=True)
    summary={}
    for m in ('default','shelves','stacked'):
        rr=[r for r in rows if r['method']==m and 'error' not in r];diff=[]
        for reference in ('default','shelves'):
            w=l=t=0
            for r in rr:
                b=next(b for b in rows if b['instance']==r['instance'] and b['seed']==r['seed'] and b['method']==reference)
                if 'error' in b:continue
                w+=r['bins']<b['bins'];l+=r['bins']>b['bins'];t+=r['bins']==b['bins']
                if r['bins']!=b['bins']:diff.append({'reference':reference,'instance':r['instance'],'seed':r['seed'],'baseline':b['bins'],'candidate':r['bins']})
            summary[f'{m}_vs_{reference}']={'wins':w,'losses':l,'ties':t}
        summary[m]={'runs':len(rr),'errors':12-len(rr),'total_bins':sum(r['bins'] for r in rr),'differences':diff}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(alg.q.pj.ROOT/'detail-results'),'zip',ROOT);print('ABLATION_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(worker(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
