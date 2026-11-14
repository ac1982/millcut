"""Offline evidence diagnosis, run in Colab; oracle certificates are not solver inputs."""
import sys,pathlib,csv,json,collections,time
sys.path.insert(0,'/content');import quality_colab as q
sys.path.insert(0,str(q.pj.ROOT));import packingsolver.rectangleguillotine as ps
q.ps=ps;ROOT=q.pj.ROOT/'detail';ROOT.mkdir(exist_ok=True)
p=pathlib.Path('/content/detail-inputs/results/quality-transfer/results/Class_04.2bp_100_1_default_31_10.csv')
rows=list(csv.DictReader(p.open()));nodes={(int(r['PLATE_ID']),int(r['NODE_ID'])):r for r in rows}
cols=collections.defaultdict(list);strips=collections.defaultdict(list)
for r in rows:
    if int(r['TYPE'])<0 or not r['PARENT']:continue
    if int(r['CUT'])<3:continue
    plate=int(r['PLATE_ID']);n=r
    while int(n['CUT'])>2:n=nodes[plate,int(n['PARENT'])]
    if int(n['CUT'])==2:cols[plate,int(n['NODE_ID'])].append(r)
    n=r
    while int(n['CUT'])>1:n=nodes[plate,int(n['PARENT'])]
    strips[plate,int(n['NODE_ID'])].append(r)
stacked=[c for c in cols.values() if len(c)>1]
report={'instance':'Class_04.2bp_100_1','reference_bins':len({r['PLATE_ID'] for r in rows}),'columns':len(cols),'stacked_columns':len(stacked),'items_in_stacked_columns':sum(map(len,stacked)),'max_items_per_column':max(map(len,cols.values())),'equal_width_stacked_columns':sum(len({int(r['WIDTH']) for r in c})==1 for c in stacked),'strips':len(strips),'interpretation':'specific native 3-bin certificate cannot be represented by one-item-per-column shelves; this does not prove no alternative 3-bin shelf layout exists'}
inst=q.make(report['instance']);start=time.perf_counter();cert,status=q.cp_shelves(inst,3,start+30,41)
report['full_shelf_model_diagnostic']={'status':status,'seconds':time.perf_counter()-start,'found_three_bins':cert is not None,'scope':'30s diagnostic, not a matched-budget benchmark; UNKNOWN is not infeasible'}
(ROOT/'diagnosis.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
