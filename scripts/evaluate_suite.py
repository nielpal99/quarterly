"""Repeatable offline retrieval and API-contract evals; writes a human-readable report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.retrieval import retrieve


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--portable',action='store_true',help='Synthetic fixtures and API contracts only; excludes real-source accuracy checks')
    args=parser.parse_args()
    (ROOT/'outputs').mkdir(exist_ok=True)
    stem='portable-eval-results' if args.portable else 'eval-suite-results'
    results=[]
    def record(name,category,passed,detail='',status=None):
        results.append(dict(id=name,category=category,status=status or ('pass' if passed else 'fail'),detail=detail))
    manifest=json.loads((ROOT/'evals/earnings-retrieval.json').read_text())
    for entry in ([] if args.portable else manifest['sources']):
        path=ROOT/entry.get('snapshot','missing')
        if entry['status']!='ready' or not path.is_file():
            record('corpus-'+entry['company'],'corpus',False,entry.get('reason','Missing snapshot'),status='blocked');continue
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            record('hash-'+entry['company'],'corpus',False,'Snapshot changed; no questions scored.');continue
        source=json.loads(raw)
        for i,case in enumerate(c for c in manifest['cases'] if c['company']==entry['company']):
            hits=retrieve(case['question'],[source])
            ranks=[j+1 for j,e in enumerate(hits) if case['expected_quote_contains'] and case['expected_quote_contains'] in e['quote']]
            passed=not hits if case['expect_no_evidence'] else bool(ranks)
            record(f"real-{entry['company']}-{i}",'real-release-retrieval',passed,
                f"{case['question']} | first relevant rank: {min(ranks) if ranks else 'none'}; returned {len(hits)}")
    challenges=json.loads((ROOT/'evals/challenges.json').read_text())
    for case in challenges['cases']:
        hits=retrieve(case['question'],challenges['sources'])
        quotes=[e['quote'] for e in hits[:case['rank']]]
        expected=case.get('expected_all') or ([case['expected']] if case.get('expected') else [])
        passed=all(any(text in q for q in quotes) for text in expected) if expected else not hits
        record(case['id'],case['category'],passed,'Top result: '+(hits[0]['quote'] if hits else '(none)'))
    # API checks use a disposable database, never the user's current library.
    with tempfile.TemporaryDirectory() as folder:
        os.environ['QUARTERLY_DATA_DIR']=folder
        os.environ['OPENAI_API_KEY']=''
        from app.main import app,db
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            with db() as c:
                for packet in ['p1','p2']:
                    c.execute('INSERT INTO research_packets VALUES (?,?,?,?,?,?,?,?)',(packet,'TEST','ExampleCo','123','2026-11-19',0,'[]','latest'))
                for sid,pid,state,text in [('s1','p1','ready','Product revenue and revenue growth were $150 million and 50%.'),('s2','p1','ready','Product revenue and revenue growth were $999 million and 99%.'),('foreign','p2','ready','Revenue was $888 million.'),('unread','p1','discovered','Revenue was $777 million.')]:
                    c.execute('INSERT INTO research_sources VALUES (?,?,?,?,?,?,?)',(sid,pid,json.dumps({'title':sid,'url':'https://example.com/'+sid}),state,json.dumps([{'text':text,'locator':'Paragraph 1','kind':'paragraph'}]),None,None))
            endpoint='/api/research/packets/p1/briefs'
            for sid in ['foreign','unread']:
                response=client.post(endpoint,json={'source_ids':[sid]})
                record('reject-'+sid,'source-scope',response.status_code==422,str(response.status_code))
            response=client.post(endpoint,json={'source_ids':['s1']})
            record('build','brief-workflow',response.status_code==201,str(response.status_code))
            if response.status_code==201:
                brief=response.json();serialized=json.dumps(brief)
                record('exclude-unselected','source-scope','$999' not in serialized and '$888' not in serialized and '$777' not in serialized)
                record('include-selected','source-scope','$150' in serialized)
                record('missing-section','abstention',not brief['sections'][2]['evidence'])
                record('no-fabricated-synthesis','brief-workflow',brief['mode']=='excerpts' and all(s['answer'] is None for s in brief['sections']))
                with db() as c:c.execute("UPDATE research_sources SET blocks='[]' WHERE id='s1'")
                record('immutable-snapshot','persistence',client.get('/api/research/briefs/'+brief['id']).json()==brief)
                export=client.get('/api/research/briefs/'+brief['id']+'/markdown')
                record('export-source','export',export.status_code==200 and 'https://example.com/s1' in export.text and '$150' in export.text)
                record('history','persistence',len(client.get(endpoint).json())==1)
            response=client.post(endpoint,json={'source_ids':['s1'],'synthesize':True})
            record('model-key-gate','configuration',response.status_code==409)
    for requirement in challenges['unscored_requirements']:
        record(requirement,'unmeasured',False,'Requires a separately labeled parser/model evaluation.',status='not-run')
    counts={status:sum(r['status']==status for r in results) for status in ['pass','fail','blocked','not-run']}
    report={'suite':'quarterly-portable-v1' if args.portable else 'quarterly-development-v2','excluded_scope':['Real-release corpus and accuracy checks'] if args.portable else [],'counts':counts,'limitations':'Development examples and synthetic contracts. No held-out validation, live model calls, browser inspection, or LlamaParse quality measurement. A pass means the stated check passed, not financial answer accuracy.','results':results}
    (ROOT/'outputs'/f'{stem}.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Quarterly evaluation results','',('Portable mode: real-release corpus checks are excluded, not passed.' if args.portable else 'Full development mode.'),'',report['limitations'],'',f"Passed: {counts['pass']} · Failed: {counts['fail']} · Blocked: {counts['blocked']} · Not run: {counts['not-run']}",'']
    for status in ['fail','blocked','not-run','pass']:
        lines.extend(['## '+status,''])
        for r in results:
            if r['status']==status:lines.extend([f"- **{r['id']}** ({r['category']}): {r['detail']}"])
        lines.append('')
    (ROOT/'outputs'/f'{stem}.md').write_text('\n'.join(lines))
    print(json.dumps(counts))
    return 1 if counts['fail'] or counts['blocked'] else 0

if __name__=='__main__':raise SystemExit(run())
