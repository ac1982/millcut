"""Colab-only per-instance ablation and mechanism checks."""
import pathlib,json,collections
root=pathlib.Path('/content/portfolio-study/detail');rows=json.loads((root/'rows.json').read_text());report={'per_instance':{},'certificates':[]}
for name in sorted({r['instance'] for r in rows}):
    report['per_instance'][name]={m:[{'seed':r['seed'],'bins':r.get('bins'),'status':r.get('status'),'seconds':r.get('wall_s')} for r in rows if r['instance']==name and r['method']==m] for m in ('default','shelves','stacked')}
for p in (root/'results').glob('*-strips.json'):
    layout=json.loads(p.read_text());columns=[]
    for sheet in layout:
        for strip in sheet:
            cc=collections.Counter(n['x'] for n in strip['items']);columns.extend(cc.values())
    report['certificates'].append({'file':p.name,'stacked_columns':sum(n>1 for n in columns),'items_in_stacks':sum(n for n in columns if n>1),'max_stack':max(columns)})
report['interpretation']='If a stacked-generator win uses no stacked columns, the result does not demonstrate that stacking caused improvement; sampling/selection changed as well.'
(root/'analysis.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
