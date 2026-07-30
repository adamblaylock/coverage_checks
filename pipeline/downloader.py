"""
pipeline/downloader.py — Download FCC GIS archives with SHA-256 verification,
Range-request resume support, and tqdm progress bars.
"""
import hashlib
import pathlib

import requests
from tqdm import tqdm

from db import get_conn


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_valid(path: pathlib.Path, expected_sha256: str) -> bool:
    if not path.exists():
        return False
    if not expected_sha256:
        return True  # no checksum to verify
    return _sha256_file(path) == expected_sha256


def _download_file(url: str, dest: pathlib.Path, sha256: str = "") -> None:
    """Download *url* to *dest*, resuming partial downloads when possible."""
    existing_size = dest.stat().st_size if dest.exists() else 0
    headers = {}
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"

    resp = requests.get(url, headers=headers, stream=True, timeout=60)

    # Server may not support Range; start over in that case
    if resp.status_code == 416:
        existing_size = 0
        resp = requests.get(url, stream=True, timeout=60)

    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length", 0)) + existing_size
    mode = "ab" if existing_size > 0 and resp.status_code == 206 else "wb"
    if mode == "wb":
        existing_size = 0

    with open(dest, mode) as fh, tqdm(
        total=total or None,
        initial=existing_size,
        unit="B",
        unit_scale=True,
        desc=dest.name,
        leave=False,
    ) as bar:
        for chunk in resp.iter_content(65536):
            fh.write(chunk)
            bar.update(len(chunk))


def _record_manifest(cfg: dict, release: str, state: str, carrier: str, url: str, sha256: str) -> None:
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO download_manifest (release, state, carrier, url, sha256)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (release, state, carrier) DO UPDATE
                        SET url = EXCLUDED.url,
                            sha256 = EXCLUDED.sha256,
                            downloaded_at = NOW()
                    """,
                    (release, state, carrier, url, sha256),
                )
    finally:
        conn.close()


def sync_fcc_files(cfg: dict, catalog: list[dict], force: bool = False) -> None:
    """
    Download all files listed in *catalog* into data/downloads/, verifying
    SHA-256 when available.  Skips files that already pass verification
    unless *force* is True.
    """
    downloads_dir = pathlib.Path(cfg["data_downloads"])
    downloads_dir.mkdir(parents=True, exist_ok=True)

    for entry in catalog:
        url = entry["url"]
        file_name = entry.get("file_name") or url.split("/")[-1]
        dest = downloads_dir / file_name
        sha256 = entry.get("sha256", "")
        state = entry["state"]
        carrier = entry["carrier"]
        release = entry.get("release", "")

        if not force and _is_valid(dest, sha256):
            print(f"[downloader] Skipping {file_name} (already valid)")
            _record_manifest(cfg, release, state, carrier, url, sha256)
            continue

        print(f"[downloader] Downloading {file_name} ...")
        try:
            _download_file(url, dest, sha256)
        except Exception as exc:
            print(f"[downloader] ERROR downloading {url}: {exc}")
            continue

        actual_sha256 = _sha256_file(dest)
        if sha256 and actual_sha256 != sha256:
            print(
                f"[downloader] WARNING: SHA-256 mismatch for {file_name}. "
                f"Expected {sha256}, got {actual_sha256}. Removing."
            )
            dest.unlink(missing_ok=True)
            continue

        _record_manifest(cfg, release, state, carrier, url, actual_sha256)
        print(f"[downloader] OK: {file_name}")
