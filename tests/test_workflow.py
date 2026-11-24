import io
import importlib
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from app.core import normalize, change, table_candidates


def test_numeric_semantics():
    assert normalize('($1,250.50)') == '-1250.50'
    for value in ['NaN', 'Infinity', '', 'not available']:
        with pytest.raises(ValueError):
            normalize(value)
    assert change('20','25','percent','percent')['value'] == '5'
    assert change('0','1','USD','USD')['status'] == 'zero_denominator'
    assert change('1','2','USD','USD_millions')['status'] == 'not_comparable'


def test_table_geometry_and_missing_values():
    html='<table><tr><th></th><th>FY26</th></tr><tr><td>Revenue</td><td>—</td></tr><tr><td>Margin</td><td>25%</td></tr></table>'
    out=table_candidates([{'page':25,'markdown':html}])
    assert len(out)==1 and out[0]['value']=='25' and out[0]['page']==25
    assert table_candidates([{'page':1,'markdown':html.replace('<th>FY26','<th colspan="2">FY26')}]) == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUARTERLY_DATA_DIR', str(tmp_path))
    import app.main as module
    importlib.reload(module)
    with TestClient(module.app) as client:
        yield client, module


def test_review_export_and_audit(client):
    client, module=client
    writer=PdfWriter(); writer.add_blank_page(width=100,height=100)
    content=io.BytesIO(); writer.write(content)
    def upload():
        return client.post('/api/documents',files={'file':('sample.pdf',content.getvalue(),'application/pdf')},data={'company':'Test','period':'FY26'})
    first=upload(); assert first.status_code==200
    assert upload().json()['duplicate'] is True
    doc=first.json()['id']
    image=client.get(f'/api/documents/{doc}/pages/1/image')
    assert image.status_code==200 and image.content.startswith(b'\x89PNG')
    assert client.get(f'/api/documents/{doc}/pages/2/image').status_code==404
    assert client.post(f'/api/documents/{doc}/runs',json={'provider':'baseline','pages':[2]}).status_code==400
    run=client.post(f'/api/documents/{doc}/runs',json={'provider':'baseline','pages':[1]}).json()['id']
    # Wait for the submitted local worker before adding independent test evidence.
    import time
    for _ in range(100):
        if client.get('/api/runs/'+run).json()['state'] in ('succeeded','failed'): break
        time.sleep(.01)
    with module.db() as c:
        c.execute("UPDATE runs SET state='running' WHERE id=?",(run,))
    module.finish_run(run,[{'page':1,'markdown':'| Metric | FY26 |\n|---|---|\n| Revenue | 100 |'}],0)
    module.finish_run(run,[{'page':1,'markdown':'| Metric | FY26 |\n|---|---|\n| Revenue | 999 |'}],0)
    assert len(client.get('/api/runs/'+run).json()['observations']) == 1
    obs=client.get('/api/runs/'+run).json()['observations'][0]
    review={'metric':'Revenue','value':'105','period':'FY26','unit':'unresolved','classification':'actual','status':'accepted'}
    assert client.patch('/api/observations/'+obs['id'],json=review).status_code==422
    assert 'Revenue' not in client.get('/api/runs/'+run+'/export.csv').text
    review['unit']='USD_millions'
    assert client.patch('/api/observations/'+obs['id'],json=review).status_code==200
    assert '105' in client.get('/api/runs/'+run+'/export.csv').text
    with module.db() as c:
        original=c.execute('SELECT original FROM observations WHERE id=?',(obs['id'],)).fetchone()[0]
        assert '100' in original
        assert c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==1
    assert client.get('/api/documents/nonexistent/pdf').status_code==404
