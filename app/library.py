"""Canonical source registry over existing research and upload provenance."""
import json
import time
import uuid
from typing import Literal
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field


def canonical_url(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname:return None
        query=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if not k.lower().startswith('utm_')]
        return urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path or '/',urlencode(query),''))
    except ValueError:return None


def install_library(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS library_sources(id TEXT PRIMARY KEY, identity TEXT UNIQUE,
      notes TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', created REAL);
    CREATE TABLE IF NOT EXISTS library_members(entity_type TEXT,entity_id TEXT,source_id TEXT,
      PRIMARY KEY(entity_type,entity_id));
    ''')
    if 'archived' not in [r[1] for r in c.execute('PRAGMA table_info(library_sources)')]:
        c.execute('ALTER TABLE library_sources ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')


class Organization(BaseModel):
    notes:str=Field(default='',max_length=4000)
    tags:list[str]=Field(default_factory=list,max_length=12)


class BulkOrganization(BaseModel):
    source_ids:list[str]=Field(min_length=1,max_length=500)
    action:Literal['archive','restore','add_tags']
    tags:list[str]=Field(default_factory=list,max_length=12)


def create_library_router(db):
    router=APIRouter()

    def index(c):
        members=[]
        for row in c.execute('SELECT * FROM documents ORDER BY created').fetchall():
            doc=dict(row)
            runs=[dict(r) for r in c.execute('SELECT id,provider,state,created FROM runs WHERE document_id=? ORDER BY created DESC',(doc['id'],))]
            ready=next((r for r in runs if r['state']=='succeeded'),None)
            members.append({'entity_type':'document','entity_id':doc['id'],'title':doc['name'],
                'company':doc['company'],'ticker':None,'period':doc['period'],'url':doc['source'],
                'hash':doc['hash'],'kind':'PDF','state':'ready' if ready else 'processing' if any(r['state'] in ('queued','running') for r in runs) else 'uploaded',
                'packet_id':None,'document_id':doc['id'],'run_id':ready['id'] if ready else None,
                'pages':doc['pages'],'evidence_count':0,'provenance':'Uploaded document library',
                'timestamp':doc['created']})
        rows=c.execute('''SELECT s.id,s.packet_id,s.metadata,s.state,s.error,s.content_hash,
           json_array_length(s.blocks) AS block_count,p.ticker,p.company,p.created
           FROM research_sources s JOIN research_packets p ON p.id=s.packet_id ORDER BY p.created''').fetchall()
        for row in rows:
            meta=json.loads(row['metadata'])
            members.append({'entity_type':'research','entity_id':row['id'],'title':meta['title'],
                'company':row['company'],'ticker':row['ticker'],'period':meta.get('period_end') or meta.get('period_label') or 'Not identified',
                'url':meta.get('url',''),'hash':row['content_hash'],'kind':meta.get('form') or ('PDF' if meta.get('kind')=='local_parsed_pdf' or row['state']=='pdf_downloaded' else 'Web source'),
                'state':row['state'],'packet_id':row['packet_id'],'document_id':meta.get('document_id'),
                'run_id':meta.get('run_id'),'pages':meta.get('pdf_pages'),
                'evidence_count':row['block_count'],'provenance':meta.get('discovery','Research'),
                'timestamp':row['created'],'error':row['error'],'period_is_filing_end':bool(meta.get('period_end'))})
        # Stable registry memberships persist even when metadata changes. Same URL
        # groups discovery history; content hashes expose distinct downloaded versions.
        for member in members:
            linked=c.execute('SELECT source_id FROM library_members WHERE entity_type=? AND entity_id=?',(member['entity_type'],member['entity_id'])).fetchone()
            if linked:
                member['source_id']=linked['source_id'];continue
            url=canonical_url(member['url'])
            identity='url:'+url if url else 'hash:'+member['hash'] if member['hash'] else member['entity_type']+':'+member['entity_id']
            existing=c.execute('SELECT id FROM library_sources WHERE identity=?',(identity,)).fetchone()
            source_id=existing['id'] if existing else uuid.uuid4().hex
            if not existing:c.execute('INSERT INTO library_sources (id,identity,created) VALUES (?,?,?)',(source_id,identity,time.time()))
            c.execute('INSERT INTO library_members VALUES (?,?,?)',(member['entity_type'],member['entity_id'],source_id))
            member['source_id']=source_id
        grouped={}
        for member in members:grouped.setdefault(member['source_id'],[]).append(member)
        result=[]
        for source_id,links in grouped.items():
            links.sort(key=lambda link:link['timestamp'],reverse=True)
            primary=links[0]
            ready=next((link for link in links if link['state']=='ready'),None)
            states={link['state'] for link in links}
            state='ready' if ready else 'processing' if states&{'fetching','parsing','queued','running'} else 'downloaded' if states&{'pdf_downloaded','uploaded'} else 'failed' if 'failed' in states else 'discovered'
            organization=c.execute('SELECT notes,tags,created,archived FROM library_sources WHERE id=?',(source_id,)).fetchone()
            hashes=sorted({link['hash'] for link in links if link['hash']})
            prepared=next((link for link in links if link.get('run_id') and link['state']=='ready'),None)
            doc=next((link for link in links if link.get('document_id') and (not prepared or link['document_id']==prepared.get('document_id'))),None)
            result.append({'id':source_id,'title':primary['title'],'company':primary['company'],
                'tickers':sorted({link['ticker'] for link in links if link['ticker']}),
                'companies':sorted({link['company'] for link in links}),
                'periods':sorted({link['period'] for link in links if link['period'] not in ('Unresolved','Not identified')}),
                'kind':doc['kind'] if doc and doc['entity_type']=='document' else primary['kind'],
                'url':primary['url'],'state':state,'evidence_count':max(link['evidence_count'] for link in links),
                'version_hashes':hashes,'version_count':len(hashes),'appearances':len(links),
                'document_id':doc['document_id'] if doc else None,
                'run_id':prepared['run_id'] if prepared else None,
                'packet_ids':list(dict.fromkeys(link['packet_id'] for link in links if link['packet_id'])),
                'notes':organization['notes'],'tags':json.loads(organization['tags']),'archived':bool(organization['archived']),
                'provenance':sorted({link['provenance'] for link in links}),
                'coverage_note':('Prepared in at least one saved context; this does not mean the full document has been read.' if ready else 'No searchable evidence yet.'),
                'members':links})
        return sorted(result,key=lambda entry:entry['title'].casefold())

    @router.get('/api/library')
    def listing():
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            sources=index(c)
        return {'sources':sources,'summary':{'total':len(sources),
            'ready':sum(s['state']=='ready' for s in sources),'unread':sum(s['state']!='ready' for s in sources),
            'appearances':sum(s['appearances'] for s in sources),'archived':sum(s['archived'] for s in sources)}}

    @router.post('/api/library/bulk')
    def bulk(request:BulkOrganization):
        ids=list(dict.fromkeys(request.source_ids))
        tags=list(dict.fromkeys(tag.strip() for tag in request.tags if tag.strip()))
        if any(len(tag)>60 for tag in tags):raise HTTPException(422,'Tags must be 60 characters or fewer.')
        if request.action=='add_tags' and not tags:raise HTTPException(422,'Enter at least one tag.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=[]
            for source_id in ids:
                row=c.execute('SELECT id,tags FROM library_sources WHERE id=?',(source_id,)).fetchone()
                if not row:raise HTTPException(404,'One or more sources no longer exist. Nothing changed.')
                merged=list(dict.fromkeys(json.loads(row['tags'])+tags))
                if request.action=='add_tags' and len(merged)>12:raise HTTPException(422,'A source would exceed the 12-tag limit. Nothing changed.')
                rows.append((source_id,merged))
            for source_id,merged in rows:
                if request.action=='add_tags':c.execute('UPDATE library_sources SET tags=? WHERE id=?',(json.dumps(merged),source_id))
                else:c.execute('UPDATE library_sources SET archived=? WHERE id=?',(int(request.action=='archive'),source_id))
        return {'updated':len(ids),'action':request.action}

    @router.patch('/api/library/{source_id}')
    def organize(source_id:str,request:Organization):
        tags=list(dict.fromkeys(tag.strip() for tag in request.tags if tag.strip()))
        if any(len(tag)>60 for tag in tags):raise HTTPException(422,'Tags must be 60 characters or fewer.')
        with db() as c:
            if not c.execute('SELECT 1 FROM library_sources WHERE id=?',(source_id,)).fetchone():raise HTTPException(404,'Library source not found.')
            c.execute('UPDATE library_sources SET notes=?,tags=? WHERE id=?',(request.notes,json.dumps(tags),source_id))
        return {'id':source_id,'notes':request.notes,'tags':tags}
    return router
