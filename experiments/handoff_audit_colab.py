"""Read-only handoff evidence audit; execute on Colab only."""
import tarfile,zipfile,json,hashlib,pathlib,collections
root=pathlib.Path('/content/handoff-audit');root.mkdir(exist_ok=True)
with tarfile.open('/content/cut-handoff.tar') as t:t.extractall(root,filter='data')
z=zipfile.ZipFile(root/'results/mill-study-results.zip'); names=z.namelist()
def read(suffix):
 p=[n for n in names if n.endswith(suffix)];assert len(p)==1,(suffix,p);return z.read(p[0])
def j(s):return json.loads(read(s))
h=j('source-sha256.json');checks={}
for p,k in [('experiments/paper_mill_cutter.py','paper_mill_cutter.py'),('experiments/native/colstrip_gen.cpp','colstrip_gen.cpp'),('experiments/mill_policy.json','mill-policy.json')]:checks[p]=hashlib.sha256((root/p).read_bytes()).hexdigest()==h[k]
rows=[]; bad=[]
for r,f in [('r51','ablation'),('r52','dev'),('r53','validation'),('r54','validation-v2'),('r55','test')]:
 rr=j(f'{r}/{f}-rows.json');rows+=rr
 for x in rr:
  matches=[n for n in names if '/certificates/' in n and n.endswith('/'+x['certificate'])]; assert matches, x['certificate']; b=z.read(matches[0])
  if hashlib.sha256(b).hexdigest()!=x['certificate_sha256']:bad.append(x['certificate'])
rr=j('r55/test-rows.json'); totals={m:sum(x['bins'] for x in rr if x['method']==m) for m in {x['method'] for x in rr}}
pairs={(x['instance'],x['seed']):x['bins'] for x in rr if x['method']=='default'}
d=[x['bins']-pairs[x['instance'],x['seed']] for x in rr if x['method']=='mill_router']
s=json.loads((root/'results/routing-data-v2/current-label-status.json').read_text());m=json.loads((root/'results/routing-data-v2/manifest.json').read_text())
out={'zip_crc_bad':z.testzip(),'zip_top_paths':names[:5],'source_matches':checks,'row_count':len(rows),'certificate_hash_failures':bad,'r55_totals':totals,'r55_win_tie_loss':[sum(x<0 for x in d),sum(x==0 for x in d),sum(x>0 for x in d)],'max_wall_s':max(x['wall_s'] for x in rows),'over_10s':sum(x['wall_s']>10 for x in rows),'status_instances':len(s),'status_runs':sum(x['label_runs'] for x in s.values()),'consumed_validation':sum(x.get('validation_consumed',False) for x in s.values()),'consumed_test':sum(x.get('test_consumed',False) for x in s.values()),'manifest_instances':len(m)}
print(json.dumps(out,indent=2));pathlib.Path('/content/handoff-audit.json').write_text(json.dumps(out,indent=2))
out['certificate_csv_files']=sum('/certificates/' in n and n.endswith('.csv') for n in names)
out['unique_referenced_certificates']=len({x['certificate'] for x in rows})
pathlib.Path('/content/handoff-audit.json').write_text(json.dumps(out,indent=2))
print(out['certificate_csv_files'],out['unique_referenced_certificates'])
