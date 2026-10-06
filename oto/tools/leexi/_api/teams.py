"""Leexi teams — the structure that carries users and their calls.

This mixin is never instantiated on its own: it is composed into `LeexiClient`, which
provides the transport (`_request`, `_list`). `write_teams` scope for the
writes — it also commits billed licenses, and is granted on the admin side.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class _TeamsMixin:
    """Workspace teams."""

    def list_teams(self, page: Optional[int] = None,
                   items: Optional[int] = None) -> Any:
        """GET /v1/teams — workspace teams. Scope `read_teams`."""
        return self._list("/teams", page, items)

    def get_team(self, uuid: str) -> Any:
        """GET /v1/teams/{uuid} — one team. Scope `read_teams`."""
        return self._request("GET", f"/teams/{uuid}")

    def create_team(self, payload: Dict[str, Any]) -> Any:
        """POST /v1/teams — creates a team. Scope `write_teams`.

        Required: `name`. Optional: `active`. The team inherits the company's
        settings and receives the default email templates. A name already taken returns 409.
        """
        return self._request("POST", "/teams", json=dict(payload))

    def update_team(self, uuid: str, payload: Dict[str, Any]) -> Any:
        """PATCH /v1/teams/{uuid} — updates a team. Scope `write_teams`.

        Fields: `active`, `name`. `active=False` is the way **recommended by
        the vendor** to retire a team that still carries users or
        calls — `delete_team` would refuse it.
        """
        return self._request("PATCH", f"/teams/{uuid}", json=dict(payload))

    def delete_team(self, uuid: str) -> Any:
        """DELETE /v1/teams/{uuid} — deletes a team. Scope `write_teams`.

        ⚠️ Only succeeds on a team with no users or calls; otherwise **422**.
        For all the others, `update_team(uuid, {"active": False})`.
        """
        return self._request("DELETE", f"/teams/{uuid}")
