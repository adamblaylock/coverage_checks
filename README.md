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

## Run everything

```bash
make run INPUT=addresses.csv OUTPUT=data/output/coverage_results.csv
```
