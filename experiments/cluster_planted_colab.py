"""Colab-only geometric pruning of shelf CP, on development planted instances."""
import sys,pathlib,json,random,subprocess,shutil
sys.path.insert(0,'/content');import cluster_model_colab as cm
ROOT=cm.pj.ROOT/'planted-quality';cm.pj.DATA=ROOT/'data';cm.OUT=ROOT/'cluster-results';cm.OUT.mkdir(exist_ok=True)

def main():
    names=[r['name'] for r in json.loads((ROOT/'protocol.json').read_text())['instances']]
    (cm.OUT/'protocol.json').write_text(json.dumps({'instances':names,'budget_s':10,'methods':['default','cp_shelves'],'change':'only group items into a shelf if their height is at least 85% of leader height; reduces assignment variables; no witness information used','scope':'development hypothesis tested on constructed shelf instances; restricted model infeasibility is not global infeasibility'},indent=2))
    rows=[];jobs=[(n,m) for n in names for m in ('default','cp_shelves')];random.Random(801).shuffle(jobs)
    for i,(n,m) in enumerate(jobs):
        proc=subprocess.run([sys.executable,'/content/cluster_planted_colab.py','worker',n,m,'2'],capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:])
        row=json.loads(proc.stdout.strip().splitlines()[-1]);rows.append(row);(cm.OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('CLUSTER',i+1,len(jobs),json.dumps(row),flush=True)
    summary={m:{'total_bins':sum(r['bins'] for r in rows if r['method']==m),'optimal_instances':sum(r['bins']==3 for r in rows if r['method']==m),'runs':sum(r['method']==m for r in rows)} for m in ('default','cp_shelves')}
    (cm.OUT/'summary.json').write_text(json.dumps(summary,indent=2));shutil.make_archive(str(cm.pj.ROOT/'cluster-results'),'zip',cm.OUT);print('CLUSTER_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(cm.run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
