"""Colab-only repeated 10s confirmation, including the original full optimizer."""
import sys,pathlib,json,subprocess,random,time,zipfile,hashlib
sys.path.insert(0,'/content');import native_run_colab as nr
ROOT=nr.ROOT;OUT=ROOT/'confirmation';OUT.mkdir(exist_ok=True)

def original(name,rep):
    sys.path.insert(0,str(ROOT/'python-original'));import packingsolver.rectangleguillotine as ps
    start=time.perf_counter();b=ps.InstanceBuilder();b.read_item_types(str(nr.DATA/f'{name}_items.csv'));b.read_bin_types(str(nr.DATA/f'{name}_bins.csv'));b.set_bin_types_infinite_copies();b.set_objective(ps.Objective.BinPacking);b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_item_types_oriented(True);b.set_cut_thickness(0);inst=b.build();p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(.001,10-(time.perf_counter()-start));o=ps.optimize(inst,p);wall=time.perf_counter()-start;assert o.solution.full() and o.solution.feasible();path=OUT/f'{name}_full_default_{rep}.csv';o.solution.write(str(path));nr.validate(name,path)
    return {'instance':name,'config':'full_default','rep':rep,'bins':o.solution.number_of_bins(),'wall_s':wall,'valid':True,'independent_valid':True,'bound':o.bin_packing_bound}

def main():
    configs=sys.argv[1].split(',');names=sys.argv[2].split(',');reps=int(sys.argv[3]) if len(sys.argv)>3 else 3
    with zipfile.ZipFile('/content/compiled-package.zip') as z:z.extractall(ROOT/'python-original')
    (OUT/'protocol.json').write_text(json.dumps({'configs':configs,'instances':names,'repeats':reps,'budget_s':10,'scope':'development confirmation; full_default is original optimized portfolio and candidate is native tree search only','binary_sha256':{b:hashlib.sha256((ROOT/'build'/b).read_bytes()).hexdigest() for b in ('native_baseline','native_patched')},'weights':nr.CONFIGS},indent=2))
    jobs=[(n,c,r) for n in names for c in configs for r in range(1,reps+1)];random.Random(1730).shuffle(jobs);rows=[]
    for n,c,r in jobs:
        start=time.perf_counter();p=subprocess.run([sys.executable,__file__,'worker',n,c,str(r)],capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError(p.stderr[-3000:])
        row=json.loads(p.stdout.strip().splitlines()[-1]);row['wrapper_wall_s']=time.perf_counter()-start;rows.append(row);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('CONFIRM',len(rows),len(jobs),n,c,r,row['bins'],flush=True)
    summary={}
    for c in configs:
        rr=[r for r in rows if r['config']==c];diff=[];w=l=t=0
        for r in rr:
            b=next(b for b in rows if b['instance']==r['instance'] and b['rep']==r['rep'] and b['config']=='full_default');w+=r['bins']<b['bins'];l+=r['bins']>b['bins'];t+=r['bins']==b['bins']
            if r['bins']!=b['bins']:diff.append({'instance':r['instance'],'rep':r['rep'],'baseline':b['bins'],'candidate':r['bins']})
        summary[c]={'total_bins':sum(r['bins'] for r in rr),'wins':w,'losses':l,'ties':t,'differences':diff}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));print('CONFIRM_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':
        n,c,r=sys.argv[2],sys.argv[3],int(sys.argv[4]);nr.OUT=OUT
        print(json.dumps(original(n,r) if c=='full_default' else nr.run(n,c,10,r)))
    else:main()
