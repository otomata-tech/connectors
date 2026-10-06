"""Zapier AI Actions API client — exposed actions + execution.

Zapier is an automation platform ("Zaps"). Rather than a Zap-management API,
Zapier exposes the **AI Actions API** for agents (`actions.zapier.com`): a
catalogue of **actions** that the user has explicitly exposed (e.g. "create a
Google Sheets row", "send a Slack message"), executable via natural language +
parameters.

Auth = **API key** (`x-api-key` header). The key is created at
https://actions.zapier.com/credentials/ (each key carries the set of actions
exposed by the user).

Key passed to the constructor.

Docs: https://actions.zapier.com/docs/

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class ZapierClient:
    """Zapier AI Actions client — list + execute exposed actions."""

    BASE_URL = "https://actions.zapier.com/api/v1"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: Zapier AI Actions API key.
        """
        self.api_key = require(api_key, "ZAPIER_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}{path}"
        resp = self.session.request(method, url, timeout=60, **kwargs)
        raise_for_upstream(resp, service="zapier")
        return resp.json() if resp.content else {}

    def list_actions(self) -> Dict[str, Any]:
        """List the actions exposed by this key (id, description, params).

        Each action carries an `id` (to pass to `execute_action`) and the list of
        its configurable fields."""
        return self._request("GET", "/exposed/")

    def execute_action(
        self,
        action_id: str,
        instructions: str,
        params: Optional[Dict[str, Any]] = None,
        preview_only: bool = False,
    ) -> Dict[str, Any]:
        """Execute an exposed action.

        Args:
            action_id: action id (see `list_actions`).
            instructions: natural-language directive — Zapier fills the
                fields left in "AI guess" mode from this text.
            params: explicit overrides for the action's fields (take precedence
                over what is inferred from `instructions`).
            preview_only: True = don't run, return what would be done.
        """
        body: Dict[str, Any] = {"instructions": instructions}
        if params:
            body.update(params)
        if preview_only:
            body["preview_only"] = True
        return self._request("POST", f"/exposed/{action_id}/execute/", json=body)

    def execution_log(self, execution_log_id: str) -> Dict[str, Any]:
        """Fetch the detail of one execution (`execution_log_id` returned by
        `execute_action`)."""
        return self._request("GET", f"/execution-log/{execution_log_id}/")
