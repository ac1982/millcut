"""Archive completed operator evidence, without retaining execution logs."""
import sys,pathlib,shutil,json,zipfile,hashlib,importlib.metadata
sys.path.insert(0,'/content');import ejection_run_colab as er
out=er.OUT;root=er.ROOT;src=out/'source';src.mkdir(exist_ok=True)
for p in pathlib.Path('/content').glob('ejection_*colab.py'):shutil.copy(p,src/p.name)
shutil.copy(root/'build-src/ejection.cpp',src/'ejection.cpp');shutil.copy(root/'build-src/CMakeLists.txt',src/'CMakeLists.txt');shutil.copy(root/'environment.json',out/'environment.json')
data=out/'data';data.mkdir(exist_ok=True)
names=[r['instance'] for r in json.loads((out/'initials.json').read_text())]
for name in names:
    for suffix in ('items','bins'):shutil.copy(er.nr.DATA/f'{name}_{suffix}.csv',data/f'{name}_{suffix}.csv')
for p in er.nr.DATA.glob('ejection_check*csv'):shutil.copy(p,data/p.name)
metadata=dict(solver_commit='28ac801df472e68736410411d70bfdb15f06e281',ortools=importlib.metadata.version('ortools'),binary_sha256=hashlib.sha256((root/'build/ejection').read_bytes()).hexdigest(),v1_binary_sha256=hashlib.sha256((out/'v1/ejection').read_bytes()).hexdigest(),scope='Offline development operator study; no frozen unseen end-to-end superiority claim')
(out/'artifact-metadata.json').write_text(json.dumps(metadata,indent=2))
hashes={}
with zipfile.ZipFile('/content/ejection-results.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.name!='ejection':
            rel=str(p.relative_to(out));z.write(p,rel);hashes[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
    z.writestr('artifact-sha256.json',json.dumps(hashes,indent=2))
with zipfile.ZipFile('/content/ejection-binaries.zip','w',zipfile.ZIP_DEFLATED) as z:
    z.write(root/'build/ejection','ejection');z.write(out/'v1/ejection','v1/ejection')
print('ARCHIVED',len(hashes),flush=True)
