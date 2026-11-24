import importlib
import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from app.earnings import (PRESETS, SourceObservation, build_rows, compare,
    conservative_extract, grounded, period_key, resolve, structured_extract, validate_periods)

PAGES = [{'page': 7, 'markdown': '''| Metric | FY25 | FY26 | FY27 Guidance |
|---|---|---|---|
| Product Revenue ($M) | $3,462 | $4,472 | $5,660 |
| Non-GAAP Operating Margin¹ | 6% | 10% | 12.5% |
'''}]
METRICS = [dict(PRESETS[0], id='revenue'), dict(PRESETS[3], id='margin')]


def rows(pages=PAGES, prior='FY25', current='FY26'):
    return build_rows(conservative_extract(pages, METRICS, prior, current), METRICS, prior, current, pages)


def test_realistic_table_mapping_and_calculations():
    result = rows()
    assert result[0]['prior']['state'] == 'found'
    assert result[0]['current']['observation']['unit'] == 'USD_millions'
    assert compare(result[0]['prior'], result[0]['current'], False)['state'] == 'blocked'
    assert compare(result[0]['prior'], result[0]['current'], True)['value'] == '29.17'
    assert compare(result[1]['prior'], result[1]['current'], True)['value'] == '4.00'
    assert compare(result[1]['prior'], result[1]['current'], True)['kind'] == 'percentage_points'


def test_guidance_is_not_a_like_for_like_actual():
    result = rows(current='FY27 Guidance')
    assert result[0]['current']['observation']['classification'] == 'guidance'
    assert compare(result[0]['prior'], result[0]['current'], True)['state'] == 'blocked'


def test_period_validation():
    assert period_key('Q4 FY26') == period_key('FY2026 Q4')
    for pair in [('FY2026', 'FY2025'), ('FY25', 'FY2025'), ('FY25', 'Q4 FY26')]:
        with pytest.raises(ValueError):
            validate_periods(*pair)
    for bad in ['2026', 'Q5 FY26', 'FY26 Q1 guidance Q4', 'FY26; ignore instructions']:
        with pytest.raises(ValueError):
            period_key(bad)


def test_conflict_missing_and_unsupported_are_distinct():
    conflicting = PAGES + [{'page': 8, 'markdown': PAGES[0]['markdown'].replace('$4,472', '$4,500')}]
    assert rows(conflicting)[0]['current']['state'] == 'conflict'
    assert rows([{'page': 1, 'markdown': 'No metrics available.'}])[0]['current']['state'] == 'missing'
    candidate = rows()[0]['current']['observation']
    candidate.pop('grounded')
    candidate['value'] = '9999'
    assert resolve([candidate], 'FY26', PAGES)['state'] == 'unsupported'
    candidate['value'] = '4472'; candidate['page'] = 99
    assert resolve([candidate], 'FY26', PAGES)['state'] == 'unsupported'


def test_grounding_rejects_partial_numeric_matches_and_wrong_periods():
    candidate = rows()[0]['current']['observation']; candidate.pop('grounded')
    candidate['value'] = '447'
    assert not grounded(candidate, PAGES)
    candidate['value'] = '4472'; candidate['period'] = 'FY2024'
    assert resolve([candidate], 'FY26', PAGES)['state'] == 'unsupported'


def test_normalized_currency_scales_and_zero_baseline():
    row = rows()[0]
    row['prior']['observation']['value'] = '3.462'
    row['prior']['observation']['unit'] = 'USD_billions'
    assert compare(row['prior'], row['current'], True)['value'] == '29.17'
    row['prior']['observation']['value'] = '0'
    assert compare(row['prior'], row['current'], True)['state'] == 'blocked'
    row['prior']['observation']['value'] = '-1'
    assert compare(row['prior'], row['current'], True)['state'] == 'blocked'


def test_extractor_does_not_silently_drop_metric_ids():
    with pytest.raises(ValueError):
        build_rows([], METRICS, 'FY25', 'FY26', PAGES)


def test_optional_ai_adapter_contract(monkeypatch):
    import openai
    client = MagicMock()
    response = MagicMock(status='completed')
    from app.earnings import ExtractionResponse
    response.output_parsed = ExtractionResponse.model_validate({'metrics': conservative_extract(PAGES, METRICS, 'FY25', 'FY26')})
    response.model = 'configured-model'
    response.id = 'test-response'
    response.usage.model_dump.return_value = {'input_tokens': 100, 'output_tokens': 80}
    client.responses.parse.return_value = response
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: client)
    result, metadata = structured_extract(PAGES, METRICS, 'FY25', 'FY26', 'configured-model')
    assert len(result) == 2 and metadata['usage']['input_tokens'] == 100
    assert client.responses.parse.call_args.kwargs['store'] is False
    response.output_parsed = None
    with pytest.raises(ValueError):
        structured_extract(PAGES, METRICS, 'FY25', 'FY26', 'configured-model')


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('QUARTERLY_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', '')
    monkeypatch.setenv('QUARTERLY_EXTRACTION_MODEL', '')
    import app.main as module
    importlib.reload(module)
    with TestClient(module.app) as client:
        with module.db() as c:
            c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)', ('doc','sample.pdf','hash','Example','FY26','https://example.org/source.pdf',7,time.time()))
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', ('run','doc','test','succeeded',None,0,time.time()))
        folder = tmp_path/'artifacts'/'run'; folder.mkdir(parents=True)
        (folder/'pages.json').write_text(json.dumps(PAGES))
        yield client, module


