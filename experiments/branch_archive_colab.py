"""Save compact research evidence and source hashes; Colab only."""
import pathlib,json,hashlib,zipfile,shutil,sys,subprocess,csv
sys.path.insert(0,'/content');import branch_collect_colab as bc
root=bc.ROOT;out=bc.OUT
source=out/'source';source.mkdir(exist_ok=True)
for p in pathlib.Path('/content').glob('branch_*colab.py'):shutil.copy(p,source/p.name)
shutil.copy(root/'build-src/branch_labels.cpp',source/'branch_labels.cpp')
shutil.copy(root/'build-src/branch_policy.cpp',source/'branch_policy.cpp')
shutil.copy('/content/native_patch_colab.py',source/'native_patch_colab.py')
shutil.copy(root/'build-src/native_scoring.hpp',source/'native_scoring.hpp')
shutil.copy(root/'build-src/CMakeLists.txt',source/'CMakeLists.txt')
shutil.copy(root/'environment.json',out/'environment.json')
rows=json.loads((out/'rows.json').read_text())
long=json.loads((out/'long-rows.json').read_text()) if (out/'long-rows.json').exists() else []
allrows=rows+long
for r in allrows:
    with (bc.nr.DATA/f"{r['instance']}_items.csv").open() as f:items=list(csv.DictReader(f))
    count=sum(int(i.get('COPIES',1)) for i in items)
    area=sum(int(i['WIDTH'])*int(i['HEIGHT'])*int(i.get('COPIES',1)) for i in items)
    assert len(r['remaining_items'])+r['child']['items']==count
    assert sum(i[1]*i[2] for i in r['remaining_items'])+r['child']['item_area']==area
audit=dict(solver_commit='28ac801df472e68736410411d70bfdb15f06e281',binary_sha256=hashlib.sha256((root/'build/branch_labels').read_bytes()).hexdigest(),recorded_rollouts=len(allrows),all_known=sum(r['bins'] is not None for r in allrows),all_completed_valid=all(r.get('independent_valid',False) and r['prefix_verified'] for r in allrows if r['bins'] is not None),total_teacher_search_seconds=sum(r['search_wall_s'] for r in allrows),total_process_seconds=sum(r['process_wall_s'] for r in allrows),input_sha256={str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for n in bc.CASES for p in (bc.nr.DATA/f'{n}_items.csv',bc.nr.DATA/f'{n}_bins.csv')})
(out/'audit.json').write_text(json.dumps(audit,indent=2))
audit['remaining_count_and_area_conservation']=True
audit['policy_binary_sha256']=hashlib.sha256((root/'build/branch_policy').read_bytes()).hexdigest()
online=json.loads((out/'online/rows.json').read_text())
references={r['instance']:r['bins'] for r in online if r['method']=='full_guide1'}
states={r['state_id']:r for r in rows};sampling=[]
for key,r in states.items():
    with (bc.nr.DATA/f"{r['instance']}_items.csv").open() as f:items=list(csv.DictReader(f))
    with (bc.nr.DATA/f"{r['instance']}_bins.csv").open() as f:bin_type=next(csv.DictReader(f))
    A=int(bin_type['WIDTH'])*int(bin_type['HEIGHT'])
    area=sum(int(i['WIDTH'])*int(i['HEIGHT'])*int(i.get('COPIES',1)) for i in items)
    lower=(area+r['parent']['waste']+A-1)//A
    sampling.append(dict(instance=r['instance'],depth=r['target_depth'],parent_lower_bound=lower,full_guide1_bins=references[r['instance']],prefix_already_worse_than_reference=lower>references[r['instance']]))
audit['sampling_states']=len(sampling)
audit['prefixes_proven_worse_than_full_guide1']=sum(s['prefix_already_worse_than_reference'] for s in sampling)
(out/'sampling-audit.json').write_text(json.dumps(sampling,indent=2))
(out/'audit.json').write_text(json.dumps(audit,indent=2))
hashes={}
with zipfile.ZipFile('/content/branch-label-results.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(out.rglob('*')):
        if p.is_file():
            rel=str(p.relative_to(out));z.write(p,rel);hashes[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
    z.writestr('artifact-sha256.json',json.dumps(hashes,indent=2))
with zipfile.ZipFile('/content/branch-label-binary.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name in ('branch_labels','branch_policy'):z.write(root/'build'/name,name)
print('BRANCH_ARCHIVED',json.dumps(audit),flush=True)
