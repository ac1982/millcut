import pathlib,hashlib,json,shutil,sys,importlib.metadata
root=pathlib.Path('/content/portfolio-study/selection');src=root/'source';src.mkdir(exist_ok=True)
for name in ['selection_core_colab.py','selection_core_v1_colab.py','selection_core_v2_colab.py','selection_screen_colab.py','selection_expand_colab.py','selection_safe_colab.py']:
    shutil.copy('/content/'+name,src/name)
for seed in (61,62):
    a=root/'screen'/f'planted_3_5_6_1_{seed}-pool.json';b=root/'expansion'/f'{seed}-pool.json';assert a.read_bytes()==b.read_bytes()
report={'python':sys.version,'versions':{p:importlib.metadata.version(p) for p in ['ortools','numpy','scipy']},'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()},'shared_pool_hash_checks_passed':True,'scope':'fixed-pool development diagnostics; not end-to-end PackingSolver superiority evidence','studies':{s:json.loads((root/s/'summary.json').read_text()) for s in ['screen','expansion','safe']}}
(root/'final-summary.json').write_text(json.dumps(report,indent=2));shutil.make_archive('/content/selection-results','zip',root);print(json.dumps(report['studies'],indent=2))
