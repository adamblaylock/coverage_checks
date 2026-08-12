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
address,city,state,zip,att,tmo,vzw,att_reason,tmo_reason,vzw_reason,att_evaluated_mindown,att_evaluated_minup,att_evaluated_minsignal,att_evaluated_indoor_signal,att_evaluated_environment,att_evaluated_penetration_loss_db,att_evaluated_technology,...,att_estimated_indoor_signal,att_environment,att_penetration_loss_db,...
1 Public Square,Nashville,TN,37201,PASS,UNKNOWN,FAIL,qualifying_coverage,no_matching_polygon,below_download_threshold,5,4,-89,-101,outdoor,12,LTE,...,-101,outdoor,12,...,,,1,,
```

The output keeps the original address plus `att`/`tmo`/`vzw` `PASS`/`FAIL`/`UNKNOWN` result fields, then adds per-carrier reason and evaluated-evidence columns, then the legacy qualifying-audit columns:

### Result column

- `att`, `tmo`, `vzw` — `PASS`, `FAIL`, or `UNKNOWN` for each carrier.
  - **PASS** — at least one FCC polygon covers the address and meets all Zoom 720p indoor thresholds (≥2 Mbps down, ≥2 Mbps up, ≥−105 dBm estimated indoor signal).
  - **FAIL** — an FCC polygon covers the address but at least one known metric falls below a threshold.
  - **UNKNOWN** — no matching polygon, geocode failed, or no polygon had sufficient metrics to conclusively pass or fail.

### Reason column (new)

- `<carrier>_reason` — stable reason code explaining the result. Examples:
  - `qualifying_coverage` — PASS: a qualifying polygon was found.
  - `below_download_threshold` — FAIL: download speed below 2 Mbps.
  - `below_upload_threshold` — FAIL: upload speed below 2 Mbps.
  - `below_indoor_signal_threshold` — FAIL: estimated indoor signal below −105 dBm.
  - `below_download_and_upload_threshold` — FAIL: both download and upload below threshold.
  - `below_download_and_signal_threshold` — FAIL: download and signal both below threshold.
  - `below_upload_and_signal_threshold` — FAIL: upload and signal both below threshold.
  - `below_download_upload_and_signal_threshold` — FAIL: all three metrics below threshold.
  - `no_matching_polygon` — UNKNOWN: no FCC polygon covers this address for this carrier.
  - `missing_signal_or_speed` — UNKNOWN: a polygon was found but lacked sufficient metrics.
  - `geocode_unavailable` — address could not be geocoded; all carriers return UNKNOWN.

### Evaluated-evidence columns (new)

These columns report the deterministically selected evidence polygon used to explain the result. They are populated for both PASS and FAIL rows:

- `<carrier>_evaluated_mindown` — download speed (Mbps) of the evidence polygon.
- `<carrier>_evaluated_minup` — upload speed (Mbps) of the evidence polygon.
- `<carrier>_evaluated_minsignal` — raw minimum signal (dBm) of the evidence polygon.
- `<carrier>_evaluated_indoor_signal` — estimated indoor signal (dBm) after applying building-penetration loss.
- `<carrier>_evaluated_environment` — FCC `environmnt` value of the evidence polygon.
- `<carrier>_evaluated_penetration_loss_db` — building-penetration loss applied (`0` for indoor, `12` for outdoor/unknown).
- `<carrier>_evaluated_technology` — technology (e.g. `LTE`) of the evidence polygon.

Selection rules:
- **PASS**: the best qualifying polygon (highest download, then best indoor signal, then lowest coverage ID).
- **FAIL**: the most conclusively failing polygon (most failed criteria, then worst download deficit, then worst signal deficit, then lowest coverage ID).
- **UNKNOWN**: blank — no polygon could establish the metrics.

### Legacy qualifying-audit columns (PASS only)

These columns retain their original meaning and are populated **only when the result is PASS**. Blank values for FAIL rows do not indicate missing source data — use the evaluated-evidence columns instead.

- `<carrier>_estimated_indoor_signal`: estimated indoor signal (dBm) from the selected qualifying polygon.
- `<carrier>_environment`: FCC `environmnt` value from the selected qualifying polygon.
- `<carrier>_penetration_loss_db`: building-penetration loss applied (`0` for indoor records, `12` otherwise).

Geocoding details and coordinates remain internal to PostgreSQL and are not exported.

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
15. Reuses cached coverage results only when they match the current coverage-model cache version for the same address and FCC release; stale rows are recomputed automatically.
16. Exports address fields, carrier `PASS`/`FAIL`/`UNKNOWN` results, and per-carrier coverage-audit fields.

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
- Coverage results are cached by normalized address and FCC release, with a cache-model version so stale evaluations are automatically refreshed on the next run.
- Points are loaded in bulk with PostgreSQL `COPY`.
- Coverage is evaluated with a set-based PostGIS join against subdivided, indexed polygons.

## Coverage rule

This pipeline screens each geocoded address against FCC mobile coverage polygon data to estimate whether the location can support an **indoor 720p one-to-one Zoom call**. This is a modeling estimate based on FCC-reported minimum signal values and minimum download/upload speeds—it is **not a live throughput or call-quality guarantee**.

For each geocoded address and carrier:

- `PASS`: at least one recognized matching FCC polygon covers the point and meets all three thresholds.
- `FAIL`: at least one recognized matching FCC polygon covers the point, none qualify for `PASS`, and available FCC metrics conclusively show one or more required thresholds are not met.
- `UNKNOWN`: the address cannot be geocoded, no recognized matching polygon covers the point, or available polygon metrics are insufficient for a conclusive pass/fail decision. Missing `mindown`, `minup`, or `minsignal` alone never produces `FAIL`—without a separate conclusive threshold miss it yields `UNKNOWN`.

The thresholds for a qualifying polygon are:

```text
mindown >= 2 Mbps
minup   >= 2 Mbps
estimated_indoor_signal >= -105 dBm
```

The estimated indoor signal is derived from the FCC polygon's `minsignal` using an **environment-aware building-penetration model** driven by the FCC `environmnt` field:

| `environmnt` value | Building-penetration loss | Notes |
|---|---:|---|
| `indoor` / `i` / `1` (case-insensitive) | **0 dB** | FCC polygon already represents an indoor prediction; no additional loss applied |
| `outdoor` / `o` / `2` or any other recognized outdoor value | **12 dB** | Typical building-penetration loss for a cellular signal |
| Null, empty, or unrecognized value | **12 dB** | Conservative default; treated as outdoor |

The indoor signal threshold of **−105 dBm** represents a stronger screen than basic device connectivity. For outdoor FCC polygons (or unknown environment) the raw `minsignal` must be at least **−93 dBm** before the 12 dB building-penetration adjustment. For polygons explicitly marked indoor, no adjustment is applied and the raw `minsignal` must be at least **−105 dBm**.

> **Important:** A `PASS` result means FCC data indicates the location *should* support an indoor 720p one-to-one Zoom call based on modeled coverage. Actual indoor signal quality and sustained throughput depend on building construction, device capability, carrier network load, and many other real-world factors. An FCC-based `PASS` is not a guarantee of reliable indoor service or call quality.

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

The row is retained and carrier columns return `UNKNOWN`, because qualifying carrier coverage cannot be confirmed. Geocoder diagnostics are stored internally in PostgreSQL.

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
