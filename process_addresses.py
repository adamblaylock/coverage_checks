#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import re
import time
import uuid
from pathlib import Path

import pandas as pd
import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
REQUIRED_COLUMNS = {"address", "city", "state", "zip"}

STATE_TO_CODE = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "PUERTO RICO": "PR", "GUAM": "GU", "AMERICAN SAMOA": "AS",
    "NORTHERN MARIANA ISLANDS": "MP", "U.S. VIRGIN ISLANDS": "VI",
}


def normalize_state(value: object) -> str:
    state = clean(value)
    if len(state) == 2:
        return state
    return STATE_TO_CODE.get(state, state)


def dbkw() -> dict[str, str]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5433"),
        "dbname": os.getenv("POSTGRES_DB", "fcc_coverage"),
        "user": os.getenv("POSTGRES_USER", "fcc"),
        "password": os.getenv("POSTGRES_PASSWORD", "fcc"),
    }


def pg_statement_timeout_ms() -> int:
    return int(os.getenv("PG_STATEMENT_TIMEOUT_MS", "600000"))


def pg_idle_in_transaction_timeout_ms() -> int:
    return int(os.getenv("PG_IDLE_IN_TRANSACTION_TIMEOUT_MS", "300000"))


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def normalize_zip(value: object) -> str:
    match = re.search(r"\d{5}", str(value or ""))
    return match.group(0) if match else ""


def normalized_address(row: pd.Series) -> str:
    return "|".join(
        [
            clean(row.get("address")),
            clean(row.get("city")),
            clean(row.get("state")),
            normalize_zip(row.get("zip")),
        ]
    )


