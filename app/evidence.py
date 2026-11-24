"""Structure-aware HTML evidence and focused, verbatim passage selection."""
import re
from bs4 import BeautifulSoup, NavigableString, Tag

EVIDENCE_VERSION = 'html-structure-v3'


def readable(text):
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'\$\s+(?=\d)', '$', text)


def split_passage(text, limit=1800):
    # Keep complete sentences together. A pathological long sentence falls back
    # to token boundaries, preserving monetary amount + unit as one token.
    sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z(])',text)
    buffer=''
    for sentence in sentences:
        if len(sentence)>limit:
            if buffer:yield buffer;buffer=''
            tokens=re.findall(r'\$\s*[\d,.]+(?:\s*(?:million|billion|thousand|%))?|\S+',sentence)
            for token in tokens:
                if buffer and len(buffer)+len(token)+1>limit:
                    yield buffer;buffer=''
                buffer=(buffer+' '+token).strip()
            if buffer:yield buffer;buffer=''
        elif buffer and len(buffer)+len(sentence)+1>limit:
            yield buffer;buffer=sentence
        else:buffer=(buffer+' '+sentence).strip()
    if buffer:yield buffer


def structured_html(content):
    soup=BeautifulSoup(content,'html.parser')
    for tag in list(soup.find_all(['script','style','noscript','nav','header','footer','ix:header'])):
        tag.decompose()
    for tag in list(soup.find_all(attrs={'hidden':True})):
        if tag.parent:tag.decompose()
    for tag in list(soup.find_all(style=re.compile(r'display\s*:\s*none',re.I))):
        if tag.parent:tag.decompose()
    blocks=[];heading=None
    def add(text,kind):
        nonlocal heading
        text=readable(text)
        if not text:return
        if kind=='heading':
            heading=text[:220]
        for piece in split_passage(text):
            blocks.append({'locator':f'Text chunk {len(blocks)+1}', 'text':piece,
                'section':heading,'kind':kind,'evidence_version':EVIDENCE_VERSION})
            if len(blocks)>5000:raise ValueError('Document exceeds supported passage count; no partial import stored.')
    def walk(tag):
        if isinstance(tag,NavigableString):
            if str(tag).strip():add(str(tag),'paragraph')
            return
        if not isinstance(tag,Tag):return
        if tag.name=='table':
            rows=[row for row in tag.find_all('tr') if row.find_parent('table') is tag]
            for row in rows:
                # Nested presentation tables are containers, not duplicate rows.
                if row.find('table'):
                    for cell in row.find_all(['td','th'],recursive=False):walk(cell)
                    continue
                cells=[readable(cell.get_text(' ',strip=True)) for cell in row.find_all(['td','th'],recursive=False)]
                if cells:add(' | '.join(cells),'table_row')
            return
        structural=tag.find(['p','div','table','h1','h2','h3','h4','h5','h6','li'],recursive=True)
        if tag.name in ['h1','h2','h3','h4','h5','h6']:
            add(tag.get_text(' ',strip=True),'heading');return
        if tag.name in ['p','li'] or (tag.name=='div' and structural is None):
            text=tag.get_text(' ',strip=True)
            bold=tag.find(['b','strong'])
            style=tag.get('style','')
            bold_span=next((span for span in tag.find_all('span',style=re.compile(r'font-weight\s*:\s*(?:bold|[6-9]00)',re.I)) if readable(span.get_text(' ',strip=True))==readable(text)),None)
            heading_like=len(text)<220 and (re.search(r'font-weight\s*:\s*(?:bold|[6-9]00)',style,re.I) or
                (bold and readable(bold.get_text(' ',strip=True))==readable(text)) or bold_span is not None or
                (len(text)<140 and not text.endswith('.') and re.match(r'^(?:\(\d+\)|Item\s+\d+[A-Z]?[.]|PART\s+[IVX]+)\s',text,re.I)))
            add(text,'heading' if heading_like else 'paragraph');return
        for child in tag.children:walk(child)
    walk(soup.body or soup)
    if not blocks:raise ValueError('No readable source content.')
    return blocks


def focused_passage(text, terms):
    """Select an existing sentence or table row; never synthesize or truncate it."""
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if len(lines)==1:
        lines=re.split(r'(?<=[.!?])\s+(?=[A-Z(])',text)
    scored=[]
    for index,line in enumerate(lines):
        tokens=set(re.findall(r'[a-z0-9]+',line.lower()))
        score=len(tokens&terms)
        if score:scored.append((score,index,line))
    if not scored:return text
    _,index,best=max(scored,key=lambda row:(row[0],-row[1]))
    # Short complete passages need no second layer of clipping.
    return best if len(text)>600 else text
