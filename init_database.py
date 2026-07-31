#!/usr/bin/env python3
from pathlib import Path
import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

def dsn():
    return dict(
        host=os.getenv('POSTGRES_HOST','localhost'),
        port=os.getenv('POSTGRES_PORT','5433'),
        dbname=os.getenv('POSTGRES_DB','fcc_coverage'),
        user=os.getenv('POSTGRES_USER','fcc'),
        **{'password': os.getenv('POSTGRES_PASSWORD','fcc')},
    )

def db_init_statement_timeout_seconds() -> int:
    return int(os.getenv('DB_INIT_STATEMENT_TIMEOUT_SECONDS', '30'))

try:
    with psycopg.connect(**dsn(), autocommit=True, connect_timeout=10) as conn:
        conn.execute("SELECT set_config('statement_timeout', %s, false)", (f"{db_init_statement_timeout_seconds()}s",))
        conn.execute(Path('sql/001_schema.sql').read_text())
except psycopg.errors.QueryCanceled as exc:
    raise RuntimeError(
        "Schema initialization timed out — likely blocked by another active session holding a lock on fcc/processing tables. "
        "Check `SELECT pid, state, query FROM pg_stat_activity WHERE state != 'idle';` and terminate stuck sessions with "
        "`SELECT pg_terminate_backend(<pid>);`."
    ) from exc

print('Database schema initialized.')
