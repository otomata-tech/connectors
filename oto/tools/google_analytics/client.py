"""Google Analytics 4 client — READ-ONLY, via a service account key.

Two Google REST APIs, a single credential:

- **Analytics Admin v1beta** (`analyticsadmin.googleapis.com`): the visible accounts and
  properties (`accountSummaries`), a property's data streams
  (`dataStreams`), its key events (`keyEvents`);
- **Analytics Data v1beta** (`analyticsdata.googleapis.com`): reports
  (`:runReport`), realtime (`:runRealtimeReport`), and the catalog of a
  property's dimensions and metrics (`/metadata`).

**Why a service account.** A user's OAuth consent to the `analytics.readonly`
scope can be blocked by Google for an unverified application. A service account
added as **Viewer** of a GA4 property reads without anyone's Google account,
and is cut off by removing its access in GA4.

**Credential** = the content of the service account's JSON key file
(see `auth.parse_service_account_key`). The access token is issued and
cached by `auth.access_token`.

**No writes.** The client exposes no method that creates, modifies or
deletes anything in GA4 — and the requested scope (`analytics.readonly`)
would forbid it anyway.

**Typed refusals** (subclasses of `UpstreamHTTPError`, read from the canonical `status`
of the Google error and from the `reason` of its details, never from the text):

- `GA4InvalidArgument` — 400 `INVALID_ARGUMENT`: a dimension or metric name
  unknown to the property, an incompatible combination, a malformed
  date. Google's message names the offending field; it is rendered as is.
- `GA4PermissionDenied` — 403 `PERMISSION_DENIED`: the service account has no
  access to the property. Carries the service account's email, which must be added
  as Viewer in GA4.
- `GA4ServiceDisabled` — 403 whose reason is `SERVICE_DISABLED`: the API is
  not enabled in the service account's Google Cloud project.

Requires: requests, google-auth (extra `google`)
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence, Union

import requests

from ..common.credentials import require
from ..common import UpstreamHTTPError
from . import auth

ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"

_HTTP_TIMEOUT = (10, 60)
_ADMIN_PAGE_SIZE = 200
# Bound on following `nextPageToken`: beyond it, upstream is looping — we say so.
_MAX_PAGES = 50

#: Default window of a report: the last 30 complete days (yesterday included,
#: today excluded — the current day is incomplete). Same definition as
#: "Last 30 days" in the GA4 interface.
DEFAULT_START_DATE = "30daysAgo"
DEFAULT_END_DATE = "yesterday"

_PROPERTY_RE = re.compile(r"^(?:properties/)?(\d+)$")

# Keys of a GA4 FilterExpression: a filter carrying one is passed through as is.
_EXPRESSION_KEYS = frozenset({"andGroup", "orGroup", "notExpression", "filter"})

# Metric types rendered as integers; all other numeric types as floats.
_INT_TYPES = frozenset({"TYPE_INTEGER"})


class GA4Error(UpstreamHTTPError):
    """Refusal from a Google Analytics API. `status` = Google's canonical status
    (`INVALID_ARGUMENT`, `PERMISSION_DENIED`…), `reason` = the `ErrorInfo` reason
    when Google gives one (`SERVICE_DISABLED`…), `message` = Google's text."""

    def __init__(self, status_code: int, body: Any, *, status: str = "",
                 reason: str = "", message: str = ""):
        self.status = status
        self.reason = reason
        self.message = message
        super().__init__(status_code, body, service="google_analytics")


class GA4InvalidArgument(GA4Error):
    """400 `INVALID_ARGUMENT` — unknown or incompatible dimension/metric, malformed
    date, invalid filter."""


class GA4PermissionDenied(GA4Error):
    """403 — the service account has no access to the requested resource."""

    def __init__(self, *args, client_email: str = "", resource: str = "", **kw):
        self.client_email = client_email
        self.resource = resource
        super().__init__(*args, **kw)
        qui = client_email or "the service account"
        quoi = resource or "this property"
        self.args = (
            f"google_analytics HTTP 403: {qui} has no access to {quoi}. Add this "
            "email as a Viewer of the property in GA4 (Admin → Property access "
            "management).",)


