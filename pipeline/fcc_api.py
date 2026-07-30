"""
pipeline/fcc_api.py — Query the FCC Public Data API to select the best
availability release and retrieve the per-state/carrier file catalog.
"""
import json
import pathlib
from datetime import date, datetime
from typing import Optional

import requests

from pipeline.config import CARRIERS


def _auth_params(cfg: dict) -> dict:
    return {
        "username": cfg["fcc_api_username"],
        "hash_value": cfg["fcc_api_hash_value"],
    }


def select_release(cfg: dict, as_of: Optional[str] = None) -> str:
    """
    Query /listAvailabilityData and return the release date string
    (YYYY-MM-DD) for the newest June 30 or December 31 mobile release,
    or the value of *as_of* if supplied.
    """
    if as_of:
        return as_of

    url = f"{cfg['fcc_api_base_url']}/listAvailabilityData"
    resp = requests.get(url, params=_auth_params(cfg), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    releases = data.get("data", data) if isinstance(data, dict) else data
    candidates = []
    for entry in releases:
        dtype = entry.get("data_type", "")
        if dtype.lower() != "mobile":
            continue
        release_date_str = entry.get("availability_date") or entry.get("release_date", "")
        try:
            d = datetime.strptime(release_date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if (d.month, d.day) in {(6, 30), (12, 31)}:
            candidates.append(d)

    if not candidates:
        raise RuntimeError(
            "No June 30 or December 31 mobile availability releases found from FCC API."
        )

    return max(candidates).strftime("%Y-%m-%d")


def fetch_catalog(cfg: dict, release: str, states: list[str]) -> list[dict]:
    """
    For each (state, carrier) combination, query the FCC catalog endpoint
    and return a list of file-entry dicts with keys:
        state, carrier, provider_id, url, sha256, file_name
    Saves unmatched entries to data/catalog/unmatched_catalog.json.
    """
    catalog_dir = pathlib.Path(cfg["data_catalog"])
    catalog_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    unmatched: list[dict] = []

    for state in states:
        for carrier_key, carrier_info in CARRIERS.items():
            provider_id = carrier_info["provider_id"]
            params = {
                **_auth_params(cfg),
                "release_type": release,
                "state_code": state,
                "category": "Mobile Broadband",
                "provider_id": provider_id,
            }
            url = f"{cfg['fcc_api_base_url']}/listAvailabilityDataFiles"
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                print(f"[fcc_api] WARNING: could not fetch catalog for {state}/{carrier_key}: {exc}")
                continue

            files = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(files, list):
                files = []

            matched = False
            for f in files:
                file_url = f.get("file_url") or f.get("url", "")
                sha256 = f.get("sha256") or f.get("file_hash", "")
                file_name = f.get("file_name") or (file_url.split("/")[-1] if file_url else "")
                if file_url:
                    entries.append(
                        {
                            "state": state,
                            "carrier": carrier_key,
                            "provider_id": provider_id,
                            "url": file_url,
                            "sha256": sha256,
                            "file_name": file_name,
                        }
                    )
                    matched = True
                else:
                    unmatched.append(
                        {"state": state, "carrier": carrier_key, "raw": f}
                    )

            if not matched and files:
                unmatched.extend(
                    {"state": state, "carrier": carrier_key, "raw": f} for f in files
                )

    unmatched_path = catalog_dir / "unmatched_catalog.json"
    with open(unmatched_path, "w") as fh:
        json.dump(unmatched, fh, indent=2)

    print(
        f"[fcc_api] Catalog: {len(entries)} file(s) for {len(states)} state(s); "
        f"{len(unmatched)} unmatched entries saved to {unmatched_path}"
    )
    return entries
