#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
import requests
from dotenv import load_dotenv
from extract_states import extract_states
from state_codes import STATE_TO_FIPS
load_dotenv()
BASE=os.getenv('FCC_API_BASE_URL','https://broadbandmap.fcc.gov/api/public/map').rstrip('/')
PROVIDERS={'att':('AT&T','130077'),'tmo':('T-Mobile',''),'vzw':('Verizon','')}
SQLITE=b'SQLite format 3\x00'
class FCCError(RuntimeError): pass

def first(r,*keys):
    d={str(k).lower():v for k,v in r.items()}
    for k in keys:
        if d.get(k.lower()) not in (None,''): return d[k.lower()]

def rows(payload):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,dict)]
    if not isinstance(payload,dict): return []
    for k in ('data','results','result','items','files'):
        v=payload.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
        if isinstance(v,dict):
            found=rows(v)
            if found:return found
    out=[]
    for v in payload.values():
        if isinstance(v,list): out += [x for x in v if isinstance(x,dict)]
    return out

def parsedate(x):
    s=str(x).strip()
    for f in ('%Y-%m-%d','%m/%d/%Y','%Y%m%d'):
        try:return datetime.strptime(s,f)
        except ValueError:pass
    try:return datetime.fromisoformat(s.replace('Z','+00:00'))
    except ValueError:return datetime.min

def dates(payload):
    vals=[]
    if isinstance(payload,list): vals=payload
    elif isinstance(payload,dict):
        for k in ('availability','availability_dates','availabilityData','availability_as_of_dates','data'):
            if isinstance(payload.get(k),list): vals += payload[k]
    out=[]
    for x in vals:
        if isinstance(x,str):out.append(x)
        elif isinstance(x,dict):
            v=first(x,'as_of_date','asOfDate','availability_date','availabilityDate','date')
            if v:out.append(str(v))
    return sorted(set(out),key=parsedate,reverse=True)

class Client:
    def __init__(self,user,token):
        self.s=requests.Session(); self.s.headers.update({'username':user,'hash_value':token,'User-Agent':'fcc-coverage-postgis/7.0','Accept':'application/json, application/octet-stream, */*'})
    def req(self,path,*,params=None,stream=False,headers=None):
        url=path if str(path).startswith('http') else f'{BASE}/{str(path).lstrip("/")}'
        err=None
        for i in range(5):
            try:
                r=self.s.get(url,params=params,stream=stream,headers=headers,timeout=180,allow_redirects=True)
                if r.status_code==429: time.sleep(int(r.headers.get('Retry-After','10'))); continue
                if r.status_code>=500: time.sleep(min(2**i,30)); continue
                r.raise_for_status(); return r
            except requests.RequestException as e: err=e; time.sleep(min(2**i,30))
        raise FCCError(f'FCC request failed: {url}: {err}')
    def js(self,path,params=None):
        r=self.req(path,params=params)
        try:return r.json()
        except ValueError: raise FCCError(f'Expected JSON from {r.url}')
    def download(self,row,dest):
        fid=first(row,'file_id','fileId','id'); ft=first(row,'file_type_id','fileTypeId','revision','version') or 1
        urls=[]; direct=first(row,'download_url','downloadUrl','url','file_url')
        if direct:urls.append(str(direct))
        urls += [f'downloads/downloadFile/availability/{fid}/{ft}',f'downloads/downloadFile/availability/{fid}/1']
        part=dest.with_suffix(dest.suffix+'.part')
        errors=[]
        for url in dict.fromkeys(urls):
            for attempt in range(5):
                try:
                    existing=part.stat().st_size if part.exists() else 0
                    r=self.req(url,stream=True,headers={'Range':f'bytes={existing}-'} if existing else None)
                    mode='ab' if existing and r.status_code==206 else 'wb'
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    with part.open(mode) as f:
                        for chunk in r.iter_content(1024*1024):
                            if chunk:f.write(chunk)
                    part.replace(dest); return
                except (requests.exceptions.ChunkedEncodingError,requests.exceptions.ConnectionError) as e:
                    # retriable: resume from .part on next attempt
                    if attempt<4: time.sleep(min(2**attempt,30)); continue
                    errors.append(f'{url}: {e}'); break
                except Exception as e: errors.append(f'{url}: {e}'); break
        raise FCCError(f'Unable to download FCC file {fid}:\n' + '\n'.join(f'  {e}' for e in errors))

