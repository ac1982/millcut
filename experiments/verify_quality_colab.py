"""Independent exported-certificate checks; executed only on Colab."""
import pathlib,json,csv,collections
root=pathlib.Path('/content/portfolio-study');report={}
for study in ('quality-holdout','quality-transfer'):
    folder=root/study
    if not (folder/'summary.json').exists():continue
    data=folder/'data' if study=='quality-holdout' else root/'berkey1987'
    checks=[]
    for path in (folder/'results').glob('*-strips.json'):
        name=path.name.split('_strips_')[0]
        with (data/f'{name}_items.csv').open() as f:items=[(int(r['WIDTH']),int(r['HEIGHT'])) for r in csv.DictReader(f)]
        with (data/f'{name}_bins.csv').open() as f:b=next(csv.DictReader(f));W,H=int(b['WIDTH']),int(b['HEIGHT'])
        layout=json.loads(path.read_text());counts=collections.Counter()
        for sheet in layout:
            ymax=0;rects=[]
            for strip in sheet:
                assert strip['y']>=ymax;assert strip['height']>0
                ymax=strip['y']+strip['height'];assert ymax<=H
                xmax=0
                for r in strip['items']:
                    i=r['id'];assert (r['w'],r['h'])==items[i];counts[i]+=1
                    assert r['x']>=xmax and r['y']==strip['y'];assert 0<r['h']<=strip['height']
                    xmax=r['x']+r['w'];assert xmax<=W;rects.append(r)
            for i,a in enumerate(rects):
                for b in rects[i+1:]:assert a['x']+a['w']<=b['x'] or b['x']+b['w']<=a['x'] or a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y']
        assert counts==collections.Counter(range(len(items)))
        checks.append({'file':path.name,'bins':len(layout),'valid':True})
    report[study]={'exported_strip_certificates':checks,'checks':'exact item dimensions and multiplicities, bounds, pairwise nonoverlap, horizontal strips/vertical columns/horizontal item trim (three-stage guillotine)'}
    summary=json.loads((folder/'summary.json').read_text())
    a,b=summary['default'],summary['strips']
    report[study]['sheet_reduction_fraction']=1-b['total_bins']/a['total_bins']
    if 'total_waste_area' in a:report[study]['waste_reduction_fraction']=1-b['total_waste_area']/a['total_waste_area']
(root/'quality-certificate-checks.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
