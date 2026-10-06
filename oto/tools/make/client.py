"""Make (ex-Integromat) REST API v2 client — scenarios + executions.

Make is a workflow automation platform ("scenarios"). The REST API
v2 exposes organizations, teams, scenarios, their execution and their logs.

Auth = **API token** (`Authorization: Token <token>` header) + **base URL** of the
account's zone (Make is regionalized: `https://eu1.make.com`, `https://us1.make.com`,
`https://eu2.make.com`…). The token is created in Make: Profile → API/MCP access →
Add token (scope at least `scenarios:read`/`scenarios:run`).

Both are passed to the constructor.

⚠️ Listing scenarios requires a `team_id` (scenarios belong to a team).
`list_organizations` then `list_teams(organization_id)` let you discover it.

Docs: https://developers.make.com/api-documentation

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class MakeClient:
    """Make client — organizations, teams, scenarios, executions (API v2)."""

    def __init__(self, api_token: Optional[str] = None,
                 base_url: Optional[str] = None):
        """Initialize the client.

        Args:
            api_token: Make API token.
            base_url: Zone URL, e.g. `https://eu1.make.com`. The `/api/v2` suffix is appended.
        """
        self.api_token = require(api_token, "MAKE_API_TOKEN")
        base = require(base_url, "MAKE_BASE_URL").rstrip("/")
        if base.endswith("/api/v2"):
            base = base[: -len("/api/v2")]
        self.base_url = base
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}/api/v2{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        raise_for_upstream(resp, service="make")
        return resp.json() if resp.content else {}

    # --- Discovery (organizations / teams) ----------------------------------

    def list_organizations(self) -> Dict[str, Any]:
        """List the organizations accessible with this token."""
        return self._request("GET", "/organizations")

    def list_teams(self, organization_id: int) -> Dict[str, Any]:
        """List an organization's teams (which own the scenarios)."""
        return self._request("GET", "/teams",
                             params={"organizationId": organization_id})

    # --- Scenarios ----------------------------------------------------------

    def list_scenarios(
        self,
        team_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List a team's scenarios (paginated).

        Args:
            team_id: team identifier (see `list_teams`).
        """
        params: Dict[str, Any] = {
            "teamId": team_id,
            "pg[limit]": min(limit, 100),
            "pg[offset]": offset,
        }
        return self._request("GET", "/scenarios", params=params)

    def get_scenario(self, scenario_id: int) -> Dict[str, Any]:
        """Fetch a scenario (metadata, schedule, state)."""
        return self._request("GET", f"/scenarios/{scenario_id}")

    def get_scenario_blueprint(self, scenario_id: int) -> Dict[str, Any]:
        """Fetch a scenario's blueprint (module structure)."""
        return self._request("GET", f"/scenarios/{scenario_id}/blueprint")

    def run_scenario(
        self,
        scenario_id: int,
        data: Optional[Dict[str, Any]] = None,
        responsive: bool = True,
    ) -> Dict[str, Any]:
        """Trigger a scenario's execution.

        Args:
            data: input payload passed to the scenario (depending on its modules).
            responsive: wait for the execution to finish (True) or return
                immediately (False).
        """
        body: Dict[str, Any] = {"responsive": responsive}
        if data is not None:
            body["data"] = data
        return self._request("POST", f"/scenarios/{scenario_id}/run", json=body)

    # --- Executions / logs --------------------------------------------------

    def list_scenario_logs(
        self,
        scenario_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List a scenario's execution logs (paginated)."""
        params: Dict[str, Any] = {
            "pg[limit]": min(limit, 100),
            "pg[offset]": offset,
        }
        return self._request("GET", f"/scenarios/{scenario_id}/logs",
                             params=params)
