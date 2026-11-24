"""Check the preview in a new directory and virtualenv without developer data or keys."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
ROOT=Path(__file__).resolve().parents[1]


def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='quarterly-clean-') as folder:
        clean=Path(folder)
        with zipfile.ZipFile(ROOT/'outputs/quarterly-community-preview.zip') as archive:
            # The bundle builder uses known relative source paths; verify before extraction.
            for name in archive.namelist():
                if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Unsafe archive path')
            archive.extractall(clean)
        env=os.environ.copy()
        for key in ['LLAMA_CLOUD_API_KEY','EXA_API_KEY','OPENAI_API_KEY','QUARTERLY_DATA_DIR','VIRTUAL_ENV']:env.pop(key,None)
        env['PYTHON_DOTENV_DISABLED']='1'
        commands=[['uv','sync','--frozen'],['uv','run','python','scripts/community_smoke.py'],
            ['uv','run','python','scripts/evaluate_suite.py','--portable'],['uv','run','pytest','-q'],
            ['uv','run','python','scripts/download_public_sample.py']]
        for command in commands:
            result=subprocess.run(command,cwd=clean,env=env,capture_output=True,text=True,timeout=180)
            checks.append({'command':' '.join(command),'status':result.returncode,'output':(result.stdout+result.stderr)[-4000:]})
            print(checks[-1]['command'],result.returncode,flush=True)
            if result.returncode:break
    report={'scope':'Fresh preview extraction and virtualenv, no .env/data/keys; uv may use its dependency cache. Downloader is checked in information-only mode; no paid calls.','checks':checks,'passed':len(checks)==len(commands) and all(c['status']==0 for c in checks)}
    (ROOT/'outputs/clean-install-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
