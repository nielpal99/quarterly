"""Explicitly paid live answer checks; local scoring, not LlamaParse's vendor eval API."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.native_answers import generate_native_answer
from app.retrieval import retrieve


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Allow two paid LlamaParse Extract requests')
    args=parser.parse_args()
    if not args.live:parser.error('--live is required; this consumes LlamaParse credits')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    source=json.loads((ROOT/'data/evaluation/Snowflake.json').read_text())
    cases=[('product-revenue','What was fourth quarter product revenue?',['$1.23 billion','$1,226.6 million'],'product revenue'),
        ('rpo','What were remaining performance obligations?',['$9.77 billion'],'remaining performance obligations')]
    results=[]
    for name,question,expected,metric in cases:
        try:
            evidence=retrieve(question,[source])[:2]
            response=generate_native_answer(question,evidence)
            claims=response['answer']['claims']
            text=' '.join(c['text'] for c in claims).lower()
            checks={'sufficient':response['answer']['sufficient'],
                'expected_value_and_unit':any(value.lower() in text for value in expected),
                'metric_named':metric in text,'nonempty_citations':bool(claims) and all(c['citation_ids'] for c in claims)}
            results.append({'id':name,'question':question,'passed':all(checks.values()),'checks':checks,'result':response})
        except Exception as exc:results.append({'id':name,'passed':False,'error_type':type(exc).__name__})
        print(name,results[-1]['passed'],flush=True)
    report={'kind':'live LlamaParse answer generation with deterministic local scoring; NOT vendor evaluation API',
        'scope':'Two known development examples from one release. Not held-out accuracy, parser quality, or a model judge.',
        'passed':sum(r['passed'] for r in results),'total':len(results),'results':results}
    (ROOT/'outputs/native-live-smoke-eval.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if all(r['passed'] for r in results) else 1

if __name__=='__main__':raise SystemExit(main())
