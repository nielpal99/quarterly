"""Find bounded exhibit candidates within an earnings announcement's SEC accession."""
import re
from urllib.parse import unquote, urljoin, urlparse
from bs4 import BeautifulSoup


def filing_index_url(filing):
    base=filing['url'].rsplit('/',1)[0]
    accession=filing['accession']
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession):
        raise ValueError('Invalid SEC accession number.')
    return f'{base}/{accession}-index.html'


def exhibit_candidates(content,index_url,parent,limit=3):
    """SEC document-table types identify exhibits; numbering alone does not prove earnings."""
    base=urlparse(index_url)
    if base.scheme!='https' or base.hostname!='www.sec.gov':
        raise ValueError('Expected an official SEC filing index.')
    directory=base.path.rsplit('/',1)[0]+'/'
    if directory!=urlparse(parent['url']).path.rsplit('/',1)[0]+'/':
        raise ValueError('Filing index redirected to another accession.')
    candidates=[];seen=set()
    soup=BeautifulSoup(content,'html.parser')
    for row in soup.select('tr'):
        cells=row.find_all('td',recursive=False)
        if len(cells)<4:continue
        exhibit_type=cells[3].get_text(' ',strip=True).upper()
        if not re.fullmatch(r'EX-99(?:\.\d+)?',exhibit_type):continue
        link=cells[2].find('a',href=True)
        if not link:continue
        url=urljoin(index_url,link['href']);parsed=urlparse(url)
        # Reject navigation, external publishers, other accessions and path traversal.
        path=unquote(parsed.path)
        if (parsed.scheme!='https' or parsed.netloc!=base.netloc or parsed.query or parsed.fragment
            or not path.startswith(directory) or '/' in path[len(directory):]
            or not path.lower().endswith(('.htm','.html','.pdf')) or url in seen):continue
        seen.add(url)
        description=cells[1].get_text(' ',strip=True)
        candidates.append({'title':f'{exhibit_type} · {description or "Results-announcement attachment"}',
            'url':url,'kind':'sec_exhibit','form':exhibit_type,'filed_on':parent['filed_on'],
            'accession':parent['accession'],'parent_url':parent['url'],'filing_index_url':index_url,
            'discovery':'SEC filing exhibits','identity_verified':True,
            'selection_role':'results-announcement exhibit','research_mode':parent['research_mode'],
            'note':'Official attachment to an Item 2.02 announcement. Confirm the covered fiscal period and whether this is a release or presentation; exhibit numbering alone does not establish either.'})
        if len(candidates)>=limit:break
    return candidates
