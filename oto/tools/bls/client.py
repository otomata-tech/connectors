"""BLS client — public data API of the Bureau of Labor Statistics (api.bls.gov, v2).

A single endpoint: `POST /publicAPI/v2/timeseries/data/`, JSON body
`{"seriesid": [...]}`. **Usable without a key**: 25 series per request and 25 requests
per day; with a registration key (`registrationkey`, free), 50 series and
500 requests per day.

This client serves the **OEWS** survey (Occupational Employment and Wage Statistics):
employment and wages by occupation (6-digit SOC code) and by area. Identifier of an
OEWS series, 25 characters:

    OEU + area type (N|S|M) + area (7) + industry (6, `000000` = all) + SOC (6) + measure (2)

⚠️ The API only returns the **latest published year** of the OEWS. Its response carries
`status`, `message` (list: the « No Data Available for Series … Year: … » lines
are NOT errors, the API sweeps a default window of years) and
`Results.series[].data[]` (`year`, `period` = `A01`, `value` as a **string**, which can
be `-` or carry a note when the estimate is capped or unpublished).

⚠️ A refusal from the API often arrives as **HTTP 200** with `status` ≠
`REQUEST_SUCCEEDED` (daily quota exhausted, malformed request): `BLSRequestError`.

Requires: requests
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..common import raise_for_upstream
from .areas import resolve_area

# OEWS measures (reference file `oe.datatype`).
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

# The full annual distribution of an occupation in an area: 7 series.
WAGE_MEASURES: Tuple[Tuple[str, str], ...] = (
    ("employment", "01"), ("mean", "04"),
    ("p10", "11"), ("p25", "12"), ("p50", "13"), ("p75", "14"), ("p90", "15"),
)

_NO_DATA = re.compile(r"^No Data Available for Series (\S+) Year: \d{4}$")


class BLSRequestError(Exception):
    """The BLS API answered with HTTP 200 but refused the request in its body.

    `status` = the returned status (`REQUEST_NOT_PROCESSED`, `REQUEST_FAILED`…),
    `messages` = the `message` list as returned (daily quota, invalid series…).
    """

    def __init__(self, status: str, messages: List[str]):
        self.status = status
        self.messages = list(messages or [])
        super().__init__(f"bls {status}: {'; '.join(self.messages) or 'no message'}")


def normalize_soc(soc: str) -> Tuple[str, Optional[str]]:
    """Entered occupation code → `(6-digit SOC without hyphen, O*NET suffix stripped or None)`.

    Accepts `15-1299`, `151299` and an 8-digit O*NET-SOC code (`15-1299.08`):
    the OEWS publishes at the 6-digit SOC, so the O*NET suffix is stripped and returned
    (`"08"`) so that the caller can say so. Raises `ValueError` on any other form.
    """
    raw = str(soc or "").strip()
    m = re.fullmatch(r"(\d{2})-?(\d{4})(?:\.(\d{2}))?", raw)
    if not m:
        raise ValueError(
            f"invalid SOC code: {soc!r} — expected 6 digits ('15-1299' or '151299'), "
            "or an O*NET-SOC code ('15-1299.08')")
    return m.group(1) + m.group(2), m.group(3)


def oews_series_id(soc: str, area_type: str, area_code: str, datatype: str,
                   industry: str = "000000") -> str:
    """Builds the identifier of an OEWS series (25 characters).

    Args:
        soc: 6-digit SOC, without hyphen (see `normalize_soc`).
        area_type: `N` (national), `S` (State) or `M` (metropolitan area).
        area_code: 7-character area (see `areas.resolve_area`).
        datatype: 2-digit measure (see `DATATYPES`).
        industry: 6-digit industry; `000000` = all industries.
    """
    if area_type not in ("N", "S", "M"):
        raise ValueError(f"invalid area type: {area_type!r} (N, S or M)")
    if not re.fullmatch(r"\d{7}", area_code):
        raise ValueError(f"invalid area code: {area_code!r} (7 digits)")
    if not re.fullmatch(r"\d{6}", soc):
        raise ValueError(f"invalid SOC: {soc!r} (6 digits without hyphen)")
    if not re.fullmatch(r"\d{6}", industry):
        raise ValueError(f"invalid industry: {industry!r} (6 digits)")
    if datatype not in DATATYPES:
        raise ValueError(f"unknown measure: {datatype!r} (see DATATYPES)")
    return f"OEU{area_type}{area_code}{industry}{soc}{datatype}"


def _parse_value(value: Any) -> Optional[float]:
    """`"188470"` → 188470; `"-"`, empty or non-numeric → None (never guessed)."""
    text = str(value if value is not None else "").replace(",", "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    return float(text) if "." in text else int(text)


class BLSClient:
    """Client of the BLS public API v2 (https://api.bls.gov)."""

    BASE_URL = "https://api.bls.gov/publicAPI/v2"
    MAX_SERIES_NO_KEY = 25
    MAX_SERIES_WITH_KEY = 50

    def __init__(self, registration_key: Optional[str] = None):
        """
        Args:
            registration_key: BLS registration key, **optional**, supplied by
                the consumer. Without it: 25 series/request, 25 requests/day.
        """
        self.registration_key = registration_key
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    @property
    def max_series(self) -> int:
        return self.MAX_SERIES_WITH_KEY if self.registration_key else self.MAX_SERIES_NO_KEY

    # --- transport ----------------------------------------------------------

    def get_series(self, series_ids: List[str], start_year: Optional[int] = None,
                   end_year: Optional[int] = None, timeout: int = 30) -> Dict[str, Any]:
        """POST /timeseries/data/ — one request (one unit of the daily quota).

        Returns the JSON body as-is. Raises `ValueError` beyond the cap of series
        per request, `UpstreamHTTPError` on an HTTP ≥ 400, `BLSRequestError` when
        the API refuses in its body (HTTP 200, `status` ≠ `REQUEST_SUCCEEDED`).
        """
        ids = list(series_ids)
        if not ids:
            raise ValueError("series_ids is empty")
        if len(ids) > self.max_series:
            raise ValueError(
                f"{len(ids)} series requested — cap {self.max_series} per request "
                f"({'with' if self.registration_key else 'without'} registration key)")
        body: Dict[str, Any] = {"seriesid": ids}
        if start_year is not None:
            body["startyear"] = str(start_year)
        if end_year is not None:
            body["endyear"] = str(end_year)
        if self.registration_key:
            body["registrationkey"] = self.registration_key   # in the body, never in params
        resp = self.session.post(f"{self.BASE_URL}/timeseries/data/", json=body,
                                 timeout=(10, timeout))
        raise_for_upstream(resp, service="bls")
        payload = resp.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise BLSRequestError(str(payload.get("status")), payload.get("message") or [])
        return payload

    # --- OEWS ---------------------------------------------------------------

    def oews_wages(self, soc: str, areas: List[str]) -> Dict[str, Any]:
        """Annual wage distribution of an occupation, for one or more areas.

        7 series per area (employment, mean, annual P10/P25/median/P75/P90), grouped
        per request up to the cap: without a key, 3 areas fit in **one** request;
        beyond that, one request per batch of 3 areas (7 with a key), each counting
        against the daily quota.

        Args:
            soc: `15-1299`, `151299` or an O*NET-SOC code (`15-1299.08`, suffix stripped).
            areas: `"US"`, State names/abbreviations, 5-digit CBSA codes.

        Returns `{"soc", "onet_suffix_stripped" (the stripped suffix, or None), "year",
        "requests", "areas": [...], "messages": [...]}`. Each area: `area`, `area_type`, `area_code`, `year`,
        `employment`, `mean`, `percentiles` (`p10`…`p90`), `raw` (the non-numeric
        values as returned, e.g. `-`), `footnotes`, `missing` (measures without
        data) and `series_ids`. `messages` does not keep the « No Data Available »
        of a series that returned its latest year.
        """
        soc6, suffix = normalize_soc(soc)
        if not areas:
            raise ValueError("areas is empty — at least one area ('US', a State, a CBSA code)")
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
            point = data[0]                       # the API returns the most recent first
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
