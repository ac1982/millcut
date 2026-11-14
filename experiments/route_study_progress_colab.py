"""Read bounded progress output; never launches a second solver."""
import pathlib,json
root=pathlib.Path('/content/route-study')
for phase,total in [('regression',10),('development',120),('validation',80)]:
    p=root/f'{phase}-rows.json'
    rows=json.loads(p.read_text()) if p.exists() else []
    print(phase,len(rows),'/',total)
    if phase=='development' and rows:
        triples=[]
        for n in {r['instance'] for r in rows}:
            rr={r['method']:r for r in rows if r['instance']==n}
            if all(m in rr for m in ('default','guide1','strips')):
                triples.append((rr['strips']['bins'],rr['default']['bins'],rr['guide1']['bins']))
        print('COMPLETE_DEV_TRIPLES',len(triples),'SPECIALIST_VS_DEFAULT',dict(wins=sum(a<b for a,b,c in triples),ties=sum(a==b for a,b,c in triples),losses=sum(a>b for a,b,c in triples)))
if (root/'learned-policy.json').exists():print('POLICY',(root/'learned-policy.json').read_text())
if (root/'failed.json').exists():print('FAILED',(root/'failed.json').read_text())
if (root/'complete.json').exists():print('COMPLETE',(root/'validation-summary.json').read_text())
elif (root/'running.log').exists():print((root/'running.log').read_text()[-900:])
