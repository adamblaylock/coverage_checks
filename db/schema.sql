CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS fcc_coverage (
    id          BIGSERIAL PRIMARY KEY,
    release     TEXT        NOT NULL,
    state       TEXT        NOT NULL,
    carrier     TEXT        NOT NULL,
    mindown     NUMERIC,
    minsignal   NUMERIC,
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS fcc_coverage_geom_gist
    ON fcc_coverage USING GIST (geom);

CREATE INDEX IF NOT EXISTS fcc_coverage_release_state_carrier
    ON fcc_coverage (release, state, carrier);

CREATE TABLE IF NOT EXISTS fcc_coverage_sub (
    id          BIGSERIAL PRIMARY KEY,
    src_id      BIGINT REFERENCES fcc_coverage(id),
    release     TEXT NOT NULL,
    carrier     TEXT NOT NULL,
    mindown     NUMERIC,
    minsignal   NUMERIC,
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS fcc_coverage_sub_geom_gist
    ON fcc_coverage_sub USING GIST (geom);

CREATE TABLE IF NOT EXISTS geocodes (
    id              BIGSERIAL PRIMARY KEY,
    normalized      TEXT UNIQUE NOT NULL,
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    match_type      TEXT,
    geocoded_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS coverage_results (
    id              BIGSERIAL PRIMARY KEY,
    normalized      TEXT NOT NULL,
    release         TEXT NOT NULL,
    carrier         TEXT NOT NULL,
    result          TEXT NOT NULL,
    evaluated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (normalized, release, carrier)
);

CREATE TABLE IF NOT EXISTS download_manifest (
    id              BIGSERIAL PRIMARY KEY,
    release         TEXT NOT NULL,
    state           TEXT NOT NULL,
    carrier         TEXT NOT NULL,
    url             TEXT NOT NULL,
    sha256          TEXT,
    downloaded_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (release, state, carrier)
);
