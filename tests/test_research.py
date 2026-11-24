import importlib
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from app.research import html_blocks, public_url, retrieve


@pytest.fixture
def research(tmp_path,monkeypatch):
    monkeypatch.setenv('QUARTERLY_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY','')
    monkeypatch.setenv('EXA_API_KEY','')
    import app.main as main
    import app.research as implementation
    importlib.reload(main)
    def sec(url):
        if 'company_tickers' in url:return {'0':{'ticker':'TEST','cik_str':123,'title':'Test Inc.'}}
        return {'name':'Test Inc.','filings':{'recent':{
            'form':['10-Q','10-Q','10-Q','10-K','8-K','10-K'],
            'filingDate':['2026-09-01','2026-06-01','2026-03-01','2026-02-01','2026-01-01','2025-02-01'],
            'accessionNumber':['000123-26-1','000123-26-2','000123-26-3','000123-26-4','000123-26-5','000123-25-6'],
            'primaryDocument':['q1.htm','q2.htm','q3.htm','k.htm','event.htm','old.htm'],
            'reportDate':['2026-07-31','2026-04-30','2026-01-31','2025-12-31','','2024-12-31']}}}
    monkeypatch.setattr(implementation,'sec_json',sec)
    with TestClient(main.app) as c:yield c,main,implementation


def discover(c,as_of='2026-11-15'):
    r=c.post('/api/research/discover',json={'ticker':'TEST','as_of':as_of,'mode':'historical'})
    assert r.status_code==201
    return r.json()


def wait_source(c,packet_id,state):
    for _ in range(200):
        packet=c.get('/api/research/packets/'+packet_id).json()
        if packet['sources'][0]['state']==state:return packet['sources'][0]
        time.sleep(.01)
    raise AssertionError('Source did not reach expected state')


def test_discovery_period_bounds_identity_and_budget(research):
    c,_,_=research
    packet=discover(c)
    forms=[s['metadata']['form'] for s in packet['sources']]
    assert forms.count('10-K')==1 and forms.count('10-Q')==2
    assert all(s['state']=='discovered' and not s['blocks'] for s in packet['sources'])
    historical=discover(c,'2025-12-31')
    assert len(historical['sources'])==1
    assert historical['sources'][0]['metadata']['filed_on']=='2025-02-01'
    assert c.post('/api/research/discover',json={'ticker':'UNKNOWN','as_of':'2026-11-15'}).status_code==404
    assert c.post('/api/research/discover',json={'ticker':'TEST','as_of':'2026-11-15','include_exa':True}).status_code==409
    assert c.post('/api/research/packets/'+packet['id']+'/ingest',json={'source_ids':['foreign']}).status_code==422


def test_html_ingestion_provenance_and_answer_scope(research,monkeypatch):
    c,main,impl=research
    packet=discover(c);source=packet['sources'][0]
    q='/api/research/packets/'+packet['id']+'/ask'
    before=c.post(q,json={'question':'What was product revenue?'}).json()
    assert before['coverage']['searchable_sources']==0 and before['evidence']==[]
    monkeypatch.setattr(impl,'fetch_source',lambda url:(b'<html><script>bad</script><h1>Revenue</h1><p>Product revenue was $100 million.</p></html>',url,'text/html'))
    assert c.post('/api/research/packets/'+packet['id']+'/ingest',json={'source_ids':[source['id']]}).status_code==202
    ready=wait_source(c,packet['id'],'ready')
    assert ready['content_hash'] and ready['metadata']['retrieved_on']
    assert (main.DATA/'research'/source['id']/'source.bin').exists()
    after=c.post(q,json={'question':'What was product revenue?'}).json()
    assert after['mode']=='evidence_only' and after['answer'] is None
    assert after['coverage']['searchable_sources']==1
    assert after['evidence'][0]['locator']=='Text chunk 2'
    assert after['evidence'][0]['section']=='Revenue'
    assert '100' in after['evidence'][0]['quote'] and 'bad' not in after['evidence'][0]['quote']
    assert c.post(q,json={'question':'What was product revenue?','synthesize':True}).status_code==409
    with main.db() as db:assert db.execute('SELECT COUNT(*) FROM research_answers').fetchone()[0]==2


