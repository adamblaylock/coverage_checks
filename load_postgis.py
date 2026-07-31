#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, re, sys
from pathlib import Path
import geopandas as gpd
import pandas as pd
import psycopg
from sqlalchemy import create_engine
from shapely.geometry import MultiPolygon
from dotenv import load_dotenv
from extract_states import extract_states

load_dotenv()
EXPECTED={'frn','providerid','brandname','technology','mindown','minup','minsignal','environmnt'}
FIPS_TO_STATE={'01':'AL','02':'AK','04':'AZ','05':'AR','06':'CA','08':'CO','09':'CT','10':'DE','11':'DC','12':'FL','13':'GA','15':'HI','16':'ID','17':'IL','18':'IN','19':'IA','20':'KS','21':'KY','22':'LA','23':'ME','24':'MD','25':'MA','26':'MI','27':'MN','28':'MS','29':'MO','30':'MT','31':'NE','32':'NV','33':'NH','34':'NJ','35':'NM','36':'NY','37':'NC','38':'ND','39':'OH','40':'OK','41':'OR','42':'PA','44':'RI','45':'SC','46':'SD','47':'TN','48':'TX','49':'UT','50':'VT','51':'VA','53':'WA','54':'WV','55':'WI','56':'WY','60':'AS','66':'GU','69':'MP','72':'PR','78':'VI'}

def dbkw():
    return dict(host=os.getenv('POSTGRES_HOST','localhost'),port=os.getenv('POSTGRES_PORT','5432'),dbname=os.getenv('POSTGRES_DB','fcc_coverage'),user=os.getenv('POSTGRES_USER','fcc'),password=os.getenv('POSTGRES_PASSWORD','fcc'))

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
    gdf=gdf.to_crs(4326)
    gdf.geometry=gdf.geometry.make_valid()
    gdf=gdf.explode(index_parts=False, ignore_index=True)
    gdf=gdf[gdf.geom_type.isin(['Polygon','MultiPolygon'])]
    gdf.geometry=gdf.geometry.apply(lambda x: x if x.geom_type=='MultiPolygon' else MultiPolygon([x]))
    for col in ('mindown','minup','minsignal'): gdf[col]=pd.to_numeric(gdf[col],errors='coerce')
    gdf['state_code']=state; gdf['release_id']=release_id; gdf['source_file']=source
    return gdf[['state_code','release_id','frn','providerid','brandname','technology','mindown','minup','minsignal','environmnt','source_file',geom_name]].rename_geometry('geom')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--coverage-dir',type=Path,default=Path('data/coverage')); p.add_argument('--input',type=Path); p.add_argument('--release-id',default='current'); p.add_argument('--replace-states',action='store_true'); p.add_argument('--subdivide',action='store_true'); p.add_argument('--chunksize',type=int,default=100_000); a=p.parse_args()
    paths=sorted([*a.coverage_dir.glob('*.gpkg'),*a.coverage_dir.glob('*.shp')])
    states=set(extract_states(a.input)) if a.input else {state_from_name(x) for x in paths}
    paths=[x for x in paths if state_from_name(x) in states]
    if not paths: raise SystemExit('No matching coverage files found.')
    engine=create_engine('postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}'.format(**dbkw()))
    with psycopg.connect(**dbkw(),autocommit=True) as conn:
        conn.execute(Path('sql/001_schema.sql').read_text())
        if a.replace_states:
            conn.execute('DELETE FROM fcc.mobile_coverage_subdivided WHERE state_code = ANY(%s)',(list(states),))
            conn.execute('DELETE FROM fcc.mobile_coverage WHERE state_code = ANY(%s)',(list(states),))
    imported=0
    for path in paths:
        state=state_from_name(path)
        for layer in layers(path):
            gdf=gpd.read_file(path,layer=layer,engine='pyogrio')
            gdf=normalize(gdf,state,a.release_id,path.name)
            gdf.to_postgis('mobile_coverage',engine,schema='fcc',if_exists='append',index=False,chunksize=a.chunksize)
            imported+=len(gdf); print(f'Imported {len(gdf):,} rows from {path.name}:{layer}')
    with psycopg.connect(**dbkw(),autocommit=True) as conn:
        conn.execute('ANALYZE fcc.mobile_coverage')
        if a.subdivide:
            conn.execute('DELETE FROM fcc.mobile_coverage_subdivided WHERE state_code = ANY(%s)',(list(states),))
            conn.execute("""INSERT INTO fcc.mobile_coverage_subdivided (coverage_id,state_code,release_id,providerid,brandname,technology,mindown,minup,minsignal,environmnt,geom)
                SELECT coverage_id,state_code,release_id,providerid,brandname,technology,mindown,minup,minsignal,environmnt,(ST_Dump(ST_Subdivide(geom,256))).geom::geometry(Polygon,4326)
                FROM fcc.mobile_coverage WHERE state_code = ANY(%s)""",(list(states),))
            conn.execute('ANALYZE fcc.mobile_coverage_subdivided')
    print(f'Complete: {imported:,} coverage polygons imported.')
if __name__=='__main__': main()
