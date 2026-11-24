"""Fetch the pinned public Snowflake PDF; no parsing, provider calls, or database writes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.research import fetch_source


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true',help='Allow downloading the public PDF')
    args=parser.parse_args()
    manifest=json.loads((ROOT/'app/fixtures/public-sample.json').read_text())
    if not args.download:
        print(json.dumps(manifest,indent=2));print('Add --download to fetch this file. No parsing credits are used.');return
    content,_,_=fetch_source(manifest['url'])
    if not content.startswith(b'%PDF-') or hashlib.sha256(content).hexdigest()!=manifest['sha256']:
        raise ValueError('Source differs from the pinned PDF. Nothing saved; review the publisher source before updating its hash.')
    folder=ROOT/'data/public-samples';folder.mkdir(parents=True,exist_ok=True)
    target=folder/'snowflake-q4-fy2026.pdf';target.write_bytes(content)
    print(f'Saved {target}. Upload this PDF through the app; choose page 25 for the sample table. LlamaParse parsing is a separate explicit paid action.')

if __name__=='__main__':main()
