import importlib
import json
import time
import pytest
from fastapi.testclient import TestClient
from app.library import canonical_url


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setenv('QUARTERLY_DATA_DIR',str(tmp_path))
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as client:
        with main.db() as c:
            c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',('doc','Earnings.pdf','hash1','Test','FY2026','https://example.com/earnings.pdf',30,time.time()))
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)',('run','doc','llamaparse','succeeded',None,1,time.time()))
            for index in (1,2):
                c.execute('INSERT INTO research_packets (id,ticker,company,cik,as_of,created,warnings,mode) VALUES (?,?,?,?,?,?,?,?)',('p'+str(index),'TEST','Test Inc.','123','2026-11-16',time.time()+index,'[]','latest'))
                metadata={'title':'Earnings report','url':'https://example.com/earnings.pdf?utm_source=demo','kind':'local_parsed_pdf','period_label':'FY2026','discovery':'Local library','document_id':'doc','run_id':'run'}
                c.execute('INSERT INTO research_sources VALUES (?,?,?,?,?,?,?)',('s'+str(index),'p'+str(index),json.dumps(metadata),'ready',json.dumps([{'locator':'PDF page 25','text':'Revenue was $100.'}]),None,'hash'+str(index)))
        yield client,main


def test_registry_groups_url_appearances_and_preserves_versions(library):
    client,main=library
    result=client.get('/api/library').json()
    assert result['summary']['total']==1 and result['summary']['appearances']==3
    source=result['sources'][0]
    assert source['version_count']==2
    assert set(source['packet_ids'])=={'p1','p2'}
    assert source['document_id']=='doc' and source['run_id']=='run'
    assert source['state']=='ready' and source['evidence_count']==1
    assert 'full document' in source['coverage_note']
    assert client.get('/api/library').json()['sources'][0]['id']==source['id']
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM research_sources').fetchone()[0]==2


def test_notes_tags_persist_and_do_not_change_evidence(library):
    client,main=library
    source=client.get('/api/library').json()['sources'][0]
    response=client.patch('/api/library/'+source['id'],json={'notes':'Check guidance next.','tags':[' earnings ','watchlist','earnings']})
    assert response.status_code==200
    saved=client.get('/api/library').json()['sources'][0]
    assert saved['notes']=='Check guidance next.' and saved['tags']==['earnings','watchlist']
    with main.db() as c:
        assert json.loads(c.execute("SELECT blocks FROM research_sources WHERE id='s1'").fetchone()[0])[0]['text']=='Revenue was $100.'
    assert client.patch('/api/library/missing',json={'notes':'x'}).status_code==404
    assert client.patch('/api/library/'+source['id'],json={'tags':['x'*61]}).status_code==422


def test_link_only_and_uploaded_sources_are_not_marked_read(library):
    client,main=library
    with main.db() as c:
        c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',('new','Unread.pdf','newhash','Other','Unresolved','',10,time.time()))
        c.execute('INSERT INTO research_sources VALUES (?,?,?,?,?,?,?)',('newsource','p1',json.dumps({'title':'Link only','url':'https://example.com/unread','kind':'sec_filing','form':'10-Q'}),'discovered','[]',None,None))
    entries=client.get('/api/library').json()['sources']
    assert next(s for s in entries if s['title']=='Unread.pdf')['state']=='downloaded'
    assert next(s for s in entries if s['title']=='Link only')['state']=='discovered'


def test_url_identity_preserves_query_values_but_ignores_tracking_and_fragments():
    assert canonical_url('https://EXAMPLE.com/report?utm_source=x#page=5')=='https://example.com/report'
    assert canonical_url('https://example.com/report?version=2')!='https://example.com/report?version=1'
    assert canonical_url('javascript:alert(1)') is None


def test_archive_restore_and_bulk_tags_are_reversible(library):
    client,main=library
    entry=client.get('/api/library').json()['sources'][0];source_id=entry['id']
    assert entry['archived'] is False
    assert client.post('/api/library/bulk',json={'source_ids':[source_id],'action':'archive'}).status_code==200
    assert client.get('/api/library').json()['sources'][0]['archived'] is True
    assert client.post('/api/library/bulk',json={'source_ids':[source_id],'action':'add_tags','tags':['earnings','watchlist']}).status_code==200
    assert client.post('/api/library/bulk',json={'source_ids':[source_id],'action':'restore'}).status_code==200
    restored=client.get('/api/library').json()['sources'][0]
    assert restored['archived'] is False and restored['tags']==['earnings','watchlist']
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM research_sources').fetchone()[0]==2


def test_invalid_bulk_selection_changes_nothing(library):
    client,_=library
    source_id=client.get('/api/library').json()['sources'][0]['id']
    assert client.post('/api/library/bulk',json={'source_ids':[source_id,'missing'],'action':'archive'}).status_code==404
    assert client.get('/api/library').json()['sources'][0]['archived'] is False
