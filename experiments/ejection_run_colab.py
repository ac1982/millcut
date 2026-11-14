"""Colab-only concrete cut-tree ejection operator pilot."""
import sys,pathlib,json,csv,subprocess,time,zipfile,random,hashlib
sys.path.insert(0,'/content');import native_run_colab as nr
ROOT=nr.ROOT;OUT=ROOT/'ejection';OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT/'python-original'))

def baseline(name,budget,guide,tag):
    import packingsolver.rectangleguillotine as ps
    start=time.perf_counter();b=ps.InstanceBuilder();b.read_item_types(str(nr.DATA/f'{name}_items.csv'));b.read_bin_types(str(nr.DATA/f'{name}_bins.csv'));b.set_bin_types_infinite_copies();b.set_objective(ps.Objective.BinPacking);b.set_number_of_stages(3);b.set_cut_type(ps.CutType.NonExact);b.set_first_stage_orientation(ps.CutOrientation.Horizontal);b.set_item_types_oriented(True);b.set_cut_thickness(0);inst=b.build()
    p=ps.OptimizeParameters();p.verbosity_level=0;p.time_limit=max(.001,budget-(time.perf_counter()-start))
    if guide is not None:p.use_tree_search=True;p.tree_search_guides=guide
    out=ps.optimize(inst,p);assert out.solution.full() and out.solution.feasible()
    cert=OUT/f'{name}_{tag}.csv';out.solution.write(str(cert));nr.validate(name,cert)
    return dict(instance=name,method=tag,bins=out.solution.number_of_bins(),bound=out.bin_packing_bound,wall_s=time.perf_counter()-start,budget_s=budget,certificate=cert.name)

def attempt(name,initial,seconds,seed,mode,tag):
    cert=OUT/f'{name}_{tag}.csv';report=OUT/f'{name}_{tag}.json'
    start=time.perf_counter();p=subprocess.run([str(ROOT/'build/ejection'),str(nr.DATA/f'{name}_items.csv'),str(nr.DATA/f'{name}_bins.csv'),str(OUT/initial),str(seconds),str(seed),mode,str(cert),str(report)],capture_output=True,text=True,timeout=seconds+20)
    if p.returncode:raise RuntimeError(p.stderr[-3000:]+p.stdout[-500:])
    row=json.loads(report.read_text());row.update(instance=name,method=tag,seed=seed,budget_s=seconds,initial_certificate=initial,process_wall_s=time.perf_counter()-start)
    if row['success']:nr.validate(name,cert);row['certificate']=cert.name;row['certificate_sha256']=hashlib.sha256(cert.read_bytes()).hexdigest()
    else:row['certificate']=initial
    row['independent_valid']=True;report.write_text(json.dumps(row,indent=2));return row

def checks():
    # Includes unequal-width pieces within a retained stack: requires the
    # allowed non-exact fourth-stage trim, not an unconstrained rectangle pack.
    for name,dims,expect in [('ejection_check_stack',[(5,3),(4,2),(5,4),(3,1)],True),('ejection_check_impossible',[(6,6),(6,6)],False)]:
        with (nr.DATA/f'{name}_items.csv').open('w') as f:
            f.write('ID,WIDTH,HEIGHT\n');f.writelines(f'{i},{w},{h}\n' for i,(w,h) in enumerate(dims))
        (nr.DATA/f'{name}_bins.csv').write_text('ID,WIDTH,HEIGHT\n0,10,10\n')
        records=[];nid=0
        # Two initial sheets, one column per sheet, with stacked items.
        for bid,ids in enumerate(([0,1],[2,3]) if expect else ([0],[1])):
            root=nid;records.append([bid,1,nid,0,0,10,10,0,0,'']);nid+=1
            height=sum(dims[i][1] for i in ids);width=max(dims[i][0] for i in ids)
            row=nid;records.append([bid,1,nid,0,0,10,height,-2,1,root]);nid+=1
            col=nid;records.append([bid,1,nid,0,0,width,height,-2,2,row]);nid+=1;y=0
            for i in ids:
                w,h=dims[i];cell=nid;records.append([bid,1,nid,0,y,width,h,i if w==width else -2,3,col]);nid+=1
                if w<width:records.append([bid,1,nid,0,y,w,h,i,4,cell]);nid+=1
                y+=h
        cert=OUT/f'{name}_initial.csv'
        with cert.open('w') as f:
            wr=csv.writer(f);wr.writerow(['PLATE_ID','COPIES','NODE_ID','X','Y','WIDTH','HEIGHT','TYPE','CUT','PARENT']);wr.writerows(records)
        r=attempt(name,cert.name,.2,1739,'anneal','check');assert r['success']==expect,(name,r)
    (OUT/'checks.json').write_text(json.dumps(dict(retained_stack_and_nonexact_trim_valid=True,impossible_two_6x6_not_reported_feasible=True),indent=2))

def main():
    with zipfile.ZipFile('/content/compiled-package.zip') as z:z.extractall(ROOT/'python-original')
    checks()
    names=['planted_3_3_5_0','planted_3_5_6_1','Class_03.2bp_100_2','Class_05.2bp_100_2']
    protocol=dict(instances=names,initial='original full optimize guide1, 10 seconds',operator_budget_s=30,methods=['anneal','monotone'],scope='Development mechanism screen with additional offline operator budget, NOT equal-time superiority claim',moves=['whole column relocation','item ejection into stack','item release into new column','column swap','column merge','column split','whole row relocation','item swap'],acceptance='Temporary width/height infeasibility and uphill moves allowed only internally; final full native SolutionBuilder feasibility and independent certificate checks mandatory',seed=1739)
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2));initials=[]
    for name in names:
        r=baseline(name,10,[1],'initial');initials.append(r);(OUT/'initials.json').write_text(json.dumps(initials,indent=2));print('INITIAL',name,r['bins'],r['bound'],flush=True)
    jobs=[(r,m) for r in initials if r['bins']>r['bound'] for m in ('anneal','monotone')];random.Random(1739).shuffle(jobs);rows=[]
    for base,mode in jobs:
        r=attempt(base['instance'],base['certificate'],30,1739,mode,mode);rows.append(r);(OUT/'rows.json').write_text(json.dumps(rows,indent=2));print('EJECTION',r['instance'],mode,r['initial_bins'],r['bins'],r['iterations'],r['best_violation_area'],flush=True)
    summary={m:dict(runs=sum(r['mode']==m for r in rows),successes=sum(r['success'] for r in rows if r['mode']==m),results=[{k:r[k] for k in ('instance','initial_bins','bins','best_violation_area','iterations','accepted_uphill')} for r in rows if r['mode']==m]) for m in ('anneal','monotone')}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));print('EJECTION_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':main()
