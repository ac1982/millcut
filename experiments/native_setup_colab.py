"""Build native scoring harness in Colab; shared unchanged geometry, two search binaries."""
import pathlib,subprocess,sys,tarfile,json,shutil,runpy,os
root=pathlib.Path('/content/native-study');root.mkdir(exist_ok=True)
with tarfile.open('/content/native-inputs.tar.gz') as t:t.extractall(root/'inputs',filter='data')
shutil.copytree(root/'inputs/experiments/native',root/'build-src',dirs_exist_ok=True)
for p in (root/'inputs/experiments').glob('native_*colab.py'):shutil.copy(p,'/content/'+p.name)
def run(args):
    p=subprocess.run(args,capture_output=True,text=True)
    print('CMD',args[0],args[1:4],p.returncode,p.stdout[-1200:],p.stderr[-1500:],flush=True);p.check_returncode()
run([sys.executable,'-m','pip','install','cmake>=3.28','ninja'])
repos={'pristine':('packingsolver','28ac801df472e68736410411d70bfdb15f06e281'),'deps/treesearchsolver':('treesearchsolver','b7254420989dd804144d16b898f323f1d606dda4'),'deps/columngenerationsolver':('columngenerationsolver','2074c57a4b23ec92e9adb9425c98ac8225755b36')}
for dest,(name,commit) in repos.items():
    path=root/dest
    if not path.exists():run(['git','clone','https://github.com/fontanf/'+name+'.git',str(path)])
    run(['git','-C',str(path),'checkout',commit])
runpy.run_path('/content/native_patch_colab.py',run_name='__main__')
run(['cmake','-S',str(root/'build-src'),'-B',str(root/'build'),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release'])
run(['cmake','--build',str(root/'build'),'--target','native_baseline','native_patched','-j2'])
with tarfile.open('/content/berkey-data.tar.gz') as t:t.extractall(root,filter='data')
shutil.copytree(root/'inputs/results/planted-quality/data',root/'berkey1987',dirs_exist_ok=True)
(root/'environment.json').write_text(json.dumps({'python':sys.version,'cpu':subprocess.check_output(['lscpu'],text=True),'repositories':repos,'scope':'direct native tree_search harness; not full optimize portfolio; no LP backend'},indent=2));print('NATIVE_BUILD_COMPLETE',flush=True)
