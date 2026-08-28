"""
Canonical location parsing for job postings.

Every source writes locations differently and none of them are structured:

    Adzuna        "Nashville, Davidson County"     (city + county, no state)
    Greenhouse    "San Francisco, CA"              (abbreviation)
    Ashby         "San Francisco, California"      (full name)
    Ashby         "US-CA-Menlo Park"               (ISO prefix)
    Greenhouse    "California; San Francisco"      (semicolon, reversed)
    various       "United States" / "Remote" / ""  (no state at all)

A naive split on the first comma produced 63 distinct "states" for one
company, including "United States", "Canada", "D.C." and
"California; San Francisco". This module resolves any of the above to one
canonical state name, or None when the string genuinely does not carry one.
"""

from __future__ import annotations

import re

US_STATES = {
    "alabama": "Alabama", "alaska": "Alaska", "arizona": "Arizona",
    "arkansas": "Arkansas", "california": "California", "colorado": "Colorado",
    "connecticut": "Connecticut", "delaware": "Delaware", "florida": "Florida",
    "georgia": "Georgia", "hawaii": "Hawaii", "idaho": "Idaho",
    "illinois": "Illinois", "indiana": "Indiana", "iowa": "Iowa",
    "kansas": "Kansas", "kentucky": "Kentucky", "louisiana": "Louisiana",
    "maine": "Maine", "maryland": "Maryland", "massachusetts": "Massachusetts",
    "michigan": "Michigan", "minnesota": "Minnesota", "mississippi": "Mississippi",
    "missouri": "Missouri", "montana": "Montana", "nebraska": "Nebraska",
    "nevada": "Nevada", "new hampshire": "New Hampshire", "new jersey": "New Jersey",
    "new mexico": "New Mexico", "new york": "New York",
    "north carolina": "North Carolina", "north dakota": "North Dakota",
    "ohio": "Ohio", "oklahoma": "Oklahoma", "oregon": "Oregon",
    "pennsylvania": "Pennsylvania", "rhode island": "Rhode Island",
    "south carolina": "South Carolina", "south dakota": "South Dakota",
    "tennessee": "Tennessee", "texas": "Texas", "utah": "Utah",
    "vermont": "Vermont", "virginia": "Virginia", "washington": "Washington",
    "west virginia": "West Virginia", "wisconsin": "Wisconsin",
    "wyoming": "Wyoming",
}

ABBR_US = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

CA_PROVINCES = {
    "ontario": "Ontario", "quebec": "Quebec", "québec": "Quebec",
    "british columbia": "British Columbia", "alberta": "Alberta",
    "manitoba": "Manitoba", "saskatchewan": "Saskatchewan",
    "nova scotia": "Nova Scotia", "new brunswick": "New Brunswick",
    "newfoundland": "Newfoundland and Labrador",
    "prince edward island": "Prince Edward Island",
}

ABBR_CA = {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia",
    "AB": "Alberta", "MB": "Manitoba", "SK": "Saskatchewan",
    "NS": "Nova Scotia", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "PE": "Prince Edward Island",
}

# "Washington, D.C." is a city, not the state of Washington. Checked first so
# it cannot be mistaken for either.
DC_RE = re.compile(r"\b(washington,?\s*d\.?\s?c\.?|district of columbia)\b", re.IGNORECASE)
ISO_RE = re.compile(r"^(US|CA)[-/]\s*([A-Z]{2})\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"\b([A-Z]{2})\b")


def parse_location(raw: str | None) -> tuple[str | None, str | None]:
    """
    Return (country, state) for a location string.

    country is "us", "ca" or None. state is a canonical full name, or None
    when the string carries only a city, a country, or "Remote".
    """
    if not raw:
        return None, None
    text = raw.strip()
    low = text.lower()

    if DC_RE.search(text):
        return "us", "District of Columbia"

    # "US-CA-Menlo Park" - the ISO form is unambiguous, so take it first.
    iso = ISO_RE.match(text)
    if iso:
        country = iso.group(1).lower()
        code = iso.group(2).upper()
        table = ABBR_US if country == "us" else ABBR_CA
        return country, table.get(code)

    # Full state or province names, longest first so "West Virginia" is not
    # matched as "Virginia".
    for name in sorted(US_STATES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return "us", US_STATES[name]
    for name in sorted(CA_PROVINCES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return "ca", CA_PROVINCES[name]

    # Two-letter codes, but only where they follow a separator - otherwise
    # "IN" in "INDIA" or a stray initial produces a false state.
    for token in TOKEN_RE.findall(text):
        if token in ABBR_CA and token not in ABBR_US:
            return "ca", ABBR_CA[token]
        if token in ABBR_US:
            return "us", ABBR_US[token]

    # Country known, state not.
    if re.search(r"\bcanada\b", low):
        return "ca", None
    if re.search(r"(united states|\bu\.?s\.?a?\b)", low):
        return "us", None
    return None, None


def clean_department(raw: str | None) -> str | None:
    """
    Strip internal codes from a department name.

    Greenhouse departments arrive as "2317 Marketing - PMM" or
    "1175 Enterprise - Account Executives (NA)"; the leading number is an
    internal identifier and is noise in any grouping.
    """
    if not raw:
        return None
    cleaned = re.sub(r"^\s*\d+\s+", "", raw.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None
