import sys,pathlib,json,shutil,runpy,subprocess
sys.path.insert(0,'/content');import native_run_colab as nr
root=nr.ROOT;old=root/'v1';old.mkdir(exist_ok=True)
shutil.copytree(nr.OUT,old/'results',dirs_exist_ok=True)
shutil.copy(root/'build/native_patched',old/'native_patched')
shutil.copytree(root/'patched',old/'patched',dirs_exist_ok=True)
checks=[]
for name in ['Class_02.2bp_60_2','planted_3_3_5_0']:
    rr=[nr.run(name,c,30,2,'deterministic',128) for c in ['native01','smooth']];checks.append({'instance':name,'same_certificate':rr[0]['certificate_sha256']==rr[1]['certificate_sha256'],'rows':rr})
(old/'scaling-diagnostic.json').write_text(json.dumps(checks,indent=2));print('V1_SCALING',json.dumps(checks),flush=True)
runpy.run_path('/content/native_patch_v2_colab.py',run_name='__main__')
p=subprocess.run(['cmake','--build',str(root/'build'),'--target','native_patched','-j2'],capture_output=True,text=True);print(p.stdout[-2000:]+p.stderr[-1000:],flush=True);p.check_returncode()
checks=[]
for name in ['Class_02.2bp_60_2','Class_03.2bp_100_2','Class_04.2bp_100_1','planted_3_3_5_0']:
    rr=[nr.run(name,c,30,3,'deterministic',128) for c in ['native01','smooth']];assert rr[0]['certificate_sha256']==rr[1]['certificate_sha256'],rr;checks.append({'instance':name,'same_certificate':True,'rows':rr})
(root/'v2-parity.json').write_text(json.dumps(checks,indent=2));print('V2_ZERO_CORRECTION_PARITY_PASS',len(checks),flush=True)