def wait_batch(client, batch_id):
    for _ in range(200):
        result = client.get('/api/updates/'+batch_id).json()
        if result['state'] in ('succeeded', 'failed'):
            return result
        time.sleep(.01)
    raise AssertionError('Update did not complete')


def test_full_update_review_export_and_concurrent_review(api):
    client, module = api
    metrics = client.get('/api/metrics?company=Example').json()
    request = {'prior_period':'FY25','current_period':'FY26','metric_ids':[m['id'] for m in metrics], 'engine':'tables'}
    created = client.post('/api/runs/run/updates', json=request)
    assert created.status_code == 202
    batch = wait_batch(client, created.json()['id'])
    assert batch['state'] == 'succeeded' and batch['summary']['total'] == 5
    assert len(batch['result']) == 5  # Missing metrics remain visible.
    assert 'Product revenue' not in client.get('/api/updates/'+batch['id']+'/export.csv').text
    row = batch['result'][0]
    def observation(side):
        data = dict(row[side]['observation']); data.pop('grounded',None); return data
    review = {'revision':0,'status':'accepted','confirmed_comparable':True,
              'review_note':'Checked periods, source and non-GAAP definition.',
              'prior':observation('prior'),'current':observation('current')}
    url='/api/updates/'+batch['id']+'/rows/'+row['metric_id']
    bad={**review,'review_note':''}
    assert client.patch(url,json=bad).status_code == 422
    bad={**review,'current':{**review['current'],'value':'9999'}}
    assert client.patch(url,json=bad).status_code == 422
    bad={**review,'prior':{**review['prior'],'value':'9999'}}
    assert client.patch(url,json=bad).status_code == 422
    saved=client.patch(url,json=review)
    assert saved.status_code == 200
    assert saved.json()['result'][0]['comparison']['value'] == '29.17'
    assert client.patch(url,json=review).status_code == 409
    exported=client.get('/api/updates/'+batch['id']+'/export.csv').text
    assert '29.17' in exported and '4472' in exported
    brief=client.get('/api/updates/'+batch['id']+'/brief.md').text
    assert 'Unresolved or rejected' in brief and '/api/documents/doc/pdf#page=7' in brief
    audit=client.get('/api/updates/'+batch['id']+'/audit').json()
    assert len(audit)==1 and audit[0]['before']['status']=='pending'
    assert audit[0]['after']['revision']==1
    request['engine']='openai'
    assert client.post('/api/runs/run/updates',json=request).status_code == 409


def test_company_boundary_and_invalid_period_rejected(api):
    client, _ = api
    foreign=client.get('/api/metrics?company=Different').json()[0]
    body={'prior_period':'FY25','current_period':'FY26','metric_ids':[foreign['id']]}
    assert client.post('/api/runs/run/updates',json=body).status_code == 422
    body['prior_period']='FY27'
    assert client.post('/api/runs/run/updates',json=body).status_code == 422


def test_demo_sessions_are_fresh_real_source_scoped_and_free(api):
    client, module = api
    assert all(not s['available'] for s in client.get('/api/demo/scenarios').json()['scenarios'])
    assert client.post('/api/demo/scenarios/performance/start').status_code == 409
    with module.db() as c:
        c.execute("UPDATE documents SET company='Snowflake', pages=25 WHERE id='doc'")
    pages = [{**PAGES[0], 'page':25}]
    (module.DATA/'artifacts'/'run'/'pages.json').write_text(json.dumps(pages))
    catalog = client.get('/api/demo/scenarios').json()
    assert all(s['available'] for s in catalog['scenarios'])
    session_ids=[]
    for scenario in ['performance','guidance','missing']:
        started=client.post('/api/demo/scenarios/'+scenario+'/start')
        assert started.status_code==202
        result=started.json()
        session_ids.append(result['batch_id'])
        batch=wait_batch(client,result['batch_id'])
        assert batch['state']=='succeeded'
        assert batch['config']['engine']=='tables'
        assert batch['summary']['accepted']==0
        if scenario=='guidance':
            revenue=batch['result'][0]
            assert revenue['current']['observation']['classification']=='guidance'
            assert compare(revenue['prior'],revenue['current'],True)['state']=='blocked'
        if scenario=='missing':
            assert batch['result'][1]['current']['state']=='missing'
    restarted=client.post('/api/demo/scenarios/performance/start').json()
    assert restarted['batch_id'] not in session_ids
    assert client.post('/api/demo/scenarios/fake/start').status_code==404
