"""Leexi calls and meetings — reading, transcripts, and the import lifecycle.

This mixin is never instantiated on its own: it is composed into `LeexiClient`, which
provides the transport (`_request`, `_list`, `_check_choice`).

⚠️ **What these methods return depends on the key's *call access scope***
(the whole company / a user's access / access rules). Out of
scope, a call is not listed and answers **404** when fetched directly: a 404 on
`get_call` therefore does not mean « does not exist ».
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ...common import raise_for_upstream
from ..const import (CALL_DATE_FILTERS, CALL_ORDERS, HTTP_TIMEOUT)


class _CallsMixin:
    """Calls and meetings."""

    def list_calls(self, page: Optional[int] = None, items: Optional[int] = None,
                   order: Optional[str] = None, date_filter: Optional[str] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   source: Optional[str] = None,
                   source_id: Optional[Any] = None,
                   owner_uuid: Optional[Any] = None,
                   participating_user_uuid: Optional[Any] = None,
                   conversation_type_uuid: Optional[str] = None,
                   customer_phone_number: Optional[Any] = None,
                   customer_email_address: Optional[Any] = None,
                   with_simple_transcript: Optional[bool] = None) -> Any:
        """GET /v1/calls — calls and meetings WITHIN THE SCOPE of the key. Scope `read_calls`.

        An empty list is a possible setting (a key whose scope covers no
        call), not an anomaly.

        `date_from`/`date_to` bound the field named by `date_filter` (default
        `created_at`). They carry this prefixed name because `from` is a reserved
        word in Python; they do go out as `from`/`to` on the wire.

        `with_simple_transcript=True` attaches the paragraph-granularity transcript
        — the response becomes noticeably heavier, page by page.

        Five filters are multi-valued (`source_id`, `owner_uuid`,
        `participating_user_uuid`, `customer_phone_number`,
        `customer_email_address`): pass a list, the transport adds the
        `[]` brackets expected by upstream.
        """
        self._check_choice("order", order, CALL_ORDERS)
        self._check_choice("date_filter", date_filter, CALL_DATE_FILTERS)
        return self._list("/calls", page, items, {
            "order": order, "date_filter": date_filter,
            "from": date_from, "to": date_to,
            "source": source, "source_id": source_id,
            "owner_uuid": owner_uuid,
            "participating_user_uuid": participating_user_uuid,
            "conversation_type_uuid": conversation_type_uuid,
            "customer_phone_number": customer_phone_number,
            "customer_email_address": customer_email_address,
            "with_simple_transcript": with_simple_transcript,
        })

    def get_call(self, uuid: str) -> Any:
        """GET /v1/calls/{uuid} — one call, **with its topics and transcript**.

        Scope `read_calls`. Same attributes as the list, plus `simple_transcript`
        (paragraph-level timestamps) and `transcript` (WORD-level timestamps).

        ⚠️ **404 = outside the key's scope**, not necessarily nonexistent.
        """
        return self._request("GET", f"/calls/{uuid}")

    def presign_recording_url(self, extension: str) -> Any:
        """POST /v1/calls/presign_recording_url — upload URL. Scope `write_calls`.

        First step of the import lifecycle: returns the URL and **the headers** to replay
        for a single-part PUT (see `upload_recording`), plus the storage key
        to then pass as `recording_s3_key` to `create_call`. The uploaded file
        expires after **3 days** if it is used for no call.
        """
        return self._request("POST", "/calls/presign_recording_url",
                             json={"extension": extension})

    def upload_recording(self, presigned: Dict[str, Any], data: Any,
                         timeout: Any = None) -> int:
        """PUT of the file to the pre-signed URL. **Outside the Leexi API** (object storage).

        `presigned` = the response of `presign_recording_url` as-is. Its
        headers are replayed IDENTICALLY: they are signed with the URL, so
        changing one — or adding one — invalidates the signature and the storage
        answers 403.

        ⚠️ Request made **outside the session** (`requests.put`, not `self.session`): the
        session carries Leexi's `Authorization` header, which has no business at
        the object storage and would break the signature there. Returns the PUT's status.
        """
        if not isinstance(presigned, dict):
            raise ValueError(
                "`presigned` must be the response of `presign_recording_url` "
                f"(an object), got {type(presigned).__name__}.")
        url = presigned.get("url") or presigned.get("presigned_url")
        if not url:
            raise ValueError(
                "`presign_recording_url` response without `url`: "
                f"keys received {sorted(presigned)}")
        headers = presigned.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError("`headers` of the pre-signed response must be an object.")
        resp = requests.put(url, data=data, headers=headers,
                            timeout=timeout or HTTP_TIMEOUT)
        raise_for_upstream(resp, service="leexi (storage)")
        return resp.status_code

    def create_call(self, payload: Dict[str, Any]) -> Any:
        """POST /v1/calls — creates a call **asynchronously**. Scope `write_calls`.

        Required: `direction`, `external_id`, `performed_at`, `recording_s3_key`,
        `user_uuid`. Optional: `customers`, `description`, `emails`, `locale`,
        `participating_user_uuids`, `raw_phone_number`, `tags`, `title`.

        ⚠️ The upload must be **finished** before this call (see
        `presign_recording_url` then `upload_recording`). Processing typically
        takes a few minutes, and the prompt completions (summary,
        chaptering) only arrive AFTERWARDS: re-read the call later, and do not
        conclude from their immediate absence that they are missing.

        ⚠️ Own, low rate limit: **10 requests/minute** (versus 50 elsewhere).
        """
        return self._request("POST", "/calls", json=dict(payload))
