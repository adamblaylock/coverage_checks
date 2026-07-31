# FCC Mobile Coverage Estimator — Automated Address-Only Bulk v7

This version requires only an address CSV. A single command starts PostGIS, initializes the database, identifies the states in the file, downloads the required FCC mobile coverage datasets, imports and optimizes them, bulk-geocodes the addresses, evaluates carrier coverage, and exports the final CSV.

## Input

```csv
address,city,state,zip
1 Public Square,Nashville,TN,37201
```

State names and two-letter abbreviations are accepted. No coordinates, FCC files, carrier identifiers, or release dates are required.

## Output

```csv
address,city,state,zip,att,tmo,vzw
1 Public Square,Nashville,TN,37201,PASS,FAIL,PASS
```

The output contains only the address information and `PASS`/`FAIL` for AT&T, T-Mobile, and Verizon. Geocoding details and coordinates remain internal.

## One-time setup

The FCC Public Data API requires an FCC account username and API hash. Add them to `.env`:

```dotenv
FCC_API_USERNAME=your-fcc-account-email@example.com
FCC_API_HASH_VALUE=replace-with-your-api-hash
FCC_API_BASE_URL=https://broadbandmap.fcc.gov/api/public/map
```

Then run:

```bash
make install
```

This creates the Python environment, installs dependencies, starts PostGIS, and initializes the database.

## Run everything

```bash
make run INPUT=addresses.csv OUTPUT=data/output/coverage_results.csv
```

That command automatically:

1. Starts the included PostGIS container.
2. Initializes or upgrades the database schema.
3. Reads the states represented in the input file.
4. Calls the FCC Public Data API for available release dates.
5. Selects the newest populated June 30 or December 31 mobile availability release.
6. Selects provider-level Mobile Broadband Raw Coverage files for the required states and carriers.
7. Downloads only missing or invalid files and resumes partial downloads when supported.
8. Extracts GeoPackages or complete shapefiles into a release-specific directory.
9. Records API catalogs, SHA-256 checksums, and a download manifest.
10. Imports the selected release into PostGIS and replaces only the states in the current file.
11. Reprojects and validates geometries, builds subdivided polygon tables, GiST indexes, and statistics.
12. Normalizes and deduplicates addresses.
13. Reuses cached geocodes and sends only new unique addresses to the Census batch geocoder.
14. Performs one set-based spatial coverage evaluation.
15. Reuses cached coverage results for the same address and FCC release.
16. Exports only address fields and carrier `PASS`/`FAIL` results.

## Optional controls

Use a specific FCC availability date:

```bash
make run INPUT=addresses.csv AS_OF=2025-12-31
```

Preview states without downloading:

```bash
make states INPUT=addresses.csv
```

Run only the FCC synchronization stage:

```bash
make sync INPUT=addresses.csv
```

Force fresh downloads:

```bash
.venv/bin/python run_pipeline.py \
  --input addresses.csv \
  --output data/output/coverage_results.csv \
  --force-download
```

## Reuse and performance

The process avoids unnecessary work:

- Existing valid FCC archives are reused.
- FCC data is stored by availability release.
- Only states present in the current address file are downloaded and imported.
- Duplicate addresses are geocoded once.
- Geocodes persist across files and runs.
- Coverage results are cached by normalized address and FCC release.
- Points are loaded in bulk with PostgreSQL `COPY`.
- Coverage is evaluated with a set-based PostGIS join against subdivided, indexed polygons.

## Coverage rule

A carrier passes when a qualifying FCC polygon covers the geocoded point and:

```text
mindown >= 5 Mbps
minsignal - 18 dB >= -105 dBm
```

`ST_Covers` is used so points on polygon boundaries are included.

## Data directories

```text
data/downloads/                 Cached FCC download payloads
data/coverage/<release>/        Extracted GIS files for one FCC release
data/catalog/                   API catalogs, selected release, and manifests
data/output/                    Final result CSV files
```

## Troubleshooting

### FCC API returns 401 or 403

Verify the exact FCC account email and current Public Data API hash in `.env`.

### No FCC files match

Inspect `data/catalog/unmatched_catalog.json`. The raw catalog is retained so changes in FCC labeling can be diagnosed without repeating manual downloads.

### Address cannot be geocoded

The row is retained and carrier columns return `FAIL`, because qualifying carrier coverage could not be confirmed. Geocoder diagnostics are stored internally in PostgreSQL.

### Restarting an interrupted run

Run the same `make run` command again. Valid downloads, geocodes, and coverage results are reused automatically.

## Census attribution

This product uses the Census Bureau Geocoding Services API but is not endorsed or certified by the Census Bureau.
