import os
import pathlib

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "fcc_coverage"),
        user=os.getenv("POSTGRES_USER", "fcc"),
        password=os.getenv("POSTGRES_PASSWORD", "fcc"),
    )


def init_db():
    sql = (pathlib.Path(__file__).parent / "schema.sql").read_text()
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        print("[db] Schema initialized.")
    finally:
        conn.close()
