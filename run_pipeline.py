#!/usr/bin/env python3
"""
Entry point for the FCC Mobile Coverage Estimator pipeline.
"""
import argparse
import csv
import pathlib
import sys

from db import init_db
from pipeline.config import load_config
from pipeline.fcc_api import select_release, fetch_catalog
from pipeline.downloader import sync_fcc_files
from pipeline.importer import import_release
from pipeline.geocoder import geocode_addresses
from pipeline.coverage import evaluate_coverage
from pipeline.exporter import export_results


def parse_args():
    p = argparse.ArgumentParser(description="FCC Mobile Coverage Estimator")
    p.add_argument("--input", required=True, help="Path to input address CSV")
    p.add_argument("--output", default="data/output/coverage_results.csv")
    p.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="FCC availability date override, e.g. 2025-12-31",
    )
    p.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download even if local files are valid",
    )
    p.add_argument(
        "--sync-only",
        action="store_true",
        help="Download/import FCC data only; skip geocoding and coverage",
    )
    p.add_argument(
        "--states-only",
        action="store_true",
        help="Print states found in the input file and exit",
    )
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_config()

    rows = list(csv.DictReader(open(args.input, newline="")))
    states = sorted({r["state"].strip().upper() for r in rows})

    if args.states_only:
        print("States in input file:")
        for s in states:
            print(f"  {s}")
        return

    print(f"[pipeline] States: {states}")

    init_db()

    release = select_release(cfg, args.as_of)
    print(f"[pipeline] FCC release: {release}")

    catalog = fetch_catalog(cfg, release, states)

    sync_fcc_files(cfg, catalog, force=args.force_download)

    import_release(cfg, release, states)

    if args.sync_only:
        print("[pipeline] --sync-only: stopping after FCC import.")
        return

    geocode_addresses(cfg, rows)

    evaluate_coverage(cfg, release)

    if args.output != "/dev/null":
        pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        export_results(cfg, rows, release, args.output)
        print(f"[pipeline] Output written to {args.output}")


if __name__ == "__main__":
    main()