class GA4ServiceDisabled(GA4Error):
    """403 `SERVICE_DISABLED` — the API is not enabled in the service account's
    Google Cloud project."""


def property_name(prop: Union[str, int]) -> str:
    """`properties/<id>` from `<id>` or `properties/<id>` — otherwise `ValueError`.

    A MEASUREMENT id (`G-XXXX`) or an account id is not a property:
    refused with what is needed instead."""
    m = _PROPERTY_RE.match(str(prop).strip())
    if not m:
        raise ValueError(
            f"Invalid GA4 property: {prop!r}. Expected the property's numeric "
            "identifier (`123456789` or `properties/123456789`) — not a measurement ID "
            "`G-…`, nor an account ID.")
    return f"properties/{m.group(1)}"


def _names(values: Union[str, Iterable[str], None], kind: str) -> list[str]:
    """A list of names; a lone text reads as a comma-separated list
    (`"sessions,activeUsers"`), never as a sequence of characters."""
    if isinstance(values, str):
        values = [v for v in values.split(",") if v.strip()]
    out = []
    for v in values or ():
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Invalid {kind} name: {v!r}.")
        out.append(v.strip())
    return out


def build_filter(spec: Optional[dict]) -> Optional[dict]:
    """A GA4 `FilterExpression` from a SIMPLE filter — or the expression as
    is.

    Simple form: `{"champ": valeur, …}`, combined with AND. A text value is an
    exact match, a list a membership (`inListFilter`), a number a
    numeric equality. A full GA4 expression (`andGroup`, `orGroup`,
    `notExpression`, `filter`) passes through untransformed — that is the route for
    operators (contains, begins with, comparisons)."""
    if spec is None:
        return None
    if not isinstance(spec, dict) or not spec:
        raise ValueError("A filter is a non-empty object: {\"champ\": valeur} or a "
                         "GA4 FilterExpression.")
    if _EXPRESSION_KEYS & set(spec):
        return spec
    exprs = []
    for field, value in spec.items():
        if isinstance(value, bool):
            raise ValueError(f"Filter on {field!r}: boolean value not supported.")
        if isinstance(value, str):
            f = {"stringFilter": {"matchType": "EXACT", "value": value}}
        elif isinstance(value, (list, tuple)):
            if not value or not all(isinstance(v, str) for v in value):
                raise ValueError(f"Filter on {field!r}: expected a non-empty list of text values.")
            f = {"inListFilter": {"values": list(value)}}
        elif isinstance(value, (int, float)):
            num = {"int64Value": str(value)} if isinstance(value, int) else {"doubleValue": value}
            f = {"numericFilter": {"operation": "EQUAL", "value": num}}
        else:
            raise ValueError(f"Filter on {field!r}: {type(value).__name__} value not "
                             "supported (text, list of texts or number).")
        exprs.append({"filter": {"fieldName": field, **f}})
    return exprs[0] if len(exprs) == 1 else {"andGroup": {"expressions": exprs}}


def build_order_bys(order_by: Optional[Sequence[str]], metrics: Sequence[str]) -> list[dict]:
    """`["-sessions", "date"]` → the GA4 `orderBys`. A leading `-` = descending.
    A name present in `metrics` sorts on the metric, otherwise on the dimension."""
    out = []
    for raw in order_by or ():
        if not isinstance(raw, str) or not raw.strip("- "):
            raise ValueError(f"Invalid sort: {raw!r}.")
        name = raw.strip()
        desc = name.startswith("-")
        name = name.lstrip("-")
        key = ({"metric": {"metricName": name}} if name in metrics
               else {"dimension": {"dimensionName": name}})
        out.append({**key, "desc": desc})
    return out


def _typed(value: Optional[str], metric_type: str) -> Any:
    if value is None:
        return None
    try:
        return int(value) if metric_type in _INT_TYPES else float(value)
    except ValueError:
        return value


