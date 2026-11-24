import csv
import hashlib
import io
import json
import os
import sqlite3
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader, PdfWriter
from app.core import normalize, table_candidates
from app.updates import install_tables, create_router
from app.research import install_research, create_research_router
from app.library import install_library, create_library_router

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('QUARTERLY_DATA_DIR', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
POOL = ThreadPoolExecutor(max_workers=1)
PDF_RENDER_LOCK = threading.Lock()


def db():
    connection = sqlite3.connect(DATA / 'quarterly.sqlite', timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, name TEXT, hash TEXT UNIQUE, company TEXT,
          period TEXT, source TEXT, pages INTEGER, created REAL);
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, document_id TEXT, provider TEXT, state TEXT,
          error TEXT, duration REAL, created REAL);
        CREATE TABLE IF NOT EXISTS observations (
          id TEXT PRIMARY KEY, run_id TEXT, original TEXT, current TEXT);
        CREATE TABLE IF NOT EXISTS reviews (
          id TEXT PRIMARY KEY, observation_id TEXT, before TEXT, after TEXT, created REAL);
        ''')
        install_tables(c)
        install_research(c)
        install_library(c)
        c.execute("UPDATE runs SET state='failed', error='Process restarted. Retry the run.' WHERE state IN ('queued','running')")


@asynccontextmanager
async def lifespan(app):
    init()
    yield

app = FastAPI(title='Quarterly', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')


@app.get('/')
def home():
    return FileResponse(ROOT / 'app/static/index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'llamaparse_configured': bool(os.getenv('LLAMA_CLOUD_API_KEY')),
            'scope': 'local_single_user', 'extraction': 'tracked_metric_updates',
            'structured_extraction_configured': bool(os.getenv('OPENAI_API_KEY') and os.getenv('QUARTERLY_EXTRACTION_MODEL'))}


@app.get('/api/documents')
def documents():
    with db() as c:
        return [dict(row) for row in c.execute('SELECT * FROM documents ORDER BY created DESC')]


@app.post('/api/documents')
async def upload(file: UploadFile = File(...), company: str = Form(...), period: str = Form(...), source: str = Form('')):
    # Read one byte beyond the cap without loading an unbounded upload.
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024 or not content.startswith(b'%PDF-'):
        raise HTTPException(400, 'Upload a PDF under 20 MB.')
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError('Encrypted PDF')
        pages = len(reader.pages)
        if not pages:
            raise ValueError('Empty PDF')
    except Exception:
        raise HTTPException(400, 'PDF could not be read; use a valid, unencrypted PDF.')
    digest = hashlib.sha256(content).hexdigest()
    with db() as c:
        found = c.execute('SELECT * FROM documents WHERE hash=?', (digest,)).fetchone()
        if found:
            return {**dict(found), 'duplicate': True}
        doc_id = uuid.uuid4().hex
        (DATA / f'{doc_id}.pdf').write_bytes(content)
        c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',
                  (doc_id, Path(file.filename or 'document.pdf').name, digest, company[:200], period[:100], source[:2000], pages, time.time()))
        return dict(c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone())


def document(doc_id):
    with db() as c:
        row = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Document not found')
    return dict(row)


@app.get('/api/documents/{doc_id}/pdf')
def pdf(doc_id: str):
    document(doc_id)
    return FileResponse(DATA / f'{doc_id}.pdf', media_type='application/pdf')


@app.get('/api/documents/{doc_id}/pages/{page_number}/image')
def page_image(doc_id: str, page_number: int):
    doc = document(doc_id)
    if page_number < 1 or page_number > doc['pages']:
        raise HTTPException(404, 'PDF page not found.')
    import pypdfium2 as pdfium
    # Render on the server so source evidence works without a browser PDF plugin.
    with PDF_RENDER_LOCK, pdfium.PdfDocument(DATA / f'{doc_id}.pdf') as pdf_doc:
        page = pdf_doc[page_number-1]
        width, height = page.get_size()
        scale = min(1.5, 1600 / max(width, height))
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        output = io.BytesIO()
        image.save(output, format='PNG')
        image.close()
        bitmap.close()
        page.close()
    return Response(output.getvalue(), media_type='image/png',
                    headers={'Cache-Control': 'private, max-age=86400'})


class RunRequest(BaseModel):
    provider: str = Field(pattern='^(baseline|llamaparse)$')
    pages: list[int] = Field(min_length=1, max_length=5)


@app.post('/api/documents/{doc_id}/runs', status_code=202)
def create_run(doc_id: str, request: RunRequest):
    doc = document(doc_id)
    pages = sorted(set(request.pages))
    if any(p < 1 or p > doc['pages'] for p in pages):
        raise HTTPException(400, 'Page numbers are outside the document.')
    if request.provider == 'llamaparse' and not os.getenv('LLAMA_CLOUD_API_KEY'):
        raise HTTPException(409, 'Set LLAMA_CLOUD_API_KEY in .env and restart. Baseline works without a key.')
    run_id = uuid.uuid4().hex
    with db() as c:
        c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)',
                  (run_id, doc_id, request.provider, 'queued', None, None, time.time()))
    POOL.submit(process, run_id, doc_id, request.provider, pages)
    return {'id': run_id}


def process(run_id, doc_id, provider, selected):
    start = time.monotonic()
    folder = DATA / 'artifacts' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with db() as c:
            c.execute("UPDATE runs SET state='running' WHERE id=?", (run_id,))
        original = DATA / f'{doc_id}.pdf'
        if provider == 'baseline':
            reader = PdfReader(original)
            pages = [{'page': p, 'markdown': reader.pages[p-1].extract_text()} for p in selected]
            raw = {'provider': 'pypdf', 'pages': pages}
        else:
            from llama_cloud import LlamaCloud
            # Submit only requested pages: a hard five-page cost boundary.
            reader, writer = PdfReader(original), PdfWriter()
            for p in selected:
                writer.add_page(reader.pages[p-1])
            subset = folder / 'submitted.pdf'
            writer.write(subset)
            client = LlamaCloud(timeout=180, max_retries=0)
            remote = client.files.create(file=subset, purpose='parse')
            result = client.parsing.parse(file_id=remote.id, tier='cost_effective', version='latest',
                output_options={'markdown': {'tables': {'output_tables_as_markdown': True}}}, expand=['markdown'])
            raw = result.model_dump(mode='json')
            (folder / 'raw.json').write_text(json.dumps(raw, indent=2))
            returned = result.markdown.pages if result.markdown else []
            if len(returned) != len(selected):
                raise ValueError('Parser page count mismatch; evidence mapping withheld.')
            pages = []
            for position, page in enumerate(returned):
                if getattr(page, 'success', True) is False:
                    raise ValueError('Parser reported an unsuccessful page.')
                pages.append({'page': selected[position], 'markdown': page.markdown})
        (folder / 'raw.json').write_text(json.dumps(raw, indent=2))
        (folder / 'pages.json').write_text(json.dumps(pages, indent=2))
        finish_run(run_id, pages, time.monotonic()-start)
    except Exception as exc:
        # Keep credentials and provider response bodies out of browser errors.
        reason = 'Processing failed (' + type(exc).__name__ + '). Check connectivity and configuration, then retry.'
        with db() as c:
            c.execute("UPDATE runs SET state='failed', error=?, duration=? WHERE id=?", (reason, time.monotonic()-start, run_id))


def finish_run(run_id, pages, duration):
    candidates = table_candidates(pages)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        existing = c.execute('SELECT state FROM runs WHERE id=?', (run_id,)).fetchone()
        if not existing or existing['state'] == 'succeeded':
            return
        for observation in candidates:
            value = json.dumps(observation)
            c.execute('INSERT INTO observations VALUES (?,?,?,?)', (uuid.uuid4().hex, run_id, value, value))
        c.execute("UPDATE runs SET state='succeeded', duration=? WHERE id=?", (duration, run_id))


@app.get('/api/documents/{doc_id}/runs')
def runs(doc_id: str):
    document(doc_id)
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM runs WHERE document_id=? ORDER BY created DESC', (doc_id,))]


@app.get('/api/runs/{run_id}')
def run_detail(run_id: str):
    with db() as c:
        row = c.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Run not found')
        observations = [{'id': r['id'], **json.loads(r['current'])} for r in c.execute('SELECT * FROM observations WHERE run_id=?', (run_id,))]
    pages_path = DATA / 'artifacts' / run_id / 'pages.json'
    return {**dict(row), 'observations': observations, 'pages': json.loads(pages_path.read_text()) if row['state'] == 'succeeded' and pages_path.exists() else []}


class Review(BaseModel):
    status: str = Field(pattern='^(accepted|rejected|pending)$')
    metric: str = Field(min_length=1, max_length=300)
    value: str = Field(max_length=100)
    period: str = Field(min_length=1, max_length=100)
    unit: str = Field(pattern='^(USD|USD_millions|USD_billions|percent|count|unresolved)$')
    classification: str = Field(pattern='^(actual|guidance|unresolved)$')


@app.patch('/api/observations/{obs_id}')
def review(obs_id: str, request: Review):
    try:
        value = normalize(request.value)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if request.status == 'accepted' and (request.unit == 'unresolved' or request.classification == 'unresolved' or request.period.lower() == 'unresolved'):
        raise HTTPException(422, 'Resolve units, reporting period and actual/guidance before acceptance.')
    with db() as c:
        row = c.execute('SELECT * FROM observations WHERE id=?', (obs_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Observation not found')
        current = {**json.loads(row['current']), **request.model_dump(), 'value': value}
        updated = json.dumps(current)
        c.execute('UPDATE observations SET current=? WHERE id=?', (updated, obs_id))
        c.execute('INSERT INTO reviews VALUES (?,?,?,?,?)', (uuid.uuid4().hex, obs_id, row['current'], updated, time.time()))
    return current


def safe_cell(value):
    text = str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


@app.get('/api/runs/{run_id}/export.csv')
def export(run_id: str):
    detail = run_detail(run_id)
    output = io.StringIO()
    fields = ['metric', 'value', 'period', 'unit', 'classification', 'page', 'evidence']
    writer = csv.writer(output)
    writer.writerow(fields)
    for obs in detail['observations']:
        if obs['status'] == 'accepted':
            writer.writerow([safe_cell(obs.get(k, '')) for k in fields])
    return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=quarterly-reviewed.csv'})


app.include_router(create_router(db, DATA, POOL, run_detail, document, safe_cell))

app.include_router(create_research_router(db, DATA, POOL, run_detail, lambda doc_id, request: create_run(doc_id, RunRequest(**request))))

app.include_router(create_library_router(db))
