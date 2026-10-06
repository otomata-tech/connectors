"""Productlane contacts — people, their companies, and blocked senders.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`, `_check_choice`).

⚠️ **Blocking a sender has a lasting effect that is invisible to the sender**: a blocked
address (or a whole domain) can no longer open a thread or write on an
existing thread, and the sender is not told. Blocking a DOMAIN cuts off
an entire organization at once.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import BLOCKED_SENDER_TYPES


class _ContactsMixin:
    """Contacts, company memberships, blocked senders."""

    def list_contacts(self, limit: Optional[int] = None,
                      cursor: Optional[str] = None,
                      email: Optional[str] = None,
                      name_contains: Optional[str] = None,
                      company_id: Optional[str] = None,
                      external_id: Optional[str] = None,
                      created_after: Optional[str] = None,
                      created_before: Optional[str] = None,
                      updated_after: Optional[str] = None,
                      updated_before: Optional[str] = None) -> Any:
        """GET /contacts — workspace contacts. Scope `contacts:read`."""
        return self._list("/contacts", limit, cursor, {
            "email": email, "name_contains": name_contains,
            "company_id": company_id, "external_id": external_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_contact(self, contact_id: str) -> Any:
        """GET /contacts/{id} — one contact. Scope `contacts:read`."""
        return self._request("GET", f"/contacts/{contact_id}")

    def create_contact(self, payload: Dict[str, Any]) -> Any:
        """POST /contacts — create a contact. Scope `contacts:write`.

        Required: `email`. Optional: `name`, `image_url`, `is_subscribed`,
        `external_ids`, `company_id`, `company_name`, `company_external_id`.

        The three `company_*` fields attach the contact to a company:
        by id, by name, or by external identifier — pick one, not all.
        """
        return self._request("POST", "/contacts", json=dict(payload))

    def update_contact(self, contact_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /contacts/{id} — update a contact. Scope `contacts:write`.

        Fields: `external_ids`, `name`, `email`, `image_url`, `is_subscribed`,
        `company_id`, `company_name`, `company_external_id`.

        ⚠️ `is_subscribed=False` **unsubscribes** the contact from changelog
        broadcasts: it is a communication preference, not a mere field.
        """
        return self._request("PATCH", f"/contacts/{contact_id}",
                             json=dict(payload))

    def delete_contact(self, contact_id: str) -> Any:
        """DELETE /contacts/{id} — **soft-delete**. Scope `contacts:write`."""
        return self._request("DELETE", f"/contacts/{contact_id}")

    # --- company memberships -----------------------------------------------

    def list_contact_companies(self, contact_id: str) -> Any:
        """GET /contacts/{id}/companies — its companies, **the primary one first**.

        Scopes `contacts:read` AND `companies:read`: without the second, it is refused.
        """
        return self._request("GET", f"/contacts/{contact_id}/companies")

    def add_contact_to_company(self, contact_id: str,
                               company_id: Optional[str] = None,
                               company_name: Optional[str] = None,
                               company_external_id: Optional[str] = None) -> Any:
        """POST /contacts/{id}/companies — attach a company. Scope `contacts:write`.

        **Idempotent**, and becomes the primary company if the contact had
        none. Designate the company by id, by name or by external id.
        """
        body = {"company_id": company_id, "company_name": company_name,
                "company_external_id": company_external_id}
        body = {k: v for k, v in body.items() if v is not None}
        if not body:
            raise ValueError(
                "designate the company by `company_id`, `company_name` or "
                "`company_external_id`.")
        return self._request("POST", f"/contacts/{contact_id}/companies",
                             json=body)

    def remove_contact_from_company(self, contact_id: str,
                                    company_id: str) -> Any:
        """DELETE /contacts/{id}/companies/{company_id} — remove a membership.

        Scope `contacts:write`. If it was the primary one, another takes over.
        """
        return self._request(
            "DELETE", f"/contacts/{contact_id}/companies/{company_id}")

    # --- what the contact is linked to --------------------------------------

    def list_contact_issues(self, contact_id: str, limit: Optional[int] = None,
                            cursor: Optional[str] = None) -> Any:
        """GET /contacts/{id}/issues — issues linked via the customer needs of its threads.

        Scopes `contacts:read` AND `issues:read`.
        """
        return self._list(f"/contacts/{contact_id}/issues", limit, cursor)

    def list_contact_projects(self, contact_id: str, limit: Optional[int] = None,
                              cursor: Optional[str] = None) -> Any:
        """GET /contacts/{id}/projects — projects linked via the customer needs of its threads.

        Scopes `contacts:read` AND `projects:read`.
        """
        return self._list(f"/contacts/{contact_id}/projects", limit, cursor)

    # --- blocked senders -----------------------------------------------------

    def list_blocked_senders(self, limit: Optional[int] = None,
                             cursor: Optional[str] = None,
                             type: Optional[str] = None) -> Any:
        """GET /contacts/blocked-senders — blocked addresses and domains.

        Scope `contacts:read`. `type` filters on `EMAIL` or `DOMAIN`.
        """
        self._check_choice("type", type, BLOCKED_SENDER_TYPES)
        return self._list("/contacts/blocked-senders", limit, cursor,
                          {"type": type})

    def block_sender(self, type: str, value: str) -> Any:
        """POST /contacts/blocked-senders — block an address or a domain.

        Scope `contacts:write`. `type="EMAIL"` for an address,
        `type="DOMAIN"` for **a whole domain**.

        ⚠️ A blocked sender can no longer open a thread or write on an existing
        thread, **and is not told**. A domain block cuts off an entire
        organization in a single call.
        """
        self._check_choice("type", type, BLOCKED_SENDER_TYPES)
        if not value:
            raise ValueError("`value` is required: the address or domain to block.")
        return self._request("POST", "/contacts/blocked-senders",
                             json={"type": type, "value": value})

    def unblock_sender(self, blocked_id: str) -> Any:
        """DELETE /contacts/blocked-senders/{id} — unblock. Scope `contacts:write`."""
        return self._request("DELETE",
                             f"/contacts/blocked-senders/{blocked_id}")