def flatten_report(resp: dict) -> dict:
    """A GA4 report (`runReport` / `runRealtimeReport`) as a TABLE: `columns`
    (dimensions then metrics) and `rows` (lists of values, typed metrics).

    Keeps `row_count` (the total of matching rows, for paging) and the
    reliability warnings GA4 attaches to the report: sampling,
    privacy thresholds, grouping into "(other)". Removing them would make
    an estimated figure read as exact."""
    dims = [h.get("name") for h in resp.get("dimensionHeaders") or ()]
    mets = resp.get("metricHeaders") or ()
    rows = []
    for r in resp.get("rows") or ():
        dv = [d.get("value") for d in r.get("dimensionValues") or ()]
        mv = [_typed(m.get("value"), h.get("type", ""))
              for m, h in zip(r.get("metricValues") or (), mets)]
        rows.append(dv + mv)
    out: dict[str, Any] = {
        "columns": dims + [h.get("name") for h in mets],
        "rows": rows,
        "row_count": resp.get("rowCount", 0),
    }
    for k in ("totals", "maximums", "minimums"):
        if resp.get(k):
            out[k] = flatten_report({"dimensionHeaders": resp.get("dimensionHeaders"),
                                     "metricHeaders": mets, "rows": resp[k]})["rows"]
    meta = resp.get("metadata") or {}
    kept = {k: meta[k] for k in ("currencyCode", "timeZone", "dataLossFromOtherRow",
                                 "samplingMetadatas", "subjectToThresholding",
                                 "emptyReason") if meta.get(k) not in (None, False, "")}
    if kept:
        out["metadata"] = kept
    if resp.get("propertyQuota"):
        out["property_quota"] = resp["propertyQuota"]
    return out