def test_ingestion_failure_does_not_become_evidence(research,monkeypatch):
    c,_,impl=research;packet=discover(c);source=packet['sources'][0]
    def fail(url):raise ValueError('test failure')
    monkeypatch.setattr(impl,'fetch_source',fail)
    c.post('/api/research/packets/'+packet['id']+'/ingest',json={'source_ids':[source['id']]})
    failed=wait_source(c,packet['id'],'failed')
    assert failed['blocks']==[]
    answer=c.post('/api/research/packets/'+packet['id']+'/ask',json={'question':'Show revenue evidence'}).json()
    assert answer['coverage']['searchable_sources']==0


def test_pdf_requires_explicit_bounded_parse(research,monkeypatch):
    c,main,impl=research;packet=discover(c);source=packet['sources'][0]
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    buffer=io.BytesIO();writer.write(buffer)
    monkeypatch.setattr(impl,'fetch_source',lambda url:(buffer.getvalue(),url,'application/pdf'))
    c.post('/api/research/packets/'+packet['id']+'/ingest',json={'source_ids':[source['id']]})
    downloaded=wait_source(c,packet['id'],'pdf_downloaded')
    assert downloaded['blocks']==[]
    assert c.post('/api/research/sources/'+source['id']+'/parse',json={'pages':[2]}).status_code==422
    assert c.post('/api/research/sources/'+source['id']+'/parse',json={'pages':[1,1,1,1,1,1]}).status_code==422


def test_public_source_validation_and_chunk_boundaries(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(None,None,None,None,('127.0.0.1',443))])
    for url in ['https://localhost/file','http://example.com/file','https://user:secret@example.com/file']:
        with pytest.raises(ValueError):public_url(url)
    blocks=html_blocks(('<p>Revenue '+('x '*1000)+'</p>').encode())
    assert all(len(b['text'])<=1800 for b in blocks)
    assert [b['locator'] for b in blocks]==['Text chunk '+str(i+1) for i in range(len(blocks))]


def test_structured_passages_keep_topics_units_and_inline_numbers_together():
    content=b'''<div><span style="font-weight:700">(9) Supply Chain Finance Program</span></div>
    <p>Participating suppliers negotiate their sales. Obligations totaled <span>$</span> <span>18</span> million.</p>
    <div><span style="font-weight:700">(10) Deferred Revenue and Performance Obligations</span></div>
    <p>Revenues recognized from beginning period deferred revenue were <span>$</span> <span>3.6</span> billion and $3.0 billion, respectively.</p>'''
    blocks=html_blocks(content)
    revenue=next(b for b in blocks if 'Revenues recognized' in b['text'])
    assert '$3.6 billion' in revenue['text']
    assert 'suppliers' not in revenue['text']
    assert revenue['section']=='(10) Deferred Revenue and Performance Obligations'
    source={'id':'s','metadata':{'title':'10-Q','url':'https://example.com'},'blocks':blocks}
    evidence=retrieve('deferred revenue',[source])
    assert evidence[0]['kind']=='paragraph'
    assert '$3.6 billion' in evidence[0]['passage']
    assert evidence[0]['context_before'].startswith('(10)')


def test_long_paragraph_does_not_split_a_word_or_money_unit():
    text=('A complete sentence about operations. '*100)+'Revenue was $ 3.6 billion for the period.'
    blocks=html_blocks(('<p>'+text+'</p>').encode())
    assert all(not b['text'].startswith('uppliers') for b in blocks)
    assert any('$3.6 billion' in b['text'] for b in blocks)
    assert sum(b['text'].count('complete sentence') for b in blocks)==100


def test_nested_table_rows_are_not_duplicated_and_heading_not_returned():
    blocks=html_blocks(b'<h2>Financial results</h2><table><tr><td><table><tr><td>Revenue</td><td>$100 million</td></tr></table></td></tr></table>')
    rows=[b for b in blocks if b['kind']=='table_row']
    assert len(rows)==1
    assert 'Revenue | $100 million'==rows[0]['text']


def test_latest_default_does_not_require_date_or_include_historical_reports(research):
    c,_,_=research
    response=c.post('/api/research/discover',json={'ticker':'TEST'})
    assert response.status_code==201
    packet=response.json()
    assert packet['mode']=='latest'
    assert len(packet['sources'])==1
    assert packet['sources'][0]['metadata']['selection_role']=='latest periodic report'
    assert c.post('/api/research/discover',json={'ticker':'TEST','mode':'historical'}).status_code==422


