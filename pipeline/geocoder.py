"""
pipeline/geocoder.py — Bulk-geocode addresses via the Census Batch Geocoder
and cache results in the geocodes table.
"""
import csv
import io
import time
from typing import List

import requests

from db import get_conn
from pipeline.config import STATE_ABBR_TO_NAME, STATE_NAME_TO_ABBR

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_SIZE = 10_000


def _normalize(row: dict) -> str:
    """Return the canonical cache key for an address row."""
    parts = [
        row.get("address", "").strip().upper(),
        row.get("city", "").strip().upper(),
        row.get("state", "").strip().upper(),
        row.get("zip", "").strip().upper(),
    ]
    return "|".join(parts)


def _state_abbr(state: str) -> str:
    """Return the two-letter abbreviation for a state, accepting full names too."""
    upper = state.strip().upper()
    if upper in STATE_ABBR_TO_NAME:
        return upper
    return STATE_NAME_TO_ABBR.get(upper, upper)


def _fetch_cached(conn, normalized_keys: list[str]) -> dict:
    """Return {normalized: (lat, lon, match_type)} for already-cached keys."""
    if not normalized_keys:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT normalized, lat, lon, match_type FROM geocodes WHERE normalized = ANY(%s)",
            (normalized_keys,),
        )
        return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}


def _save_geocodes(conn, results: list[tuple]) -> None:
    """Upsert (normalized, lat, lon, match_type) tuples into geocodes table."""
    with conn.cursor() as cur:
        for normalized, lat, lon, match_type in results:
            cur.execute(
                """
                INSERT INTO geocodes (normalized, lat, lon, match_type)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (normalized) DO NOTHING
                """,
                (normalized, lat, lon, match_type),
            )


def _call_census_batch(batch: list[tuple]) -> dict:
    """
    Call Census batch geocoder for a list of (unique_id, address, city, state, zip) tuples.
    Returns {unique_id: (lat, lon, match_type)}.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    for uid, address, city, state, zipcode in batch:
        writer.writerow([uid, address, city, state, zipcode])
    payload = buf.getvalue().encode("utf-8")

    resp = requests.post(
        CENSUS_URL,
        data={"benchmark": "Public_AR_Current", "returntype": "locations"},
        files={"addressFile": ("addresses.csv", payload, "text/csv")},
        timeout=120,
    )
    resp.raise_for_status()

    results: dict = {}
    reader = csv.reader(io.StringIO(resp.text))
    for row in reader:
        if len(row) < 4:
            continue
        uid = row[0].strip()
        match_indicator = row[2].strip() if len(row) > 2 else ""
        if match_indicator.upper() == "MATCH" and len(row) >= 9:
            coords = row[5].strip()
            try:
                lon_str, lat_str = coords.split(",")
                lat = float(lat_str.strip())
                lon = float(lon_str.strip())
                match_type = row[3].strip() if len(row) > 3 else "Match"
                results[uid] = (lat, lon, match_type)
            except (ValueError, IndexError):
                results[uid] = (None, None, "NoMatch")
        else:
            results[uid] = (None, None, "NoMatch")
    return results


def geocode_addresses(cfg: dict, rows: list[dict]) -> None:
    """
    Geocode all unique addresses in *rows* using the Census Batch Geocoder.
    Results (including failures) are cached in the geocodes table.
    """
    conn = get_conn()
    try:
        # Build normalized key → original row mapping
        unique: dict[str, tuple] = {}  # normalized → (address, city, state, zip)
        for row in rows:
            norm = _normalize(row)
            if norm not in unique:
                abbr = _state_abbr(row.get("state", ""))
                unique[norm] = (
                    row.get("address", ""),
                    row.get("city", ""),
                    abbr,
                    row.get("zip", ""),
                )

        all_keys = list(unique.keys())
        cached = _fetch_cached(conn, all_keys)
        to_geocode = [k for k in all_keys if k not in cached]

        print(
            f"[geocoder] {len(all_keys)} unique addresses; "
            f"{len(cached)} cached; {len(to_geocode)} to geocode."
        )

        # Process in batches
        for batch_start in range(0, len(to_geocode), BATCH_SIZE):
            batch_keys = to_geocode[batch_start: batch_start + BATCH_SIZE]
            batch_input = [
                (str(i), *unique[k]) for i, k in enumerate(batch_keys)
            ]
            print(
                f"[geocoder] Sending batch {batch_start // BATCH_SIZE + 1} "
                f"({len(batch_input)} addresses) to Census..."
            )
            try:
                raw_results = _call_census_batch(batch_input)
            except Exception as exc:
                print(f"[geocoder] Census API error: {exc}. Marking batch as NoMatch.")
                raw_results = {str(i): (None, None, "Error") for i in range(len(batch_input))}

            to_save = []
            for i, key in enumerate(batch_keys):
                lat, lon, match_type = raw_results.get(str(i), (None, None, "NoMatch"))
                to_save.append((key, lat, lon, match_type))

            with conn:
                _save_geocodes(conn, to_save)

            if batch_start + BATCH_SIZE < len(to_geocode):
                time.sleep(1)  # be polite to Census API

        print("[geocoder] Geocoding complete.")
    finally:
        conn.close()
