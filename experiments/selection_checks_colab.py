import sys,json
sys.path.insert(0,'/content');import selection_core_colab as a
# Exact-cover aggregate height fits, but three height-6 strips cannot fit two height-10 sheets.
pats=[{'ids':[i],'height':6} for i in range(3)]
layout,status,events=a.compatible(pats,3,10,2,a.time.perf_counter()+2,1);assert layout is None
# Feasible counterpart: two height-6 strips on two sheets.
layout,status,events2=a.compatible(pats[:2],2,10,2,a.time.perf_counter()+2,1);assert layout is not None
(a.ROOT/'checks.json').write_text(json.dumps({'aggregate_height_not_sufficient':True,'feasible_partition_found':True,'infeasible_events':events,'feasible_events':events2},indent=2));print('CHECKS_PASS')
