"""The workspace: account, users, personas, territories, Prospect Hub accounts
and mailboxes."""
from __future__ import annotations

from typing import Any, List, Optional

from .shapes import page_params, path_id


class WorkspaceMixin:
    """Needs `_request` (see `AmplemarketClient`)."""

    def get_account_info(self) -> Any:
        """GET /account-info — the workspace's id and name."""
        return self._request("GET", "/account-info")

    def list_users(self, status: Optional[str] = None, role: Optional[str] = None,
                   email: Optional[str] = None, page_size: Optional[int] = None,
                   page_after: Optional[str] = None,
                   page_before: Optional[str] = None) -> Any:
        """GET /users — the workspace's users (id, email, name, role, status)."""
        return self._request("GET", "/users", params={
            "status": status, "role": role, "email": email,
            **page_params(page_size, page_after, page_before)})

    def list_personas(self, name: Optional[str] = None,
                      enabled: Optional[bool] = None,
                      page_size: Optional[int] = None,
                      page_after: Optional[str] = None) -> Any:
        """GET /personas — the buyer personas (page_size <= 20)."""
        return self._request("GET", "/personas", params={
            "name": name, "enabled": enabled, **page_params(page_size, page_after)})

    def list_territories(self, name: Optional[str] = None,
                         page_size: Optional[int] = None,
                         page_after: Optional[str] = None) -> Any:
        """GET /territories — the sales territories (page_size <= 20)."""
        return self._request("GET", "/territories", params={
            "name": name, **page_params(page_size, page_after)})

    # --- Prospect Hub accounts ------------------------------------------------

    def list_accounts(self, name: Optional[str] = None, domain: Optional[str] = None,
                      owner_email: Optional[str] = None,
                      tags: Optional[List[str]] = None,
                      page_size: Optional[int] = None,
                      page_after: Optional[str] = None) -> Any:
        """GET /accounts — Prospect Hub accounts (companies), page_size <= 50.
        `name` matches partially, `domain` and `owner_email` exactly."""
        return self._request("GET", "/accounts", params={
            "name": name, "domain": domain, "owner_email": owner_email,
            "tags[]": tags, **page_params(page_size, page_after)})

    def get_account(self, account_id: str) -> Any:
        """GET /accounts/{id} — one Prospect Hub account, with its owner, tags
        and AI insights."""
        return self._request("GET", f"/accounts/{path_id('account_id', account_id)}")

    # --- mailboxes --------------------------------------------------------------

    def list_mailboxes(self, status: Optional[str] = None,
                       email_provider: Optional[str] = None,
                       user_email: Optional[str] = None,
                       page_size: Optional[int] = None,
                       page_after: Optional[str] = None,
                       page_before: Optional[str] = None) -> Any:
        """GET /mailboxes — the sending mailboxes (page_size <= 20). `status`:
        active, inactive, needs_reconnection; `email_provider`: google,
        outlook, other, other_mixed."""
        return self._request("GET", "/mailboxes", params={
            "status": status, "email_provider": email_provider,
            "user_email": user_email,
            **page_params(page_size, page_after, page_before)})

    def update_mailbox_daily_limit(self, mailbox_id: str, daily_email_limit: int) -> Any:
        """PATCH /mailboxes/{id} — set the mailbox's daily email limit.
        Amplemarket accepts one change per mailbox per minute and per hour."""
        if not isinstance(daily_email_limit, int) or isinstance(daily_email_limit, bool) \
                or daily_email_limit < 0:
            raise ValueError("daily_email_limit must be a non-negative integer")
        return self._request(
            "PATCH", f"/mailboxes/{path_id('mailbox_id', mailbox_id)}",
            body={"daily_email_limit": daily_email_limit})
