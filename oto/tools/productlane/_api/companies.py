"""Productlane companies — and their pairing with Linear "customers".

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`).

⚠️ **The Linear mirror is ASYNCHRONOUS**: creating a company provisions a Linear
customer once a domain is set, an identity update propagates there
later, and a deletion deletes the customer there afterwards. An immediate
read on the Linear side may therefore show nothing without anything having
failed — it is a delay, not an outage.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class _CompaniesMixin:
    """Companies."""

    def list_companies(self, limit: Optional[int] = None,
                       cursor: Optional[str] = None,
                       external_id: Optional[str] = None,
                       domain: Optional[str] = None,
                       name_contains: Optional[str] = None,
                       status_id: Optional[str] = None,
                       tier_id: Optional[str] = None,
                       size_gte: Optional[Any] = None,
                       size_lte: Optional[Any] = None,
                       revenue_gte: Optional[Any] = None,
                       revenue_lte: Optional[Any] = None,
                       created_after: Optional[str] = None,
                       created_before: Optional[str] = None,
                       updated_after: Optional[str] = None,
                       updated_before: Optional[str] = None) -> Any:
        """GET /companies — workspace companies. Scope `companies:read`.

        `size_*` and `revenue_*` are inclusive bounds (`gte`/`lte`), and
        `status_id`/`tier_id` refer to Linear options (see
        `linear_customer_options`).
        """
        return self._list("/companies", limit, cursor, {
            "external_id": external_id, "domain": domain,
            "name_contains": name_contains, "status_id": status_id,
            "tier_id": tier_id, "size_gte": size_gte, "size_lte": size_lte,
            "revenue_gte": revenue_gte, "revenue_lte": revenue_lte,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_company(self, company_id: str) -> Any:
        """GET /companies/{id} — one company. Scope `companies:read`."""
        return self._request("GET", f"/companies/{company_id}")

    def create_company(self, payload: Dict[str, Any]) -> Any:
        """POST /companies — create a company. Scope `companies:write`.

        Required: `name`. Optional: `logo_url`, `domains`, `size`, `revenue`,
        `external_ids`, `status_id`, `tier_id`, `owner_id`.

        The Linear customer is provisioned **asynchronously**, and only
        once a domain is set.
        """
        return self._request("POST", "/companies", json=dict(payload))

    def update_company(self, company_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /companies/{id} — update a company. Scope `companies:write`.

        Fields: `name`, `logo_url`, `domains`, `size`, `revenue`,
        `external_ids`, `status_id`, `tier_id`, `owner_id`. Identity fields
        are pushed to Linear **asynchronously**.
        """
        return self._request("PATCH", f"/companies/{company_id}",
                             json=dict(payload))

    def delete_company(self, company_id: str) -> Any:
        """DELETE /companies/{id} — **soft-delete**. Scope `companies:write`.

        The Linear customer is deleted asynchronously, and threads that
        lose their company link are reindexed afterwards.
        """
        return self._request("DELETE", f"/companies/{company_id}")

    def merge_company(self, company_id: str, source_id: str) -> Any:
        """POST /companies/{id}/merge — merge `source_id` INTO `company_id`.

        Scope `companies:write`.

        ⚠️ **Irreversible, and direction matters**: the company in the PATH survives,
        the one in `source_id` is deleted. Its threads, contacts and votes are
        moved to the survivor, whose empty properties are filled in
        from the source's (properties already filled do not change).
        If both have a Linear customer, they are merged too.
        """
        if not source_id:
            raise ValueError(
                "`source_id` is required: it is the ABSORBED company (the one in "
                "the path survives).")
        if source_id == company_id:
            raise ValueError(
                "cannot merge a company with itself: `source_id` must "
                "differ from the path company.")
        return self._request("POST", f"/companies/{company_id}/merge",
                             json={"source_id": source_id})

    def linear_customer_options(self, team_id: Optional[str] = None) -> Any:
        """GET /companies/linear-options — available Linear statuses and tiers.

        Returns `null` if Linear is not connected — so it is not an error,
        but the answer to "does this workspace have Linear?". Used to fill
        `status_id` / `tier_id`.
        """
        return self._request("GET", "/companies/linear-options",
                             params={"team_id": team_id})
