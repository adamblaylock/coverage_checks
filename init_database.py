#!/usr/bin/env python3
from pathlib import Path
import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

def dsn():
    return dict(host=os.getenv('POSTGRES_HOST','localhost'), port=os.getenv('POSTGRES_PORT','5433'), dbname=os.getenv('POSTGRES_DB','fcc_coverage'), user=os.getenv('POSTGRES_USER','fcc'), password=os.getenv('POSTGRES_PASSWORD','fcc'))

with psycopg.connect(**dsn(), autocommit=True) as conn:
    conn.execute(Path('sql/001_schema.sql').read_text())
print('Database schema initialized.')
