"""Joint exact relocation of retained columns, after cross-sheet ejection. Colab only."""
import sys,pathlib,json,csv,time,subprocess,hashlib,collections
sys.path.insert(0,'/content');import ejection_run_colab as er
from ortools.sat.python import cp_model
OUT=er.OUT

def solve_columns(name,state,target,row_count,seconds,tag,release=False):
    start=time.perf_counter()
    with (er.nr.DATA/f'{name}_items.csv').open() as f:items=list(csv.DictReader(f))
    with (er.nr.DATA/f'{name}_bins.csv').open() as f:bt=next(csv.DictReader(f))
    W,H=int(bt['WIDTH']),int(bt['HEIGHT']);dims=[(int(i['WIDTH']),int(i['HEIGHT'])) for i in items]
    state=sorted(state,key=lambda r:-max(sum(dims[i][1] for i in c) for c in r['columns']))
    cols=[dict(ids=c,w=max(dims[i][0] for i in c),h=sum(dims[i][1] for i in c),row=r) for r,row in enumerate(state) for c in row['columns']]
    assert collections.Counter(i for c in cols for i in c['ids'])==collections.Counter({i:int(it.get('COPIES',1)) for i,it in enumerate(items)})
    original_count=len(cols);source_area=sum(c['w']*c['h'] for c in cols)
    if release:
        for parent in range(original_count):
            if len(cols[parent]['ids'])>1:
                for i in cols[parent]['ids']:cols.append(dict(ids=[i],w=dims[i][0],h=dims[i][1],row=cols[parent]['row'],parent=parent))
    N=len(cols);R=min(row_count,N);m=cp_model.CpModel();x={};z={};heights=[];loads=[]
    keep={c:m.new_bool_var(f'keep{c}') for c in range(original_count) if release and len(cols[c]['ids'])>1}
    for c in range(N):
        for r in range(R):x[c,r]=m.new_bool_var(f'x{c}_{r}')
        if c in keep:m.add(sum(x[c,r] for r in range(R))==keep[c])
        elif 'parent' in cols[c]:m.add(sum(x[c,r] for r in range(R))==1-keep[cols[c]['parent']])
        else:m.add_exactly_one(x[c,r] for r in range(R))
    for r in range(R):
        h=m.new_int_var(0,H,f'h{r}');heights.append(h)
        m.add(sum(cols[c]['w']*x[c,r] for c in range(N))<=W)
        m.add_max_equality(h,[cols[c]['h']*x[c,r] for c in range(N)])
        used=m.new_bool_var(f'u{r}');m.add(h>=used);m.add(h<=H*used)
        for k in range(target):z[r,k]=m.new_bool_var(f'z{r}_{k}')
        m.add(sum(z[r,k] for k in range(target))==used)
        for c in range(N):m.add_hint(x[c,r],int(cols[c]['row']==r and 'parent' not in cols[c]))
        if r<len(state):
            m.add_hint(h,max(c['h'] for c in cols if c['row']==r))
            for k in range(target):m.add_hint(z[r,k],int(state[r]['bin']%target==k))
    for r in range(R-1):m.add(heights[r]>=heights[r+1])
    for k in range(target):
        hh=[]
        for r in range(R):
            h=m.new_int_var(0,H,f'hb{r}_{k}');m.add(h<=heights[r]);m.add(h<=H*z[r,k]);m.add(h>=heights[r]-H*(1-z[r,k]));hh.append(h)
        load=m.new_int_var(0,H,f'load{k}');m.add(load==sum(hh));loads.append(load)
    # Bin labels are interchangeable; canonicalize their used heights.
    for k in range(target-1):m.add(loads[k]>=loads[k+1])
    s=cp_model.CpSolver();s.parameters.max_time_in_seconds=max(.001,seconds-(time.perf_counter()-start));s.parameters.num_search_workers=2;s.parameters.random_seed=1740
    status=s.solve(m);success=status in (cp_model.FEASIBLE,cp_model.OPTIMAL)
    result=dict(instance=name,method=tag,target_bins=target,columns=N,row_slots=R,budget_s=seconds,status=s.status_name(status),success=success,wall_s=time.perf_counter()-start,branches=s.num_branches,conflicts=s.num_conflicts,scope='Feasibility only for these retained column subtrees and row count; not proof for unrestricted guillotine packing')
    result.update(retained_column_area=None if release else source_area,source_column_area=source_area,target_area=target*W*H,item_count_conserved=True,selective_subtree_release=release)
    if success:
        result['released_subtrees']=sum(not s.value(v) for v in keep.values())
        records=[];nid=0
        for k in range(target):
            rr=[r for r in range(R) if s.value(z[r,k])]
            if not rr:continue
            root=nid;records.append([k,1,nid,0,0,W,H,0,0,'']);nid+=1;y=0
            for r in rr:
                h=s.value(heights[r]);row=nid;records.append([k,1,nid,0,y,W,h,-2,1,root]);nid+=1;xx=0
                for c in range(N):
                    if not s.value(x[c,r]):continue
                    col=cols[c];w=col['w'];colid=nid;records.append([k,1,nid,xx,y,w,h,-2,2,row]);nid+=1;yy=y
                    for i in col['ids']:
                        iw,ih=dims[i];cell=nid;records.append([k,1,nid,xx,yy,w,ih,i if iw==w else -2,3,colid]);nid+=1
                        if iw<w:records.append([k,1,nid,xx,yy,iw,ih,i,4,cell]);nid+=1
                        yy+=ih
                    xx+=w
                y+=h
        cert=OUT/f'{name}_{tag}.csv'
        with cert.open('w') as f:
            wr=csv.writer(f);wr.writerow(['PLATE_ID','COPIES','NODE_ID','X','Y','WIDTH','HEIGHT','TYPE','CUT','PARENT']);wr.writerows(records)
        er.nr.validate(name,cert)
        # Re-read with the original native SolutionBuilder and require full
        # cutting feasibility; zero time merely disables further search.
        checked=er.attempt(name,cert.name,0,1740,'monotone',tag+'-native-check')
        assert checked['initial_bins']<=target and checked['subtree_roundtrip_valid']
        result.update(certificate=cert.name,valid=True,certificate_sha256=hashlib.sha256(cert.read_bytes()).hexdigest())
    (OUT/f'{name}_{tag}-report.json').write_text(json.dumps(result,indent=2));return result

def main():
    names=['planted_3_5_6_1','Class_03.2bp_100_2']
    (OUT/'refinement-protocol.json').write_text(json.dumps(dict(instances=names,anneal_budget_s=60,cp_budget_s=30,comparison='Same row-count budget; CP can reassign whole retained columns jointly to arbitrary rows and sheets. Compare original column subtrees with annealing-recombined column subtrees.',scope='Additional offline mechanism budget, selected after failed short screen; not equal-time generalization evidence'),indent=2))
    rows=[]
    for name in names:
        r=er.attempt(name,f'{name}_initial.csv',60,1740,'anneal','long-anneal');print('LONG_ANNEAL',name,r['success'],r['best_violation_area'],flush=True)
        R=max(len(r['initial_state']),len(r['best_state']))
        for state,tag in ((r['initial_state'],'cp-original-columns'),(r['best_state'],'cp-recombined-columns')):
            result=solve_columns(name,state,r['target_bins'],R,30,tag);rows.append(result);(OUT/'refinement-rows.json').write_text(json.dumps(rows,indent=2));print('JOINT_COLUMNS',json.dumps(result),flush=True)
    print('REFINEMENT_COMPLETE',flush=True)
if __name__=='__main__':main()
