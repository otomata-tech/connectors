"""Key identity, lookups, and the organization routes.

Never instantiated on its own: composed into `LumaClient`, which provides the
transport (`_get`, `_post`, `_list`, `_body`) and the checks.

`get_self()` is the connector's probe: any valid key can call it, it does not
depend on what the calendar holds, and a refused key answers 401.

The organization routes need an ORGANIZATION key; a calendar key is refused
there by the API.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import CALENDAR_LAUNCH_STATUSES, IMAGE_CONTENT_TYPES, SORT_DIRECTIONS


class _MetaMixin:
    """Identity and lookups."""

    def get_self(self) -> Any:
        """GET /v1/users/get-self — the user that owns the key."""
        return self._get("/v1/users/get-self")

    def lookup_entity(self, slug: str) -> Any:
        """GET /v1/entities/lookup — an event or a calendar from its slug, its
        Luma URL, or a permanent path (`event/evt-…`, `e/evt-…`,
        `calendar/cal-…`). Query string and fragment are ignored."""
        return self._get("/v1/entities/lookup", {"slug": self._need(slug, "slug")})

    def search_places(self, query: str) -> Any:
        """GET /v1/places/search — up to 5 Google Maps candidates. Pass the
        chosen `place_id` as `geo_address_json: {type: "google", place_id}`
        when creating or updating an event."""
        return self._get("/v1/places/search", {"query": self._need(query, "query")})

    def search_images(self, query: str, *, limit: Optional[int] = None,
                      cursor: Optional[str] = None) -> Any:
        """GET /v1/images/search — Luma's gallery of stock cover images. The
        chosen `image_url` is usable as `cover_url` without uploading it."""
        return self._list("/v1/images/search", limit, cursor,
                          {"query": self._need(query, "query")})

    def create_upload_url(self, content_type: Optional[str] = None) -> Any:
        """POST /v1/images/create-upload-url — a signed URL to upload an image
        (jpeg or png); the returned file URL then serves as `cover_url`."""
        self._check_choice("content_type", content_type, IMAGE_CONTENT_TYPES)
        return self._post("/v1/images/create-upload-url",
                          self._body({}, optional={"content_type": content_type}))


class _OrganizationMixin:
    """Organization routes (organization key only)."""

    def list_organization_admins(self) -> Any:
        """GET /v1/organizations/admins/list — admins of the organization."""
        return self._get("/v1/organizations/admins/list")

    def list_organization_calendars(self, *, limit: Optional[int] = None,
                                    cursor: Optional[str] = None) -> Any:
        """GET /v1/organizations/calendars/list — calendars of the organization."""
        return self._list("/v1/organizations/calendars/list", limit, cursor)

    def list_organization_events(self, *, after: Optional[str] = None,
                                 before: Optional[str] = None,
                                 sort_direction: Optional[str] = None,
                                 limit: Optional[int] = None,
                                 cursor: Optional[str] = None) -> Any:
        """GET /v1/organizations/events/list — events across every calendar of
        the organization, deduplicated. Entries omit the description: read it
        with `get_event`."""
        self._check_choice("sort_direction", sort_direction, SORT_DIRECTIONS)
        return self._list("/v1/organizations/events/list", limit, cursor,
                          {"after": after, "before": before,
                           "sort_direction": sort_direction})

    def create_organization_calendar(self, name: str, *,
                                     launch_status: Optional[str] = None,
                                     fields: Optional[Dict[str, Any]] = None) -> Any:
        """POST /v2/organizations/calendars/create — a new calendar, managed by
        the key's owner. `fields`: slug, description, avatar_url, tint_color,
        website, social handles, location."""
        self._check_choice("launch_status", launch_status, CALENDAR_LAUNCH_STATUSES)
        return self._post("/v2/organizations/calendars/create", self._body(
            {"name": self._need(name, "name")}, fields,
            {"launch_status": launch_status}))

    def transfer_event_calendar(self, event_id: str, calendar_id: str) -> Any:
        """POST /v1/organizations/events/transfer-calendar — move an event to
        another calendar of the same organization (its MANAGING calendar
        changes)."""
        return self._post("/v1/organizations/events/transfer-calendar", {
            "event_id": self._need(event_id, "event_id"),
            "calendar_id": self._need(calendar_id, "calendar_id")})
