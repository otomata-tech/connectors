"""Zones géographiques OEWS — du nom lisible au code de zone BLS (7 caractères).

Trois types de zone, chacun avec sa lettre dans l'identifiant de série :
- `N` national : `0000000` ;
- `S` État : FIPS sur 2 chiffres + `00000` (Illinois → `1700000`) ;
- `M` aire métropolitaine : `00` + code CBSA sur 5 chiffres (Chicago-Naperville-Elgin
  → `0016980`).

Les États se résolvent par nom ou abréviation postale (table statique : les codes
FIPS ne bougent pas). Les aires métropolitaines se passent **par code CBSA** : leur
liste change à chaque redélimitation de l'OMB, elle ne s'embarque pas ici.
"""
from __future__ import annotations

import re
from typing import Dict

NATIONAL_CODE = "0000000"

# (FIPS, abréviation postale, nom) — 50 États, DC et les territoires couverts par l'OEWS.
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
    """Résout une zone saisie → `{"area_type", "area_code", "area"}`.

    Accepte `"US"` (ou `"national"`), un nom d'État ou son abréviation postale
    (`"Illinois"`, `"IL"`), ou un **code CBSA à 5 chiffres** pour une aire
    métropolitaine (`"16980"`). Un nom d'aire métropolitaine n'est pas résolu :
    passer son code CBSA.

    Lève `ValueError` sur une zone non reconnue.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("zone vide — attendu 'US', un État (nom ou abréviation) "
                         "ou un code CBSA à 5 chiffres")
    k = _key(raw)
    if k in _NATIONAL_ALIASES:
        return {"area_type": "N", "area_code": NATIONAL_CODE, "area": "US"}
    if k in _STATE_INDEX:
        fips, name = _STATE_INDEX[k]
        return {"area_type": "S", "area_code": f"{fips}00000", "area": name}
    if re.fullmatch(r"\d{5}", raw):
        return {"area_type": "M", "area_code": f"00{raw}", "area": f"CBSA {raw}"}
    raise ValueError(
        f"zone non reconnue : {value!r} — attendu 'US', un État des États-Unis (nom "
        "ou abréviation postale) ou le code CBSA à 5 chiffres d'une aire "
        "métropolitaine (les noms d'aires métropolitaines ne sont pas résolus)")
