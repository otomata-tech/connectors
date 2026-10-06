"""OEWS geographic areas — from the readable name to the BLS area code (7 characters).

Three area types, each with its letter in the series identifier:
- `N` national: `0000000`;
- `S` State: 2-digit FIPS + `00000` (Illinois → `1700000`);
- `M` metropolitan area: `00` + 5-digit CBSA code (Chicago-Naperville-Elgin
  → `0016980`).

States are resolved by name or postal abbreviation (static table: FIPS codes
do not move). Metropolitan areas are passed **by CBSA code**: their
list changes with every OMB redelineation, it is not embedded here.
"""
from __future__ import annotations

import re
from typing import Dict

NATIONAL_CODE = "0000000"

# (FIPS, postal abbreviation, name) — 50 States, DC and the territories covered by the OEWS.
_STATES = (
    ("01", "AL", "Alabama"), ("02", "AK", "Alaska"), ("04", "AZ", "Arizona"),
    ("05", "AR", "Arkansas"), ("06", "CA", "California"), ("08", "CO", "Colorado"),
    ("09", "CT", "Connecticut"), ("10", "DE", "Delaware"),
    ("11", "DC", "District of Columbia"), ("12", "FL", "Florida"),
    ("13", "GA", "Georgia"), ("15", "HI", "Hawaii"), ("16", "ID", "Idaho"),
    ("17", "IL", "Illinois"), ("18", "IN", "Indiana"), ("19", "IA", "Iowa"),
    ("20", "KS", "Kansas"), ("21", "KY", "Kentucky"), ("22", "LA", "Louisiana"),
    ("23", "ME", "Maine"), ("24", "MD", "Maryland"), ("25", "MA", "Massachusetts"),
    ("26", "MI", "Michigan"), ("27", "MN", "Minnesota"), ("28", "MS", "Mississippi"),
    ("29", "MO", "Missouri"), ("30", "MT", "Montana"), ("31", "NE", "Nebraska"),
    ("32", "NV", "Nevada"), ("33", "NH", "New Hampshire"), ("34", "NJ", "New Jersey"),
    ("35", "NM", "New Mexico"), ("36", "NY", "New York"), ("37", "NC", "North Carolina"),
    ("38", "ND", "North Dakota"), ("39", "OH", "Ohio"), ("40", "OK", "Oklahoma"),
    ("41", "OR", "Oregon"), ("42", "PA", "Pennsylvania"), ("44", "RI", "Rhode Island"),
    ("45", "SC", "South Carolina"), ("46", "SD", "South Dakota"), ("47", "TN", "Tennessee"),
    ("48", "TX", "Texas"), ("49", "UT", "Utah"), ("50", "VT", "Vermont"),
    ("51", "VA", "Virginia"), ("53", "WA", "Washington"), ("54", "WV", "West Virginia"),
    ("55", "WI", "Wisconsin"), ("56", "WY", "Wyoming"),
    ("66", "GU", "Guam"), ("72", "PR", "Puerto Rico"), ("78", "VI", "Virgin Islands"),
)

_NATIONAL_ALIASES = {"us", "usa", "u.s.", "u.s.a.", "united states", "national", "nation"}


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


_STATE_INDEX: Dict[str, tuple] = {}
for _fips, _abbr, _name in _STATES:
    _STATE_INDEX[_key(_abbr)] = (_fips, _name)
    _STATE_INDEX[_key(_name)] = (_fips, _name)
_STATE_INDEX["washington dc"] = _STATE_INDEX["washington, dc"] = _STATE_INDEX["dc"]


def resolve_area(value: str) -> Dict[str, str]:
    """Resolves an entered area → `{"area_type", "area_code", "area"}`.

    Accepts `"US"` (or `"national"`), a State name or its postal abbreviation
    (`"Illinois"`, `"IL"`), or a **5-digit CBSA code** for a metropolitan
    area (`"16980"`). A metropolitan area name is not resolved: pass its
    CBSA code.

    Raises `ValueError` on an unrecognized area.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty area — expected 'US', a State (name or abbreviation) "
                         "or a 5-digit CBSA code")
    k = _key(raw)
    if k in _NATIONAL_ALIASES:
        return {"area_type": "N", "area_code": NATIONAL_CODE, "area": "US"}
    if k in _STATE_INDEX:
        fips, name = _STATE_INDEX[k]
        return {"area_type": "S", "area_code": f"{fips}00000", "area": name}
    if re.fullmatch(r"\d{5}", raw):
        return {"area_type": "M", "area_code": f"00{raw}", "area": f"CBSA {raw}"}
    raise ValueError(
        f"unrecognized area: {value!r} — expected 'US', a US State (name "
        "or postal abbreviation) or the 5-digit CBSA code of a "
        "metropolitan area (metropolitan area names are not resolved)")
