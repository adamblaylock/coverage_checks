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
10. Imports the selected release into PostGIS and replaces only matching state rows for that same release.
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

Run only the PostGIS coverage import with a specific worker count:

```bash
.venv/bin/python load_postgis.py --input addresses.csv --workers 4
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
- Existing extracted GIS files are reused when the cached archive is unchanged.
- FCC data is stored by availability release.
- Only states present in the current address file are downloaded and imported.
- Coverage reloads are skipped when the same release/state files were already imported and subdivided.
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

## PostGIS performance tuning

The PostGIS service is tuned for bulk loads. The `docker-compose.yml` `command` block starts `postgres` with a larger WAL budget (`max_wal_size=4GB`, `min_wal_size=1GB`), longer checkpoint intervals (`checkpoint_timeout=15min`, `checkpoint_completion_target=0.9`), and WAL compression (`wal_compression=on`). This eliminates the frequent forced-checkpoint warnings that appear during large `COPY` imports and `ST_Subdivide` work.

After pulling this configuration change, apply it without deleting the database volume:

```bash
docker compose up -d --force-recreate postgis
```

Ensure Docker Desktop has enough disk capacity to accommodate the larger WAL allowance in addition to the existing data volume.

### Import worker count

`load_postgis.py` defaults to **2 import workers**, which is a conservative choice for local and Docker Desktop workloads (including amd64 emulation on Apple Silicon). Two workers avoid saturating CPU, memory, and WAL generation while still providing parallelism over multi-state imports.

Override the default when running on a well-resourced host:

```bash
.venv/bin/python load_postgis.py --input addresses.csv --workers 4
```

Use `--workers 1` for fully sequential, single-connection imports.

## Troubleshooting

### FCC API returns 401 or 403

Verify the exact FCC account email and current Public Data API hash in `.env`.

### No FCC files match

Inspect `data/catalog/unmatched_catalog.json`. The raw catalog is retained so changes in FCC labeling can be diagnosed without repeating manual downloads.

### Address cannot be geocoded

The row is retained and carrier columns return `FAIL`, because qualifying carrier coverage could not be confirmed. Geocoder diagnostics are stored internally in PostgreSQL.

### Restarting an interrupted run

Run the same `make run` command again. Valid downloads, geocodes, and coverage results are reused automatically.
If the selected FCC release and state files are unchanged, the coverage import step also skips reloading and rebuilding subdivided polygons.

### `make run` hangs at `init_database.py`

In rare cases (for example, a network partition or killed worker during PostgreSQL `COPY`), an orphaned backend can remain active and hold locks that block schema initialization.

Check active sessions:

```sql
SELECT pid, state, query
FROM pg_stat_activity
WHERE datname = current_database()
  AND state != 'idle';
```

Terminate the stuck backend:

```sql
SELECT pg_terminate_backend(<pid>);
```

The pipeline now sets statement and idle-in-transaction timeouts on `COPY` sessions and schema initialization to reduce the chance of indefinite hangs, but this check is still useful for diagnosis and recovery.

## Census attribution

This product uses the Census Bureau Geocoding Services API but is not endorsed or certified by the Census Bureau.
