"""Colab-only check after transformer_colab.py in the same kernel."""
import json, pathlib, shutil
import torch
root=pathlib.Path('/content/search-study')
x,m=features(['Class_01.2bp_60_2'])
perm=torch.randperm(x.shape[1])
checks=[]
for model in models:
    model.eval()
    with torch.no_grad():
        expected=model(x,m)
        actual=model(x[:,perm],m[:,perm])
        assert torch.isfinite(expected).all()
        assert torch.allclose(expected,actual,atol=1e-5,rtol=1e-5)
        changed=x.clone();changed[m]=123
        assert torch.allclose(expected,model(changed,m),atol=1e-5,rtol=1e-5)
        checks.append({'finite':True,'permutation_invariant':True,'padding_invariant':True})
(root/'transformer/model-checks.json').write_text(json.dumps(checks,indent=2))
rr=json.loads((root/'transformer/rows.json').read_text())
losses=[]
for r in rr:
    if r['method']!='transformer':continue
    b=next(b for b in rr if b['method']=='default' and b['instance']==r['instance'] and b['repeat']==r['repeat'])
    if r['bins']>b['bins']:losses.append({'instance':r['instance'],'config':r['config'],'repeat':r['repeat'],'baseline':b['bins'],'transformer':r['bins']})
(root/'transformer/losses.json').write_text(json.dumps(losses,indent=2))
shutil.make_archive(str(root/'transformer-results'),'zip',root/'transformer')
print('MODEL_CHECKS_PASS',checks,'LOSSES',losses)
