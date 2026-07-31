#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path

def run(*args):
    print('+',' '.join(map(str,args)),flush=True); subprocess.run([str(x) for x in args],check=True)
def main():
    p=argparse.ArgumentParser(description='Run the complete FCC address coverage workflow.'); p.add_argument('--input',required=True,type=Path); p.add_argument('--output',type=Path,default=Path('data/output/coverage_results.csv')); p.add_argument('--as-of'); p.add_argument('--providers',default='att,tmo,vzw'); p.add_argument('--skip-docker',action='store_true'); p.add_argument('--force-download',action='store_true'); a=p.parse_args()
    py=Path(sys.executable)
    if not a.skip_docker:run('docker','compose','up','-d')
    run(py,'init_database.py')
    sync=[py,'sync_fcc_data.py','--input',a.input,'--providers',a.providers]
    if a.as_of:sync += ['--as-of',a.as_of]
    if a.force_download:sync += ['--force']
    run(*sync)
    release=Path('data/catalog/selected_release.txt').read_text().strip(); safe=''.join(c if c.isalnum() or c in '_-' else '_' for c in release); coverage=Path('data/coverage')/safe
    run(py,'load_postgis.py','--coverage-dir',coverage,'--input',a.input,'--release-id',release,'--replace-states','--subdivide')
    run(py,'process_addresses.py','--input',a.input,'--output',a.output,'--release-id',release)
    print(f'Complete: {a.output}')
if __name__=='__main__':main()
