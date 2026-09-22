"""BLS client — API publique de données du Bureau of Labor Statistics (api.bls.gov, v2).

Un seul endpoint : `POST /publicAPI/v2/timeseries/data/`, corps JSON
`{"seriesid": [...]}`. **Utilisable sans clé** : 25 séries par requête et 25 requêtes
par jour ; avec une clé d'enregistrement (`registrationkey`, gratuite), 50 séries et
500 requêtes par jour.

Ce client sert l'enquête **OEWS** (Occupational Employment and Wage Statistics) :
emploi et salaires par métier (code SOC à 6 chiffres) et par zone. Identifiant d'une
série OEWS, 25 caractères :

    OEU + type de zone (N|S|M) + zone (7) + secteur (6, `000000` = tous) + SOC (6) + mesure (2)

⚠️ L'API ne rend que la **dernière année publiée** de l'OEWS. Sa réponse porte
`status`, `message` (liste : les lignes « No Data Available for Series … Year: … »
ne sont PAS des erreurs, l'API balaie une fenêtre d'années par défaut) et
`Results.series[].data[]` (`year`, `period` = `A01`, `value` en **chaîne**, qui peut
valoir `-` ou porter une note quand l'estimation est plafonnée ou non publiée).

⚠️ Un refus de l'API arrive souvent en **HTTP 200** avec `status` ≠
`REQUEST_SUCCEEDED` (quota du jour épuisé, requête mal formée) : `BLSRequestError`.

Requires: requests
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from ...config import get_secret
from ..common import raise_for_upstream
from .areas import resolve_area

# Mesures OEWS (fichier de référence `oe.datatype`).
DATATYPES: Dict[str, str] = {
    "01": "Employment",
    "02": "Employment percent relative standard error",
    "03": "Hourly mean wage",
    "04": "Annual mean wage",
    "05": "Wage percent relative standard error",
    "06": "Hourly 10th percentile wage",
    "07": "Hourly 25th percentile wage",
    "08": "Hourly median wage",
    "09": "Hourly 75th percentile wage",
    "10": "Hourly 90th percentile wage",
    "11": "Annual 10th percentile wage",
    "12": "Annual 25th percentile wage",
    "13": "Annual median wage",
    "14": "Annual 75th percentile wage",
    "15": "Annual 90th percentile wage",
    "16": "Employment per 1,000 jobs",
    "17": "Location Quotient",
}

# La distribution annuelle complète d'un métier dans une zone : 7 séries.
WAGE_MEASURES: Tuple[Tuple[str, str], ...] = (
    ("employment", "01"), ("mean", "04"),
    ("p10", "11"), ("p25", "12"), ("p50", "13"), ("p75", "14"), ("p90", "15"),
)

_NO_DATA = re.compile(r"^No Data Available for Series (\S+) Year: \d{4}$")


class BLSRequestError(Exception):
    """L'API BLS a répondu en HTTP 200 mais a refusé la requête dans son corps.

    `status` = le statut rendu (`REQUEST_NOT_PROCESSED`, `REQUEST_FAILED`…),
    `messages` = la liste `message` telle que rendue (quota du jour, série invalide…).
    """

    def __init__(self, status: str, messages: List[str]):
        self.status = status
        self.messages = list(messages or [])
        super().__init__(f"bls {status}: {'; '.join(self.messages) or 'sans message'}")


def normalize_soc(soc: str) -> Tuple[str, Optional[str]]:
    """Code métier saisi → `(SOC à 6 chiffres sans tiret, suffixe O*NET retiré ou None)`.

    Accepte `15-1299`, `151299` et un code O*NET-SOC à 8 chiffres (`15-1299.08`) :
    l'OEWS publie au SOC à 6 chiffres, le suffixe O*NET est donc retiré et rendu
    (`"08"`) pour que l'appelant puisse le dire. Lève `ValueError` sur toute autre forme.
    """
    raw = str(soc or "").strip()
    m = re.fullmatch(r"(\d{2})-?(\d{4})(?:\.(\d{2}))?", raw)
    if not m:
        raise ValueError(
            f"code SOC invalide : {soc!r} — attendu 6 chiffres ('15-1299' ou '151299'), "
            "ou un code O*NET-SOC ('15-1299.08')")
    return m.group(1) + m.group(2), m.group(3)


def oews_series_id(soc: str, area_type: str, area_code: str, datatype: str,
                   industry: str = "000000") -> str:
    """Compose l'identifiant d'une série OEWS (25 caractères).

    Args:
        soc: SOC à 6 chiffres, sans tiret (cf. `normalize_soc`).
        area_type: `N` (national), `S` (État) ou `M` (aire métropolitaine).
        area_code: zone sur 7 caractères (cf. `areas.resolve_area`).
        datatype: mesure sur 2 chiffres (cf. `DATATYPES`).
        industry: secteur sur 6 chiffres ; `000000` = tous secteurs.
    """
    if area_type not in ("N", "S", "M"):
        raise ValueError(f"type de zone invalide : {area_type!r} (N, S ou M)")
    if not re.fullmatch(r"\d{7}", area_code):
        raise ValueError(f"code de zone invalide : {area_code!r} (7 chiffres)")
    if not re.fullmatch(r"\d{6}", soc):
        raise ValueError(f"SOC invalide : {soc!r} (6 chiffres sans tiret)")
    if not re.fullmatch(r"\d{6}", industry):
        raise ValueError(f"secteur invalide : {industry!r} (6 chiffres)")
    if datatype not in DATATYPES:
        raise ValueError(f"mesure inconnue : {datatype!r} (cf. DATATYPES)")
    return f"OEU{area_type}{area_code}{industry}{soc}{datatype}"


def _parse_value(value: Any) -> Optional[float]:
    """`"188470"` → 188470 ; `"-"`, vide ou non numérique → None (jamais deviné)."""
    text = str(value if value is not None else "").replace(",", "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    return float(text) if "." in text else int(text)


class BLSClient:
    """Client de l'API publique BLS v2 (https://api.bls.gov)."""

    BASE_URL = "https://api.bls.gov/publicAPI/v2"
    MAX_SERIES_NO_KEY = 25
    MAX_SERIES_WITH_KEY = 50

    def __init__(self, registration_key: Optional[str] = None):
        """
        Args:
            registration_key: clé d'enregistrement BLS, **facultative** (ou variable
                d'env `BLS_API_KEY`). Sans elle : 25 séries/requête, 25 requêtes/jour.
        """
        self.registration_key = registration_key or get_secret("BLS_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    @property
    def max_series(self) -> int:
        return self.MAX_SERIES_WITH_KEY if self.registration_key else self.MAX_SERIES_NO_KEY

    # --- transport ----------------------------------------------------------

    def get_series(self, series_ids: List[str], start_year: Optional[int] = None,
                   end_year: Optional[int] = None, timeout: int = 30) -> Dict[str, Any]:
        """POST /timeseries/data/ — une requête (une unité du quota journalier).

        Rend le corps JSON tel quel. Lève `ValueError` au-delà du plafond de séries
        par requête, `UpstreamHTTPError` sur un HTTP ≥ 400, `BLSRequestError` quand
        l'API refuse dans son corps (HTTP 200, `status` ≠ `REQUEST_SUCCEEDED`).
        """
        ids = list(series_ids)
        if not ids:
            raise ValueError("series_ids est vide")
        if len(ids) > self.max_series:
            raise ValueError(
                f"{len(ids)} séries demandées — plafond {self.max_series} par requête "
                f"({'avec' if self.registration_key else 'sans'} clé d'enregistrement)")
        body: Dict[str, Any] = {"seriesid": ids}
        if start_year is not None:
            body["startyear"] = str(start_year)
        if end_year is not None:
            body["endyear"] = str(end_year)
        if self.registration_key:
            body["registrationkey"] = self.registration_key   # dans le corps, jamais en params
        resp = self.session.post(f"{self.BASE_URL}/timeseries/data/", json=body,
                                 timeout=(10, timeout))
        raise_for_upstream(resp, service="bls")
        payload = resp.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise BLSRequestError(str(payload.get("status")), payload.get("message") or [])
        return payload

    # --- OEWS ---------------------------------------------------------------

    def oews_wages(self, soc: str, areas: List[str]) -> Dict[str, Any]:
        """Distribution annuelle des salaires d'un métier, pour une ou plusieurs zones.

        7 séries par zone (emploi, moyenne, P10/P25/médiane/P75/P90 annuels), groupées
        par requête jusqu'au plafond : sans clé, 3 zones tiennent en **une** requête ;
        au-delà, une requête par tranche de 3 zones (7 avec clé), chacune comptant
        dans le quota journalier.

        Args:
            soc: `15-1299`, `151299` ou un code O*NET-SOC (`15-1299.08`, suffixe retiré).
            areas: `"US"`, noms/abréviations d'États, codes CBSA à 5 chiffres.

        Rend `{"soc", "onet_suffix_stripped" (le suffixe retiré, ou None), "year",
        "requests", "areas": [...], "messages": [...]}`. Chaque zone : `area`, `area_type`, `area_code`, `year`,
        `employment`, `mean`, `percentiles` (`p10`…`p90`), `raw` (les valeurs non
        numériques telles que rendues, ex. `-`), `footnotes`, `missing` (mesures sans
        donnée) et `series_ids`. `messages` ne garde pas les « No Data Available »
        d'une série qui a rendu sa dernière année.
        """
        soc6, suffix = normalize_soc(soc)
        if not areas:
            raise ValueError("areas est vide — au moins une zone ('US', un État, un code CBSA)")
        resolved = [resolve_area(a) for a in areas]
        plan = [
            (area, {name: oews_series_id(soc6, area["area_type"], area["area_code"], code)
                    for name, code in WAGE_MEASURES})
            for area in resolved
        ]
        per_request = max(1, self.max_series // len(WAGE_MEASURES))
        by_id: Dict[str, Dict[str, Any]] = {}
        messages: List[str] = []
        requests_made = 0
        for i in range(0, len(plan), per_request):
            ids = [sid for _, series in plan[i:i + per_request] for sid in series.values()]
            payload = self.get_series(ids)
            requests_made += 1
            messages += payload.get("message") or []
            for s in (payload.get("Results") or {}).get("series") or []:
                by_id[s.get("seriesID")] = s

        out_areas = [self._area_result(area, series, by_id) for area, series in plan]
        with_data = {sid for sid, s in by_id.items() if s.get("data")}
        kept = [m for m in messages
                if not ((hit := _NO_DATA.match(m)) and hit.group(1) in with_data)]
        years = sorted({a["year"] for a in out_areas if a["year"]})
        return {
            "soc": f"{soc6[:2]}-{soc6[2:]}",
            "onet_suffix_stripped": suffix,
            "year": years[-1] if years else None,
            "requests": requests_made,
            "areas": out_areas,
            "messages": kept,
        }

    @staticmethod
    def _area_result(area: Dict[str, str], series: Dict[str, str],
                     by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        raw: Dict[str, str] = {}
        footnotes: List[Dict[str, Any]] = []
        missing: List[str] = []
        year = None
        for name, sid in series.items():
            data = (by_id.get(sid) or {}).get("data") or []
            if not data:
                values[name] = None
                missing.append(name)
                continue
            point = data[0]                       # l'API rend la plus récente en premier
            year = max(year or "", str(point.get("year") or "")) or None
            values[name] = _parse_value(point.get("value"))
            if values[name] is None:
                raw[name] = point.get("value")
            for fn in point.get("footnotes") or []:
                if fn and (fn.get("code") or fn.get("text")):
                    footnotes.append({"measure": name, "code": fn.get("code"),
                                      "text": fn.get("text")})
        return {
            **area,
            "year": year,
            "employment": values["employment"],
            "mean": values["mean"],
            "percentiles": {k: values[k] for k in ("p10", "p25", "p50", "p75", "p90")},
            "raw": raw,
            "footnotes": footnotes,
            "missing": missing,
            "series_ids": series,
        }
