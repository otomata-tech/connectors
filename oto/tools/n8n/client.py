"""n8n public REST API client — workflows + executions.

n8n is a workflow automation platform (open source, self-hosted
OR n8n Cloud). The **public API** exposes workflows, their executions,
credentials and tags.

Auth = **API key** (`X-N8N-API-KEY` header) + the instance's **base URL** (self-hosting
requires its own URL — n8n Cloud: `https://<sub>.app.n8n.cloud`).
The key is created in n8n: Settings → n8n API → Create an API key.

Both passed to the constructor.

Docs: https://docs.n8n.io/api/

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require


class N8nClient:
    """n8n client — workflows, executions, tags (public API v1)."""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: n8n API key.
            base_url: instance URL, e.g. `https://acme.app.n8n.cloud`.
                The `/api/v1` suffix is added.
        """
        self.api_key = require(api_key, "N8N_API_KEY")
        base = require(base_url, "N8N_BASE_URL").rstrip("/")
        # Tolerate being passed the URL already carrying /api/v1.
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        self.base_url = base
        self.session = requests.Session()
        self.session.headers.update({
            "X-N8N-API-KEY": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise Exception(f"n8n HTTP {resp.status_code}: {body}")
        return resp.json() if resp.content else {}

    # --- Workflows ----------------------------------------------------------

    def list_workflows(
        self,
        limit: int = 50,
        active: Optional[bool] = None,
        tags: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List workflows (paginated via `nextCursor`).

        Args:
            active: keep only active/inactive workflows.
            tags: comma-separated list of tags.
            cursor: pagination cursor (`nextCursor` from the previous page).
        """
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if active is not None:
            params["active"] = str(active).lower()
        if tags:
            params["tags"] = tags
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/workflows", params=params)

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Fetch a workflow (nodes, connections, settings)."""
        return self._request("GET", f"/workflows/{workflow_id}")

    def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Activate a workflow (triggers/cron started)."""
        return self._request("POST", f"/workflows/{workflow_id}/activate")

    def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Deactivate a workflow."""
        return self._request("POST", f"/workflows/{workflow_id}/deactivate")

    # --- Executions ---------------------------------------------------------

    def list_executions(
        self,
        limit: int = 50,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List executions (paginated).

        Args:
            workflow_id: filter by workflow.
            status: `success` | `error` | `waiting`.
            cursor: pagination cursor.
        """
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/executions", params=params)

    def get_execution(self, execution_id: int,
                      include_data: bool = False) -> Dict[str, Any]:
        """Fetch an execution. `include_data` includes the detailed data
        of the nodes (voluminous)."""
        params = {"includeData": "true"} if include_data else None
        return self._request("GET", f"/executions/{execution_id}", params=params)

    # --- Tags ---------------------------------------------------------------

    def list_tags(self, limit: int = 50,
                  cursor: Optional[str] = None) -> Dict[str, Any]:
        """List tags."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/tags", params=params)
