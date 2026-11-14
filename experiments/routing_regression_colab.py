"""Previously known harmful-transfer case; excluded from fresh test totals."""
import sys,json,shutil,pathlib,subprocess
sys.path.insert(0,'/content');import routing_core_colab as rc
name='old_transfer_regression'
for suffix in ('items','bins'):shutil.copy(rc.alg.q.pj.ROOT/'berkey1987'/f'Class_04.2bp_100_1_{suffix}.csv',rc.DATA/f'{name}_{suffix}.csv')
f=rc.read_features(name);assert rc.choose(f)=='default'
rows=[]
for method in ('strips','default','router'):
    p=subprocess.run([sys.executable,'/content/routing_core_colab.py',name,method,'93'],capture_output=True,text=True,timeout=35)
    if p.returncode:raise RuntimeError(p.stderr[-2000:])
    r=json.loads(p.stdout.strip().splitlines()[-1]);rows.append(r);print('KNOWN_REGRESSION',method,r['bins'],r['branch'],flush=True)
(rc.ROOT/'known-regression.json').write_text(json.dumps(dict(scope='Previously seen Class_04.2bp_100_1; not a fresh test. Force specialist vs original automatic vs router, one repeat.',features=f,rows=rows),indent=2))
