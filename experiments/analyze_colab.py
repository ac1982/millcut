import json, pathlib, collections, shutil
root=pathlib.Path('/content/cut-research')
rows=json.loads((root/'baseline/results.json').read_text())
report={}
for budget in (1,5):
    rr=[r for r in rows if r['budget_s']==budget]
    report[str(budget)]={'runs':len(rr),'valid':sum(r['valid'] for r in rr),'matching_solver_bound':sum(r['valid'] and r['bins']==r['solver_lower_bound'] for r in rr),'mean_waste_fraction':sum(r['full_sheet_waste_fraction'] for r in rr)/len(rr),'mean_wall_s':sum(r['wall_s'] for r in rr)/len(rr),'max_wall_s':max(r['wall_s'] for r in rr),'unresolved':sorted({r['instance'] for r in rr if r['bins']>r['solver_lower_bound']})}
(root/'baseline/analysis.json').write_text(json.dumps(report,indent=2))
shutil.make_archive(str(root/'baseline'),'zip',root/'baseline')
print(json.dumps(report,indent=2))
