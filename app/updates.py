"""Earnings update batches: extraction snapshots, review concurrency and exports."""
import csv
import io
import json
import os
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app.earnings import (PRESETS, PIPELINE_VERSION, SourceObservation, build_rows,
    compare, conservative_extract, period_key, resolve, structured_extract, validate_periods)


class MetricInput(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    definition: str = Field(min_length=1, max_length=2000)
    unit: str = Field(pattern='^(USD|USD_millions|USD_billions|percent|count|unresolved)$')


class UpdateRequest(BaseModel):
    prior_period: str = Field(max_length=100)
    current_period: str = Field(max_length=100)
    engine: Literal['tables', 'openai'] = 'tables'
    metric_ids: list[str] = Field(min_length=1, max_length=10)


class RowReview(BaseModel):
    revision: int = Field(ge=0)
    status: Literal['accepted', 'rejected', 'pending']
    confirmed_comparable: bool
    review_note: str = Field(max_length=2000)
    prior: SourceObservation | None
    current: SourceObservation | None


def install_tables(connection):
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS tracked_metrics (
      id TEXT PRIMARY KEY, company TEXT, name TEXT, aliases TEXT, definition TEXT, unit TEXT,
      UNIQUE(company, name));
    CREATE TABLE IF NOT EXISTS update_batches (
      id TEXT PRIMARY KEY, run_id TEXT, state TEXT, config TEXT, result TEXT,
      error TEXT, created REAL, duration REAL, metadata TEXT);
    CREATE TABLE IF NOT EXISTS update_reviews (
      id TEXT PRIMARY KEY, batch_id TEXT, metric_id TEXT, before TEXT, after TEXT, created REAL);
    ''')
    connection.execute("UPDATE update_batches SET state='failed', error='Server restarted. Create a new update.' WHERE state IN ('queued','running')")


def create_router(db, data, pool, run_detail, document, safe_cell):
    router = APIRouter()

    def serialize_metric(row):
        return {**dict(row), 'aliases': json.loads(row['aliases'])}

    @router.get('/api/metrics')
    def metrics(company: str):
        company = company.strip().casefold()
        if not company:
            raise HTTPException(422, 'Company is required.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM tracked_metrics WHERE company=?', (company,)).fetchone():
                for preset in PRESETS:
                    c.execute('INSERT INTO tracked_metrics VALUES (?,?,?,?,?,?)', (uuid.uuid4().hex,
                        company, preset['name'], json.dumps(preset['aliases']), preset['definition'], preset['unit']))
            return [serialize_metric(r) for r in c.execute('SELECT * FROM tracked_metrics WHERE company=? ORDER BY rowid', (company,))]

    @router.post('/api/metrics', status_code=201)
    def add_metric(request: MetricInput):
        if not request.company.strip() or not request.name.strip() or not request.definition.strip():
            raise HTTPException(422, 'Company, metric and definition must contain text.')
        if any(not a.strip() or len(a) > 200 for a in request.aliases):
            raise HTTPException(422, 'Aliases must contain 1–200 characters.')
        with db() as c:
            import sqlite3
            try:
                metric_id = uuid.uuid4().hex
                c.execute('INSERT INTO tracked_metrics VALUES (?,?,?,?,?,?)', (metric_id,
                    request.company.strip().casefold(), request.name.strip(), json.dumps(request.aliases), request.definition, request.unit))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'That metric name already exists for this company.')
            return serialize_metric(c.execute('SELECT * FROM tracked_metrics WHERE id=?', (metric_id,)).fetchone())

    def pages_for(batch):
        source = run_detail(batch['run_id'])
        if source['state'] != 'succeeded':
            raise HTTPException(409, 'Source processing must succeed first.')
        return source['pages']

    def execute(batch_id, pages, selected_metrics, config):
        start = time.monotonic()
        with db() as c:
            c.execute("UPDATE update_batches SET state='running' WHERE id=?", (batch_id,))
        try:
            if config['engine'] == 'tables':
                extracted = conservative_extract(pages, selected_metrics, config['prior_period'], config['current_period'])
                metadata = {'engine': 'deterministic_table_mapping', 'version': PIPELINE_VERSION,
                            'usage': None, 'note': 'Configured aliases; no model call or parser spend.'}
            else:
                extracted, metadata = structured_extract(pages, selected_metrics,
                    config['prior_period'], config['current_period'], config['model'])
            rows = build_rows(extracted, selected_metrics, config['prior_period'], config['current_period'], pages)
            with db() as c:
                c.execute("UPDATE update_batches SET state='succeeded', result=?, duration=?, metadata=? WHERE id=?",
                    (json.dumps(rows), time.monotonic()-start, json.dumps(metadata), batch_id))
        except Exception as exc:
            with db() as c:
                c.execute("UPDATE update_batches SET state='failed', error=?, duration=? WHERE id=?",
                    ('Extraction failed ('+type(exc).__name__+'). No review values were accepted. Verify configuration and try a new update.', time.monotonic()-start, batch_id))

    @router.post('/api/runs/{run_id}/updates', status_code=202)
    def create_update(run_id: str, request: UpdateRequest):
        source = run_detail(run_id)
        if source['state'] != 'succeeded' or not source['pages']:
            raise HTTPException(409, 'Select a successfully parsed run containing source pages.')
        try:
            validate_periods(request.prior_period, request.current_period)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if len(request.metric_ids) != len(set(request.metric_ids)):
            raise HTTPException(422, 'Select each tracked metric once.')
        company = document(source['document_id'])['company'].strip().casefold()
        with db() as c:
            selected_metrics = []
            for metric_id in request.metric_ids:
                row = c.execute('SELECT * FROM tracked_metrics WHERE id=? AND company=?', (metric_id, company)).fetchone()
                if not row:
                    raise HTTPException(422, 'Metric does not belong to this company.')
                selected_metrics.append(serialize_metric(row))
        if sum(len(p['markdown']) for p in source['pages']) > 100000:
            raise HTTPException(422, 'Select a smaller source run: maximum 100,000 parsed characters per update.')
        model = os.getenv('QUARTERLY_EXTRACTION_MODEL', '').strip()
        if request.engine == 'openai' and (not os.getenv('OPENAI_API_KEY') or not model):
            raise HTTPException(409, 'Configure OPENAI_API_KEY and QUARTERLY_EXTRACTION_MODEL, then restart. Table mapping works without them.')
        config = {**request.model_dump(), 'prior_period': period_key(request.prior_period),
                  'current_period': period_key(request.current_period), 'metrics': selected_metrics,
                  'model': model if request.engine == 'openai' else None, 'pipeline_version': PIPELINE_VERSION}
        batch_id = uuid.uuid4().hex
        with db() as c:
            c.execute('INSERT INTO update_batches VALUES (?,?,?,?,?,?,?,?,?)',
                (batch_id, run_id, 'queued', json.dumps(config), None, None, time.time(), None, None))
        pool.submit(execute, batch_id, source['pages'], selected_metrics, config)
        return {'id': batch_id}

    @router.get('/api/runs/{run_id}/updates')
    def list_updates(run_id: str):
        run_detail(run_id)
        with db() as c:
            result = []
            for row in c.execute('SELECT id, state, config, created FROM update_batches WHERE run_id=? ORDER BY created DESC', (run_id,)):
                result.append({**dict(row), 'config': json.loads(row['config'])})
            return result

    def get_batch(batch_id):
        with db() as c:
            row = c.execute('SELECT * FROM update_batches WHERE id=?', (batch_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Update not found.')
        return dict(row)

    @router.get('/api/updates/{batch_id}')
    def detail(batch_id: str):
        batch = get_batch(batch_id)
        rows = json.loads(batch['result']) if batch['result'] else []
        for row in rows:
            row['comparison'] = compare(row['prior'], row['current'], row['confirmed_comparable'])
        source = run_detail(batch['run_id'])
        return {**batch, 'config': json.loads(batch['config']), 'metadata': json.loads(batch['metadata']) if batch['metadata'] else None,
                'result': rows, 'document': document(source['document_id']),
                'summary': {'total': len(rows), 'accepted': sum(r['status']=='accepted' for r in rows),
                            'pending': sum(r['status']=='pending' for r in rows),
                            'rejected': sum(r['status']=='rejected' for r in rows)}}

    @router.patch('/api/updates/{batch_id}/rows/{metric_id}')
    def review(batch_id: str, metric_id: str, request: RowReview):
        batch = get_batch(batch_id)
        if batch['state'] != 'succeeded':
            raise HTTPException(409, 'Wait for extraction to finish.')
        config = json.loads(batch['config'])
        pages = pages_for(batch)
        resolved = {}
        for side in ('prior', 'current'):
            observation = getattr(request, side)
            try:
                resolved[side] = resolve([observation.model_dump()] if observation else [], config[side+'_period'], pages)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        if request.status == 'accepted':
            current = resolved['current']
            if resolved['prior']['state'] not in ('found', 'missing'):
                raise HTTPException(422, 'Resolve the unsupported prior value or mark it missing before acceptance.')
            if current['state'] != 'found':
                raise HTTPException(422, 'Acceptance requires a current value supported by the selected source page.')
            if current['observation']['unit'] == 'unresolved' or current['observation']['classification'] == 'unresolved':
                raise HTTPException(422, 'Resolve current units and actual/guidance before acceptance.')
            if not current['observation']['definition'].strip():
                raise HTTPException(422, 'Record the current metric definition or accounting context.')
            if not request.review_note.strip():
                raise HTTPException(422, 'Add a review note confirming the source and financial context.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            fresh = c.execute('SELECT result FROM update_batches WHERE id=?', (batch_id,)).fetchone()
            rows = json.loads(fresh['result'])
            row = next((r for r in rows if r['metric_id'] == metric_id), None)
            if row is None:
                raise HTTPException(404, 'Tracked metric not found in this update.')
            if row['revision'] != request.revision:
                raise HTTPException(409, 'This row changed in another review. Reload before saving.')
            before = json.dumps(row)
            row.update(resolved)
            row.update({'status': request.status, 'confirmed_comparable': request.confirmed_comparable,
                        'review_note': request.review_note, 'revision': row['revision']+1})
            c.execute('UPDATE update_batches SET result=? WHERE id=?', (json.dumps(rows), batch_id))
            c.execute('INSERT INTO update_reviews VALUES (?,?,?,?,?,?)',
                      (uuid.uuid4().hex, batch_id, metric_id, before, json.dumps(row), time.time()))
        return detail(batch_id)

    @router.get('/api/updates/{batch_id}/audit')
    def audit(batch_id: str):
        get_batch(batch_id)
        with db() as c:
            return [{**dict(r), 'before': json.loads(r['before']), 'after': json.loads(r['after'])}
                    for r in c.execute('SELECT * FROM update_reviews WHERE batch_id=? ORDER BY created', (batch_id,))]

    @router.get('/api/updates/{batch_id}/export.csv')
    def export(batch_id: str):
        batch = detail(batch_id)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['company', 'metric', 'prior_period', 'prior_value', 'prior_unit', 'current_period',
                         'current_value', 'current_unit', 'classification', 'change', 'change_kind',
                         'comparison_note', 'source_url', 'prior_pdf_page', 'current_pdf_page', 'source_quote', 'review_note'])
        for row in batch['result']:
            if row['status'] != 'accepted':
                continue
            p = row['prior']['observation'] or {}
            c = row['current']['observation']
            delta = row['comparison']
            values = [batch['document']['company'], row['metric'], batch['config']['prior_period'],
                p.get('value',''), p.get('unit',''), batch['config']['current_period'], c['value'], c['unit'],
                c['classification'], delta.get('value',''), delta.get('kind',''), delta['reason'],
                batch['document']['source'], p.get('page',''), c['page'], c['quote'], row['review_note']]
            writer.writerow([safe_cell(v) for v in values])
        return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition':'attachment; filename=quarterly-update.csv'})

    @router.get('/api/updates/{batch_id}/brief.md')
    def brief(batch_id: str):
        batch = detail(batch_id)
        # Escape user/source text before generating Markdown; source links use local IDs.
        def esc(value):
            value = str(value).replace('\n',' ').replace('\r',' ')
            return ''.join('\\'+char if char in '\\`*_{}[]<>|#!' else char for char in value)
        lines = [f"# {esc(batch['document']['company'])} — earnings update",
                 f"\n{esc(batch['config']['prior_period'])} → {esc(batch['config']['current_period'])}",
                 '\nReviewed observations only. Changes are deterministic; no investment recommendation.', '\n## Accepted updates']
        accepted = [r for r in batch['result'] if r['status']=='accepted']
        if not accepted:
            lines.append('\nNo accepted updates yet.')
        for row in accepted:
            current = row['current']['observation']
            delta = row['comparison']
            change_text = (delta['value']+' '+delta['kind'].replace('_',' ')) if delta['state']=='ready' else delta['reason']
            doc_link = f"/api/documents/{batch['document']['id']}/pdf#page={current['page']}"
            lines.append(f"\n- **{esc(row['metric'])}**: {esc(current['value'])} {esc(current['unit'])} ({esc(current['classification'])}). Change: {esc(change_text)}. [Source, PDF page {current['page']}]({doc_link}). Review: {esc(row['review_note'])}")
        lines.extend(['\n## Unresolved or rejected items'])
        for row in batch['result']:
            if row['status'] != 'accepted':
                lines.append(f"\n- {esc(row['metric'])}: {row['status']}; prior: {row['prior']['state']}; current: {row['current']['state']}.")
        lines.append('\nScope: selected parsed pages only. Source links require the local app; not a complete document audit.')
        return Response('\n'.join(lines)+'\n', media_type='text/markdown',
                        headers={'Content-Disposition':'attachment; filename=quarterly-brief.md'})

    # Curated demos reuse real saved parses; launching never triggers paid APIs.
    demo_stories = [
        {'id': 'performance', 'title': 'What changed year over year?', 'company': 'Snowflake',
         'subtitle': 'Update five operating metrics from FY2025 to FY2026.',
         'prior_period': 'FY2025', 'current_period': 'FY2026',
         'steps': ['Inspect the product revenue source row and its FY2025/FY2026 column headers.',
                   'Review definitions and units; confirm comparability and add your review note.',
                   'Accept an update, then export the tracker and brief.'],
         'lesson': 'Source-linked metric updates, reviewed calculations and an auditable export.'},
        {'id': 'guidance', 'title': 'Actuals or forward guidance?', 'company': 'Snowflake',
         'subtitle': 'Compare FY2026 actuals with FY2027 guidance without confusing the two.',
         'prior_period': 'FY2026', 'current_period': 'FY2027',
         'steps': ['Inspect the historical and guidance columns on the original slide.',
                   'Keep the classifications as actual and guidance when reviewing the values.',
                   'Confirm definitions and save: like-for-like change remains blocked because contexts differ.'],
         'lesson': 'A plausible number can still support a misleading comparison. Context matters.'},
        {'id': 'missing', 'title': 'Know when evidence is missing', 'company': 'Snowflake',
         'subtitle': 'Request remaining performance obligations from a page that does not contain it.',
         'prior_period': 'FY2025', 'current_period': 'FY2026',
         'steps': ['Review the supported product revenue observation.',
                   'Open the missing-evidence filter and inspect remaining performance obligations.',
                   'Download the brief: unresolved metrics stay explicit rather than becoming fabricated values.'],
         'lesson': 'Not found in selected pages is different from not disclosed by the company.'},
    ]

    def demo_source():
        with db() as c:
            sources = c.execute("SELECT r.id, r.document_id FROM runs r JOIN documents d ON r.document_id=d.id WHERE lower(trim(d.company))='snowflake' AND r.state='succeeded' ORDER BY CASE WHEN r.provider='llamaparse' THEN 0 ELSE 1 END, r.created DESC").fetchall()
        for source in sources:
            parsed = run_detail(source['id'])
            page = next((p for p in parsed['pages'] if p['page'] == 25), None)
            if page and '4,472' in page['markdown'] and '5,660' in page['markdown']:
                return source, parsed
        return None, None

    @router.get('/api/demo/scenarios')
    def demo_catalog():
        source, _ = demo_source()
        return {'scenarios': [{**story, 'available': source is not None} for story in demo_stories],
                'source_note': 'Real Snowflake Q4 FY2026 investor presentation, PDF page 25. Historical sample, not latest results. Launch uses a saved parse and no cloud API calls.',
                'next_companies': [
                    {'name': 'ServiceNow', 'use_case': 'Subscription revenue and current remaining performance obligations.',
                     'url': 'https://investor.servicenow.com/overview/default.aspx', 'status': 'Research candidate; not loaded'},
                    {'name': 'Datadog', 'use_case': 'Revenue, large-customer counts and qualified retention disclosures.',
                     'url': 'https://investors.datadoghq.com/financial-information/quarterly-results', 'status': 'Research candidate; not loaded'}]}

    @router.post('/api/demo/scenarios/{scenario_id}/start', status_code=202)
    def start_demo(scenario_id: str):
        story = next((s for s in demo_stories if s['id'] == scenario_id), None)
        if not story:
            raise HTTPException(404, 'Demo scenario not found.')
        source, _ = demo_source()
        if source is None:
            raise HTTPException(409, 'The curated real sample is not available. Import or parse the Snowflake presentation page 25 first.')
        doc = document(source['document_id'])
        available_metrics = metrics(doc['company'])
        if scenario_id == 'missing':
            name = 'Remaining performance obligations'
            matching = next((m for m in available_metrics if m['name'] == name), None)
            if matching is None:
                matching = add_metric(MetricInput(company=doc['company'], name=name,
                    aliases=['Remaining Performance Obligations ($M)', 'Remaining Performance Obligations'],
                    definition='Company-defined contracted revenue not yet recognized. Not present on selected demo page.', unit='USD_millions'))
            selected = [next(m for m in available_metrics if m['name'] == PRESETS[0]['name']), matching]
        else:
            selected = [m for preset in PRESETS for m in available_metrics if m['name'] == preset['name']]
        created = create_update(source['id'], UpdateRequest(prior_period=story['prior_period'],
            current_period=story['current_period'], engine='tables', metric_ids=[m['id'] for m in selected]))
        return {'batch_id': created['id'], 'run_id': source['id'], 'document': doc, 'story': story,
                'mode': 'fresh_review_session_from_real_saved_parse'}

    return router
