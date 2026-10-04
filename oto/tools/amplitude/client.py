"""Amplitude API client — READ ONLY: taxonomy, Dashboard REST queries, saved
charts, user lookup, behavioral cohorts.

HTTP Basic auth with the project's **API key + secret key**. One method = one
endpoint; responses are returned as-is, the client invents no semantics.
Paths and parameters follow the public reference
(https://amplitude.com/docs/apis/analytics/dashboard-rest,
https://amplitude.com/docs/apis/analytics/taxonomy,
https://amplitude.com/docs/apis/analytics/behavioral-cohorts).

What the caller needs to know, and cannot guess:

- **One key pair = one project.** There is no project parameter anywhere:
  the keys ARE the project. Another project needs another connection.
- **The API key alone reads nothing.** It is the public key of the SDK
  snippet; every read endpoint needs the pair. A missing or wrong secret
  answers `403 {"error": {"metadata": {"details": "Invalid API/Secret Key
  combination"}}}` — a **403, not a 401**.
- **Two deployments, chosen at construction** (`region`): `us` →
  `amplitude.com`, `eu` → `analytics.eu.amplitude.com`. A key is unknown to
  the other one, which answers `403 … "Invalid API Key"`. The two
  messages differ, which is what lets a caller tell "wrong region" from
  "wrong secret".
- **Query parameters are URL-encoded JSON** (`e`, `se`, `re`, `s`): the
  methods take dicts/lists and encode them once. A funnel repeats `e` once per
  step, in order.
- **Dates are `YYYYMMDD`.** `YYYY-MM-DD` is accepted and converted.
- **Rate limits are a cost budget, not a request count**: 108,000 cost/hour
  and 5 concurrent requests per project, cost = days × conditions × query-type
  cost. `user_search`/`user_activity` have their own: 360/hour. A 429 is the
  only signal.
- Segmentation and funnel group-bys return at most 1,000 rows (`limit`).
- **Cohort downloads are asynchronous** (Behavioral Cohorts API — a Growth /
  Enterprise add-on, 500 downloads a month): `request_cohort` → poll
  `cohort_status` until `JOB COMPLETED` → `cohort_file`.

Not here, on purpose: event ingestion (HTTP V2 / Identify — API key only,
another host; an agent writing events into the dataset it reports on corrupts
its own evidence), taxonomy writes, cohort upload, the raw Export API.

Requires: requests
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

#: (connect, read) — a long query can take a while, never an unbounded wait.
_HTTP_TIMEOUT = (10, 60)

#: Data residency → API host.
REGIONS: Dict[str, str] = {
    "us": "https://amplitude.com",
    "eu": "https://analytics.eu.amplitude.com",
}

_DAY = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")


def day(value: str) -> str:
    """`YYYYMMDD` or `YYYY-MM-DD` → `YYYYMMDD`. Anything else is refused."""
    m = _DAY.match(str(value or "").strip())
    if not m:
        raise ValueError(f"date {value!r}: expected YYYY-MM-DD or YYYYMMDD.")
    return "".join(m.groups())


def _js(value: Any) -> str:
    """One JSON query parameter, compact (requests URL-encodes it)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _segment(name: str, value: Any) -> str:
    """An id or name placed in a path: escaped, and never `.`/`..`."""
    s = str(value if value is not None else "").strip()
    if not s or s in (".", ".."):
        raise ValueError(f"`{name}` is required.")
    return quote(s, safe="")


Params = List[Tuple[str, Any]]


