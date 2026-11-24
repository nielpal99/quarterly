"""Deterministic relevance ranking and display-ready evidence, without model calls."""
import math
import re
from collections import Counter
from app.evidence import focused_passage

STOP = set('what when where which how the a an and or is was were are did does do of in to for on with company much many please tell me show give reported report reports reporting amount amounts explain compare comparison changed change between during period periods'.split())
CONCEPTS = {
 'deferred revenue': ['deferred revenue'],
 'product revenue': ['product revenue'],
 'subscription revenue': ['subscription revenue'],
 'operating margin': ['operating margin'],
 'gross margin': ['gross margin'],
 'remaining performance obligations': ['remaining performance obligation', 'rpo'],
 'net revenue retention': ['net revenue retention', 'net retention'],
 'supply chain finance': ['supply chain finance', 'scf'],
 'cash flow': ['cash flow'],
}


def stem(word):
    if len(word)>4 and word.endswith('ies'):return word[:-3]+'y'
    if len(word)>4 and word.endswith('s') and not word.endswith(('ss','us')):return word[:-1]
    return word


def tokens(text):
    return [stem(word) for word in re.findall(r'[a-z0-9]+',text.casefold())]


def normalized(text):return ' '.join(tokens(text))


def concepts_for(question):
    text=' '+normalized(question)+' '
    return {label:aliases for label,aliases in CONCEPTS.items() if any(' '+normalized(alias)+' ' in text for alias in aliases)}


def matching_concepts(text,concepts):
    text=' '+normalized(text)+' '
    return [label for label,aliases in concepts.items() if any(' '+normalized(alias)+' ' in text for alias in aliases)]


def table_context(source,index):
    block=source['blocks'][index]
    if block.get('kind')!='table_row':return []
    rows=[]
    # Only contiguous rows from the same section; never cross a paragraph/table break.
    for candidate in reversed(source['blocks'][max(0,index-6):index]):
        if candidate.get('kind')!='table_row' or candidate.get('section')!=block.get('section'):break
        rows.insert(0,candidate['text'])
    return rows


def retrieve(question,sources):
    concepts=concepts_for(question)
    terms=set(tokens(question))-{stem(word) for word in STOP}
    for label in concepts:terms.update(tokens(label))
    records=[]
    requested_years=set(re.findall(r'\bfy\s*(20\d{2})\b',question,re.I))
    for source in sources:
        if requested_years:
            # Explicit fiscal-year requests must exist in this source's scope.
            # Do not treat a calendar filing date as the covered fiscal year.
            scope=' '.join([source['metadata'].get('title',''),source['metadata'].get('period_label','') or '']+[b['text'] for b in source['blocks']])
            available_years=set(re.findall(r'\bfy\s*(20\d{2})\b',scope,re.I))
            if not requested_years.intersection(available_years):continue
        for index,block in enumerate(source['blocks']):
            if block.get('kind')=='heading':continue
            counts=Counter(tokens(block['text']))
            if counts:records.append((source,index,block,counts))
    if not terms or not records:return []
    frequency={term:sum(term in counts for _,_,_,counts in records) for term in terms}
    average=sum(sum(counts.values()) for _,_,_,counts in records)/len(records)
    ranked=[]
    for source,index,block,counts in records:
        direct=matching_concepts(block['text'],concepts)
        contextual=matching_concepts(block.get('section') or '',concepts)
        context_only=not direct and bool(contextual) and block.get('kind')=='table_row'
        if concepts and not direct and not context_only:continue
        overlap=terms & counts.keys()
        if not overlap and not context_only:continue
        # For free-form questions, a single common word is insufficient evidence.
        if not concepts and len(terms)>=3 and len(overlap)<2:continue
        length=sum(counts.values());score=0
        for term in overlap:
            idf=math.log(1+(len(records)-frequency[term]+.5)/(frequency[term]+.5))
            tf=counts[term]
            score+=idf*tf*2.2/(tf+1.2*(.25+.75*length/max(average,1)))
        score+=4*len(direct)+(1 if context_only else 0)
        if re.search(r'\d',block['text']):score+=.3
        ranked.append((score,source,index,block,direct,context_only))
    ranked.sort(key=lambda r:r[0],reverse=True)
    # Reserve one strong result per requested concept before filling remaining slots.
    seeds=[]
    for label in concepts:
        candidate=next((row for row in ranked if label in row[4]),None)
        if candidate is not None and all(candidate is not row for row in seeds):seeds.append(candidate)
    ranked=seeds+[row for row in ranked if all(row is not seed for seed in seeds)]
    evidence=[];seen=set()
    for score,source,index,block,direct,context_only in ranked:
        passage=focused_passage(block['text'],set(re.findall(r'[a-z0-9]+',question.lower()))-STOP)
        fingerprint=normalized(passage)
        if fingerprint in seen:continue
        seen.add(fingerprint)
        if len(evidence)>=6:break
        metadata=source['metadata']
        matches=sorted({word for word in re.findall(r'[a-z0-9]+',passage.lower()) if stem(word) in terms})
        headers=table_context(source,index)
        evidence.append({'id':f"{source['id']}:{index}",'source_id':source['id'],
            'title':metadata['title'],'url':metadata['url'],'locator':block['locator'],
            'quote':block['text'],'passage':passage,'section':block.get('section'),
            'kind':block.get('kind','parsed_page'),'matched_terms':matches,
            'matched_concepts':direct,'match_reason':('Table row within a matching section; verify column context.' if context_only else 'Matches '+', '.join(direct) if direct else 'Matches the requested terms.'),
            'period_end':metadata.get('period_end'),'filed_on':metadata.get('filed_on'),
            'period_label':metadata.get('period_label'),'table_context':headers,
            'context_before':source['blocks'][index-1]['text'] if index>0 else None,
            'context_after':source['blocks'][index+1]['text'] if index+1<len(source['blocks']) else None,
            'pdf_url':block.get('pdf_url'),'image_url':block.get('image_url')})
    return evidence
