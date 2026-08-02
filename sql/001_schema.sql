CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS fcc;
CREATE SCHEMA IF NOT EXISTS processing;

CREATE TABLE IF NOT EXISTS fcc.mobile_coverage (
    coverage_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    state_code text NOT NULL,
    release_id text NOT NULL DEFAULT 'unknown',
    frn text,
    providerid text,
    brandname text,
    technology text,
    mindown numeric,
    minup numeric,
    minsignal numeric,
    environmnt text,
    source_file text NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS mobile_coverage_geom_gix ON fcc.mobile_coverage USING gist (geom);
CREATE INDEX IF NOT EXISTS mobile_coverage_state_brand_idx ON fcc.mobile_coverage (state_code, brandname);
CREATE INDEX IF NOT EXISTS mobile_coverage_release_idx ON fcc.mobile_coverage (release_id);

CREATE TABLE IF NOT EXISTS fcc.mobile_coverage_staging (
    load_id uuid NOT NULL,
    state_code text NOT NULL,
    release_id text NOT NULL DEFAULT 'unknown',
    frn text,
    providerid text,
    brandname text,
    technology text,
    mindown numeric,
    minup numeric,
    minsignal numeric,
    environmnt text,
    source_file text NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS mobile_coverage_staging_load_idx
    ON fcc.mobile_coverage_staging (load_id);

CREATE TABLE IF NOT EXISTS fcc.coverage_import_manifest (
    release_id text NOT NULL,
    state_code text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    layer_names text[] NOT NULL,
    subdivided boolean NOT NULL DEFAULT false,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (release_id, state_code, source_path)
);
CREATE INDEX IF NOT EXISTS coverage_import_manifest_release_state_idx
    ON fcc.coverage_import_manifest (release_id, state_code);

CREATE TABLE IF NOT EXISTS fcc.mobile_coverage_subdivided (
    coverage_id bigint NOT NULL,
    state_code text NOT NULL,
    release_id text NOT NULL,
    providerid text,
    brandname text,
    technology text,
    mindown numeric,
    minup numeric,
    minsignal numeric,
    environmnt text,
    geom geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS mobile_coverage_sub_geom_gix ON fcc.mobile_coverage_subdivided USING gist (geom);
CREATE INDEX IF NOT EXISTS mobile_coverage_sub_release_state_brand_idx
    ON fcc.mobile_coverage_subdivided (release_id, state_code, brandname);
ALTER TABLE fcc.mobile_coverage_subdivided ADD COLUMN IF NOT EXISTS environmnt text;

CREATE TABLE IF NOT EXISTS processing.address_geocode_cache (
    address_hash text PRIMARY KEY,
    normalized_address text NOT NULL,
    state_code text NOT NULL,
    latitude double precision,
    longitude double precision,
    geom geometry(Point, 4326),
    geocode_status text NOT NULL,
    geocoder text,
    match_status text,
    match_type text,
    matched_address text,
    tigerline_id text,
    tigerline_side text,
    geocoded_at timestamptz NOT NULL DEFAULT now(),
    error_message text
);
ALTER TABLE processing.address_geocode_cache ADD COLUMN IF NOT EXISTS match_status text;
ALTER TABLE processing.address_geocode_cache ADD COLUMN IF NOT EXISTS match_type text;
ALTER TABLE processing.address_geocode_cache ADD COLUMN IF NOT EXISTS matched_address text;
ALTER TABLE processing.address_geocode_cache ADD COLUMN IF NOT EXISTS tigerline_id text;
ALTER TABLE processing.address_geocode_cache ADD COLUMN IF NOT EXISTS tigerline_side text;
CREATE INDEX IF NOT EXISTS address_geocode_cache_geom_gix ON processing.address_geocode_cache USING gist (geom);
CREATE INDEX IF NOT EXISTS address_geocode_cache_state_idx ON processing.address_geocode_cache (state_code);

CREATE UNLOGGED TABLE IF NOT EXISTS processing.address_batch (
    batch_id uuid NOT NULL,
    row_number bigint NOT NULL,
    source_id text,
    address_hash text NOT NULL,
    address text,
    city text,
    state_code text NOT NULL,
    zip text,
    geocode_status text,
    matched_address text,
    latitude double precision,
    longitude double precision,
    geom geometry(Point, 4326),
    PRIMARY KEY (batch_id, row_number)
);
ALTER TABLE processing.address_batch ADD COLUMN IF NOT EXISTS geocode_status text;
ALTER TABLE processing.address_batch ADD COLUMN IF NOT EXISTS matched_address text;
CREATE INDEX IF NOT EXISTS address_batch_batch_idx ON processing.address_batch (batch_id);
CREATE INDEX IF NOT EXISTS address_batch_geom_gix ON processing.address_batch USING gist (geom);
CREATE INDEX IF NOT EXISTS address_batch_state_idx ON processing.address_batch (batch_id, state_code);

CREATE TABLE IF NOT EXISTS processing.address_coverage_cache (
    address_hash text NOT NULL,
    release_id text NOT NULL,
    carrier_code text NOT NULL,
    cache_model_version text,
    result text NOT NULL,
    best_mindown numeric,
    best_minsignal numeric,
    best_estimated_indoor_signal numeric,
    best_environment text,
    best_penetration_loss_db numeric,
    technology text,
    result_reason text,
    evaluated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (address_hash, release_id, carrier_code)
);
ALTER TABLE processing.address_coverage_cache ADD COLUMN IF NOT EXISTS cache_model_version text;
ALTER TABLE processing.address_coverage_cache ADD COLUMN IF NOT EXISTS best_estimated_indoor_signal numeric;
ALTER TABLE processing.address_coverage_cache ADD COLUMN IF NOT EXISTS best_environment text;
ALTER TABLE processing.address_coverage_cache ADD COLUMN IF NOT EXISTS best_penetration_loss_db numeric;
ALTER TABLE processing.address_coverage_cache ADD COLUMN IF NOT EXISTS result_reason text;

CREATE TABLE IF NOT EXISTS processing.batch_run (
    batch_id uuid PRIMARY KEY,
    source_file text NOT NULL,
    release_id text NOT NULL,
    row_count bigint NOT NULL,
    unique_address_count bigint NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    error_message text
);
ALTER TABLE processing.batch_run ADD COLUMN IF NOT EXISTS error_message text;
