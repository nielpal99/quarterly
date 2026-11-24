"""Offline, empty-database walkthrough for a fresh local installation."""
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    # Do not load a developer's .env or use their database or provider credentials.
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    for key in ['LLAMA_CLOUD_API_KEY','EXA_API_KEY','OPENAI_API_KEY']:os.environ[key]=''
    with tempfile.TemporaryDirectory() as folder:
        os.environ['QUARTERLY_DATA_DIR']=folder
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            assert c.get('/').status_code==200
            r=c.post('/api/demo/research/start');assert r.status_code==201,r.text
            packet=r.json();base='/api/research/packets/'+packet['id']
            result=c.post(base+'/ask',json={'question':'What product revenue was reported?'})
            assert result.status_code==200 and result.json()['evidence']
            brief=c.post(base+'/briefs',json={'source_ids':[packet['sources'][0]['id']]})
            assert brief.status_code==201,brief.text
            saved=brief.json()
            assert c.get('/api/research/briefs/'+saved['id']).json()==saved
            assert c.get('/api/research/briefs/'+saved['id']+'/markdown').status_code==200
            assert c.get('/api/library').status_code==200
    print('PASS: empty database → fictional source → evidence → saved brief → reopen → export → library; no keys required.')

if __name__=='__main__':main()
