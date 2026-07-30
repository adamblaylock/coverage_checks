"""
pipeline/coverage.py — Evaluate cellular coverage for all geocoded addresses
using PostGIS ST_Covers spatial joins against fcc_coverage_sub.

Coverage rule:
    PASS when ST_Covers(polygon, point)
         AND mindown >= 5
         AND (minsignal - 18) >= -105
"""
from db import get_conn
from pipeline.config import CARRIERS, COVERAGE_RULES


def evaluate_coverage(cfg: dict, release: str) -> None:
    """
    For every (geocoded_point, carrier) not already in coverage_results,
    run a spatial join against fcc_coverage_sub and write PASS/FAIL.
    """
    conn = get_conn()
    try:
        carriers = list(CARRIERS.keys())
        mindown_threshold = COVERAGE_RULES["mindown"]
        signal_floor = COVERAGE_RULES["signal_floor"]

        with conn:
            with conn.cursor() as cur:
                # Find all geocoded points that have a valid location
                cur.execute(
                    """
                    SELECT normalized, lat, lon
                    FROM geocodes
                    WHERE lat IS NOT NULL AND lon IS NOT NULL
                    """
                )
                geocoded = cur.fetchall()

                print(
                    f"[coverage] Evaluating {len(geocoded)} geocoded address(es) "
                    f"x {len(carriers)} carrier(s) for release={release} ..."
                )

                for normalized, lat, lon in geocoded:
                    for carrier in carriers:
                        # Skip if already evaluated
                        cur.execute(
                            """
                            SELECT 1 FROM coverage_results
                            WHERE normalized=%s AND release=%s AND carrier=%s
                            """,
                            (normalized, release, carrier),
                        )
                        if cur.fetchone():
                            continue

                        # ST_Covers spatial join with coverage rules
                        cur.execute(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM fcc_coverage_sub s
                                WHERE s.release = %s
                                  AND s.carrier = %s
                                  AND s.mindown >= %s
                                  AND (s.minsignal - 18) >= %s
                                  AND ST_Covers(
                                      s.geom,
                                      ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                                  )
                            )
                            """,
                            (release, carrier, mindown_threshold, signal_floor, lon, lat),
                        )
                        row = cur.fetchone()
                        result = "PASS" if (row and row[0]) else "FAIL"

                        cur.execute(
                            """
                            INSERT INTO coverage_results (normalized, release, carrier, result)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (normalized, release, carrier) DO NOTHING
                            """,
                            (normalized, release, carrier, result),
                        )

                # Addresses that failed geocoding are FAIL for all carriers
                cur.execute(
                    "SELECT normalized FROM geocodes WHERE lat IS NULL OR lon IS NULL"
                )
                no_geocode = [r[0] for r in cur.fetchall()]
                for normalized in no_geocode:
                    for carrier in carriers:
                        cur.execute(
                            """
                            INSERT INTO coverage_results (normalized, release, carrier, result)
                            VALUES (%s, %s, %s, 'FAIL')
                            ON CONFLICT (normalized, release, carrier) DO NOTHING
                            """,
                            (normalized, release, carrier),
                        )

        print("[coverage] Coverage evaluation complete.")
    finally:
        conn.close()
