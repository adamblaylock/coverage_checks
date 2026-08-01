#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, io, os, re, sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import geopandas as gpd
import pandas as pd
import psycopg
from shapely.geometry import MultiPolygon
from shapely import wkb
from dotenv import load_dotenv
from extract_states import extract_states

load_dotenv()
EXPECTED={'frn','providerid','brandname','technology','mindown','minup','minsignal','environmnt'}
FIPS_TO_STATE={'01':'AL','02':'AK','04':'AZ','05':'AR','06':'CA','08':'CO','09':'CT','10':'DE','11':'DC','12':'FL','13':'GA','15':'HI','16':'ID','17':'IL','18':'IN','19':'IA','20':'KS','21':'KY','22':'LA','23':'ME','24':'MD','25':'MA','26':'MI','27':'MN','28':'MS','29':'MO','30':'MT','31':'NE','32':'NV','33':'NH','34':'NJ','35':'NM','36':'NY','37':'NC','38':'ND','39':'OH','40':'OK','41':'OR','42':'PA','44':'RI','45':'SC','46':'SD','47':'TN','48':'TX','49':'UT','50':'VT','51':'VA','53':'WA','54':'WV','55':'WI','56':'WY','60':'AS','66':'GU','69':'MP','72':'PR','78':'VI'}

def dbkw():
    out=dict(host=os.getenv('POSTGRES_HOST','localhost'),port=os.getenv('POSTGRES_PORT','5432'),dbname=os.getenv('POSTGRES_DB','fcc_coverage'),user=os.getenv('POSTGRES_USER','fcc'))
    out['password']=os.getenv('POSTGRES_PASSWORD','fcc')
    return out

def pg_statement_timeout_ms()->int:
    return int(os.getenv('PG_STATEMENT_TIMEOUT_MS','600000'))

def pg_idle_in_transaction_timeout_ms()->int:
    return int(os.getenv('PG_IDLE_IN_TRANSACTION_TIMEOUT_MS','300000'))

def state_from_name(path: Path)->str:
    m=re.search(r'(?:^|_)bdc_(\d{2})_',path.name,re.I)
    if not m or m.group(1) not in FIPS_TO_STATE: raise ValueError(f'Cannot determine state FIPS from {path.name}')
    return FIPS_TO_STATE[m.group(1)]

def layers(path:Path):
    import pyogrio
    return [x[0] for x in pyogrio.list_layers(path)]

def normalize(gdf:gpd.GeoDataFrame,state:str,release_id:str,source:str):
    gdf.columns=[str(c).lower() for c in gdf.columns]
    geom_name=gdf.geometry.name
    missing=EXPECTED-set(gdf.columns)
    if missing: raise ValueError(f'{source} missing columns: {sorted(missing)}')
    keep=list(EXPECTED)+[geom_name]
    gdf=gdf[keep].copy()
    gdf=gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.crs is None: raise ValueError(f'{source} has no CRS')
    if gdf.crs.to_epsg() != 4326: gdf=gdf.to_crs(4326)
    invalid_mask=~gdf.geometry.is_valid
    if invalid_mask.any(): gdf.loc[invalid_mask,gdf.geometry.name]=gdf.loc[invalid_mask,gdf.geometry.name].make_valid()
    gdf=gdf.explode(index_parts=False, ignore_index=True)
    gdf=gdf[gdf.geom_type.isin(['Polygon','MultiPolygon'])]
    is_polygon=gdf.geom_type=='Polygon'
    if is_polygon.any(): gdf.loc[is_polygon,gdf.geometry.name]=gdf.loc[is_polygon,gdf.geometry.name].apply(lambda g: MultiPolygon([g]))
    for col in ('mindown','minup','minsignal'): gdf[col]=pd.to_numeric(gdf[col],errors='coerce')
    gdf['state_code']=state; gdf['release_id']=release_id; gdf['source_file']=source
    return gdf[['state_code','release_id','frn','providerid','brandname','technology','mindown','minup','minsignal','environmnt','source_file',geom_name]].rename_geometry('geom')

def copy_mobile_coverage(gdf:gpd.GeoDataFrame,conn:psycopg.Connection,chunksize:int)->None:
    output=io.StringIO()
    writer=csv.writer(output,lineterminator='\n')
    flush_every=max(chunksize,1)
    with conn.cursor() as cur:
        with cur.copy("COPY fcc.mobile_coverage (state_code,release_id,frn,providerid,brandname,technology,mindown,minup,minsignal,environmnt,source_file,geom) FROM STDIN WITH CSV") as copy:
            for idx,row in enumerate(gdf.itertuples(index=False,name=None),1):
                state_code,release_id,frn,providerid,brandname,technology,mindown,minup,minsignal,environmnt,source_file,geom=row
                writer.writerow([
                    None if pd.isna(state_code) else state_code,
                    None if pd.isna(release_id) else release_id,
                    None if pd.isna(frn) else frn,
                    None if pd.isna(providerid) else providerid,
                    None if pd.isna(brandname) else brandname,
                    None if pd.isna(technology) else technology,
                    None if pd.isna(mindown) else mindown,
                    None if pd.isna(minup) else minup,
                    None if pd.isna(minsignal) else minsignal,
                    None if pd.isna(environmnt) else environmnt,
                    None if pd.isna(source_file) else source_file,
                    wkb.dumps(geom,hex=True,srid=4326),
                ])
                if idx % flush_every == 0:
                    output.seek(0)
                    while data:=output.read(1024*1024): copy.write(data)
                    output.seek(0); output.truncate(0)
            output.seek(0)
            while data:=output.read(1024*1024): copy.write(data)
    conn.commit()

