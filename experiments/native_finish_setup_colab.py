import subprocess,pathlib,tarfile,shutil,json,sys
root=pathlib.Path('/content/native-study')
for args in [['cmake','-S',str(root/'build-src'),'-B',str(root/'build'),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(root/'build'),'--target','native_baseline','native_patched','-j2']]:
    p=subprocess.run(args,capture_output=True,text=True);print(p.stdout[-3000:]+p.stderr[-1000:],flush=True);p.check_returncode()
with tarfile.open('/content/berkey-data.tar.gz') as t:t.extractall(root,filter='data')
shutil.copytree(root/'inputs/results/planted-quality/data',root/'berkey1987',dirs_exist_ok=True)
(root/'environment.json').write_text(json.dumps({'python':sys.version,'cpu':subprocess.check_output(['lscpu'],text=True),'commit':'28ac801df472e68736410411d70bfdb15f06e281','scope':'direct tree search native harness, no optimize/reduction/postprocess portfolio'},indent=2));print('NATIVE_BUILD_COMPLETE')
