"""Minari API client — phone prospecting: transcribed call log,
contact lists, custom fields, team analytics.

Minari (minari.ai) is an outbound call dialer: the team dials, Minari
records, transcribes, summarizes and detects objections. The public v1 API
(`https://api.minari.ai/v1`) uses Bearer auth; the key is created in
**Settings → API & webhook** and applies to the WHOLE company (not per person).

**Written from the contract, NOT verified live** (2026-08-31). Everything here is
derived from the published OpenAPI 3.1 (`https://api.minari.ai/docs/openapi.json`)
and the vendor's LLM usage guide. No probe was run against a real account: the
four points below are therefore readings of the contract, not measurements — to
be confirmed with the first connected account.

**(1) The envelope is NOT uniform, and `data` is not the whole response.** The contract
states "every response is wrapped in `data`", but `has_more` / `next_url`
(pagination) and `period` (resolved analytics window) are **siblings** of
`data`, not children. A client that unwrapped `data` would silently lose
pagination — so **all** methods here return the ENTIRE envelope, as is.
Unwrapping is the caller's job, since the caller knows whether it wants the next page.

**(2) The scope of lists is not the scope of calls.** The
lists/contacts endpoints see ONLY the **CSV import** source: an account whose
contacts come from HubSpot or Salesforce has perfectly real lists that these endpoints
never return. The calls and analytics endpoints, on the other hand, cover **all**
sources. The trap is that an empty `GET /lists` reads as "no lists" when it
should read "no CSV lists"; `analytics_lists()` is the all-sources view.

**(3) There is no recording URL in a call record.** `CallSummary`
carries `public_call_link` (the shareable page) but no `recording_url` — that
field only exists in the webhook payload. The only access to the audio is
`GET /calls/{id}/recording`, which **streams an MP3**. Hence `call_recording_status()`
below: it probes availability without ever pulling the audio body.

**(4) 60 requests/minute PER COMPANY**, not per key or per user. Two
automations running under the same key therefore share the same budget.
The 429 surfaces with the reset seconds read from `RateLimit-Reset`,
otherwise the caller has no way to know how long to wait.

This module invents no capability: Minari does not expose call triggering,
contact editing, or user management. What is missing here is missing from the API.

Requires: requests
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, urlparse

import requests

from ..common.credentials import require
from ..common import UpstreamHTTPError, raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read)
_BASE_URL = "https://api.minari.ai/v1"
#: Prefix read by the recording probe — enough to recognize
#: JSON, never enough to pull an MP3.
_PROBE_PREFIX_BYTES = 8192

#: Contact cap, the same per request and per list (Minari contract).
MAX_CONTACTS_PER_REQUEST = 1500
MAX_CONTACTS_PER_LIST = 1500

#: The only conversation thresholds accepted by the analytics endpoints.
CONVERSATION_THRESHOLDS = (0, 30, 60, 90, 120)

#: Counting windows of `analytics_lists`.
LIST_PERIODS = ("day", "week", "month", "all")

#: Values accepted by the `status` FILTER of `list_calls` — it has NINE.
#: ⚠️ Contract asymmetry: the `status` of a call row only takes the first
#: eight (`CALL_STATUSES_RETURNED`). `meeting-booked` is a search
#: criterion, not a returned state — on the row side, it is the boolean `meeting_booked`.
#: Copying the RESPONSE enum into the filter costs the only way to
#: ask for "the calls that produced a meeting" without sweeping the log.
CALL_STATUSES = (
    "connected", "missed", "voicemail", "left-voicemail",
    "canceled", "busy", "failed", "no-answer", "meeting-booked",
)
CALL_STATUSES_RETURNED = CALL_STATUSES[:-1]

#: A contact must carry at least one of these fields, otherwise Minari rejects it.
_CONTACT_IDENTIFYING_FIELDS = ("firstName", "lastName", "email")


def _id(value: Any) -> str:
    """Escape an identifier before pasting it into a URL path.

    These ids come from an agent. Unescaped, a `call_id` containing `?` or `#`
    adds parameters to the request or truncates the path: the server then answers
    a DIFFERENT question than the one asked. `safe=""` lets `/` be escaped
    too — an id never has a segment.
    """
    return quote(str(value), safe="")


class MinariClient:
    """Minari client (public v1 API), Bearer auth, company-level key."""

    def __init__(self, api_key: Optional[str] = None, *,
                 base_url: Optional[str] = None):
        """
        Args:
            api_key: Minari API key, created
                in Settings → API & webhook. It carries the rights of the
                ENTIRE company, not of a single user.
            base_url: host override, for a test or a dedicated
                environment. Defaults to `https://api.minari.ai/v1`.
        """
        self.api_key = require(api_key, "MINARI_API_KEY")
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.session.headers["Accept"] = "application/json"

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json_body: Any = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self.session.request(
            method, f"{self.base_url}{path}",
            params=clean or None, json=json_body, timeout=_HTTP_TIMEOUT)
        self._raise(resp)
        return self._body(resp)

    @staticmethod
    def _body(resp: Any) -> Any:
        """The response JSON — an unreadable body is an UPSTREAM fault.

        `resp.json()` raises `json.JSONDecodeError`, a subclass of `ValueError`:
        left as is, it gets confused downstream with this module's validation
        `ValueError`s, and the caller then blames the user's arguments
        for an outage of the remote server.
        """
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as e:
            raise UpstreamHTTPError(
                resp.status_code,
                {"detail": f"unreadable Minari response (JSON expected): {e}",
                 "body": resp.text[:500]},
                service="minari") from e

    def _raise(self, resp: Any) -> None:
        """Like `raise_for_upstream`, but a 429 carries its wait time.

        Without `RateLimit-Reset` in the message, a caller hit by a 429 can
        only guess how long to wait — and guesses wrong.
        """
        if resp.status_code == 429:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            reset = resp.headers.get("RateLimit-Reset")
            detail = (f"60 requests/minute limit (per company) reached; "
                      f"resets in {reset} s" if reset else
                      "60 requests/minute limit (per company) reached")
            raise UpstreamHTTPError(429, {"detail": detail, "body": body},
                                    service="minari")
        raise_for_upstream(resp, service="minari")

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def next_page(self, next_url: str) -> Any:
        """Follow the `next_url` of a paginated response — it is an ABSOLUTE URL.

        The URL is checked against the configured host before being followed:
        following a URL returned by upstream without validating it would turn a
        third-party-controlled response into an arbitrary outbound request (SSRF), with our
        `Authorization` header on it.
        """
        parsed = urlparse(next_url)
        expected = urlparse(self.base_url)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            raise ValueError(
                f"`next_url` points outside the configured host "
                f"({parsed.scheme}://{parsed.netloc} ≠ {self.base_url}) — not followed.")
        # `allow_redirects=False`: without it the guard above is worthless —
        # requests follows redirects by default, so a conforming `next_url`
        # that answers `302 → elsewhere` would carry our `Authorization` there. An
        # upstream that redirects its own pagination link is already abnormal.
        resp = self.session.get(next_url, timeout=_HTTP_TIMEOUT,
                                allow_redirects=False)
        if 300 <= resp.status_code < 400:
            raise ValueError(
                f"`next_url` redirects (HTTP {resp.status_code} to "
                f"{resp.headers.get('Location')!r}) — not followed: the target of a "
                "redirect is no longer the one that was validated.")
        self._raise(resp)
        return self._body(resp)

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _check_contacts(contacts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Refuse here what Minari would refuse, but naming the culprit.

        A batch of 1500 contacts rejected wholesale for "at least one of firstName,
        lastName or email" does not say WHICH contact is at fault: the index is
        the only useful information for fixing an import.
        """
        items = list(contacts or [])
        if not items:
            raise ValueError("`contacts` is empty — Minari requires at least one contact.")
        if len(items) > MAX_CONTACTS_PER_REQUEST:
            raise ValueError(
                f"{len(items)} contacts for a cap of "
                f"{MAX_CONTACTS_PER_REQUEST} per request and "
                f"{MAX_CONTACTS_PER_LIST} per list — split the import into batches.")
        for i, c in enumerate(items):
            if not isinstance(c, dict):
                raise ValueError(f"contact #{i}: expected an object, got {type(c).__name__}.")
            if not any(str(c.get(f) or "").strip() for f in _CONTACT_IDENTIFYING_FIELDS):
                raise ValueError(
                    f"contact #{i}: at least one of `firstName`, `lastName` "
                    f"or `email` is required — Minari rejects a contact with none of the three.")
        return items

    @staticmethod
    def _check_threshold(value: Optional[int]) -> Optional[int]:
        if value is None or value in CONVERSATION_THRESHOLDS:
            return value
        raise ValueError(
            f"`conversation_threshold` must be one of "
            f"{', '.join(str(v) for v in CONVERSATION_THRESHOLDS)} (got {value}).")

    # --- calls --------------------------------------------------------------

    def list_calls(self, *,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None,
                   user_id: Optional[Sequence[Any]] = None,
                   status: Optional[Sequence[str]] = None,
                   direction: Optional[str] = None,
                   min_duration: Optional[int] = None,
                   search: Optional[str] = None,
                   transcript_search: Optional[str] = None,
                   language: Optional[str] = None,
                   contact_id: Optional[int] = None,
                   list_id: Optional[Sequence[Any]] = None,
                   cursor: Optional[str] = None) -> Any:
        """GET /calls — completed calls, most recent first.

        Page fixed at 50; follow `next_url` via `next_page()`. `search` applies to
        the contact (name, company, number) and `transcript_search` to what was
        SAID — literal substring, AND-ed words, never semantic, and without
        translation: searching an English term does not find a French call.
        Both require at least 3 characters.
        """
        return self._get(
            "/calls", start_date=start_date, end_date=end_date,
            user_id=list(user_id) if user_id else None,
            status=list(status) if status else None,
            direction=direction, min_duration=min_duration, search=search,
            transcript_search=transcript_search, language=language,
            contact_id=contact_id,
            list_id=list(list_id) if list_id else None, cursor=cursor)

    def get_call(self, call_id: str) -> Any:
        """GET /calls/{id} — the full record: complete transcript + detailed
        objections (category, summary, salesperson's reply, outcome)."""
        return self._get(f"/calls/{_id(call_id)}")

    def get_call_transcript(self, call_id: str) -> Any:
        """GET /calls/{id}/transcript — the transcript only.

        `transcript` is `null` when the call did not connect or the
        transcription is still in progress: this is not an error.
        """
        return self._get(f"/calls/{_id(call_id)}/transcript")

    def call_recording_status(self, call_id: str) -> Dict[str, Any]:
        """Recording availability, WITHOUT pulling the audio.

        `GET /calls/{id}/recording` streams an MP3 when one exists, and returns
        JSON (`recording_url: null`) otherwise. Loading the body to find out
        which of the two would cost the weight of an entire call on every probe —
        so we read the headers and close.

        Returns `{available, content_type, size_bytes, url}`. `url` is the address of the
        stream, to open with the same Bearer; for a SHAREABLE link without a key,
        use the call record's `public_call_link` instead.
        """
        url = f"{self.base_url}/calls/{_id(call_id)}/recording"
        # The session header announces `application/json`; here we expect audio
        # first. Without this override, a server that negotiates content
        # might answer 406 — or serve a JSON error that we would read as
        # "no recording".
        headers = {"Accept": "audio/mpeg, application/json;q=0.5"}
        with self.session.get(url, timeout=_HTTP_TIMEOUT, stream=True,
                              headers=headers) as resp:
            self._raise(resp)
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            size = resp.headers.get("Content-Length")
            size_bytes = int(size) if size and size.isdigit() else None
            if content_type.startswith("audio/"):
                return {"available": True, "content_type": content_type,
                        "size_bytes": size_bytes, "url": url}
            # Unexpected type: do NOT call `resp.json()`, which would read the
            # ENTIRE body — on a mislabeled MP3 (or one served as `octet-stream`) that is
            # all the audio pulled into memory for a simple probe. We read a bounded
            # prefix and only conclude "no recording" if that
            # prefix is actually JSON.
            head = next(resp.iter_content(_PROBE_PREFIX_BYTES), b"") or b""
            try:
                detail = json.loads(head.decode("utf-8", "replace"))
            except ValueError:
                # Neither declared audio nor readable JSON: something IS being served.
                # Reporting it as available with its real type beats denying a
                # recording that exists.
                return {"available": True, "content_type": content_type or None,
                        "size_bytes": size_bytes, "url": url,
                        "note": "unexpected type — check `content_type`"}
            return {"available": False, "content_type": content_type or None,
                    "size_bytes": None, "url": None, "detail": detail}

    # --- team ---------------------------------------------------------------

    def list_users(self) -> Any:
        """GET /users — active members (invitation accepted). Their `id` is
        what `assigned_to` expects when creating a list."""
        return self._get("/users")

    # --- lists --------------------------------------------------------------

    def list_lists(self, *, cursor: Optional[str] = None) -> Any:
        """GET /lists — contact lists, most recent first.

        ⚠️ **CSV import source only**: lists synced from a
        CRM do not appear here. For the all-sources view, use `analytics_lists()`.
        """
        return self._get("/lists", cursor=cursor)

    def get_list(self, list_id: str) -> Any:
        """GET /lists/{id} — the list and ALL its contacts (up to 1500), with
        their notes. No pagination: the response can be large."""
        return self._get(f"/lists/{_id(list_id)}")

    def create_list(self, *, name: str, assigned_to: int,
                    contacts: Sequence[Dict[str, Any]],
                    update_existing_contacts: bool = False) -> Any:
        """POST /lists — create a list and import contacts into it.

        `assigned_to` = the `id` of a member returned by `list_users()`. A contact
        already known to the account is ADDED to the list, never duplicated; its
        stored fields stay unchanged unless `update_existing_contacts=True` (an
        empty value never overwrites existing data).
        """
        return self._request("POST", "/lists", json_body={
            "name": name,
            "assignedTo": assigned_to,
            "contacts": self._check_contacts(contacts),
            "updateExistingContacts": update_existing_contacts,
        })

    def delete_list(self, list_id: str) -> Any:
        """DELETE /lists/{id} — delete the list. Irreversible."""
        return self._request("DELETE", f"/lists/{_id(list_id)}")

    # --- contacts -----------------------------------------------------------

    def add_contacts(self, list_id: str, contacts: Sequence[Dict[str, Any]], *,
                     update_existing_contacts: bool = False) -> Any:
        """POST /lists/{id}/contacts — add contacts to an existing list.

        ⚠️ Exceeding the cap of 1500 per list does NOT make the
        request fail: the excess contacts are ignored and counted in
        `skippedCount`. A non-zero `skippedCount` therefore needs to be read.
        """
        return self._request("POST", f"/lists/{_id(list_id)}/contacts", json_body={
            "contacts": self._check_contacts(contacts),
            "updateExistingContacts": update_existing_contacts,
        })

    def remove_contacts(self, list_id: str, contact_ids: Sequence[int]) -> Any:
        """DELETE /lists/{id}/contacts — remove contacts from the list.

        Minari refuses to empty a list entirely through this path: for that,
        use `delete_list()`.
        """
        ids = list(contact_ids or [])
        if not ids:
            raise ValueError("`contact_ids` is empty — nothing to remove.")
        return self._request("DELETE", f"/lists/{_id(list_id)}/contacts",
                             json_body={"contactIds": ids})

    # --- custom fields ------------------------------------------------------

    def list_custom_fields(self) -> Any:
        """GET /custom-fields — the declared custom fields. Their `id` is
        the key to use in the `customFields` of an imported contact."""
        return self._get("/custom-fields")

    def create_custom_field(self, *, field_id: str, label: str) -> Any:
        """POST /custom-fields — declare a field. `field_id` must be unique
        (409 otherwise) and becomes the usable key in `customFields`."""
        return self._request("POST", "/custom-fields",
                             json_body={"id": field_id, "label": label})

    def delete_custom_field(self, field_id: str) -> Any:
        """DELETE /custom-fields — remove a field from imports and the UI."""
        return self._request("DELETE", "/custom-fields",
                             json_body={"id": field_id})

    # --- analytics ----------------------------------------------------------

    def analytics_overview(self, *,
                           start_date: Optional[str] = None,
                           end_date: Optional[str] = None,
                           user_id: Optional[Sequence[Any]] = None,
                           list_id: Optional[Sequence[Any]] = None,
                           conversation_threshold: Optional[int] = None) -> Any:
        """GET /analytics/overview — company-wide figures over a period.

        ⚠️ Without `start_date`/`end_date`, the window is **today**, not
        "everything". The two dates go together. Rates are percentages
        (0–100) and are `null` when their denominator is zero. The response
        returns the resolved window in `period`, a sibling of `data`.
        """
        return self._get(
            "/analytics/overview", start_date=start_date, end_date=end_date,
            user_id=list(user_id) if user_id else None,
            list_id=list(list_id) if list_id else None,
            conversation_threshold=self._check_threshold(conversation_threshold))

    def analytics_users(self, *,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None,
                        user_id: Optional[Sequence[Any]] = None,
                        list_id: Optional[Sequence[Any]] = None,
                        conversation_threshold: Optional[int] = None) -> Any:
        """GET /analytics/users — the same metrics, one row per member.

        Same "today" default as the overview. Rows sum to the overview
        totals with equal filters.
        """
        return self._get(
            "/analytics/users", start_date=start_date, end_date=end_date,
            user_id=list(user_id) if user_id else None,
            list_id=list(list_id) if list_id else None,
            conversation_threshold=self._check_threshold(conversation_threshold))

    def analytics_objections(self, *,
                             start_date: Optional[str] = None,
                             end_date: Optional[str] = None,
                             user_id: Optional[Sequence[Any]] = None,
                             list_id: Optional[Sequence[Any]] = None) -> Any:
        """GET /analytics/objections — the most frequent objections, by
        category, with the share engaged / passed / that ended the call.

        ⚠️ Default window: the **last 7 days** — not "today"
        like the overview. No pagination, one row per category.
        """
        return self._get(
            "/analytics/objections", start_date=start_date, end_date=end_date,
            user_id=list(user_id) if user_id else None,
            list_id=list(list_id) if list_id else None)

    def analytics_lists(self, *, period: str, call_limit: int,
                        user_id: Optional[Sequence[Any]] = None,
                        list_id: Optional[Sequence[Any]] = None,
                        cursor: Optional[str] = None) -> Any:
        """GET /analytics/lists — list progress, **all sources**.

        `period` and `call_limit` are REQUIRED because they define what
        "completed" means: `period` is the call counting window,
        `call_limit` the number of attempts after which a never-reached
        contact is considered exhausted. Two different answers to the same
        question are therefore two different definitions, not an inconsistency.

        Page fixed at 10 here (not 50); follow `next_url` via `next_page()`.
        """
        if period not in LIST_PERIODS:
            raise ValueError(
                f"`period` must be one of {', '.join(LIST_PERIODS)} (got {period!r}).")
        if not 1 <= int(call_limit) <= 10:
            raise ValueError(
                f"`call_limit` must be between 1 and 10 (got {call_limit}).")
        return self._get(
            "/analytics/lists", period=period, call_limit=int(call_limit),
            user_id=list(user_id) if user_id else None,
            list_id=list(list_id) if list_id else None, cursor=cursor)
