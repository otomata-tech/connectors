"""Campaigns and flows, read only: nothing here sends, creates or edits one."""

from __future__ import annotations

import re
from typing import List, Optional

from .shapes import path_id

_CHANNEL = re.compile(
    r"equals\(messages\.channel,\s*['\"](email|sms|mobile_push)['\"]\)")

_CAMPAIGN_NOT_FOUND = (
    "campaign_not_found",
    "No campaign with this id in this Klaviyo account: read the ids from the "
    "list of campaigns.")
_FLOW_NOT_FOUND = (
    "flow_not_found",
    "No flow with this id in this Klaviyo account: read the ids from the "
    "list of flows.")


class CatalogMixin:
    """Needs `_call` and `_list` (see `KlaviyoClient`)."""

    def list_campaigns(self, filter: str, sort: Optional[str] = None,
                       page_size: Optional[int] = None,
                       page_cursor: Optional[str] = None,
                       include: Optional[List[str]] = None, *,
                       all_pages: bool = False, max_pages: int = 10) -> dict:
        """The campaigns of one channel: `filter` must hold
        `equals(messages.channel,'email')` ('sms', 'mobile_push'), further
        clauses after a comma. Scope campaigns:read."""
        if not isinstance(filter, str) or not _CHANNEL.search(filter):
            raise ValueError(
                "list_campaigns needs the channel in filter: "
                "equals(messages.channel,'email'), 'sms' or 'mobile_push'.")
        return self._list("/campaigns", "list_campaigns", {
            "filter": filter, "sort": sort, "page[size]": page_size,
            "page[cursor]": page_cursor, "include": include,
        }, all_pages=all_pages, max_pages=max_pages)

    def get_campaign(self, campaign_id: str,
                     include: Optional[List[str]] = None) -> dict:
        """One campaign by id; `include`: campaign-messages, tags. Scope
        campaigns:read."""
        return self._call(
            "GET", f"/campaigns/{path_id('campaign_id', campaign_id)}",
            "get_campaign", params={"include": include},
            refusals={404: _CAMPAIGN_NOT_FOUND})

    def list_flows(self, filter: Optional[str] = None,
                   sort: Optional[str] = None,
                   page_size: Optional[int] = None,
                   page_cursor: Optional[str] = None, *,
                   all_pages: bool = False, max_pages: int = 10) -> dict:
        """List the flows (page_size <= 50). Scope flows:read; 3/s, 60/min."""
        return self._list("/flows", "list_flows", {
            "filter": filter, "sort": sort, "page[size]": page_size,
            "page[cursor]": page_cursor,
        }, all_pages=all_pages, max_pages=max_pages)

    def get_flow(self, flow_id: str,
                 additional_fields: Optional[List[str]] = None) -> dict:
        """One flow by id; `additional_fields=["definition"]` adds its full
        definition. Scope flows:read."""
        return self._call(
            "GET", f"/flows/{path_id('flow_id', flow_id)}", "get_flow",
            params={"additional-fields[flow]": additional_fields},
            refusals={404: _FLOW_NOT_FOUND})
