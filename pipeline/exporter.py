"""
pipeline/exporter.py — Join input rows to coverage_results and write the
output CSV with columns: address, city, state, zip, att, tmo, vzw.
"""
import csv
import pathlib

from db import get_conn
from pipeline.geocoder import _normalize


def export_results(cfg: dict, rows: list[dict], release: str, output_path: str) -> None:
    """
    Look up coverage_results for each input row and write the output CSV.
    Rows without a coverage result receive FAIL for all carriers.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT normalized, carrier, result
                FROM coverage_results
                WHERE release = %s
                """,
                (release,),
            )
            rows_db = cur.fetchall()
    finally:
        conn.close()

    # Build lookup: {normalized: {carrier: result}}
    lookup: dict[str, dict[str, str]] = {}
    for normalized, carrier, result in rows_db:
        if normalized not in lookup:
            lookup[normalized] = {}
        lookup[normalized][carrier] = result

    out_path = pathlib.Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["address", "city", "state", "zip", "att", "tmo", "vzw"]
        )
        writer.writeheader()
        for row in rows:
            norm = _normalize(row)
            coverage = lookup.get(norm, {})
            writer.writerow(
                {
                    "address": row.get("address", ""),
                    "city": row.get("city", ""),
                    "state": row.get("state", ""),
                    "zip": row.get("zip", ""),
                    "att": coverage.get("att", "FAIL"),
                    "tmo": coverage.get("tmo", "FAIL"),
                    "vzw": coverage.get("vzw", "FAIL"),
                }
            )

    print(f"[exporter] Wrote {len(rows)} row(s) to {out_path}")
