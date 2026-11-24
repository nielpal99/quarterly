from types import SimpleNamespace
import pytest
from app.native_answers import generate_native_answer

EVIDENCE=[{'id':'s:0','title':'Results','locator':'p1','quote':'FY2025 revenue was $150 million.'}]


def install(monkeypatch,result):
 calls={}
 def upload(**kwargs):calls['upload']=kwargs;return SimpleNamespace(id='file')
 def create(**kwargs):
  calls['create']=kwargs
  return SimpleNamespace(id='job',status='COMPLETED',extract_result=result,usage=None,extract_metadata=None)
 monkeypatch.setattr('llama_cloud.LlamaCloud',lambda **kwargs:SimpleNamespace(files=SimpleNamespace(create=upload),extract=SimpleNamespace(create=create)))
 return calls


def test_native_answer_calls_extract_with_bounded_evidence_and_validates_quote(monkeypatch):
 calls=install(monkeypatch,{'sufficient':True,'claims':[{'text':'Revenue was $150 million.','citation_ids':['s:0'],'source_quote':'revenue was $150 million.'}]})
 result=generate_native_answer('What revenue was reported?',EVIDENCE)
 assert result['provider']=='llamaparse'
 assert calls['upload']['purpose']=='extract'
 assert calls['create']['configuration']['cite_sources'] is True
 assert calls['create']['configuration']['max_pages']==5
 assert 'What revenue was reported?' in calls['create']['configuration']['system_prompt']
 assert result['answer']['claims'][0]['citation_ids']==['s:0']


@pytest.mark.parametrize('claim',[
 {'text':'Revenue was $150 million.','citation_ids':['invented'],'source_quote':'revenue was $150 million.'},
 {'text':'Revenue was $999 million.','citation_ids':['s:0'],'source_quote':'revenue was $999 million.'},
 {'text':'Revenue was $999 million.','citation_ids':['s:0'],'source_quote':'   '},
])
def test_native_rejects_unknown_ids_or_unmatched_quotes(monkeypatch,claim):
 install(monkeypatch,{'sufficient':True,'claims':[claim]})
 with pytest.raises(ValueError):generate_native_answer('Revenue?',EVIDENCE)


def test_native_insufficient_discards_claims(monkeypatch):
 install(monkeypatch,{'sufficient':False,'claims':[{'text':'Unverified','citation_ids':['s:0'],'source_quote':'revenue was $150 million.'}]})
 assert generate_native_answer('Missing metric?',EVIDENCE)['answer']['claims']==[]
