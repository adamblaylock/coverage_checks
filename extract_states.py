from pathlib import Path
import pandas as pd

STATE_TO_CODE = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "PUERTO RICO": "PR", "GUAM": "GU", "AMERICAN SAMOA": "AS",
    "NORTHERN MARIANA ISLANDS": "MP", "U.S. VIRGIN ISLANDS": "VI",
}


def normalize_state(value: str) -> str:
    state = " ".join(str(value).strip().upper().split())
    return state if len(state) == 2 else STATE_TO_CODE.get(state, state)


def extract_states(path: Path, state_column: str = "state") -> list[str]:
    values: set[str] = set()
    for chunk in pd.read_csv(
        path,
        usecols=[state_column],
        dtype=str,
        chunksize=100_000,
        encoding="utf-8-sig",
    ):
        values.update(
            normalize_state(value)
            for value in chunk[state_column].dropna()
            if str(value).strip()
        )
    invalid = sorted(value for value in values if len(value) != 2)
    if invalid:
        raise ValueError(f"Could not normalize these state values: {invalid}")
    return sorted(values)