def test_earnings_exhibits_discovered_without_indexing_or_paid_parse(research,monkeypatch):
    c,_,impl=research
    original=impl.sec_json
    def sec(url):
        result=original(url)
        if 'filings' in result:
            recent=result['filings']['recent']
            recent['filingDate'][4]='2026-09-01'
            recent['accessionNumber'][4]='0000000123-26-000005'
            recent['items']=['','','','','2.02, 9.01','']
        return result
    monkeypatch.setattr(impl,'sec_json',sec)
    calls=[]
    def fetch(url):
        calls.append(url)
        return b'<table><tr><td>2</td><td>Earnings release</td><td><a href="release.htm">release.htm</a></td><td>EX-99.1</td></tr></table>',url,'text/html'
    monkeypatch.setattr(impl,'fetch_source',fetch)
    packet=c.post('/api/research/discover',json={'ticker':'TEST'}).json()
    exhibit=next(s for s in packet['sources'] if s['metadata']['kind']=='sec_exhibit')
    assert len(calls)==1 and calls[0].endswith('0000000123-26-000005-index.html')
    assert exhibit['state']=='discovered' and exhibit['blocks']==[]
    assert exhibit['metadata']['parent_url'].endswith('/event.htm')
    assert 'period_end' not in exhibit['metadata']


def test_exhibit_failure_retains_periodic_reports(research,monkeypatch):
    c,_,impl=research
    original=impl.sec_json
    def sec(url):
        result=original(url)
        if 'filings' in result:
            r=result['filings']['recent'];r['filingDate'][4]='2026-09-01'
            r['accessionNumber'][4]='0000000123-26-000005';r['items']=['','','','','2.02','']
        return result
    monkeypatch.setattr(impl,'sec_json',sec)
    def fail(url):raise ValueError('unavailable')
    monkeypatch.setattr(impl,'fetch_source',fail)
    packet=c.post('/api/research/discover',json={'ticker':'TEST'}).json()
    assert {s['metadata']['form'] for s in packet['sources']}=={'10-Q','8-K'}
    assert any('SEC exhibit discovery failed' in w for w in packet['warnings'])


def test_saved_brief_scope_reopen_export_and_immutable_snapshot(research):
    c,main,_=research;packet=discover(c)
    first,other=packet['sources'][:2]
    with main.db() as db:
        for source,value in [(first,'$100 million'),(other,'$999 million')]:
            db.execute("UPDATE research_sources SET state='ready',blocks=? WHERE id=?",(json.dumps(html_blocks(f'<h2>Results</h2><p>Revenue and revenue growth were {value} and 20%.</p><p>Operating margin was 15%.</p>'.encode())),source['id']))
    url='/api/research/packets/'+packet['id']+'/briefs'
    assert c.post(url,json={'source_ids':['foreign']}).status_code==422
    assert c.post(url,json={'source_ids':[]}).status_code==422
    response=c.post(url,json={'source_ids':[first['id']]})
    assert response.status_code==201,response.text
    brief=response.json();assert brief['mode']=='excerpts' and len(brief['sections'])==4
    assert len(brief['sources'])==1
    assert '$999' not in json.dumps(brief)
    assert '$100' in json.dumps(brief)
    assert all(e['source_id']==first['id'] for section in brief['sections'] for e in section['evidence'])
    assert not brief['sections'][2]['evidence']  # Missing cash flow remains explicit.
    with main.db() as db:db.execute("UPDATE research_sources SET blocks='[]' WHERE id=?",(first['id'],))
    assert c.get('/api/research/briefs/'+brief['id']).json()==brief
    assert c.get(url).json()[0]['id']==brief['id']
    export=c.get('/api/research/briefs/'+brief['id']+'/markdown')
    assert export.status_code==200 and 'Insufficient evidence' in export.text
    assert '$100' in export.text and 'https://www.sec.gov/' in export.text
    assert c.get('/api/research/briefs/missing').status_code==404
    failed=c.post(url,json={'source_ids':[first['id']],'synthesize':True})
    assert failed.status_code==409
    assert len(c.get(url).json())==1