def load_layer(path:str,layer:str,state:str,release_id:str,chunksize:int,conn_kw:dict[str,str])->int:
    conn=None
    try:
        gdf=gpd.read_file(path,layer=layer,engine='pyogrio')
        gdf=normalize(gdf,state,release_id,Path(path).name)
        conn=psycopg.connect(**conn_kw)
        conn.execute("SELECT set_config('statement_timeout', '0', false)")
        conn.execute("SELECT set_config('idle_in_transaction_session_timeout', '0', false)")
        copy_mobile_coverage(gdf,conn,chunksize)
        return len(gdf)
    except Exception as exc:
        if conn is not None and not conn.closed:
            try: conn.rollback()
            except Exception: pass
        raise RuntimeError(f'Failed to import {Path(path).name}:{layer}: {exc}') from exc
    except BaseException:
        if conn is not None and not conn.closed:
            try: conn.rollback()
            except Exception: pass
        raise
    finally:
        if conn is not None and not conn.closed:
            conn.close()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--coverage-dir',type=Path,default=Path('data/coverage')); p.add_argument('--input',type=Path); p.add_argument('--release-id',default='current'); p.add_argument('--replace-states',action='store_true'); p.add_argument('--subdivide',action='store_true'); p.add_argument('--chunksize',type=int,default=100_000); p.add_argument('--workers',type=int,default=(os.cpu_count() or 1)); a=p.parse_args()
    if a.workers < 1: raise SystemExit('--workers must be at least 1')
    paths=sorted([*a.coverage_dir.glob('*.gpkg'),*a.coverage_dir.glob('*.shp')])
    states=set(extract_states(a.input)) if a.input else {state_from_name(x) for x in paths}
    paths=[x for x in paths if state_from_name(x) in states]
    if not paths: raise SystemExit('No matching coverage files found.')
    conn_kw=dbkw()
    with psycopg.connect(**conn_kw,autocommit=True) as conn:
        conn.execute(Path('sql/001_schema.sql').read_text())
        if a.replace_states:
            conn.execute('DELETE FROM fcc.mobile_coverage_subdivided WHERE state_code = ANY(%s)',(list(states),))
            conn.execute('DELETE FROM fcc.mobile_coverage WHERE state_code = ANY(%s)',(list(states),))
    imported=0
    jobs=[]
    for path in paths:
        state=state_from_name(path)
        for layer in layers(path):
            jobs.append((str(path),layer,state,path.name))
    total_jobs=len(jobs)
    unit='unit' if total_jobs == 1 else 'units'
    worker_word='worker' if a.workers == 1 else 'workers'
    print(f'Importing {total_jobs:,} coverage file/layer {unit} with {a.workers} {worker_word}...')
    if a.workers == 1:
        for completed,(path,layer,state,path_name) in enumerate(jobs,start=1):
            count=load_layer(path,layer,state,a.release_id,a.chunksize,conn_kw)
            imported+=count
            print(f'[{completed}/{total_jobs} | {total_jobs-completed} remaining] Imported {count:,} rows from {path_name}:{layer}')
    else:
        with ProcessPoolExecutor(max_workers=a.workers) as executor:
            futures={executor.submit(load_layer,path,layer,state,a.release_id,a.chunksize,conn_kw):(path_name,layer) for path,layer,state,path_name in jobs}
            for completed,future in enumerate(as_completed(futures),start=1):
                path_name,layer=futures[future]
                try:
                    count=future.result()
                except Exception as exc:
                    raise RuntimeError(f'Failed processing {path_name}:{layer}') from exc
                imported+=count
                print(f'[{completed}/{total_jobs} | {total_jobs-completed} remaining] Imported {count:,} rows from {path_name}:{layer}')
    with psycopg.connect(**conn_kw,autocommit=True) as conn:
        conn.execute('ANALYZE fcc.mobile_coverage')
        if a.subdivide:
            state_count=len(states)
            state_word='state' if state_count == 1 else 'states'
            print(f'Subdividing coverage polygons for {state_count:,} {state_word}...')
            conn.execute('DELETE FROM fcc.mobile_coverage_subdivided WHERE state_code = ANY(%s)',(list(states),))
            inserted=conn.execute("""INSERT INTO fcc.mobile_coverage_subdivided (coverage_id,state_code,release_id,providerid,brandname,technology,mindown,minup,minsignal,environmnt,geom)
                SELECT coverage_id,state_code,release_id,providerid,brandname,technology,mindown,minup,minsignal,environmnt,(ST_Dump(ST_Subdivide(geom,256))).geom::geometry(Polygon,4326)
                FROM fcc.mobile_coverage WHERE state_code = ANY(%s)""",(list(states),))
            conn.execute('ANALYZE fcc.mobile_coverage_subdivided')
            inserted_count=inserted.rowcount
            if inserted_count >= 0:
                print(f'Coverage polygon subdivision complete. {inserted_count:,} subdivided rows created.')
            else:
                print('Coverage polygon subdivision complete.')
    print(f'Complete: {imported:,} coverage polygons imported.')
if __name__=='__main__': main()