def text(r):return ' '.join(str(v) for v in r.values() if not isinstance(v,(dict,list)) and v is not None).lower()
def raw_mobile(r):
    cat=str(first(r,'category') or '').lower(); sub=str(first(r,'subcategory') or '').lower(); tech=str(first(r,'technology_type','technologyType') or '').lower(); typ=str(first(r,'file_type','fileType') or '').lower(); name=str(first(r,'file_name','filename','fileName','name') or '').lower(); t=text(r)
    return (not cat or cat=='provider') and (not sub or sub=='raw coverage') and (not tech or tech=='mobile broadband') and (not typ or typ in {'gis','gpkg','geopackage','zip'}) and '_h3_' not in name and 'mobile_voice' not in name and ('mobile broadband' in t or 'mobile_broadband' in name) and ('raw coverage' in t) and first(r,'provider_id','providerid','providerId','provider_name','providerName') is not None
def state_match(r,s):
    f=STATE_TO_FIPS[s]; rf=str(first(r,'state_fips','stateFips','state_code','stateCode','fips') or '').zfill(2); rs=str(first(r,'state','state_name','stateName','state_abbreviation') or '').upper(); name=str(first(r,'file_name','filename','fileName','name') or ''); m=re.search(r'(?:^|_)bdc_(\d{2})_',name,re.I)
    return rf==f or rs==s or (m and m.group(1)==f)
def provider_match(r,p):
    name,pid=PROVIDERS[p]; rp=str(first(r,'provider_id','providerid','providerId') or '')
    return name.lower() in text(r) or (pid and rp==pid)
def valid(path):
    if not path.exists() or path.stat().st_size==0:return False
    with path.open('rb') as f: head=f.read(len(SQLITE))
    return head==SQLITE or zipfile.is_zipfile(path)
def filename(r):return Path(str(first(r,'file_name','filename','fileName','name') or f"fcc_{first(r,'file_id','id')}.zip")).name
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()
def load_manifest(path):
    try:
        payload=json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(payload,list): return {}
    out={}
    for row in payload:
        if not isinstance(row,dict): continue
        download=row.get('download_path')
        if not download: continue
        out[Path(str(download)).name]=row
    return out
def reusable(row,path):
    try:
        stat=path.stat()
    except FileNotFoundError:
        return False
    if row.get('download_size') != stat.st_size or row.get('download_mtime_ns') != stat.st_mtime_ns: return False
    extracted=[Path(str(x)) for x in row.get('extracted_files',[]) if x]
    return bool(row.get('sha256')) and bool(extracted) and all(x.exists() for x in extracted)
