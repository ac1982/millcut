"""Restore existing winning branch and historical development inputs in Colab."""
import pathlib,tarfile,zipfile,shutil,subprocess,sys,json,csv,math,collections
ROOT=pathlib.Path('/content/portfolio-study');ROOT.mkdir(exist_ok=True)
(ROOT/'planted-quality').mkdir(exist_ok=True)
with zipfile.ZipFile('/content/compiled-package.zip') as z:z.extractall(ROOT)
with tarfile.open('/content/berkey-data.tar.gz') as t:t.extractall(ROOT,filter='data')
with tarfile.open('/content/routing-inputs.tar.gz') as t:t.extractall('/content/routing-inputs',filter='data')
inp=pathlib.Path('/content/routing-inputs')
for p in (inp/'experiments').glob('*.py'):shutil.copy(p,'/content/'+p.name)
p=subprocess.run([sys.executable,'-m','pip','install','ortools==9.15.6755','scipy'],capture_output=True,text=True);print(p.stdout[-1000:],p.stderr[-1000:]);p.check_returncode()
OUT=ROOT/'routing';OUT.mkdir(exist_ok=True)
rows=[]
for family,data,names in (
    ('planted-dev',inp/'results/planted-quality/data',None),
    ('planted-previous-test',inp/'results/quality-holdout/data',None),
    ('berkey-previous-transfer',ROOT/'berkey1987',[f'Class_{i:02d}.2bp_100_1' for i in range(1,7)])):
    files=sorted(data.glob('*_items.csv')) if names is None else [data/f'{n}_items.csv' for n in names]
    for p in files:
        name=p.name.removesuffix('_items.csv')
        with p.open() as f:items=list(csv.DictReader(f))
        with (data/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
        W,H=int(bt['WIDTH']),int(bt['HEIGHT']);N=sum(int(i.get('COPIES',1)) for i in items)
        area=sum(int(i['WIDTH'])*int(i['HEIGHT'])*int(i.get('COPIES',1)) for i in items);lb=math.ceil(area/(W*H))
        rows.append(dict(instance=name,family=family,items=N,unique_width_fraction=len({int(i['WIDTH']) for i in items})/N,lower_bound_fill=area/(lb*W*H),area_lower_bound=lb))
(OUT/'historical-features.json').write_text(json.dumps(rows,indent=2))
summary={}
for family in sorted({r['family'] for r in rows}):
    rr=[r for r in rows if r['family']==family]
    summary[family]={k:[min(r[k] for r in rr),max(r[k] for r in rr)] for k in ('unique_width_fraction','lower_bound_fill')}
print('HISTORICAL_FEATURES',json.dumps(summary));print('TRANSFER_FEATURES',json.dumps([r for r in rows if r['family']=='berkey-previous-transfer']))
