"""Build a reviewable local preview archive from an explicit source allowlist."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT_NAMES=['evaluate_suite.py','evaluate_research.py','evaluate_native_smoke.py','community_smoke.py','build_community_bundle.py','download_public_sample.py','verify_community_bundle.py']


def main():
    paths=[ROOT/name for name in ['README.md','LICENSE','pyproject.toml','uv.lock','.env.example','.gitignore']]
    for folder in ['app','tests','evals']:
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.js','.css','.html','.json'} and '__pycache__' not in p.parts)
    paths.extend(ROOT/'scripts'/name for name in SCRIPT_NAMES)
    # Prevent configured secrets entering a preview even if accidentally copied into source.
    secrets=[]
    if (ROOT/'.env').exists():
        for line in (ROOT/'.env').read_text().splitlines():
            if '=' in line and line.split('=',1)[0].strip() in {'LLAMA_CLOUD_API_KEY','EXA_API_KEY','OPENAI_API_KEY'}:
                value=line.split('=',1)[1].strip().strip('\"\'')
                if len(value)>12:secrets.append(value.encode())
    for path in paths:
        if any(secret in path.read_bytes() for secret in secrets):raise ValueError('Configured secret found in '+str(path.relative_to(ROOT)))
    (ROOT/'outputs').mkdir(exist_ok=True)
    target=ROOT/'outputs/quarterly-community-preview.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):archive.write(path,path.relative_to(ROOT))
    print(f'Preview archive: {target.name} ({len(set(paths))} source files). MIT licensed; archive creation does not publish it.')

if __name__=='__main__':main()