def test_synthesized_brief_validates_citations_before_saving(research,monkeypatch):
    from types import SimpleNamespace
    c,main,_=research;packet=discover(c);source=packet['sources'][0]
    with main.db() as db:db.execute("UPDATE research_sources SET state='ready',blocks=? WHERE id=?",(json.dumps(html_blocks(b'<p>Revenue and revenue growth were $100 million and 20%.</p>')),source['id']))
    monkeypatch.setenv('OPENAI_API_KEY','fixture-not-a-real-key')
    monkeypatch.setenv('QUARTERLY_EXTRACTION_MODEL','fixture-model')
    bad=False
    def parse(**kwargs):
        evidence=json.loads(kwargs['input'][1]['content'])['evidence']
        citation='invented' if bad else evidence[0]['id']
        parsed=kwargs['text_format'](sufficient=True,claims=[{'text':'Reported revenue was $100 million.','citation_ids':[citation]}])
        return SimpleNamespace(status='completed',output_parsed=parsed,usage=None,model='fixture-model')
    monkeypatch.setattr('openai.OpenAI',lambda **kwargs:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    url='/api/research/packets/'+packet['id']+'/briefs'
    result=c.post(url,json={'source_ids':[source['id']],'synthesize':True})
    assert result.status_code==201,result.text
    assert result.json()['sections'][0]['answer']['claims'][0]['citation_ids']==[source['id']+':0']
    bad=True
    assert c.post(url,json={'source_ids':[source['id']],'synthesize':True}).status_code==502
    assert len(c.get(url).json())==1


def test_sec_directory_outage_uses_exa_candidate_but_verifies_sec_ticker(research,monkeypatch):
    from types import SimpleNamespace
    c,_,impl=research;original=impl.sec_json
    monkeypatch.setenv('EXA_API_KEY','fixture-only')
    def sec(url):
        if 'company_tickers' in url:raise ValueError('SEC 403')
        data=original(url);data['tickers']=['TEST'];return data
    monkeypatch.setattr(impl,'sec_json',sec)
    calls=[]
    def post(url,**kwargs):
        calls.append(kwargs['json'])
        items=[{'url':'https://www.sec.gov/edgar/browse/?CIK=123'}] if len(calls)==1 else [{'title':'Official release candidate','url':'https://example.com/results'}]
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'results':items})
    monkeypatch.setattr(impl.httpx,'post',post)
    response=c.post('/api/research/discover',json={'ticker':'TEST'})
    assert response.status_code==201,response.text
    packet=response.json()
    assert packet['company']=='Test Inc.' and packet['cik']=='0000000123'
    assert any('SEC company records verified' in w for w in packet['warnings'])
    assert any(s['metadata']['kind']=='web_candidate' for s in packet['sources'])
    assert len(calls)==2


def test_fallback_cannot_substitute_another_ticker(research,monkeypatch):
    from types import SimpleNamespace
    c,_,impl=research;original=impl.sec_json
    monkeypatch.setenv('EXA_API_KEY','fixture-only')
    def sec(url):
        if 'company_tickers' in url:raise ValueError('SEC 403')
        data=original(url);data['tickers']=['OTHER'];return data
    monkeypatch.setattr(impl,'sec_json',sec)
    monkeypatch.setattr(impl.httpx,'post',lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'results':[{'url':'https://www.sec.gov/edgar/browse/?CIK=123'}]}))
    response=c.post('/api/research/discover',json={'ticker':'TEST'})
    assert response.status_code==502 and 'could not verify' in response.json()['detail']
    assert c.get('/api/research/packets').json()==[]


def test_directory_outage_without_fallback_has_actionable_error(research,monkeypatch):
    c,_,impl=research
    def fail(url):raise ValueError('SEC 403')
    monkeypatch.setattr(impl,'sec_json',fail)
    r=c.post('/api/research/discover',json={'ticker':'TEST'})
    assert r.status_code==502 and 'SEC ticker directory is unavailable' in r.json()['detail']