class GA4Client:
    """GA4 client (Admin + Data v1beta), auth via service account key."""

    def __init__(self, service_account_key: Union[str, bytes, dict, None] = None, *,
                 session: Optional[requests.Session] = None):
        """
        Args:
            service_account_key: the JSON content of the service account key
                (text or dict), supplied by the consumer (required).
            session: HTTP transport (default: a fresh `requests.Session`).
        """
        raw = require(service_account_key, "GA4_SERVICE_ACCOUNT_JSON")
        self._key = auth.parse_service_account_key(raw)
        self.client_email: str = self._key["client_email"]
        self.session = session or requests.Session()

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, url: str, *, params: Optional[dict] = None,
                 json_body: Any = None, resource: str = "") -> Any:
        token = auth.access_token(self._key, session=self.session)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self.session.request(
            method, url, params=clean or None, json=json_body,
            headers={"Authorization": f"Bearer {token}"}, timeout=_HTTP_TIMEOUT)
        if resp.status_code >= 400:
            self._raise(resp, resource)
        return resp.json() if resp.content else {}

    def _raise(self, resp, resource: str) -> None:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        status = err.get("status") or ""
        message = err.get("message") or ""
        reasons = [d.get("reason") for d in err.get("details") or ()
                   if isinstance(d, dict) and d.get("reason")]
        reason = reasons[0] if reasons else ""
        kw = dict(status=status, reason=reason, message=message)
        if resp.status_code == 403 and reason == "SERVICE_DISABLED":
            raise GA4ServiceDisabled(resp.status_code, body, **kw)
        if resp.status_code == 403 or status == "PERMISSION_DENIED":
            raise GA4PermissionDenied(resp.status_code, body, client_email=self.client_email,
                                      resource=resource, **kw)
        if status == "INVALID_ARGUMENT":
            raise GA4InvalidArgument(resp.status_code, body, **kw)
        raise GA4Error(resp.status_code, body, **kw)

    def _paged(self, url: str, items_key: str, resource: str = "") -> list[dict]:
        items: list[dict] = []
        token = None
        for _ in range(_MAX_PAGES):
            page = self._request("GET", url, params={"pageSize": _ADMIN_PAGE_SIZE,
                                                      "pageToken": token},
                                 resource=resource)
            items.extend(page.get(items_key) or ())
            token = page.get("nextPageToken")
            if not token:
                return items
        raise RuntimeError(f"google_analytics: more than {_MAX_PAGES} pages of "
                           f"`{items_key}` — pagination interrupted.")

    # --- Admin API -----------------------------------------------------------

    def account_summaries(self) -> list[dict]:
        """The visible GA accounts, each with its properties (`propertySummaries`)."""
        return self._paged(f"{ADMIN_BASE}/accountSummaries", "accountSummaries")

    def list_data_streams(self, prop: Union[str, int]) -> list[dict]:
        """A property's data streams (web, iOS, Android)."""
        name = property_name(prop)
        return self._paged(f"{ADMIN_BASE}/{name}/dataStreams", "dataStreams", name)

    def list_key_events(self, prop: Union[str, int]) -> list[dict]:
        """The key events (formerly "conversions") configured on a property."""
        name = property_name(prop)
        return self._paged(f"{ADMIN_BASE}/{name}/keyEvents", "keyEvents", name)

    # --- Data API ------------------------------------------------------------

    def get_metadata(self, prop: Union[str, int]) -> dict:
        """The dimensions and metrics usable on the property (standard and
        custom). `properties/0` returns the catalog common to all."""
        name = property_name(prop)
        return self._request("GET", f"{DATA_BASE}/{name}/metadata", resource=name)

    def run_report(self, prop: Union[str, int], *,
                   metrics: Optional[Sequence[str]] = None,
                   dimensions: Optional[Sequence[str]] = None,
                   start_date: str = DEFAULT_START_DATE,
                   end_date: str = DEFAULT_END_DATE,
                   dimension_filter: Optional[dict] = None,
                   metric_filter: Optional[dict] = None,
                   order_by: Optional[Sequence[str]] = None,
                   limit: Optional[int] = None, offset: Optional[int] = None,
                   keep_empty_rows: bool = False) -> dict:
        """`:runReport` — raw GA4 response (see `flatten_report` for a table).

        Dates: `YYYY-MM-DD`, `today`, `yesterday` or `NdaysAgo`. Defaults to the last 30
        complete days. Filters: see `build_filter`. Sort: see
        `build_order_bys`."""
        name = property_name(prop)
        mets, dims = _names(metrics, "metric"), _names(dimensions, "dimension")
        if not mets and not dims:
            raise ValueError("A GA4 report requires at least one metric or one dimension.")
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [{"name": d} for d in dims],
            "metrics": [{"name": m} for m in mets],
        }
        body.update(self._common(dimension_filter, metric_filter, order_by, mets, limit))
        if offset:
            body["offset"] = offset
        if keep_empty_rows:
            body["keepEmptyRows"] = True
        return self._request("POST", f"{DATA_BASE}/{name}:runReport", json_body=body,
                             resource=name)

    def run_realtime_report(self, prop: Union[str, int], *,
                            metrics: Optional[Sequence[str]] = None,
                            dimensions: Optional[Sequence[str]] = None,
                            dimension_filter: Optional[dict] = None,
                            metric_filter: Optional[dict] = None,
                            order_by: Optional[Sequence[str]] = None,
                            limit: Optional[int] = None,
                            minutes_ago: Optional[int] = None) -> dict:
        """`:runRealtimeReport` — activity of the last few minutes (30 by default
        on the GA4 side, 60 on GA4 360). `minutes_ago` bounds the window to N minutes."""
        name = property_name(prop)
        mets, dims = _names(metrics, "metric"), _names(dimensions, "dimension")
        if not mets and not dims:
            raise ValueError("A realtime report requires at least one metric or one "
                             "dimension.")
        body: dict[str, Any] = {"dimensions": [{"name": d} for d in dims],
                                "metrics": [{"name": m} for m in mets]}
        body.update(self._common(dimension_filter, metric_filter, order_by, mets, limit))
        if minutes_ago is not None:
            if minutes_ago < 1:
                raise ValueError("`minutes_ago` is a number of minutes ≥ 1.")
            body["minuteRanges"] = [{"startMinutesAgo": minutes_ago - 1,
                                     "endMinutesAgo": 0}]
        return self._request("POST", f"{DATA_BASE}/{name}:runRealtimeReport",
                             json_body=body, resource=name)

    @staticmethod
    def _common(dimension_filter, metric_filter, order_by, mets, limit) -> dict:
        body: dict[str, Any] = {}
        if dimension_filter is not None:
            body["dimensionFilter"] = build_filter(dimension_filter)
        if metric_filter is not None:
            body["metricFilter"] = build_filter(metric_filter)
        if order_by:
            body["orderBys"] = build_order_bys(order_by, mets)
        if limit is not None:
            if limit < 1:
                raise ValueError("`limit` is a number of rows ≥ 1.")
            body["limit"] = limit
        return body
