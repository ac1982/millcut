"""Independent final-certificate audit and compact evidence archive, Colab only."""
import pathlib,json,csv,collections,hashlib,zipfile,shutil,subprocess,sys
ROOT=pathlib.Path('/content/portfolio-study/routing');DATA=ROOT/'data';OUT=ROOT/'results'
rows=json.loads((ROOT/'rows.json').read_text());checks=[]
regression=json.loads((ROOT/'known-regression.json').read_text())['rows'] if (ROOT/'known-regression.json').exists() else []
for r in rows+regression:
    name=r['instance'];path=OUT/r['certificate']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==r['certificate_sha256']
    with (DATA/f'{name}_items.csv').open() as f:items=list(csv.DictReader(f))
    with (DATA/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
    W,H=int(bt['WIDTH']),int(bt['HEIGHT']);rects=collections.defaultdict(list);counts=collections.Counter()
    if path.suffix=='.json':
        plates=json.loads(path.read_text());bins=len(plates)
        for b,plate in enumerate(plates):
            y=0
            for strip in plate:
                assert strip['y']==y;x=0
                for it in strip['items']:
                    assert it['x']==x and it['y']==y and it['h']<=strip['height']
                    rects[b].append((it['id'],x,y,it['w'],it['h'],1));x+=it['w']
                assert x<=W;y+=strip['height']
            assert y<=H
    else:
        with path.open() as f:ns=list(csv.DictReader(f))
        bins=sum(int(n['COPIES']) for n in ns if not n['PARENT'])
        for n in ns:
            if not n['PARENT'] or int(n['TYPE'])<0:continue
            rects[int(n['PLATE_ID'])].append(tuple(int(n[k]) for k in ('TYPE','X','Y','WIDTH','HEIGHT','COPIES')))
    for plate in rects.values():
        for a,(i,x,y,w,h,copies) in enumerate(plate):
            assert (w,h)==(int(items[i]['WIDTH']),int(items[i]['HEIGHT']))
            assert 0<=x<x+w<=W and 0<=y<y+h<=H;counts[i]+=copies
            for _,xx,yy,ww,hh,_ in plate[a+1:]:assert x+w<=xx or xx+ww<=x or y+h<=yy or yy+hh<=y
    assert counts==collections.Counter({i:int(it.get('COPIES',1)) for i,it in enumerate(items)})
    assert bins==r['bins'];checks.append(dict(instance=name,method=r['method'],seed=r['seed'],bins=bins,verified=True))
summary=json.loads((ROOT/'summary.json').read_text())
for control in ('default','guide1'):
    robust=[]
    for name in sorted({r['instance'] for r in rows}):
        rr=[r for r in rows if r['instance']==name and r['method']=='router']
        if all(r['bins']<next(b['bins'] for b in rows if b['instance']==name and b['seed']==r['seed'] and b['method']==control) for r in rr):robust.append(name)
    summary[control]['instances_improved_in_both_repeats']=robust
(ROOT/'summary.json').write_text(json.dumps(summary,indent=2))
(ROOT/'certificate-audit.json').write_text(json.dumps(dict(certificates=len(checks),all_valid=True,checks=checks),indent=2))
sources=ROOT/'source';sources.mkdir(exist_ok=True)
names=['routing_setup_colab.py','routing_core_colab.py','routing_prepare_colab.py','routing_evaluate_colab.py','routing_regression_colab.py','routing_audit_colab.py','strip_lp_colab.py','quality_colab.py','quality_planted_colab.py','pattern_pool_colab.py','portfolio_colab.py']
for name in names:
    p=pathlib.Path('/content',name)
    if p.exists():shutil.copy(p,sources/name)
(ROOT/'environment.json').write_text(json.dumps(dict(python=sys.version,cpu=subprocess.check_output(['lscpu'],text=True),solver_commit='28ac801df472e68736410411d70bfdb15f06e281'),indent=2))
hashes={}
with zipfile.ZipFile('/content/routing-results.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ROOT.rglob('*')):
        if p.is_file():
            rel=str(p.relative_to(ROOT));z.write(p,rel);hashes[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
    z.writestr('artifact-sha256.json',json.dumps(hashes,indent=2))
print('ROUTING_AUDIT',json.dumps(dict(certificates=len(checks),all_valid=True,robust_wins={k:summary[k]['instances_improved_in_both_repeats'] for k in ('default','guide1')})),flush=True)
