import pytest
from app.exhibits import exhibit_candidates,filing_index_url

PARENT={'url':'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
 'accession':'0000000123-26-000001','filed_on':'2026-10-28','research_mode':'latest'}
INDEX=filing_index_url(PARENT)

def row(href,kind='EX-99.1',description='Quarterly results release'):
 return f'<tr><td>2</td><td>{description}</td><td><a href="{href}">attachment</a></td><td>{kind}</td></tr>'

def test_exhibits_preserve_parent_provenance_and_do_not_invent_period():
 candidates=exhibit_candidates('<table>'+row('release.htm')+row('slides.pdf','EX-99.2','Investor presentation')+'</table>',INDEX,PARENT)
 assert len(candidates)==2
 assert candidates[0]['parent_url']==PARENT['url']
 assert candidates[0]['accession']==PARENT['accession']
 assert candidates[0]['form']=='EX-99.1'
 assert 'period_end' not in candidates[0]  # 8-K event date is not the financial period.
 assert candidates[1]['url'].endswith('/slides.pdf')

def test_exhibits_reject_wrong_type_external_accession_and_duplicates():
 html='<table>'+''.join(row(href,kind) for href,kind in [
  ('release.htm','EX-99.1'),('release.htm','EX-99.1'),('image.jpg','EX-99.2'),
  ('facts.xml','XML'),('https://example.com/release.htm','EX-99.1'),
  ('../other/release.htm','EX-99.1'),('%2e%2e%2fother.htm','EX-99.1'),
  ('/Archives/edgar/data/456/other/release.htm','EX-99.1')])+'</table>'
 assert len(exhibit_candidates(html,INDEX,PARENT))==1
 assert len(exhibit_candidates('<table>'+''.join(row(f'{i}.htm') for i in range(10))+'</table>',INDEX,PARENT))==3
 with pytest.raises(ValueError):exhibit_candidates(html,'https://example.com/index.html',PARENT)
 with pytest.raises(ValueError):exhibit_candidates(html,INDEX.replace('/123/','/456/'),PARENT)
 with pytest.raises(ValueError):filing_index_url({**PARENT,'accession':'../bad'})
