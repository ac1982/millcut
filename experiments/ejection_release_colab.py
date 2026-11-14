"""Jointly choose which initial stacks to unpack, and relocate their pieces."""
import sys,json,csv
sys.path.insert(0,'/content');import ejection_refine_colab as ef
# A retained-column model is infeasible by area (220 > 200), but releasing
# the two mixed-width stacks yields two legal sheets (200 area exactly).
check='ejection_check_release';dims=[(6,5),(4,5),(6,5),(4,5),(10,10)]
(ef.er.nr.DATA/f'{check}_items.csv').write_text('ID,WIDTH,HEIGHT\n'+''.join(f'{i},{w},{h}\n' for i,(w,h) in enumerate(dims)))
(ef.er.nr.DATA/f'{check}_bins.csv').write_text('ID,WIDTH,HEIGHT\n0,10,10\n')
state=[dict(bin=0,columns=[[0,1]]),dict(bin=1,columns=[[2,3]]),dict(bin=2,columns=[[4]])]
a=ef.solve_columns(check,state,2,3,5,'fixed-check')
b=ef.solve_columns(check,state,2,3,5,'release-check',release=True)
assert a['status']=='INFEASIBLE' and b['success'] and b['released_subtrees']==2
(ef.OUT/'release-checks.json').write_text(json.dumps(dict(fixed_columns_proven_infeasible=True,selective_release_native_valid=True,released_subtrees=2),indent=2))
jobs=[('planted_3_5_6_1','initial_state'),('Class_03.2bp_100_2','best_state')]
(ef.OUT/'release-protocol.json').write_text(json.dumps(dict(jobs=jobs,budget_s=90,operator='A retained multi-item column can either stay whole or be split into its individual items; these alternatives are exclusive and cover each original item once. CP jointly chooses releases, row membership, row heights and sheet membership.',scope='Selected offline follow-up, not unseen or equal-time result'),indent=2))
rows=[]
for name,source in jobs:
    data=json.loads((ef.OUT/f'{name}_long-anneal.json').read_text());R=max(len(data['initial_state']),len(data['best_state']))
    r=ef.solve_columns(name,data[source],data['target_bins'],R,90,'cp-selective-release',release=True);r['source_state']=source
    rows.append(r);(ef.OUT/'release-summary.json').write_text(json.dumps(rows,indent=2));print('SELECTIVE_RELEASE',json.dumps(r),flush=True)
