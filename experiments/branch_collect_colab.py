"""Collect verified finite-budget branch continuation labels, Colab only."""
import sys,pathlib,json,subprocess,hashlib,random,time,collections
sys.path.insert(0,'/content');import native_run_colab as nr
ROOT=nr.ROOT;OUT=ROOT/'branch-labels';OUT.mkdir(exist_ok=True)
CASES={'Class_02.2bp_60_2':60,'Class_02.2bp_100_3':100,'Class_04.2bp_100_1':100,'Class_04.2bp_100_3':100,'planted_3_3_5_0':45,'planted_3_5_6_1':90}
FEATURES=['original_guide1_scaled','waste_over_bin_area','packed_area_fraction','packed_item_fraction','bins','x1_previous','x1_current','x3_current','y2_previous','y2_current','row_remaining_width','plate_remaining_height','insertion_depth','z1','z2']

def solve(name,depth,rank,seconds,phase):
    directory=OUT/phase;directory.mkdir(exist_ok=True)
    tag=f'{name}_d{depth}_r{rank}';cert=directory/(tag+'.csv');output=directory/(tag+'.json')
    cmd=[str(ROOT/'build/branch_labels'),str(nr.DATA/f'{name}_items.csv'),str(nr.DATA/f'{name}_bins.csv'),str(seconds),str(depth),str(rank),str(cert),str(output)]
    start=time.perf_counter();p=subprocess.run(cmd,capture_output=True,text=True,timeout=seconds+20)
    if p.returncode:raise RuntimeError(p.stderr[-3000:]+p.stdout[-500:])
    row=json.loads(output.read_text());row.update(instance=name,target_depth=depth,budget_s=seconds,phase=phase,process_wall_s=time.perf_counter()-start)
    row['state_id']=hashlib.sha256(json.dumps([name,row['prefix']],sort_keys=True).encode()).hexdigest()
    if row['bins'] is not None:
        nr.validate(name,cert);assert row['prefix_verified'];row['independent_valid']=True
        row['certificate']=str(cert.relative_to(OUT));row['certificate_sha256']=hashlib.sha256(cert.read_bytes()).hexdigest()
    output.write_text(json.dumps(row,indent=2));return row

def summarize(rows):
    groups=collections.defaultdict(dict)
    for r in rows:groups[r['state_id']][r['rank']]=r
    info=[]
    for state,rr in groups.items():
        valid=[r for r in rr.values() if r['bins'] is not None]
        if not valid:continue
        first=rr.get(0);best=min(r['bins'] for r in valid);worst=max(r['bins'] for r in valid)
        certified=sum(a['bins']<b['branch_area_lower_bound'] for a in valid for b in rr.values())
        info.append(dict(state_id=state,instance=valid[0]['instance'],depth=valid[0]['target_depth'],observed_best=best,observed_worst=worst,rank0_bins=first['bins'] if first else None,better_than_rank0=first is not None and first['bins'] is not None and best<first['bins'],certified_order_pairs=certified,ranks={k:v['bins'] for k,v in rr.items()}))
    return dict(runs=len(rows),unknown=sum(r['bins'] is None for r in rows),unique_states=len(groups),different_outcome_states=sum(g['observed_best']<g['observed_worst'] for g in info),rank0_missed_best_states=sum(g['better_than_rank0'] for g in info),certified_order_pairs=sum(g['certified_order_pairs'] for g in info),groups=info)

def main():
    protocol=dict(cases=CASES,depth_fractions=[.1,.3,.6],candidate_original_ranks=[0,1,4,16],teacher='Original guide 1 iterative beam search, growth factor 2, resume at fixed child, 2 seconds per child',labels='Feasible completion counts are upper bounds under a fixed search budget. Unknown is censored. Pairwise observed preference is not proof of branch optimality.',features=FEATURES,scope='Development label diagnostic, not packing quality evaluation or generalization',binary_sha256=hashlib.sha256((ROOT/'build/branch_labels').read_bytes()).hexdigest(),train_instances=['Class_02.2bp_60_2','Class_04.2bp_100_1','planted_3_3_5_0','planted_3_5_6_1'],validation_instances=['Class_02.2bp_100_3','Class_04.2bp_100_3'])
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2))
    # Verify replay identity before interpreting differences as branch effects.
    checks=[solve('Class_02.2bp_60_2',6,0,2,'replay-a'),solve('Class_02.2bp_60_2',6,0,2,'replay-b')]
    assert checks[0]['state_id']==checks[1]['state_id'] and checks[0]['child']==checks[1]['child']
    assert all(r['independent_valid'] for r in checks)
    (OUT/'replay-check.json').write_text(json.dumps(dict(prefix_and_child_identical=True,bins=[r['bins'] for r in checks],certificates_identical=checks[0]['certificate_sha256']==checks[1]['certificate_sha256']),indent=2))
    jobs=[(n,int(num*f),r) for n,num in CASES.items() for f in (.1,.3,.6) for r in (0,1,4,16)]
    random.Random(1736).shuffle(jobs);rows=[]
    for n,d,r in jobs:
        row=solve(n,d,r,2,'short');rows.append(row)
        (OUT/'rows.json').write_text(json.dumps(rows,indent=2))
        print('BRANCH',len(rows),len(jobs),n,d,r,row['bins'],flush=True)
    summary=summarize(rows);(OUT/'summary.json').write_text(json.dumps(summary,indent=2));print('BRANCH_COMPLETE',json.dumps(summary),flush=True)
if __name__=='__main__':main()
