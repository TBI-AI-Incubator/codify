"""Generate geo.json from REST Countries API + manual overrides for non-country jurisdictions.

Usage:
    uv run python scripts/generate_geo_data.py
"""

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

JURISDICTIONS_DIR = Path(__file__).parents[1] / "data" / "jurisdictions"
OUTPUT = JURISDICTIONS_DIR / "geo.json"
API_BASE = "https://restcountries.com/v3.1/alpha"
FIELDS = "cca3,ccn3,latlng,population,area,region,subregion,continents"

# Codes that need manual overrides (not in REST Countries API)
MANUAL_OVERRIDES = {
    # UK sub-national
    "gb-eng": {
        "iso_alpha3": "GBR",
        "coordinates": {"lat": 52.3555, "lng": -1.1743},
        "population": 56490048,
        "area_km2": 130279,
        "continent": "Europe",
        "region": "Europe",
        "subregion": "Northern Europe",
    },
    "gb-sct": {
        "iso_alpha3": "GBR",
        "coordinates": {"lat": 56.4907, "lng": -4.2026},
        "population": 5454000,
        "area_km2": 77933,
        "continent": "Europe",
        "region": "Europe",
        "subregion": "Northern Europe",
    },
    "gb-nir": {
        "iso_alpha3": "GBR",
        "coordinates": {"lat": 54.7877, "lng": -6.4923},
        "population": 1903100,
        "area_km2": 14130,
        "continent": "Europe",
        "region": "Europe",
        "subregion": "Northern Europe",
    },
    "gb-wls": {
        "iso_alpha3": "GBR",
        "coordinates": {"lat": 52.1307, "lng": -3.7837},
        "population": 3107500,
        "area_km2": 20779,
        "continent": "Europe",
        "region": "Europe",
        "subregion": "Northern Europe",
    },
    # Supranational
    "eu": {
        "iso_alpha3": "EU",
        "coordinates": {"lat": 50.8503, "lng": 4.3517},
        "population": 447700000,
        "area_km2": 4233262,
        "continent": "Europe",
        "region": "Europe",
        "subregion": "European Union",
    },
    "un": {
        "iso_alpha3": "UN",
        "coordinates": {"lat": 40.7489, "lng": -73.9680},
        "population": 8000000000,
        "area_km2": 510072000,
        "continent": "Global",
        "region": "Global",
        "subregion": "Global",
    },
    "ohada": {
        "iso_alpha3": "OHADA",
        "coordinates": {"lat": 3.8480, "lng": 11.5021},
        "population": 400000000,
        "area_km2": 12000000,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "OHADA",
    },
    "eac": {
        "iso_alpha3": "EAC",
        "coordinates": {"lat": -3.3869, "lng": 36.6830},
        "population": 302000000,
        "area_km2": 4828135,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "East Africa",
    },
    "ecowas": {
        "iso_alpha3": "ECOWAS",
        "coordinates": {"lat": 9.0765, "lng": 7.3986},
        "population": 387000000,
        "area_km2": 5112903,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "West Africa",
    },
    "sadc": {
        "iso_alpha3": "SADC",
        "coordinates": {"lat": -24.6282, "lng": 25.9231},
        "population": 402000000,
        "area_km2": 9882959,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "Southern Africa",
    },
    "gcc": {
        "iso_alpha3": "GCC",
        "coordinates": {"lat": 24.7136, "lng": 46.6753},
        "population": 59000000,
        "area_km2": 2673108,
        "continent": "Asia",
        "region": "Asia",
        "subregion": "Arabian Peninsula",
    },
    "uemoa": {
        "iso_alpha3": "UEMOA",
        "coordinates": {"lat": 12.3714, "lng": -1.5197},
        "population": 146000000,
        "area_km2": 3506126,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "West Africa",
    },
    "cemac": {
        "iso_alpha3": "CEMAC",
        "coordinates": {"lat": 4.3947, "lng": 18.5582},
        "population": 60000000,
        "area_km2": 3020145,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "Central Africa",
    },
    "caricom": {
        "iso_alpha3": "CARICOM",
        "coordinates": {"lat": 6.8013, "lng": -58.1551},
        "population": 18000000,
        "area_km2": 462344,
        "continent": "North America",
        "region": "Americas",
        "subregion": "Caribbean",
    },
    "african-union": {
        "iso_alpha3": "AU",
        "coordinates": {"lat": 9.0320, "lng": 38.7469},
        "population": 1400000000,
        "area_km2": 30370000,
        "continent": "Africa",
        "region": "Africa",
        "subregion": "Pan-African",
    },
    "council-of-europe": {
        "iso_alpha3": "COE",
        "coordinates": {"lat": 48.5734, "lng": 7.7521},
        "population": 680000000,
        "area_km2": 23500000,
        "continent": "Europe",
        "region": "Europe",
        "subregion": "Pan-European",
    },
}

# Map jurisdiction codes to REST Countries alpha-2 codes where they differ.
# Add entries here if a Codify code ever diverges from the ISO alpha-2 code.
CODE_MAP: dict[str, str] = {}


def fetch_country(code: str) -> dict | None:
    """Fetch country data from REST Countries API."""
    api_code = CODE_MAP.get(code, code).upper()
    url = f"{API_BASE}/{api_code}?fields={FIELDS}"
    req = Request(url, headers={"User-Agent": "Codify/1.0"})

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if isinstance(data, list):
                data = data[0]
            return {
                "iso_alpha3": data.get("cca3", ""),
                "iso_numeric": data.get("ccn3", ""),
                "coordinates": {
                    "lat": data["latlng"][0] if data.get("latlng") else 0,
                    "lng": data["latlng"][1]
                    if data.get("latlng") and len(data["latlng"]) > 1
                    else 0,
                },
                "population": data.get("population", 0),
                "area_km2": data.get("area", 0),
                "continent": data["continents"][0] if data.get("continents") else "",
                "region": data.get("region", ""),
                "subregion": data.get("subregion", ""),
            }
    except HTTPError as e:
        print(f"  {code}: HTTP {e.code} — skipped")
        return None
    except Exception as e:
        print(f"  {code}: Error {e} — skipped")
        return None


def main() -> int:
    registry = json.loads((JURISDICTIONS_DIR / "registry.json").read_text())
    codes = [j["code"] for j in registry["jurisdictions"]]

    geo = {}
    fetched = 0
    manual = 0
    failed = 0

    for code in codes:
        if code in MANUAL_OVERRIDES:
            geo[code] = MANUAL_OVERRIDES[code]
            manual += 1
            print(f"  {code}: manual override")
            continue

        # Extract the base country code for sub-national jurisdictions
        base_code = code.split("-")[0] if "-" in code else code

        data = fetch_country(base_code)
        if data:
            geo[code] = data
            fetched += 1
            print(f"  {code}: fetched ({data['iso_alpha3']})")
        else:
            failed += 1

        # Rate limiting, be polite to the API
        time.sleep(0.15)

    with open(OUTPUT, "w") as f:
        json.dump(geo, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nResults: {fetched} fetched, {manual} manual, {failed} failed")
    print(f"Written to {OUTPUT} ({len(geo)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