def test_native_provider_config_and_saved_answer_without_openai(research,monkeypatch):
    c,main,_=research
    monkeypatch.setenv('LLAMA_CLOUD_API_KEY','fixture-only')
    config=c.get('/api/research/config').json()
    assert config['answers_configured'] and config['answer_provider']=='llamaparse'
    packet=discover(c);source=packet['sources'][0]
    with main.db() as db:db.execute("UPDATE research_sources SET state='ready',blocks=? WHERE id=?",(json.dumps(html_blocks(b'<p>Product revenue and revenue growth were $150 million and 50%.</p>')),source['id']))
    calls=[]
    def native(question,evidence):
        calls.append(question)
        return {'provider':'llamaparse','answer':{'sufficient':True,'claims':[{'text':'Product revenue was $150 million.','citation_ids':[evidence[0]['id']],'source_quote':evidence[0]['quote']}]},'provider_job_id':'test-job','model':'extract/cost_effective/latest','usage':None}
    monkeypatch.setattr('app.native_answers.generate_native_answer',native)
    result=c.post('/api/research/packets/'+packet['id']+'/ask',json={'question':'What product revenue was reported?','synthesize':True,'provider':'llamaparse'})
    assert result.status_code==200,result.text
    assert result.json()['provider']=='llamaparse' and result.json()['mode']=='synthesized'
    brief=c.post('/api/research/packets/'+packet['id']+'/briefs',json={'source_ids':[source['id']],'synthesize':True,'provider':'llamaparse'})
    assert brief.status_code==201,brief.text
    assert brief.json()['sections'][0]['provider']=='llamaparse'
    assert c.get('/api/research/briefs/'+brief.json()['id']).json()==brief.json()
    assert len(calls)>=2
    monkeypatch.setenv('LLAMA_CLOUD_API_KEY','')
    assert c.post('/api/research/packets/'+packet['id']+'/ask',json={'question':'What product revenue was reported?','synthesize':True,'provider':'llamaparse'}).status_code==409


def test_portable_demo_runs_on_empty_database_without_network(research,monkeypatch):
    c,main,impl=research
    def forbidden(*args,**kwargs):raise AssertionError('Demo must not call external services')
    monkeypatch.setattr(impl,'sec_json',forbidden)
    monkeypatch.setattr(impl,'fetch_source',forbidden)
    monkeypatch.setattr('app.native_answers.generate_native_answer',forbidden)
    assert c.get('/api/research/packets').json()==[]
    result=c.post('/api/demo/research/start')
    assert result.status_code==201,result.text
    packet=result.json();source=packet['sources'][0]
    assert source['state']=='ready' and source['metadata']['synthetic']
    assert 'fictional' in packet['company']
    assert 'Synthetic' in c.get(source['metadata']['url']).text
    answer=c.post('/api/research/packets/'+packet['id']+'/ask',json={'question':'What product revenue was reported?'}).json()
    assert any('$150 million' in e['quote'] for e in answer['evidence'])
    brief=c.post('/api/research/packets/'+packet['id']+'/briefs',json={'source_ids':[source['id']]}).json()
    assert brief['mode']=='excerpts' and len(brief['sections'])==4
    assert all(s['evidence'] for s in brief['sections'])
    assert c.get('/api/research/briefs/'+brief['id']).json()==brief
    assert '$150' in c.get('/api/research/briefs/'+brief['id']+'/markdown').text
    assert c.post('/api/demo/research/start').json()['id']!=packet['id']


def test_blocked_source_has_actionable_error_and_retry_preserves_other_sources(research,monkeypatch):
    import httpx
    c,main,impl=research;packet=discover(c);first,other=packet['sources'][:2]
    with main.db() as db:db.execute("UPDATE research_sources SET state='ready',blocks=? WHERE id=?",(json.dumps(html_blocks(b'<p>Product revenue was $100 million.</p>')),other['id']))
    def blocked(url):raise httpx.HTTPStatusError('403',request=httpx.Request('GET',url),response=httpx.Response(403))
    monkeypatch.setattr(impl,'fetch_source',blocked)
    endpoint='/api/research/packets/'+packet['id']+'/ingest'
    c.post(endpoint,json={'source_ids':[first['id']]})
    failed=wait_source(c,packet['id'],'failed')
    assert 'HTTP 403' in failed['error'] and 'investor-relations alternative' in failed['error']
    monkeypatch.setattr(impl,'fetch_source',lambda url:(b'<p>Revenue was $200 million.</p>',url,'text/html'))
    c.post(endpoint,json={'source_ids':[first['id']]})
    assert wait_source(c,packet['id'],'ready')['error'] is None
    sources=c.get('/api/research/packets/'+packet['id']).json()['sources']
    assert any(s['id']==other['id'] and '$100 million' in json.dumps(s['blocks']) for s in sources)
