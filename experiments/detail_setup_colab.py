import pathlib,tarfile,shutil,sys,subprocess
with tarfile.open('/content/inputs.tar.gz') as t:t.extractall('/content/detail-inputs',filter='data')
for p in pathlib.Path('/content/detail-inputs/experiments').glob('*.py'):shutil.copy(p,'/content/'+p.name)
sys.path.insert(0,'/content');import portfolio_colab as pj
pj.setup()
shutil.copytree('/content/detail-inputs/results/planted-quality/data',pj.DATA,dirs_exist_ok=True)
(pj.ROOT/'planted-quality').mkdir(exist_ok=True)
p=subprocess.run([sys.executable,'-m','pip','install','ortools==9.15.6755','scipy'],capture_output=True,text=True);assert p.returncode==0,p.stderr
print('READY',sys.version)
