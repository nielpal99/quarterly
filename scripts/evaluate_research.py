"""Offline, source-pinned evidence retrieval evaluation. No model or paid API calls."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.retrieval import retrieve


def main():
    manifest=json.loads((ROOT/'evals/earnings-retrieval.json').read_text())
    sources={};unavailable=[]
    for entry in manifest['sources']:
        path=ROOT/entry.get('snapshot','missing')
        if entry['status']!='ready' or not path.is_file():
            unavailable.append({'company':entry['company'],'reason':entry.get('reason','Snapshot missing.')});continue
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('Snapshot hash mismatch: '+entry['company'])
        sources[entry['company']]=json.loads(raw)
    results=[]
    for case in manifest['cases']:
        if case['company'] not in sources:continue
        evidence=retrieve(case['question'],[sources[case['company']]])
        expected=case['expected_quote_contains']
        passed=not evidence if case['expect_no_evidence'] else any(expected in e['quote'] for e in evidence)
        results.append({**case,'passed':passed,'returned':len(evidence),'top_quote':evidence[0]['quote'] if evidence else None})
    report={'name':manifest['name'],'scope':manifest['scope'],'evaluated':len(results),
        'planned':len(manifest['cases']),'passed':sum(r['passed'] for r in results),
        'unavailable_sources':unavailable,'results':results}
    target=ROOT/'outputs/research-evaluation.json';target.write_text(json.dumps(report,indent=2)+'\n')
    print(f"{report['passed']}/{report['evaluated']} development checks passed; {len(unavailable)} source(s) unavailable. See outputs/research-evaluation.json")
    return 0 if len(results)==len(manifest['cases']) and all(r['passed'] for r in results) else 1

if __name__=='__main__':raise SystemExit(main())
