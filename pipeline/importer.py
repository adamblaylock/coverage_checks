"""
pipeline/importer.py — Extract downloaded FCC GIS archives and import
coverage polygons into the PostGIS fcc_coverage table.

Supports GeoPackage (.gpkg) and Shapefile-in-zip archives.
Reprojects to EPSG:4326, validates geometries, and builds the
fcc_coverage_sub subdivided table for fast spatial queries.
"""
import pathlib
import zipfile

import fiona
import fiona.crs
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

from db import get_conn
from pipeline.config import CARRIERS

# FCC field name variations for minimum download speed and signal
_MINDOWN_FIELDS = ("mindown", "min_download", "lowdownload", "dl_mhz")
_MINSIGNAL_FIELDS = ("minsignal", "min_signal", "signal", "rsrp")


def _find_field(props: dict, candidates: tuple) -> str | None:
    lower = {k.lower(): k for k in props}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def _carrier_from_path(path: pathlib.Path) -> str | None:
    name = path.stem.lower()
    for key, info in CARRIERS.items():
        if key in name or info["provider_id"] in name:
            return key
        fcc_name = info["fcc_name"].lower().replace("&", "").replace("-", "").replace(" ", "")
        if fcc_name in name.replace("-", "").replace("_", ""):
            return key
    return None


def _import_file(conn, file_path: pathlib.Path, release: str, state: str, carrier: str) -> int:
    """Import polygons from *file_path* into fcc_coverage; return row count."""
    count = 0
    with fiona.open(str(file_path)) as src:
        src_crs = fiona.crs.to_string(src.crs) if src.crs else "EPSG:4326"
        need_reproject = "4326" not in src_crs and "WGS84" not in src_crs.upper()

        if need_reproject:
            from pyproj import Transformer
            transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        else:
            transformer = None

        with conn.cursor() as cur:
            # Delete existing rows for this (release, state, carrier)
            cur.execute(
                "DELETE FROM fcc_coverage WHERE release=%s AND state=%s AND carrier=%s",
                (release, state, carrier),
            )

            for feat in src:
                geom = shape(feat["geometry"])
                if not geom.is_valid:
                    geom = make_valid(geom)

                if transformer:
                    from shapely.ops import transform as shapely_transform
                    geom = shapely_transform(transformer.transform, geom)

                props = feat.get("properties") or {}
                mindown_field = _find_field(props, _MINDOWN_FIELDS)
                minsignal_field = _find_field(props, _MINSIGNAL_FIELDS)

                mindown = props.get(mindown_field) if mindown_field else None
                minsignal = props.get(minsignal_field) if minsignal_field else None

                cur.execute(
                    """
                    INSERT INTO fcc_coverage (release, state, carrier, mindown, minsignal, geom)
                    VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                    """,
                    (release, state, carrier, mindown, minsignal, geom.wkt),
                )
                count += 1

    return count


def _build_subdivided(conn, release: str, states: list[str]) -> None:
    """Populate fcc_coverage_sub from fcc_coverage for the given states."""
    with conn.cursor() as cur:
        # Remove existing subdivided rows for this release/states
        cur.execute(
            "DELETE FROM fcc_coverage_sub WHERE release=%s AND src_id IN "
            "(SELECT id FROM fcc_coverage WHERE release=%s AND state = ANY(%s))",
            (release, release, states),
        )
        cur.execute(
            """
            INSERT INTO fcc_coverage_sub (src_id, release, carrier, mindown, minsignal, geom)
            SELECT id, release, carrier, mindown, minsignal,
                   ST_Subdivide(geom, 256)
            FROM fcc_coverage
            WHERE release = %s AND state = ANY(%s)
            """,
            (release, states),
        )


def import_release(cfg: dict, release: str, states: list[str]) -> None:
    """
    For each state in *states*, find all downloaded archives in data/downloads/,
    extract them to data/coverage/<release>/, and import polygons into PostGIS.
    """
    downloads_dir = pathlib.Path(cfg["data_downloads"])
    coverage_base = pathlib.Path(cfg["data_coverage"]) / release
    coverage_base.mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    try:
        total = 0
        for state in states:
            for carrier_key in CARRIERS:
                # Locate the archive: match on state and carrier in filename
                archives = list(downloads_dir.glob(f"*{state}*")) + list(
                    downloads_dir.glob(f"*{state.lower()}*")
                )
                # Narrow to carrier
                carrier_archives = [
                    a for a in archives
                    if carrier_key in a.stem.lower()
                    or CARRIERS[carrier_key]["provider_id"] in a.stem
                ]
                if not carrier_archives:
                    # Try all archives matching state; infer carrier from filename
                    carrier_archives = archives

                for archive in carrier_archives:
                    inferred_carrier = _carrier_from_path(archive)
                    if inferred_carrier and inferred_carrier != carrier_key:
                        continue

                    extract_dir = coverage_base / archive.stem
                    if not extract_dir.exists():
                        extract_dir.mkdir(parents=True, exist_ok=True)
                        if archive.suffix.lower() == ".zip":
                            with zipfile.ZipFile(archive) as zf:
                                zf.extractall(extract_dir)
                        elif archive.suffix.lower() == ".gpkg":
                            import shutil
                            shutil.copy2(archive, extract_dir / archive.name)
                        else:
                            print(f"[importer] Unknown archive type: {archive}")
                            continue

                    # Find GIS files to import
                    gis_files = (
                        list(extract_dir.rglob("*.gpkg"))
                        + list(extract_dir.rglob("*.shp"))
                    )
                    for gis_file in gis_files:
                        effective_carrier = _carrier_from_path(gis_file) or carrier_key
                        print(
                            f"[importer] Importing {gis_file.name} "
                            f"({state}, {effective_carrier}) ..."
                        )
                        with conn:
                            n = _import_file(conn, gis_file, release, state, effective_carrier)
                        total += n
                        print(f"[importer] {n} rows inserted.")

        print(f"[importer] Building subdivided table for release={release}, states={states} ...")
        with conn:
            _build_subdivided(conn, release, states)
        print(f"[importer] Done. Total rows imported: {total}")
    finally:
        conn.close()