def extract(path,out):
    out.mkdir(parents=True,exist_ok=True)
    with path.open('rb') as f:isdb=f.read(len(SQLITE))==SQLITE
    if isdb or path.suffix.lower()=='.gpkg':
        target=out/(path.name if path.suffix.lower()=='.gpkg' else path.name+'.gpkg'); shutil.copy2(path,target); return [target]
    if not zipfile.is_zipfile(path):raise FCCError(f'Unsupported FCC payload: {path}')
    made=[]
    with zipfile.ZipFile(path) as z:
        members=[m for m in z.infolist() if not m.is_dir()]
        for m in members:
            with z.open(m) as f:isgpkg=Path(m.filename).suffix.lower()=='.gpkg' or f.read(len(SQLITE))==SQLITE
            if isgpkg:
                target=out/(Path(m.filename).name if Path(m.filename).suffix.lower()=='.gpkg' else Path(m.filename).name+'.gpkg')
                with z.open(m) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
                made.append(target)
        if made:return made
        stems={}
        for m in members:
            p=Path(m.filename); ext=p.suffix.lower()
            if ext in {'.shp','.dbf','.shx','.prj','.cpg','.qix'}:stems.setdefault(p.stem,{})[ext]=m
        for stem,parts in stems.items():
            if not {'.shp','.dbf','.shx'} <= parts.keys():continue
            for ext,m in parts.items():
                target=out/f'{stem}{ext}'
                with z.open(m) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
            made.append(out/f'{stem}.shp')
    if not made:raise FCCError(f'No GeoPackage or shapefile in {path}')
    return made

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True,type=Path); ap.add_argument('--as-of'); ap.add_argument('--providers',default='att,tmo,vzw'); ap.add_argument('--download-dir',type=Path,default=Path('data/downloads')); ap.add_argument('--coverage-root',type=Path,default=Path('data/coverage')); ap.add_argument('--catalog-dir',type=Path,default=Path('data/catalog')); ap.add_argument('--workers',type=int,default=3); ap.add_argument('--max-dates',type=int,default=8); ap.add_argument('--force',action='store_true'); a=ap.parse_args()
    user=os.getenv('FCC_API_USERNAME','').strip(); token=os.getenv('FCC_API_HASH_VALUE','').strip()
    if not user or not token:raise SystemExit('Set FCC_API_USERNAME and FCC_API_HASH_VALUE in .env.')
    wanted=[x.strip() for x in a.providers.split(',') if x.strip()]; unknown=set(wanted)-set(PROVIDERS)
    if unknown:raise SystemExit(f'Unknown providers: {sorted(unknown)}')
    states=extract_states(a.input); c=Client(user,token); a.catalog_dir.mkdir(parents=True,exist_ok=True)
    d_payload=c.js('listAsOfDates'); (a.catalog_dir/'as_of_dates.json').write_text(json.dumps(d_payload,indent=2,default=str))
    candidates=[a.as_of] if a.as_of else [d for d in dates(d_payload) if (parsedate(d).month,parsedate(d).day) in {(6,30),(12,31)}][:a.max_dates]
    selected=[]; chosen=None; catalog=[]
    for date in candidates:
        seen={};
        for params in ({'category':'Provider','subcategory':'Raw Coverage','technology_type':'Mobile Broadband'},{'category':'Provider','subcategory':'Raw Coverage'},{'category':'Provider'}):
            for r in rows(c.js(f'downloads/listAvailabilityData/{date}',params=params)):seen[json.dumps(r,sort_keys=True,default=str)]=r
            if any(raw_mobile(r) for r in seen.values()):break
        catalog=list(seen.values()); selected=[r for r in catalog if raw_mobile(r) and any(state_match(r,s) for s in states) and any(provider_match(r,p) for p in wanted)]
        print(f'Checked {date}: {len(selected)} matching FCC file(s)')
        if selected:chosen=date;break
    if not chosen:
        diag=a.catalog_dir/'unmatched_catalog.json'; diag.write_text(json.dumps(catalog,indent=2,default=str)); raise SystemExit(f'No matching FCC files found. Inspect {diag}.')
    safe=re.sub(r'[^0-9A-Za-z_-]+','_',chosen); coverage=a.coverage_root/safe; coverage.mkdir(parents=True,exist_ok=True); a.download_dir.mkdir(parents=True,exist_ok=True)
    manifest_path=a.catalog_dir/f'sync_manifest_{safe}.json'
    prior=load_manifest(manifest_path) if not a.force and manifest_path.exists() else {}
    manifest=[]
    def fetch(r):
        dest=a.download_dir/filename(r)
        if a.force or not valid(dest):
            if dest.exists():dest.unlink()
            c.download(r,dest); downloaded=True
        else:downloaded=False
        if not valid(dest):raise FCCError(f'Invalid downloaded file: {dest}')
        return r,dest,downloaded
    with ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex:
        for fut in as_completed([ex.submit(fetch,r) for r in selected]):
            r,path,downloaded=fut.result(); cached=prior.get(path.name)
            if not downloaded and cached and reusable(cached,path):
                extracted=[Path(str(x)) for x in cached['extracted_files']]; sha=cached['sha256']; reused=True
            else:
                extracted=extract(path,coverage); sha=digest(path); reused=False
            stat=path.stat()
            manifest.append({'as_of_date':chosen,'downloaded':downloaded,'download_path':str(path),'download_size':stat.st_size,'download_mtime_ns':stat.st_mtime_ns,'sha256':sha,'extracted_files':[str(x) for x in extracted],'catalog_record':r}); print('DOWNLOADED' if downloaded else ('REUSED' if reused else 'CACHED'),path.name)
    manifest_path.write_text(json.dumps(manifest,indent=2,default=str)); (a.catalog_dir/'selected_release.txt').write_text(chosen+'\n'); print(f'FCC_RELEASE={chosen}'); print(f'COVERAGE_DIR={coverage}')
if __name__=='__main__':main()
