"""The calendar itself, its admins, event tags, contacts and contact tags.

Composed into `LumaClient`, which provides the transport and the checks.

Contacts are the calendar's audience (people who registered, subscribed or
were imported). Three moderation gestures, NOT interchangeable:

- `remove_contact` — stops invites and newsletters; they can follow again on
  their own later;
- `block_contact` — they can no longer follow the calendar nor join its
  events, until restored;
- `restore_contact` — back to active, from either state.

A tag is addressed by its id or its NAME (`apply`/`unapply`); applying a
contact tag never creates contacts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..const import (CALENDAR_LAUNCH_STATUSES, CONTACT_SORT_COLUMNS,
                     MEMBERSHIP_LIST_STATUSES, SORT_DIRECTIONS, TAG_COLORS)


class _CalendarMixin:
    """The calendar, its admins and its event tags."""

    def get_calendar(self) -> Any:
        """GET /v1/calendars/get — the calendar the key (or `calendar_id`)
        designates."""
        return self._get("/v1/calendars/get")

    def update_calendar(self, calendar_id: str, fields: Dict[str, Any]) -> Any:
        """POST /v1/calendars/update — only the given keys change: name,
        slug, description, avatar_url, tint_color, launch_status, website,
        social handles, location. A null or empty website/handle clears it."""
        self._need(fields, "fields")
        self._check_choice("fields.launch_status", fields.get("launch_status"),
                           CALENDAR_LAUNCH_STATUSES)
        return self._post("/v1/calendars/update", self._body(
            {"calendar_id": self._need(calendar_id, "calendar_id")}, fields))

    def list_calendar_admins(self) -> Any:
        """GET /v1/calendars/admins/list — admins of the calendar."""
        return self._get("/v1/calendars/admins/list")

    def add_calendar_admins(self, emails: List[str]) -> Any:
        """POST /v1/calendars/admins/add — existing admins are skipped; adds
        that would need more paid Luma Plus seats are refused."""
        return self._post("/v1/calendars/admins/add",
                          {"emails": list(self._need(emails, "emails"))})

    # --- event tags ---------------------------------------------------------

    def list_event_tags(self) -> Any:
        """GET /v1/calendars/event-tags/list."""
        return self._get("/v1/calendars/event-tags/list")

    def create_event_tag(self, name: str, *, color: Optional[str] = None) -> Any:
        """POST /v1/calendars/event-tags/create."""
        self._check_choice("color", color, TAG_COLORS)
        return self._post("/v1/calendars/event-tags/create", self._body(
            {"name": self._need(name, "name")}, optional={"color": color}))

    def update_event_tag(self, tag_id: str, *, name: Optional[str] = None,
                         color: Optional[str] = None) -> Any:
        """POST /v1/calendars/event-tags/update."""
        self._check_choice("color", color, TAG_COLORS)
        return self._post("/v1/calendars/event-tags/update", self._body(
            {"tag_id": self._need(tag_id, "tag_id")},
            optional={"name": name, "color": color}))

    def delete_event_tag(self, tag_id: str) -> Any:
        """POST /v1/calendars/event-tags/delete."""
        return self._post("/v1/calendars/event-tags/delete",
                          {"tag_id": self._need(tag_id, "tag_id")})

    def apply_event_tag(self, tag: str, event_ids: List[str]) -> Any:
        """POST /v1/calendars/event-tags/apply — `tag`: id or name."""
        return self._post("/v1/calendars/event-tags/apply", {
            "tag": self._need(tag, "tag"),
            "event_ids": list(self._need(event_ids, "event_ids"))})

    def unapply_event_tag(self, tag: str, event_ids: List[str]) -> Any:
        """POST /v1/calendars/event-tags/unapply — `tag`: id or name."""
        return self._post("/v1/calendars/event-tags/unapply", {
            "tag": self._need(tag, "tag"),
            "event_ids": list(self._need(event_ids, "event_ids"))})


class _ContactsMixin:
    """The calendar's audience and its tags."""

    def list_contacts(self, *, query: Optional[str] = None,
                      tags: Optional[List[str]] = None,
                      membership_tier_id: Optional[str] = None,
                      membership_status: Optional[str] = None,
                      sort_column: Optional[str] = None,
                      sort_direction: Optional[str] = None,
                      limit: Optional[int] = None,
                      cursor: Optional[str] = None) -> Any:
        """GET /v1/calendars/contacts/list — `query` searches names and
        emails; `tags` (names or ids) keeps contacts holding ANY of them."""
        self._check_choice("membership_status", membership_status,
                           MEMBERSHIP_LIST_STATUSES)
        self._check_choice("sort_column", sort_column, CONTACT_SORT_COLUMNS)
        self._check_choice("sort_direction", sort_direction, SORT_DIRECTIONS)
        return self._list("/v1/calendars/contacts/list", limit, cursor, {
            "query": query, "tags": list(tags) if tags else None,
            "calendar_membership_tier_id": membership_tier_id,
            "membership_status": membership_status,
            "sort_column": sort_column, "sort_direction": sort_direction})

    def import_contacts(self, contacts: List[Dict[str, Any]], *,
                        tags: Optional[List[str]] = None) -> Any:
        """POST /v1/calendars/contacts/import — `[{email, name?}]`, tagged
        with `tags` when given. Imported contacts can then be invited and
        receive the calendar's newsletters."""
        self._need(contacts, "contacts")
        for i, c in enumerate(contacts):
            if not isinstance(c, dict) or not c.get("email"):
                raise ValueError(f"`contacts[{i}]` must carry an `email`.")
        return self._post("/v1/calendars/contacts/import", self._body(
            {"contacts": contacts}, optional={"tags": tags or None}))

    def _moderate(self, path: str, contact_id: Optional[str],
                  email: Optional[str]) -> Any:
        if not contact_id and not email:
            raise ValueError("`contact_id` or `email` is required.")
        return self._post(path, self._body(
            {}, optional={"contact_id": contact_id, "email": email}))

    def block_contact(self, *, contact_id: Optional[str] = None,
                      email: Optional[str] = None) -> Any:
        """POST /v1/calendars/contacts/block — no following, no joining."""
        return self._moderate("/v1/calendars/contacts/block", contact_id, email)

    def remove_contact(self, *, contact_id: Optional[str] = None,
                       email: Optional[str] = None) -> Any:
        """POST /v1/calendars/contacts/remove — no more invites/newsletters."""
        return self._moderate("/v1/calendars/contacts/remove", contact_id, email)

    def restore_contact(self, *, contact_id: Optional[str] = None,
                        email: Optional[str] = None) -> Any:
        """POST /v1/calendars/contacts/restore — back to active."""
        return self._moderate("/v1/calendars/contacts/restore", contact_id, email)

    # --- contact tags -------------------------------------------------------

    def list_contact_tags(self) -> Any:
        """GET /v1/calendars/contact-tags/list."""
        return self._get("/v1/calendars/contact-tags/list")

    def create_contact_tag(self, name: str, *, color: Optional[str] = None) -> Any:
        """POST /v1/calendars/contact-tags/create."""
        self._check_choice("color", color, TAG_COLORS)
        return self._post("/v1/calendars/contact-tags/create", self._body(
            {"name": self._need(name, "name")}, optional={"color": color}))

    def update_contact_tag(self, tag_id: str, *, name: Optional[str] = None,
                           color: Optional[str] = None) -> Any:
        """POST /v1/calendars/contact-tags/update."""
        self._check_choice("color", color, TAG_COLORS)
        return self._post("/v1/calendars/contact-tags/update", self._body(
            {"tag_id": self._need(tag_id, "tag_id")},
            optional={"name": name, "color": color}))

    def delete_contact_tag(self, tag_id: str) -> Any:
        """POST /v1/calendars/contact-tags/delete."""
        return self._post("/v1/calendars/contact-tags/delete",
                          {"tag_id": self._need(tag_id, "tag_id")})

    def _tag_people(self, path: str, tag: str, user_ids: Optional[List[str]],
                    emails: Optional[List[str]]) -> Any:
        if not user_ids and not emails:
            raise ValueError("`user_ids` or `emails` is required.")
        return self._post(path, self._body(
            {"tag": self._need(tag, "tag")},
            optional={"user_ids": user_ids or None, "emails": emails or None}))

    def apply_contact_tag(self, tag: str, *, user_ids: Optional[List[str]] = None,
                          emails: Optional[List[str]] = None) -> Any:
        """POST /v1/calendars/contact-tags/apply — existing contacts only."""
        return self._tag_people("/v1/calendars/contact-tags/apply", tag,
                                user_ids, emails)

    def unapply_contact_tag(self, tag: str, *,
                            user_ids: Optional[List[str]] = None,
                            emails: Optional[List[str]] = None) -> Any:
        """POST /v1/calendars/contact-tags/unapply."""
        return self._tag_people("/v1/calendars/contact-tags/unapply", tag,
                                user_ids, emails)
