"""PostHog API client — analytics produit (events, HogQL, personnes, insights,
feature flags, session recordings).

Bearer (`Authorization: Bearer phx_…`) on the private REST API + the `/query/`
endpoint. **Live-tested on 2026-08-22** against a real PostHog Cloud
US project: identity, project discovery, HogQL, base schema, and the 14 resource
families below respond exactly as coded. The four points
below can NOT be deduced from the docs and were established by probing.

**(1) THREE key types, and the most visible one does not work.** The private API
only accepts the **personal** key `phx_…`. The PROJECT key `phc_…` — the one
PostHog puts forward (JS snippet, ingestion) — returns
`401 authentication_failed: "Personal API key found in request Authorization
header is invalid."` (verified live with the project's real token). The
`phs_…` key (project secret, beta) does not carry the analytics scopes. Hence the
refusal AT CONSTRUCTION of a `phc_`/`phs_`: without it, this connector's most
likely configuration mistake shows up as a 401
indistinguishable from a revoked key.

**(2) The region is part of the address, not of the account.** `https://us.posthog.com`
and `https://eu.posthog.com` are two distinct deployments; a key from one
is unknown to the other, and the symptom is again a 401. The host is therefore a
NON-secret config field paired with the key (same pattern as Zoho's `data_center`
or n8n's `base_url`), never a constant.

**(3) `project_id` is discoverable from the key alone** — `GET /api/users/@me/`
returns `organization.teams[]`, each team carrying its numeric `id`. The config
field remains useful to PIN a project when the key sees several, but
it is not mandatory: `resolve_project_id()` resolves it otherwise.

**(4) The list envelope is NOT uniform** (measured): `/insights/` returns
`{count, next, previous, results}` (offset), `/persons/` returns
`{next, previous, results}` and `/events/` returns `{next, results}` — without `count`.
Never assume `count`. `next` is an **absolute URL**; `next_page()` follows
it after checking that it really points at the configured host (an upstream URL
followed blindly is an SSRF).

**No writes except annotations.** Creating/editing/toggling a feature flag,
writing an insight or a cohort, deleting a person or a recording
do not exist here — not merely "not exposed at the tool level". Toggling a
flag changes the product's behavior for real users, and deleting
a person is irreversible and regulated (GDPR). Same doctrine as
`StripeClient`: a missing method forces the decision back through a PR.
Annotation is the exception because it is purely additive — it is the
"deployment v2.3 here" sticky note on a graph.

⚠️ **Ingestion is not here either** (`/i/v0/e/`, `/batch/`): it
authenticates with the PROJECT key, not the personal key, and an agent that
writes events into the dataset it reports on corrupts its own
evidence. Instrumentation belongs to the product's SDK.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

_HTTP_TIMEOUT = (10, 120)  # (connect, read) — a HogQL query can be long
_DEFAULT_HOST = "https://us.posthog.com"

# The known PostHog Cloud hosts. A self-hosted instance is accepted as is
# (that is the use case of the `host` field) — this table only serves to
# produce a useful message when a key from one region is used on the other.
CLOUD_HOSTS = {
    "us": "https://us.posthog.com",
    "eu": "https://eu.posthog.com",
}


class PostHogClient:
    """PostHog client (private REST API + `/query/`), Bearer auth, personal key."""

    def __init__(self, api_key: Optional[str] = None, *,
                 host: Optional[str] = None,
                 project_id: Optional[Any] = None):
        """
        Args:
            api_key: PostHog **personal** key `phx_…`, created in Settings → Personal API keys.
                A project key `phc_…` or a project secret key `phs_…`
                are REFUSED here (see the module docstring).
            host: `https://us.posthog.com` (default), `https://eu.posthog.com`,
                or the URL of a self-hosted instance. The region is part of
                the address: a US key is unknown on the EU side.
            project_id: default project. Optional — `resolve_project_id()`
                discovers it from the key. Set it to PIN a project
                when the key sees several.
        """
        self.api_key = require(api_key, "POSTHOG_API_KEY")
        if self.api_key.startswith("phc_"):
            raise ValueError(
                "PostHog PROJECT key (`phc_…`): this is the public ingestion token, "
                "refused by the read API (401 authentication_failed). A "
                "PERSONAL `phx_…` key is required — PostHog → Settings → Personal API keys.")
        if self.api_key.startswith("phs_"):
            raise ValueError(
                "PostHog project secret key (`phs_…`): its scopes do not cover "
                "analytics reads. A PERSONAL `phx_…` key is required — "
                "PostHog → Settings → Personal API keys.")
        self.host = (host or _DEFAULT_HOST).rstrip("/")
        self.project_id = str(project_id) if project_id is not None else None
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self._resolved_project_id: Optional[str] = None

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json_body: Any = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self.session.request(
            method, f"{self.host}{path}", params=clean or None, json=json_body,
            timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="posthog")
        return resp.json() if resp.content else {}

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def next_page(self, next_url: str) -> Any:
        """Follow the `next` of a paginated response — it is an ABSOLUTE URL.

        The URL is checked against the configured host before being followed: following
        a URL returned by upstream without validating it would turn a response
        controlled by a third party into an arbitrary outbound request (SSRF), with our
        `Authorization` header on it.
        """
        parsed = urlparse(next_url)
        expected = urlparse(self.host)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            raise ValueError(
                f"`next` points outside the configured host ({parsed.scheme}://{parsed.netloc} "
                f"≠ {self.host}) — not followed.")
        resp = self.session.get(next_url, timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="posthog")
        return resp.json() if resp.content else {}

    # --- projet -------------------------------------------------------------

    def current_user(self) -> Any:
        """GET /api/users/@me/ — the identity behind the key: email, organization,
        and `organization.teams[]` (the visible projects, with their `id`)."""
        return self._get("/api/users/@me/")

    def list_projects(self) -> Any:
        """GET /api/projects/ — the projects this key can read."""
        return self._get("/api/projects/")

    def resolve_project_id(self) -> str:
        """The project to operate on: the configured one, otherwise the first the
        key sees (`organization.teams[0].id`), memoized for the instance.

        Raises if the key sees no project — the case of a key restricted to
        another organization, which would otherwise fail further on with an opaque 404.
        """
        if self.project_id:
            return self.project_id
        if self._resolved_project_id:
            return self._resolved_project_id
        me = self.current_user()
        teams = (me.get("organization") or {}).get("teams") or me.get("teams") or []
        if not teams:
            raise ValueError(
                "This PostHog key sees no project — check its scopes "
                "(`project:read`) and its organization/project restriction.")
        self._resolved_project_id = str(teams[0]["id"])
        return self._resolved_project_id

    def _p(self, project_id: Optional[Any] = None) -> str:
        """The `/api/projects/{id}` prefix of every project endpoint."""
        return f"/api/projects/{project_id if project_id is not None else self.resolve_project_id()}"

    # ================================================================
    # HogQL — the endpoint that answers almost everything
    # ================================================================

    def query(self, hogql: str, *, project_id: Optional[Any] = None,
              **query_fields: Any) -> Any:
        """POST /api/projects/{id}/query/ — runs **HogQL** (SQL on
        ClickHouse) and returns `{columns, types, results, hasMore, hogql, …}`.

        Args:
            hogql: the query, e.g.
                `SELECT event, count() AS n FROM events
                 WHERE timestamp > now() - INTERVAL 7 DAY
                 GROUP BY event ORDER BY n DESC LIMIT 20`.
                Tables and columns are discovered with `database_schema()`.
            project_id: target a project other than the default.
            **query_fields: additional fields of the `query` object (e.g. `values`
                for placeholders, `filters`).

        Note: PostHog bounds the result itself (`LIMIT 101 OFFSET 0` added
        when the query has no LIMIT) and signals truncation via
        `hasMore`. A query error returns **400** with
        `detail` = "Unable to resolve field: …" and
        `extra.hogql_metadata.errors[]` carrying character offsets:
        precise enough for an agent to correct itself, so this message
        must be surfaced as is rather than replaced by generic text.
        """
        body = {"query": {"kind": "HogQLQuery", "query": hogql, **query_fields}}
        return self._request("POST", f"{self._p(project_id)}/query/", json_body=body)

    def run_query(self, query: Dict[str, Any], *, project_id: Optional[Any] = None,
                  **body_fields: Any) -> Any:
        """POST /api/projects/{id}/query/ with a COMPLETE `query` object — the route
        for PostHog's NAMED query types (`TrendsQuery`, `FunnelsQuery`,
        `RetentionQuery`, `StickinessQuery`, `LifecycleQuery`…), as opposed
        to the free-form SQL of `query()`.

        ⚠️ **Prefer this over hand-written HogQL for funnels and
        retention.** PostHog's funnel semantics (ordered or unordered
        steps, conversion window, exclusion steps, attribution) cannot be
        faithfully rebuilt in SQL: you get a plausible number, and
        it does not match the one the team reads on their dashboard.
        Passing the named type makes PostHog compute, hence the SAME number as the UI.

        Args:
            query: the query object, `kind` included.
            **body_fields: additional body fields (`refresh`,
                `client_query_id`, `filters_override`).
        """
        if not isinstance(query, dict) or not query.get("kind"):
            raise ValueError("`query` must be a dict carrying a `kind` "
                             "(HogQLQuery, TrendsQuery, FunnelsQuery, RetentionQuery…).")
        return self._request("POST", f"{self._p(project_id)}/query/",
                             json_body={"query": query, **body_fields})

    def run_insight(self, insight_id: Any, *, date_from: Optional[str] = None,
                    date_to: Optional[str] = None,
                    project_id: Optional[Any] = None) -> Any:
        """Re-run a SAVED insight, optionally over another window.

        Reads the insight's definition then replays ITS own query via
        `/query/`. It is "our funnel, but over last week" with nothing
        reinterpreted: the definition comes from the team, the
        computation comes from PostHog, so the figure returned is the dashboard's.
        Always prefer this over a rebuild in HogQL.

        Args:
            insight_id: the insight's id (or `short_id`).
            date_from/date_to: replacement window, PostHog syntax
                (`-7d`, `-30d`, `mStart`, `yStart`, or an ISO date). Omitted =
                the window saved with the insight.
        """
        insight = self.get_insight(insight_id, project_id=project_id)
        query = insight.get("query")
        if not query:
            raise ValueError(
                f"Insight {insight_id} has no re-runnable `query` — it is a "
                "legacy-format insight (`filters`), which PostHog does not replay via "
                "this route. Open it in the UI, or rephrase the question in HogQL.")
        source = query.get("source") if isinstance(query.get("source"), dict) else query
        if date_from is not None or date_to is not None:
            date_range = dict(source.get("dateRange") or {})
            if date_from is not None:
                date_range["date_from"] = date_from
            if date_to is not None:
                date_range["date_to"] = date_to
            source["dateRange"] = date_range
        return self.run_query(query, project_id=project_id)

    def database_schema(self, project_id: Optional[Any] = None) -> Any:
        """POST /api/projects/{id}/query/ `{kind: DatabaseSchemaQuery}` — the
        tables queryable in HogQL and their columns.

        ⚠️ Voluminous: 156 tables on a fresh project, with `events` at 52
        columns (measured on 2026-08-22). Project it before handing to an agent —
        table names first, then the columns of ONE table.
        """
        return self._request("POST", f"{self._p(project_id)}/query/",
                             json_body={"query": {"kind": "DatabaseSchemaQuery"}})

    # ================================================================
    # Definitions — the project's vocabulary
    # ================================================================

    def list_event_definitions(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/event_definitions/ — the event types known to the project.
        `search` filters by name, `limit`/`offset` paginate."""
        return self._get(f"{self._p(project_id)}/event_definitions/", **params)

    def list_property_definitions(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/property_definitions/ — the known properties.
        `search`, `type` (event | person | group), `event_names`, `limit`."""
        return self._get(f"{self._p(project_id)}/property_definitions/", **params)

    def list_property_values(self, key: str, project_id: Optional[Any] = None,
                             **params: Any) -> Any:
        """GET {p}/persons/properties/{key}/values/ — the observed values
        of a property (to suggest a plausible filter rather than an invented one)."""
        return self._get(f"{self._p(project_id)}/persons/properties/{key}/values/",
                         **params)

    # ================================================================
    # Events & persons
    # ================================================================

    def list_events(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/events/ — raw events, most recent first.

        Args:
            **params: `event` (name), `distinct_id`, `person_id`, `after`/`before`
                (ISO 8601), `properties` (JSON), `limit`.

        ⚠️ `{next, results}` envelope — **no `count`**. To count or
        aggregate, go through `query()`: counting by paginating raw events is
        both slow and wrong as soon as there is more than one page.
        """
        return self._get(f"{self._p(project_id)}/events/", **params)

    def get_event(self, event_id: str, project_id: Optional[Any] = None) -> Any:
        """GET {p}/events/{id}/."""
        return self._get(f"{self._p(project_id)}/events/{event_id}/")

    def list_persons(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/persons/ — filters `search` (email/name/distinct_id),
        `properties` (JSON), `cohort`, `distinct_id`, `limit`.
        `{next, previous, results}` envelope, without `count`."""
        return self._get(f"{self._p(project_id)}/persons/", **params)

    def get_person(self, person_id: Any, project_id: Optional[Any] = None) -> Any:
        """GET {p}/persons/{id}/ — a person's record and their properties."""
        return self._get(f"{self._p(project_id)}/persons/{person_id}/")

    def list_person_activity(self, person_id: Any, project_id: Optional[Any] = None,
                             **params: Any) -> Any:
        """GET {p}/persons/{id}/activity/ — a person's activity."""
        return self._get(f"{self._p(project_id)}/persons/{person_id}/activity/", **params)

    # ================================================================
    # Insights, dashboards, cohorts — the work already saved
    # ================================================================

    def list_insights(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/insights/ — the saved insights. `search`, `saved`,
        `favorited`, `limit`/`offset`. `{count, next, previous, results}` envelope."""
        return self._get(f"{self._p(project_id)}/insights/", **params)

    def get_insight(self, insight_id: Any, project_id: Optional[Any] = None) -> Any:
        """GET {p}/insights/{id}/ — an insight's definition."""
        return self._get(f"{self._p(project_id)}/insights/{insight_id}/")

    def list_dashboards(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/dashboards/ — the dashboards and their tiles."""
        return self._get(f"{self._p(project_id)}/dashboards/", **params)

    def get_dashboard(self, dashboard_id: Any, project_id: Optional[Any] = None) -> Any:
        """GET {p}/dashboards/{id}/."""
        return self._get(f"{self._p(project_id)}/dashboards/{dashboard_id}/")

    def list_cohorts(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/cohorts/ — the defined cohorts."""
        return self._get(f"{self._p(project_id)}/cohorts/", **params)

    def get_cohort(self, cohort_id: Any, project_id: Optional[Any] = None) -> Any:
        """GET {p}/cohorts/{id}/."""
        return self._get(f"{self._p(project_id)}/cohorts/{cohort_id}/")

    def list_cohort_persons(self, cohort_id: Any, project_id: Optional[Any] = None,
                            **params: Any) -> Any:
        """GET {p}/cohorts/{id}/persons/ — who is IN a cohort."""
        return self._get(f"{self._p(project_id)}/cohorts/{cohort_id}/persons/", **params)

    def list_actions(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/actions/ — the actions (events named by the team)."""
        return self._get(f"{self._p(project_id)}/actions/", **params)

    # ================================================================
    # Groups — B2B analytics (accounts, organizations)
    # ================================================================

    def list_group_types(self, project_id: Optional[Any] = None) -> Any:
        """GET {p}/groups_types/ — the defined group TYPES ("company",
        "workspace"…) with their `group_type_index`.

        Call this first: without the index, no group call is possible.
        An empty list means the project does not do group analytics —
        questions by ACCOUNT ("which customers are dropping off") then have no
        answer here, and that should be said rather than answering per person.
        ⚠️ Returns a BARE list, not the `{results}` envelope (measured on 22/08/2026).
        """
        return self._get(f"{self._p(project_id)}/groups_types/")

    def list_groups(self, group_type_index: int, project_id: Optional[Any] = None,
                    **params: Any) -> Any:
        """GET {p}/groups/ — the groups of a given type (the ACCOUNTS, in B2B).

        Args:
            group_type_index: the index returned by `list_group_types()`.
            **params: `search`, `cursor`.
        """
        return self._get(f"{self._p(project_id)}/groups/",
                         group_type_index=group_type_index, **params)

    def find_group(self, group_type_index: int, group_key: str,
                   project_id: Optional[Any] = None) -> Any:
        """GET {p}/groups/find/ — a specific group by its business key."""
        return self._get(f"{self._p(project_id)}/groups/find/",
                         group_type_index=group_type_index, group_key=group_key)

    # ================================================================
    # Feature flags, experiments — READ-only
    # ================================================================

    def list_feature_flags(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/feature_flags/ — the flags, their `key`, their `active` state and
        their rollout conditions. Creating, editing or toggling one
        does not exist here: a toggled flag changes the product for real
        users (see the module docstring)."""
        return self._get(f"{self._p(project_id)}/feature_flags/", **params)

    def get_feature_flag(self, flag_id: Any, project_id: Optional[Any] = None) -> Any:
        """GET {p}/feature_flags/{id}/."""
        return self._get(f"{self._p(project_id)}/feature_flags/{flag_id}/")

    def list_experiments(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/experiments/ — the A/B tests and their metrics."""
        return self._get(f"{self._p(project_id)}/experiments/", **params)

    def get_experiment(self, experiment_id: Any, project_id: Optional[Any] = None) -> Any:
        """GET {p}/experiments/{id}/."""
        return self._get(f"{self._p(project_id)}/experiments/{experiment_id}/")

    # ================================================================
    # Session recordings, surveys
    # ================================================================

    def list_session_recordings(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/session_recordings/ — filters `date_from`/`date_to`,
        `person_uuid`, `limit`. `{next, results}` envelope, without `count`."""
        return self._get(f"{self._p(project_id)}/session_recordings/", **params)

    def get_session_recording(self, recording_id: str,
                              project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/session_recordings/{id}/ — a recording's metadata
        (duration, person, visited URLs). NOT the video."""
        return self._get(f"{self._p(project_id)}/session_recordings/{recording_id}/",
                         **params)

    def list_surveys(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/surveys/ — the in-app surveys and their state."""
        return self._get(f"{self._p(project_id)}/surveys/", **params)

    # ================================================================
    # Annotations — the ONLY write
    # ================================================================

    def list_annotations(self, project_id: Optional[Any] = None, **params: Any) -> Any:
        """GET {p}/annotations/ — the markers placed on the timeline."""
        return self._get(f"{self._p(project_id)}/annotations/", **params)

    def create_annotation(self, content: str, *, date_marker: Optional[str] = None,
                          project_id: Optional[Any] = None, **body: Any) -> Any:
        """POST {p}/annotations/ — places a dated marker on the project's graphs
        ("deployment v2.3", "campaign start"). Purely ADDITIVE: it
        alters no measured data, only how it is read — hence the only
        write retained by this connector.

        Args:
            content: the marker's text.
            date_marker: the marked instant (ISO 8601). PostHog default = now.
            **body: `scope` ("project" | "organization"), `dashboard_item`
                (pin to a specific insight).
        """
        payload = {"content": content, **body}
        if date_marker is not None:
            payload["date_marker"] = date_marker
        return self._request("POST", f"{self._p(project_id)}/annotations/",
                             json_body=payload)
