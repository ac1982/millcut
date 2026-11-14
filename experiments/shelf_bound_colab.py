"""Colab-only exact pricing of fractional shelf cover, with dual feasibility bound."""
import sys,pathlib,csv,json,time,math
import numpy as np
from fractions import Fraction
from scipy.optimize import linprog
from scipy.sparse import coo_matrix
ROOT=pathlib.Path('/content/portfolio-study');OUT=ROOT/'detail'

def price(items,W,dual):
    # Any feasible shelf has height equal to some item's height. For each
    # height solve exact 0/1 width knapsack, obtaining all-row dual constraints.
    found=[];ratio=1.0
    for height in sorted({h for w,h in items}):
        dp=[0.0]*(W+1);bits=[0]*(W+1)
        for i,(w,h) in enumerate(items):
            if h>height:continue
            val=dual[i]
            if val<=0:continue
            for cap in range(W,w-1,-1):
                v=dp[cap-w]+val
                if v>dp[cap]+1e-10:dp[cap]=v;bits[cap]=bits[cap-w]|(1<<i)
        value=dp[W];ratio=max(ratio,value/height)
        if value>height+1e-7:found.append((bits[W],height))
    return ratio,found

def run(name):
    with (ROOT/'berkey1987'/f'{name}_items.csv').open() as f:items=[(int(r['WIDTH']),int(r['HEIGHT'])) for r in csv.DictReader(f)]
    with (ROOT/'berkey1987'/f'{name}_bins.csv').open() as f:b=next(csv.DictReader(f));W,H=int(b['WIDTH']),int(b['HEIGHT'])
    n=len(items);pats={1<<i:h for i,(w,h) in enumerate(items)};start=time.perf_counter();best=0;iterations=0;history=[]
    while time.perf_counter()-start<60:
        keys=list(pats);ri=[];ci=[]
        for j,mask in enumerate(keys):
            for i in range(n):
                if mask>>i&1:ri.append(i);ci.append(j)
        matrix=coo_matrix((np.ones(len(ri)),(ri,ci)),shape=(n,len(keys))).tocsc()
        lp=linprog([pats[k] for k in keys],A_eq=matrix,b_eq=np.ones(n),bounds=(0,None),method='highs')
        assert lp.success,lp.message
        ratio,new=price(items,W,lp.eqlin.marginals)
        # Scale duals to satisfy every possible shelf's constraint, not just pool.
        # Small downward margin protects the floating point certificate.
        bound=float(sum(lp.eqlin.marginals))/(ratio*(1+1e-8))-1e-6;best=max(best,bound)
        iterations+=1;history.append({'iteration':iterations,'restricted_lp':lp.fun,'dual_scaling':ratio,'valid_height_lower_bound':bound,'columns':len(pats)})
        if not new:break
        before=len(pats)
        for mask,h in new:pats[mask]=min(h,pats.get(mask,h))
        if len(pats)==before:break
    # Turn the final numerical dual into a rational certificate. Integer
    # knapsack checks EVERY feasible row-height constraint without floats.
    scale=1000000;prices=[math.floor(float(v)*scale) for v in lp.eqlin.marginals];denom=Fraction(scale);constraints=[]
    for height in sorted({h for w,h in items}):
        dp=[0]*(W+1)
        for i,(w,h) in enumerate(items):
            if h>height or prices[i]<=0:continue
            for cap in range(W,w-1,-1):dp[cap]=max(dp[cap],dp[cap-w]+prices[i])
        denom=max(denom,Fraction(dp[W],height));constraints.append({'height':height,'max_integer_dual_sum':dp[W]})
    exact_bound=Fraction(sum(prices))/denom
    return {'instance':name,'items':n,'sheet_height':H,'area_bound':math.ceil(sum(w*h for w,h in items)/(W*H)),'shelf_total_height_lower_bound':float(exact_bound),'shelf_bins_lower_bound':math.ceil(exact_bound/H),'rational_certificate':{'prices':prices,'divisor_numerator':denom.numerator,'divisor_denominator':denom.denominator,'height_bound_numerator':exact_bound.numerator,'height_bound_denominator':exact_bound.denominator,'all_height_knapsack_checks':constraints},'converged':not new,'iterations':iterations,'seconds':time.perf_counter()-start,'history':history,'meaning':'rigorous rational lower bound for single-item-per-column shelf model only; does not bound general stacked guillotine layouts'}
rows=[run(n) for n in ['Class_04.2bp_100_1','planted_3_3_5_0','planted_3_5_6_1']]
(OUT/'shelf-bounds.json').write_text(json.dumps(rows,indent=2))
print(json.dumps([{k:v for k,v in r.items() if k!='history'} for r in rows],indent=2))
