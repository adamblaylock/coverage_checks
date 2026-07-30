"""
pipeline/config.py — Load environment configuration and define carrier/rule constants.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# FCC carrier provider IDs
CARRIERS = {
    "att": {"fcc_name": "AT&T", "provider_id": "10017"},
    "tmo": {"fcc_name": "T-Mobile", "provider_id": "40676"},
    "vzw": {"fcc_name": "Verizon", "provider_id": "80178"},
}

# Indoor coverage thresholds
COVERAGE_RULES = {
    "mindown": 5,          # Mbps
    "signal_floor": -105,  # dBm  (minsignal - 18 dB >= -105 dBm)
}

# US state abbreviation → full name mapping
STATE_ABBR_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}

# Reverse: full name (upper) → abbreviation
STATE_NAME_TO_ABBR = {v.upper(): k for k, v in STATE_ABBR_TO_NAME.items()}


def load_config() -> dict:
    """Return a dict of environment-driven configuration values."""
    return {
        "fcc_api_base_url": os.getenv(
            "FCC_API_BASE_URL", "https://broadbandmap.fcc.gov/api/public/map"
        ),
        "fcc_api_username": os.getenv("FCC_API_USERNAME", ""),
        "fcc_api_hash_value": os.getenv("FCC_API_HASH_VALUE", ""),
        "postgres_host": os.getenv("POSTGRES_HOST", "localhost"),
        "postgres_port": int(os.getenv("POSTGRES_PORT", 5432)),
        "postgres_db": os.getenv("POSTGRES_DB", "fcc_coverage"),
        "postgres_user": os.getenv("POSTGRES_USER", "fcc"),
        "postgres_password": os.getenv("POSTGRES_PASSWORD", "fcc"),
        "carriers": CARRIERS,
        "coverage_rules": COVERAGE_RULES,
        "data_downloads": "data/downloads",
        "data_coverage": "data/coverage",
        "data_catalog": "data/catalog",
        "data_output": "data/output",
    }
