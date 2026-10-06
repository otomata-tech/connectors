"""Leexi users — and the licenses they consume.

This mixin is never instantiated on its own: it is composed into `LeexiClient`, which
provides the transport (`_request`, `_list`).

⚠️ The three writes here require the `write_users` scope, **which commits billed
licenses** — a Leexi admin must grant it explicitly, a new key
does not have it. The safeguard sits with the vendor; this client does not get around it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class _UsersMixin:
    """Workspace users."""

    def list_users(self, page: Optional[int] = None,
                   items: Optional[int] = None) -> Any:
        """GET /v1/users — workspace users. Scope `read_users`."""
        return self._list("/users", page, items)

    def get_user(self, uuid: str) -> Any:
        """GET /v1/users/{uuid} — one user. Scope `read_users`."""
        return self._request("GET", f"/users/{uuid}")

    def create_user(self, payload: Dict[str, Any]) -> Any:
        """POST /v1/users — creates a user. Scope `write_users`.

        Required: `email`, `name`, `team_uuid` (see `list_teams`). Optional:
        `active`, `license`, `roles` (list), `send_welcome_email`.

        ⚠️ **Consumes a billed license**, and the user receives a welcome email
        unless `send_welcome_email=False`. An email already taken returns 409.
        """
        return self._request("POST", "/users", json=dict(payload))

    def update_user(self, uuid: str, payload: Dict[str, Any]) -> Any:
        """PATCH /v1/users/{uuid} — updates a user. Scope `write_users`.

        Fields: `active`, `email`, `license`, `name`, `roles`, `team_uuid`.
        `active=True` **reactivates** a deactivated user, which takes back a
        license — it is a billing write, just like creation.
        """
        return self._request("PATCH", f"/users/{uuid}", json=dict(payload))

    def deactivate_user(self, uuid: str) -> Any:
        """DELETE /v1/users/{uuid} — **deactivates** (does not delete). Scope `write_users`.

        Calls and history are kept, sessions revoked, and the
        license stops being consumed. Reactivate with `update_user(active=True)`.
        The HTTP verb says « delete », the effect is a deactivation: it is the name
        of this method that is accurate, and deliberately so.
        """
        return self._request("DELETE", f"/users/{uuid}")