def address_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_input(path: Path, chunk_size: int) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    row_offset = 0

    for chunk in pd.read_csv(
        path,
        dtype=str,
        chunksize=chunk_size,
        keep_default_na=False,
        encoding="utf-8-sig",
    ):
        chunk.columns = [column.strip().lower() for column in chunk.columns]
        missing = REQUIRED_COLUMNS - set(chunk.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        chunk["row_number"] = range(row_offset + 1, row_offset + len(chunk) + 1)
        row_offset += len(chunk)

        if "source_id" not in chunk.columns:
            chunk["source_id"] = chunk["row_number"].astype(str)

        chunk["address"] = chunk["address"].str.strip()
        chunk["city"] = chunk["city"].str.strip()
        chunk["state"] = chunk["state"].str.strip()
        chunk["zip"] = chunk["zip"].map(normalize_zip)
        chunk["state_code"] = chunk["state"].map(normalize_state)
        address_clean = (
            chunk["address"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
            .str.upper()
        )
        city_clean = (
            chunk["city"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
            .str.upper()
        )
        chunk["normalized_address"] = (
            address_clean
            + "|"
            + city_clean
            + "|"
            + chunk["state_code"].fillna("").astype(str)
            + "|"
            + chunk["zip"].fillna("").astype(str)
        )
        chunk["address_hash"] = chunk["normalized_address"].map(address_hash)
        chunks.append(chunk)

    if not chunks:
        raise ValueError("The input file contains no address rows.")

    return pd.concat(chunks, ignore_index=True)


def fetch_cached_hashes(conn: psycopg.Connection, hashes: list[str]) -> set[str]:
    cached: set[str] = set()
    page_size = 20_000
    with conn.cursor() as cur:
        for start in range(0, len(hashes), page_size):
            page = hashes[start : start + page_size]
            cur.execute(
                "SELECT address_hash FROM processing.address_geocode_cache "
                "WHERE address_hash = ANY(%s)",
                (page,),
            )
            cached.update(row[0] for row in cur.fetchall())
    return cached


def make_census_batch_csv(rows: pd.DataFrame) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for row in rows.itertuples(index=False):
        writer.writerow(
            [
                row.address_hash,
                row.address,
                row.city,
                row.state_code,
                row.zip,
            ]
        )
    return output.getvalue().encode("utf-8")


def parse_coordinates(value: str) -> tuple[float | None, float | None]:
    try:
        longitude_text, latitude_text = value.split(",", 1)
        return float(latitude_text), float(longitude_text)
    except (AttributeError, TypeError, ValueError):
        return None, None


def parse_census_response(content: bytes) -> list[tuple]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    parsed: list[tuple] = []

    for row in reader:
        if not row:
            continue
        row += [""] * max(0, 8 - len(row))
        hash_value = row[0].strip()
        input_address = row[1].strip()
        match_status = row[2].strip()
        match_type = row[3].strip()
        matched_address = row[4].strip()
        latitude, longitude = parse_coordinates(row[5].strip())
        tigerline_id = row[6].strip() or None
        tigerline_side = row[7].strip() or None
        success = match_status.lower() == "match" and latitude is not None and longitude is not None

        parsed.append(
            (
                hash_value,
                input_address,
                latitude,
                longitude,
                "success" if success else "failed",
                "census_batch",
                match_status or None,
                match_type or None,
                matched_address or None,
                tigerline_id,
                tigerline_side,
                None if success else (match_status or "No match returned"),
            )
        )
    return parsed


def submit_census_batch(rows: pd.DataFrame, timeout: int, max_retries: int) -> list[tuple]:
    payload = make_census_batch_csv(rows)
    benchmark = os.getenv("CENSUS_GEOCODER_BENCHMARK", "Public_AR_Current")
    user_agent = os.getenv("GEOCODER_USER_AGENT", "fcc-coverage-bulk/6.0")

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                CENSUS_BATCH_URL,
                data={"benchmark": benchmark},
                files={"addressFile": ("addresses.csv", payload, "text/csv")},
                headers={"User-Agent": user_agent},
                timeout=timeout,
            )
            response.raise_for_status()
            parsed = parse_census_response(response.content)
            if not parsed:
                raise RuntimeError("Census geocoder returned an empty response")
            return parsed
        except (requests.RequestException, RuntimeError) as exc:
            if attempt == max_retries:
                raise RuntimeError(
                    f"Census batch geocoder failed after {max_retries} attempts: {exc}"
                ) from exc
            sleep_seconds = min(60, 2 ** (attempt - 1) * 5)
            print(
                f"Geocoder attempt {attempt}/{max_retries} failed; "
                f"retrying in {sleep_seconds} seconds: {exc}"
            )
            time.sleep(sleep_seconds)

    raise AssertionError("unreachable")


def upsert_geocodes(conn: psycopg.Connection, rows: list[tuple], metadata: dict[str, tuple[str, str]]) -> None:
    values = []
    for row in rows:
        (
            hash_value,
            _input_address,
            latitude,
            longitude,
            status,
            geocoder,
            match_status,
            match_type,
            matched_address,
            tigerline_id,
            tigerline_side,
            error_message,
        ) = row
        normalized, state_code = metadata[hash_value]
        values.append(
            (
                hash_value,
                normalized,
                state_code,
                latitude,
                longitude,
                longitude,
                latitude,
                longitude,
                latitude,
                status,
                geocoder,
                match_status,
                match_type,
                matched_address,
                tigerline_id,
                tigerline_side,
                error_message,
            )
        )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO processing.address_geocode_cache
            (
                address_hash, normalized_address, state_code,
                latitude, longitude, geom, geocode_status, geocoder,
                match_status, match_type, matched_address,
                tigerline_id, tigerline_side, error_message
            )
            VALUES
            (
                %s, %s, %s, %s, %s,
                CASE WHEN %s::double precision IS NULL OR %s::double precision IS NULL THEN NULL
                     ELSE ST_SetSRID(ST_MakePoint(%s::double precision, %s::double precision), 4326) END,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (address_hash) DO UPDATE SET
                normalized_address = excluded.normalized_address,
                state_code = excluded.state_code,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                geom = excluded.geom,
                geocode_status = excluded.geocode_status,
                geocoder = excluded.geocoder,
                match_status = excluded.match_status,
                match_type = excluded.match_type,
                matched_address = excluded.matched_address,
                tigerline_id = excluded.tigerline_id,
                tigerline_side = excluded.tigerline_side,
                geocoded_at = now(),
                error_message = excluded.error_message
            """,
            values,
        )
    conn.commit()


def geocode_uncached(df: pd.DataFrame, conn: psycopg.Connection, batch_size: int) -> None:
    unique = df[
        [
            "address_hash",
            "normalized_address",
            "state_code",
            "address",
            "city",
            "zip",
        ]
    ].drop_duplicates("address_hash")

    cached = fetch_cached_hashes(conn, unique["address_hash"].tolist())
    uncached = unique[~unique["address_hash"].isin(cached)].copy()

    print(
        f"Geocode cache: {len(cached):,} found; "
        f"{len(uncached):,} unique addresses require geocoding."
    )
    if uncached.empty:
        return

    timeout = int(os.getenv("CENSUS_GEOCODER_TIMEOUT_SECONDS", "180"))
    max_retries = int(os.getenv("CENSUS_GEOCODER_MAX_RETRIES", "4"))
    pause = float(os.getenv("CENSUS_GEOCODER_BATCH_PAUSE_SECONDS", "1"))
    metadata = {
        row.address_hash: (row.normalized_address, row.state_code)
        for row in uncached.itertuples(index=False)
    }

    total_batches = (len(uncached) + batch_size - 1) // batch_size
    for batch_number, start in enumerate(range(0, len(uncached), batch_size), 1):
        batch = uncached.iloc[start : start + batch_size]
        print(
            f"Submitting geocoder batch {batch_number:,}/{total_batches:,} "
            f"({len(batch):,} addresses)..."
        )
        parsed = submit_census_batch(batch, timeout, max_retries)
        returned_hashes = {row[0] for row in parsed}

        # Preserve a durable failure record if the service omits any input row.
        for row in batch.itertuples(index=False):
            if row.address_hash not in returned_hashes:
                parsed.append(
                    (
                        row.address_hash,
                        "",
                        None,
                        None,
                        "failed",
                        "census_batch",
                        None,
                        None,
                        None,
                        None,
                        None,
                        "Address omitted from Census response",
                    )
                )

        upsert_geocodes(conn, parsed, metadata)
        successes = sum(1 for row in parsed if row[4] == "success")
        print(f"Geocoder batch complete: {successes:,}/{len(batch):,} matched.")
        if batch_number < total_batches and pause > 0:
            time.sleep(pause)


def copy_batch(df: pd.DataFrame, conn: psycopg.Connection, batch_id: uuid.UUID) -> None:
    conn.execute(
        "SELECT set_config('statement_timeout', %s, false)",
        (f"{pg_statement_timeout_ms()}ms",),
    )
    conn.execute(
        "SELECT set_config('idle_in_transaction_session_timeout', %s, false)",
        (f"{pg_idle_in_transaction_timeout_ms()}ms",),
    )
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for row in df.itertuples(index=False):
        writer.writerow(
            [
                str(batch_id),
                row.row_number,
                row.source_id,
                row.address_hash,
                row.address,
                row.city,
                row.state_code,
                row.zip,
            ]
        )
    output.seek(0)

    with conn.cursor() as cur:
        with cur.copy(
            "COPY processing.address_batch "
            "(batch_id,row_number,source_id,address_hash,address,city,state_code,zip) "
            "FROM STDIN WITH CSV"
        ) as copy:
            while data := output.read(1024 * 1024):
                copy.write(data)

        cur.execute(
            """
            UPDATE processing.address_batch b
               SET latitude = c.latitude,
                   longitude = c.longitude,
                   geom = c.geom,
                   geocode_status = c.geocode_status,
                   matched_address = c.matched_address
              FROM processing.address_geocode_cache c
             WHERE b.batch_id = %s
               AND c.address_hash = b.address_hash
            """,
            (batch_id,),
        )
        cur.execute("ANALYZE processing.address_batch")
    conn.commit()


def evaluate(conn: psycopg.Connection, batch_id: uuid.UUID, release_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH addresses AS
            (
                SELECT DISTINCT address_hash, state_code, geom
                FROM processing.address_batch
                WHERE batch_id = %s
                  AND geom IS NOT NULL
            ),
            needed AS
            (
                SELECT a.address_hash, a.state_code, a.geom, carrier.carrier_code
                FROM addresses a
                CROSS JOIN LATERAL unnest(ARRAY['att', 'tmo', 'vzw']) AS carrier(carrier_code)
                WHERE NOT EXISTS
                (
                    SELECT 1
                    FROM processing.address_coverage_cache cache
                    WHERE cache.address_hash = a.address_hash
                      AND cache.release_id = %s
                      AND cache.carrier_code = carrier.carrier_code
                )
            ),
            hits AS
            (
                SELECT
                    needed.address_hash,
                    needed.carrier_code,
                    coverage.technology,
                    coverage.mindown,
                    coverage.minsignal,
                    row_number() OVER
                    (
                        PARTITION BY needed.address_hash, needed.carrier_code
                        ORDER BY coverage.mindown DESC NULLS LAST,
                                 coverage.minsignal DESC NULLS LAST
                    ) AS rank
                FROM needed
                JOIN fcc.mobile_coverage_subdivided coverage
                  ON coverage.state_code = needed.state_code
                 AND coverage.release_id = %s
                 AND coverage.geom && needed.geom
                 AND ST_Covers(coverage.geom, needed.geom)
                WHERE CASE
                          WHEN coverage.brandname ILIKE 'AT&T%%' THEN 'att'
                          WHEN coverage.brandname ILIKE 'T-Mobile%%' THEN 'tmo'
                          WHEN coverage.brandname ILIKE 'Verizon%%' THEN 'vzw'
                      END = needed.carrier_code
                  AND coverage.mindown >= 5
                  AND (
                      CASE
                          WHEN LOWER(TRIM(COALESCE(coverage.environmnt, ''))) IN ('indoor', 'i', '1')
                              THEN coverage.minsignal
                          ELSE coverage.minsignal - 12
                      END
                  ) >= -115
            )
            INSERT INTO processing.address_coverage_cache
            (
                address_hash, release_id, carrier_code,
                result, best_mindown, best_minsignal, technology
            )
            SELECT
                needed.address_hash,
                %s,
                needed.carrier_code,
                CASE WHEN hits.address_hash IS NULL THEN 'FAIL' ELSE 'PASS' END,
                hits.mindown,
                hits.minsignal,
                hits.technology
            FROM needed
            LEFT JOIN hits
              ON hits.address_hash = needed.address_hash
             AND hits.carrier_code = needed.carrier_code
             AND hits.rank = 1
            ON CONFLICT (address_hash, release_id, carrier_code) DO NOTHING
            """,
            (batch_id, release_id, release_id, release_id),
        )
    conn.commit()


def export_results(
    conn: psycopg.Connection,
    batch_id: uuid.UUID,
    release_id: str,
    path: Path,
) -> None:
    """Export only input address fields and PASS/FAIL carrier results.

    Geocoding details, coordinates, cache metadata, and batch identifiers remain
    internal to PostgreSQL and are intentionally excluded from the deliverable.
    An address that could not be geocoded is exported as FAIL for each carrier
    because no qualifying coverage polygon could be confirmed.
    """
    query = """
        SELECT
            batch.address,
            batch.city,
            batch.state_code AS state,
            batch.zip,
            CASE
                WHEN batch.geom IS NULL THEN 'FAIL'
                ELSE coalesce(
                    max(cache.result) FILTER (WHERE cache.carrier_code = 'att'),
                    'FAIL'
                )
            END AS att,
            CASE
                WHEN batch.geom IS NULL THEN 'FAIL'
                ELSE coalesce(
                    max(cache.result) FILTER (WHERE cache.carrier_code = 'tmo'),
                    'FAIL'
                )
            END AS tmo,
            CASE
                WHEN batch.geom IS NULL THEN 'FAIL'
                ELSE coalesce(
                    max(cache.result) FILTER (WHERE cache.carrier_code = 'vzw'),
                    'FAIL'
                )
            END AS vzw
        FROM processing.address_batch batch
        LEFT JOIN processing.address_coverage_cache cache
          ON cache.address_hash = batch.address_hash
         AND cache.release_id = %s
        WHERE batch.batch_id = %s
        GROUP BY
            batch.row_number, batch.address, batch.city,
            batch.state_code, batch.zip, batch.geom
        ORDER BY batch.row_number
    """
    output = pd.read_sql_query(query, conn, params=(release_id, batch_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Geocode address-only input and evaluate FCC mobile coverage in bulk."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-id", default="current")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument(
        "--geocoder-batch-size",
        type=int,
        default=9_000,
        help="Addresses per Census request; must not exceed 10,000.",
    )
    parser.add_argument("--keep-batch", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.geocoder_batch_size <= 10_000:
        parser.error("--geocoder-batch-size must be between 1 and 10,000")

    frame = read_input(args.input, args.chunk_size)
    batch_id = uuid.uuid4()
    unique_count = frame["address_hash"].nunique()

    with psycopg.connect(**dbkw()) as conn:
        conn.execute(Path("sql/001_schema.sql").read_text())
        conn.commit()
        conn.execute(
            """
            INSERT INTO processing.batch_run
                (batch_id, source_file, release_id, row_count, unique_address_count)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (batch_id, args.input.name, args.release_id, len(frame), unique_count),
        )
        conn.commit()

        try:
            geocode_uncached(frame, conn, args.geocoder_batch_size)
            copy_batch(frame, conn, batch_id)
            evaluate(conn, batch_id, args.release_id)
            export_results(conn, batch_id, args.release_id, args.output)
            conn.execute(
                "UPDATE processing.batch_run "
                "SET completed_at = now(), status = 'complete' WHERE batch_id = %s",
                (batch_id,),
            )
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "UPDATE processing.batch_run "
                "SET completed_at = now(), status = 'failed', error_message = %s "
                "WHERE batch_id = %s",
                (str(exc)[:4000], batch_id),
            )
            conn.commit()
            raise
        finally:
            if not args.keep_batch:
                conn.execute(
                    "DELETE FROM processing.address_batch WHERE batch_id = %s",
                    (batch_id,),
                )
            conn.commit()

    print(
        f"Processed {len(frame):,} rows ({unique_count:,} unique addresses). "
        f"Output: {args.output}"
    )


if __name__ == "__main__":
    main()
