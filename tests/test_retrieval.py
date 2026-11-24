from app.retrieval import retrieve


def source(blocks,source_id='source',period='2026-09-30'):
    return {'id':source_id,'metadata':{'title':'10-Q','url':'https://example.com/filing','period_end':period,'filed_on':'2026-10-28'},'blocks':blocks}


def paragraph(text,section='Results'):
    return {'locator':'Text chunk 1','text':text,'section':section,'kind':'paragraph'}


def test_named_concept_excludes_generic_revenue_matches():
    result=retrieve('How much deferred revenue was recognized?',[
        source([paragraph('Total revenue rose to $100 million.'),
                paragraph('Deferred revenue recognized was $3.6 billion.')])])
    assert len(result)==1
    assert 'Deferred revenue' in result[0]['passage']
    assert result[0]['matched_concepts']==['deferred revenue']
    assert result[0]['period_end']=='2026-09-30'


def test_plural_and_acronym_queries_resolve_to_concepts():
    result=retrieve('What are RPO?', [source([paragraph('Remaining performance obligations totaled $10 billion.')])])
    assert len(result)==1
    assert result[0]['matched_concepts']==['remaining performance obligations']


def test_multi_concept_query_does_not_starve_second_metric():
    blocks=[paragraph(f'Product revenue was ${i} million.') for i in range(20)]
    blocks.append(paragraph('Operating margin was 20%.'))
    result=retrieve('What product revenue and operating margin were reported?',[source(blocks)])
    assert len(result)==6
    assert any('Operating margin' in row['quote'] for row in result)


def test_duplicate_passages_are_not_repeated_across_documents():
    block=paragraph('Deferred revenue was $100 million.')
    result=retrieve('deferred revenue',[source([block]),source([block],'other')])
    assert len(result)==1


def test_weak_keyword_match_and_headings_do_not_masquerade_as_evidence():
    result=retrieve('What caused supplier financing obligations?',[
        source([paragraph('Obligations include payments.'),
                {'text':'Supplier financing obligations','kind':'heading','locator':'Text chunk 2'}])])
    assert result==[]


def test_table_context_does_not_cross_a_paragraph_break():
    blocks=[{'text':'Old row | 2024','kind':'table_row','section':'Results','locator':'1'},
        paragraph('A separate table follows.'),
        {'text':'Metric | 2026 | 2025','kind':'table_row','section':'Results','locator':'3'},
        {'text':'Product revenue | $100 | $90','kind':'table_row','section':'Results','locator':'4'}]
    result=retrieve('product revenue',[source(blocks)])
    assert result[0]['table_context']==['Metric | 2026 | 2025']
    assert 'Old row' not in ' '.join(result[0]['table_context'])


def test_explicit_missing_fiscal_year_does_not_return_other_years():
 source={'id':'fy','metadata':{'title':'FY2025 results','url':'https://example.com','filed_on':'2027-01-01'},'blocks':[{'locator':'p1','text':'FY2025 product revenue was $150 million.'}]}
 assert retrieve('What was FY2025 product revenue?',[source])
 assert retrieve('What was FY 2025 product revenue?',[source])
 assert retrieve('What was FY2027 product revenue?',[source])==[]
 assert retrieve('What was FY2023 product revenue?',[source])==[]
 # Unqualified questions retain ordinary matching behavior.
 assert retrieve('What was product revenue?',[source])


def test_fiscal_comparison_can_span_separate_source_documents():
 sources=[{'id':str(y),'metadata':{'title':f'FY{y} results','url':'https://example.com'},'blocks':[{'locator':'p1','text':f'FY{y} product revenue was ${amount} million.'}]} for y,amount in [(2024,100),(2025,150)]]
 hits=retrieve('Compare FY2024 and FY2025 product revenue',sources)
 assert {h['source_id'] for h in hits}=={'2024','2025'}