class AmplitudeClient:
    """Amplitude read APIs (Dashboard REST, Taxonomy, Behavioral Cohorts)."""

    def __init__(self, api_key: Optional[str] = None,
                 secret_key: Optional[str] = None, *, region: str = "us"):
        """
        Args:
            api_key: the project's API key (Settings → Projects → <project> →
                General).
            secret_key: the same project's secret key — required, the API key
                alone is refused by every read endpoint.
            region: `us` (default) or `eu`, where the project's data lives.
        """
        self.api_key = require(api_key, "AMPLITUDE_API_KEY")
        self.secret_key = require(secret_key, "AMPLITUDE_SECRET_KEY")
        region = (region or "us").strip().lower()
        if region not in REGIONS:
            raise ValueError(f"Amplitude region {region!r}: expected one of "
                             f"{', '.join(REGIONS)}.")
        self.region = region
        self.BASE_URL = REGIONS[region]
        self.session = requests.Session()
        self.session.auth = (self.api_key, self.secret_key)

    # --- transport ----------------------------------------------------------

    def _get(self, path: str, params: Optional[Params] = None, *,
             raw: bool = False) -> Any:
        clean = [(k, v) for k, v in (params or []) if v is not None]
        resp = self.session.get(f"{self.BASE_URL}{path}", params=clean or None,
                                timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="amplitude")
        if raw:
            return resp.text
        return resp.json() if resp.content else {}

    @staticmethod
    def _common(start: str, end: str, segments: Optional[Sequence[dict]],
                group_by: Optional[str]) -> Params:
        return [("start", day(start)), ("end", day(end)),
                ("s", _js(list(segments)) if segments else None),
                ("g", group_by or None)]

    # ================================================================
    # Taxonomy — the project's vocabulary (cost 1 per GET)
    # ================================================================

    def list_event_types(self) -> Any:
        """GET /api/2/taxonomy/event — every event type of the project, with
        category, description and visibility. `{success, data: [...]}`."""
        return self._get("/api/2/taxonomy/event")

    def list_event_properties(self, event_type: Optional[str] = None) -> Any:
        """GET /api/2/taxonomy/event-property — event properties, optionally of
        ONE event type."""
        return self._get("/api/2/taxonomy/event-property",
                         [("event_type", event_type)])

    def list_user_properties(self) -> Any:
        """GET /api/2/taxonomy/user-property — user properties. Custom ones
        come back prefixed `gp:` (the name to use in a filter or group-by)."""
        return self._get("/api/2/taxonomy/user-property")

    def list_group_properties(self) -> Any:
        """GET /api/2/taxonomy/group-property — group (account) properties,
        prefixed `grp:`. Empty when the project has no Accounts add-on."""
        return self._get("/api/2/taxonomy/group-property")

    def list_events(self) -> Any:
        """GET /api/2/events/list — event types with their weekly totals and
        uniques (what is actually firing, not only what is declared)."""
        return self._get("/api/2/events/list")

    # ================================================================
    # Dashboard REST queries
    # ================================================================

    def segmentation(self, event: dict, start: str, end: str, *,
                     metric: Optional[str] = None, interval: Optional[int] = None,
                     segments: Optional[Sequence[dict]] = None,
                     group_by: Optional[str] = None,
                     limit: Optional[int] = None,
                     event2: Optional[dict] = None,
                     formula: Optional[str] = None) -> Any:
        """GET /api/2/events/segmentation — an event's metric over time.

        Args:
            event: `{"event_type": "Sign Up", "filters": [...], "group_by":
                [{"type": "event", "value": "plan"}]}`. `_active` / `_all` are
                Amplitude's "any active event" / "any event".
            metric: uniques (default) | totals | pct_dau | average | histogram
                | sums | value_avg | formula.
            interval: 1 daily (default), 7 weekly, 30 monthly, -3600000 hourly,
                -300000 realtime.
            segments: user segments, `[{"prop": "country", "op": "is",
                "values": ["France"]}]`.
            group_by: one property to break down by (`country`, `gp:plan`).
            limit: group-by values returned, max 1000.
            event2/formula: the second event and formula of `metric=formula`.
        """
        params = [("e", _js(event)), ("e2", _js(event2) if event2 else None),
                  ("m", metric), ("i", interval), ("limit", limit),
                  ("formula", formula)]
        return self._get("/api/2/events/segmentation",
                         params + self._common(start, end, segments, group_by))

    def funnel(self, events: Sequence[dict], start: str, end: str, *,
               mode: Optional[str] = None, new_or_active: Optional[str] = None,
               conversion_window_seconds: Optional[int] = None,
               segments: Optional[Sequence[dict]] = None,
               group_by: Optional[str] = None,
               limit: Optional[int] = None) -> Any:
        """GET /api/2/funnels — conversion through ordered steps.

        Args:
            events: the steps, in order — one `e` parameter each.
            mode: ordered (default) | unordered | sequential.
            new_or_active: active (default) | new.
            conversion_window_seconds: default 2592000 (30 days).
        """
        if len(events or []) < 2:
            raise ValueError("A funnel needs at least two steps.")
        params: Params = [("e", _js(ev)) for ev in events]
        params += [("mode", mode), ("n", new_or_active),
                   ("cs", conversion_window_seconds), ("limit", limit)]
        return self._get("/api/2/funnels",
                         params + self._common(start, end, segments, group_by))

    def retention(self, start_event: dict, return_event: dict, start: str,
                  end: str, *, mode: Optional[str] = None,
                  interval: Optional[int] = None,
                  segments: Optional[Sequence[dict]] = None,
                  group_by: Optional[str] = None) -> Any:
        """GET /api/2/retention — of the users who did `start_event`, how many
        came back to do `return_event`.

        Args:
            mode: n-day (default) | rolling | bracket (`rm`).
            interval: 1 daily, 7 weekly, 30 monthly.
        """
        params = [("se", _js(start_event)), ("re", _js(return_event)),
                  ("rm", mode), ("i", interval)]
        return self._get("/api/2/retention",
                         params + self._common(start, end, segments, group_by))

    def active_users(self, start: str, end: str, *, metric: Optional[str] = None,
                     interval: Optional[int] = None,
                     segments: Optional[Sequence[dict]] = None,
                     group_by: Optional[str] = None) -> Any:
        """GET /api/2/users — active (default) or `new` users over time."""
        params = [("m", metric), ("i", interval)]
        return self._get("/api/2/users",
                         params + self._common(start, end, segments, group_by))

    def sessions(self, kind: str, start: str, end: str) -> Any:
        """GET /api/2/sessions/{length|average|peruser}."""
        if kind not in ("length", "average", "peruser"):
            raise ValueError("sessions kind: length | average | peruser.")
        return self._get(f"/api/2/sessions/{kind}",
                         [("start", day(start)), ("end", day(end))])

    # ================================================================
    # Saved charts — the number the team reads in Amplitude
    # ================================================================

    def chart_query(self, chart_id: str) -> Any:
        """GET /api/3/chart/{id}/query — a saved chart's results as JSON,
        computed by Amplitude with the chart's own definition.

        ⚠️ Not in the reference's endpoint table; `chart_csv` is the listed
        one."""
        return self._get(f"/api/3/chart/{_segment('chart_id', chart_id)}/query")

    def chart_csv(self, chart_id: str) -> str:
        """GET /api/3/chart/{id}/csv — a saved chart's results as CSV text."""
        return self._get(f"/api/3/chart/{_segment('chart_id', chart_id)}/csv",
                         raw=True)

    # ================================================================
    # Users — 360 queries/hour shared by both
    # ================================================================

    def user_search(self, user: str) -> Any:
        """GET /api/2/usersearch — find a user by user id, device id or
        Amplitude id. `{matches: [{amplitude_id, user_id, ...}], type}`."""
        return self._get("/api/2/usersearch", [("user", user)])

    def user_activity(self, amplitude_id: Any, *, offset: Optional[int] = None,
                      limit: Optional[int] = None,
                      direction: Optional[str] = None) -> Any:
        """GET /api/2/useractivity — one user's summary and event stream.

        Args:
            amplitude_id: the Amplitude id (from `user_search`), not the
                user id.
            limit: events returned (Amplitude default 1000).
            direction: latest (default) | earliest.
        """
        return self._get("/api/2/useractivity",
                         [("user", amplitude_id), ("offset", offset),
                          ("limit", limit), ("direction", direction)])

    # ================================================================
    # Behavioral cohorts (Growth / Enterprise add-on)
    # ================================================================

    def list_cohorts(self) -> Any:
        """GET /api/3/cohorts — the project's cohorts (id, name, size,
        last computed). `{cohorts: [...]}`."""
        return self._get("/api/3/cohorts")

    def request_cohort(self, cohort_id: str, *, props: bool = False) -> Any:
        """GET /api/5/cohorts/request/{id} — start a download job.
        `{request_id, cohort_id}`. Counts against the 500 downloads/month."""
        return self._get(f"/api/5/cohorts/request/{_segment('cohort_id', cohort_id)}",
                         [("props", 1 if props else 0)])

    def cohort_status(self, request_id: str) -> Any:
        """GET /api/5/cohorts/request-status/{request_id} —
        `{async_status: "JOB INPROGRESS" | "JOB COMPLETED", ...}`."""
        return self._get(
            f"/api/5/cohorts/request-status/{_segment('request_id', request_id)}")

    def cohort_file(self, request_id: str, *, max_lines: int = 100) -> dict:
        """GET /api/5/cohorts/request/{request_id}/file — a bounded preview of
        the members (CSV, one member per line after the header), once the job is
        completed: `{total_lines, lines, truncated}`.

        Read as a STREAM: a cohort of a million members (with properties) is
        never held in memory — only the first `max_lines` non-empty lines are
        kept, the rest are counted. Small cohorts answer inline; large ones
        redirect to a short-lived S3 link, which `requests` follows (and, on a
        cross-host redirect, drops the Basic auth header — as S3 expects)."""
        url = (f"{self.BASE_URL}/api/5/cohorts/request/"
               f"{_segment('request_id', request_id)}/file")
        resp = self.session.get(url, timeout=_HTTP_TIMEOUT, stream=True)
        try:
            raise_for_upstream(resp, service="amplitude")
            if not resp.encoding:
                resp.encoding = "utf-8"
            lines: list = []
            total = 0
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.strip():
                    continue
                total += 1
                if len(lines) < max_lines:
                    lines.append(line)
            return {"total_lines": total, "lines": lines,
                    "truncated": total > len(lines)}
        finally:
            resp.close()
