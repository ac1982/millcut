"""10s end-to-end test of a fixed-width native search warm start; Colab only."""
import sys,pathlib,json,random,subprocess,time,zipfile,csv,math,shutil,hashlib
sys.path.insert(0,'/content');import native_run_colab as nr
ROOT=nr.ROOT;OUT=ROOT/'schedule';OUT.mkdir(exist_ok=True)
NAMES=['Class_02.2bp_60_2','Class_02.2bp_100_3','Class_03.2bp_100_2','Class_04.2bp_100_1','Class_05.2bp_100_2']

def run(name,method,rep):
    sys.path.insert(0,str(ROOT/'python-original'));import packingsolver.rectangleguillotine as ps
    nr.OUT=OUT;start=time.perf_counter();deadline=start+10;events=[];best=10**9;best_cert=None
    with (nr.DATA/f'{name}_items.csv').open() as f:items=list(csv.DictReader(f))
    with (nr.DATA/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
    lower=math.ceil(sum(int(r['WIDTH'])*int(r['HEIGHT'])*int(r.get('COPIES',1)) for r in items)/(int(bt['WIDTH'])*int(bt['HEIGHT'])))
    if method=='warm512':
        r=nr.run(name,'native01',min(3,deadline-time.perf_counter()),rep,'deterministic',512);events.append({'stage':'fixed512','bins':r['bins'],'wall_s':r['wall_s']});best=r['bins'];best_cert=OUT/f'{name}_native01_deterministic_{rep}.csv'
    if best>lower:
        b=ps.InstanceBuilder();b.read_item_types(str(nr.DATA/f'{name}_items.csv'));b.read_bin_types(str(nr.DATA/f'{name}_bins.csv'));b.set_bin_types_infinite_copies();b.set_objective(ps.Objective.BinPacking);b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_item_types_oriented(True);b.set_cut_thickness(0);inst=b.build();p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(.001,deadline-time.perf_counter())
        if method=='guide1':p.use_tree_search=True;p.tree_search_guides=[1]
        o=ps.optimize(inst,p);assert o.solution.full() and o.solution.feasible();events.append({'stage':'full_optimizer','bins':o.solution.number_of_bins()})
        if o.solution.number_of_bins()<best:
            best=o.solution.number_of_bins();best_cert=OUT/f'{name}_{method}_{rep}-full.csv';o.solution.write(str(best_cert))
    nr.validate(name,best_cert);wall=time.perf_counter()-start
    return {'instance':name,'method':method,'rep':rep,'bins':best,'area_bound':lower,'wall_s':wall,'budget_s':10,'valid':True,'certificate':best_cert.name,'events':events}

def main():
    with zipfile.ZipFile('/content/compiled-package.zip') as z:z.extractall(ROOT/'python-original')
    (OUT/'protocol.json').write_text(json.dumps({'instances':NAMES,'methods':['default','warm512'],'repeats':3,'budget_s':10,'fixed_search':'unmodified native tree_search with guide 0/1, deterministic widths 1 then 512, at most 3 seconds; then full original optimizer for remaining total time; retain best','scope':'development confirmation, selected after fixed-queue analysis; not new held-out results','timing':'includes all native launches, input loading, intermediate/final certificate serialization and validation; imports excluded for both methods','area_bound':'only return early if validated solution reaches area lower bound','binary_sha256':hashlib.sha256((ROOT/'build/native_baseline').read_bytes()).hexdigest()},indent=2))
    jobs=[(n,m,r) for n in NAMES for m in ('default','warm512') for r in (1,2,3)];random.Random(1732).shuffle(jobs);rows=[]
    for n,m,r in jobs:
        p=subprocess.run([sys.executable,__file__,'worker',n,m,str(r)],capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError(p.stderr[-3000:])
        row=json.loads(p.stdout.strip().splitlines()[-1]);rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('SCHEDULE',len(rows),len(jobs),n,m,r,row['bins'],flush=True)
    summary={}
    for m in ('default','warm512'):
        rr=[r for r in rows if r['method']==m];diff=[];w=l=t=0
        for r in rr:
            b=next(b for b in rows if b['instance']==r['instance'] and b['rep']==r['rep'] and b['method']=='default');w+=r['bins']<b['bins'];l+=r['bins']>b['bins'];t+=r['bins']==b['bins']
            if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'rep':r['rep'],'default':b['bins'],'candidate':r['bins'],'seconds':r['wall_s']})
        summary[m]={'runs':len(rr),'total_bins':sum(r['bins'] for r in rr),'wins':w,'losses':l,'ties':t,'differences':diff}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));print('SCHEDULE_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':print(json.dumps(run(sys.argv[2],sys.argv[3],int(sys.argv[4]))))
    else:main()
