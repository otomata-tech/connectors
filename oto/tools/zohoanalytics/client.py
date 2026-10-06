"""Zoho Analytics API v2 client — https://www.zoho.com/analytics/api/v2/

Reads a workspace's data: metadata (workspaces, views) + export
of a view's data (synchronous) and execution of SQL SELECT queries (asynchronous
export flow: create job → poll → download).

OAuth2 self-client (client_id/client_secret/refresh_token), same token
mechanics as Zoho CRM. All requests carry the `ZANALYTICS-ORGID` header.
"""

import json
import time
from typing import Any, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from ..zoho.auth import ZohoAuthError, cred_key, get_access_token, invalidate

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait


class ZohoAnalyticsClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        org_id: Optional[str] = None,
        api_domain: Optional[str] = None,
        accounts_url: Optional[str] = None,
    ):
        """Initialize the client.

        Credentials are always supplied by the consumer (multi-user server
        use). The access token is cached
        **in process memory**, keyed by credential (`_TOKEN_CACHE`) — never on
        disk, never shared between distinct credentials (key = hash of the secret).
        """
        self.client_id = require(client_id, "ZOHO_ANALYTICS_CLIENT_ID")
        self.client_secret = require(client_secret, "ZOHO_ANALYTICS_CLIENT_SECRET")
        # OPTIONAL at construction: in server-based mode it is obtained through the
        # consent flow, not pasted. Its absence is reported at refresh
        # time (actionable message) rather than as a config error.
        self.refresh_token = refresh_token
        # OPTIONAL at construction, like `refresh_token` and for the same reason:
        # in server-based mode, the organization is only known AFTER consent —
        # `list_orgs()` is what discovers it. Its absence is reported at the time of
        # the first call that needs it (actionable message), not at construction.
        self.org_id = org_id
        self.api_domain = api_domain or "https://analyticsapi.zoho.com"
        self.accounts_url = accounts_url or "https://accounts.zoho.com"
        self._cred_key = cred_key(
            self.accounts_url, self.client_id, self.refresh_token)

    # --- Auth ---

    def _get_access_token(self) -> str:
        """Valid access token, refreshed as needed. PROCESS-WIDE cache keyed by
        credential (#233): a new client instance per server call does NOT
        re-refresh if a valid token is already cached (otherwise Zoho rate-limits)."""
        return get_access_token(self.accounts_url, self.client_id,
                                self.client_secret, self.refresh_token,
                                key=self._cred_key)

    def _invalidate_token(self):
        invalidate(self._cred_key)

    # --- HTTP ---

    def _auth_headers(self) -> dict:
        """Authentication only. Only one endpoint settles for it — `list_orgs`, which
        precisely serves to discover the organization we do not know yet."""
        return {"Authorization": f"Zoho-oauthtoken {self._get_access_token()}"}

    def _headers(self) -> dict:
        if not self.org_id:
            raise ValueError(
                "Zoho Analytics Org ID missing: every request carries the "
                "ZANALYTICS-ORGID header. Discover the account's organizations with "
                "`list_orgs()`, then fill in the one that holds your workspaces.")
        return {**self._auth_headers(), "ZANALYTICS-ORGID": self.org_id}

    def _request(self, method: str, url: str, *, parse_json: bool = True,
                 with_org: bool = True, **kwargs) -> Any:
        """Authenticated request with token refresh on 401 and backoff on 429.

        `url` is absolute (the Analytics endpoints mix `/restapi/v2/…` and
        already-complete download URLs returned by the API).

        `with_org=False` omits the organization header — reserved for `list_orgs`, the only
        endpoint that answers without knowing which organization to look in."""
        build = self._headers if with_org else self._auth_headers
        headers = build()
        for attempt in range(3):
            resp = requests.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT, **kwargs)

            if resp.status_code == 401 and attempt == 0:
                self._invalidate_token()
                headers = build()
                continue
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 2)))
                continue

            raise_for_upstream(resp, service="zohoanalytics")

            if not resp.content:
                return {}
            if parse_json:
                return resp.json()
            return resp.text

        raise Exception("Request failed after retries")

    def _v2(self, endpoint: str) -> str:
        return f"{self.api_domain}/restapi/v2/{endpoint}"

    # --- Metadata ---

    def list_orgs(self) -> list[dict]:
        """Analytics organizations visible to this account: `{org_id, name, role}`.

        The ONLY endpoint that does not require `ZANALYTICS-ORGID` — hence its value:
        after an OAuth consent, it lets us fill in the organization instead of
        sending the user to look for an eleven-digit identifier in the
        Zoho interface.

        ⚠️ **An account often sees SEVERAL** (shared workspaces), and the
        response designates no default organization. Beyond a single one, it is
        therefore a CHOICE to have made — not to guess: on the first real account
        tested, two organizations came back, and taking "the first" would have
        picked the wrong one."""
        payload = self._request("GET", self._v2("orgs"), with_org=False)
        orgs = (payload or {}).get("data", {}).get("orgs") or []
        return [{"org_id": str(o.get("orgId")), "name": o.get("orgName"),
                 "role": o.get("role")} for o in orgs if o.get("orgId")]

    def list_workspaces(self) -> dict:
        """List all workspaces accessible to the user (owned + shared)."""
        return self._request("GET", self._v2("workspaces"))

    def list_views(
        self, workspace_id: str, view_types: Optional[list[int]] = None,
    ) -> dict:
        """List views of a workspace. `view_types` filters by Zoho code
        (0 Table, 2 Chart, 3 Pivot, 4 Summary, 6 QueryTable, 7 Dashboard)."""
        params = {}
        if view_types:
            params["CONFIG"] = json.dumps({"viewTypes": view_types})
        return self._request(
            "GET", self._v2(f"workspaces/{workspace_id}/views"), params=params)

    def get_view_details(self, view_id: str, *, with_meta: bool = True) -> dict:
        """Get metadata of one view (columns, type, folder…).

        The v2 API keys a view's detail on the **globally unique** `view_id` —
        the endpoint is `/restapi/v2/views/<view-id>`, **NOT** nested under the
        workspace: a GET on `/workspaces/<ws>/views/<view-id>` returns
        `INVALID_METHOD` (errorCode 8541), that path does not accept GET.
        `with_meta` (CONFIG `withInvolvedMetaInfo`) brings back the detail of columns
        + involved views — otherwise you only get the view header."""
        params = {}
        if with_meta:
            params["CONFIG"] = json.dumps({"withInvolvedMetaInfo": True})
        return self._request("GET", self._v2(f"views/{view_id}"), params=params)

    # --- Data export ---

    def export_view(
        self,
        workspace_id: str,
        view_id: str,
        response_format: str = "json",
        criteria: Optional[str] = None,
        selected_columns: Optional[list[str]] = None,
    ) -> Any:
        """Synchronous export of a view's data.

        `response_format` ∈ csv/json/xml/xls/pdf/html/image. `criteria` = Zoho
        SQL-like filter (e.g. `"Sales" > 500`). Returns the parsed JSON for `json`,
        otherwise the raw text."""
        config: dict[str, Any] = {"responseFormat": response_format}
        if criteria:
            config["criteria"] = criteria
        if selected_columns:
            config["selectedColumns"] = selected_columns
        return self._request(
            "GET",
            self._v2(f"workspaces/{workspace_id}/views/{view_id}/data"),
            params={"CONFIG": json.dumps(config)},
            parse_json=(response_format == "json"),
        )

    def query_sql(
        self,
        workspace_id: str,
        sql_query: str,
        response_format: str = "json",
        poll_interval: float = 1.5,
        max_polls: int = 40,
    ) -> Any:
        """Run a SQL SELECT query on a workspace via the asynchronous export
        flow (create job → poll until `JOB COMPLETED` → download).

        Returns the parsed JSON for `json`, otherwise the raw text. Raises if the job
        fails or does not complete within `max_polls` iterations."""
        created = self._request(
            "GET",
            self._v2(f"bulk/workspaces/{workspace_id}/data"),
            params={"CONFIG": json.dumps(
                {"responseFormat": response_format, "sqlQuery": sql_query})},
        )
        job_id = created.get("data", {}).get("jobId")
        if not job_id:
            raise ValueError(f"Zoho Analytics: no jobId in create-export response: {created}")

        job_url = self._v2(f"bulk/workspaces/{workspace_id}/exportjobs/{job_id}")
        for _ in range(max_polls):
            info = self._request("GET", job_url).get("data", {})
            status = info.get("jobStatus", "")
            if status == "JOB COMPLETED":
                download_url = info.get("downloadUrl")
                if not download_url:
                    raise ValueError(
                        f"Zoho Analytics: job completed without downloadUrl: {info}")
                return self._request(
                    "GET", download_url, parse_json=(response_format == "json"))
            if status in ("JOB FAILED", "JOB REPEATED"):
                raise ValueError(f"Zoho Analytics export job {job_id} failed: {info}")
            time.sleep(poll_interval)

        raise TimeoutError(
            f"Zoho Analytics export job {job_id} not done after {max_polls} polls")
