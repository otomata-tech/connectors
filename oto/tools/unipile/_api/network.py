"""Network & outreach: relations and invitations.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional
from urllib.parse import quote

from ..const import cursor_with_limit
from ..errors import UnipileError


# SYNTHETIC invitations cursor: `off:<offset>:<page fingerprint>`.
#
# `relation-requests` is paginated by OFFSET on the Unipile side, not by cursor — so it
# NEVER returns a `next_cursor`. To keep the tool's contract
# ("call me again with the returned cursor"), we FABRICATE this token and
# decode it again on input: it is NEVER forwarded upstream. The prefix makes it
# readable in logs and prevents any collision if Unipile ever ended up returning
# a real one (in which case upstream wins, see `list_invitations`).
#
# The token ALSO carries the fingerprint of the page that produced it, and that is
# the mechanical stop: that `offset` actually paginates this endpoint was never
# verified against the real service. If the assumption is wrong, upstream ignores
# `offset` and serves the same page forever — without a fingerprint, the calling
# loop NEVER stops (simulated: 8 pages, 400 rows returned for 50
# distinct ones). By comparing the returned page to the previous turn's one, we cut
# at the 2nd call. The `off:<n>` form without a fingerprint is still decoded: a cursor
# returned by an earlier version doesn't break mid-flight.
_INV_CURSOR = "off:"

# OBSERVED cap (2026-09-10) of `limit` on relation-requests: 100 passes,
# 101 and 200 return `Unipile 400: Invalid querystring` — a message that names
# neither the faulty param nor the bound. Unipile doesn't document it (the v2
# OpenAPI says `default: 20, minimum: 1`, with no maximum: "depends on the
# provider"). So we sort it out HERE, to return an error that can be read.
_INV_LIMIT_MAX = 100


def _invitations_page_print(page: list) -> str:
    """Fingerprint of an invitations page — what distinguishes it from the next one.

    On the `id`s when the items carry them (what upstream returns), on the whole
    item otherwise. Two "identical" pages in the sense that matters here are two
    pages that serve the SAME invitations — not two pages of the same
    size."""
    seed = json.dumps(
        [it.get("id", it) if isinstance(it, dict) else it for it in page],
        sort_keys=True, default=str, ensure_ascii=False,
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _invitations_cursor(cursor: Optional[str]) -> tuple[int, Optional[str]]:
    """Decode an invitations cursor FABRICATED by us → `(offset, fingerprint
    of the page that produced it)`. The fingerprint is `None` if the cursor doesn't
    carry one (earlier `off:<n>` form, still accepted).

    Any other cursor is refused HERE rather than forwarded: passed upstream it
    triggered the 400 "Unexpected parameters: type" (see
    `list_invitations`), unreadable for the caller."""
    if not cursor:
        return 0, None
    if cursor.startswith(_INV_CURSOR):
        head, _, tail = cursor[len(_INV_CURSOR):].partition(":")
        if head.isdigit():
            return int(head), (tail or None)
    raise UnipileError(
        "list_invitations: invalid cursor. ONLY pass back the `cursor` returned "
        "as is by the previous call; to restart from the beginning of the listing, "
        "pass none. A cursor coming from another call — or written by "
        "hand — is refused here, before any upstream call."
    )


class _NetworkMixin:
    """Network & outreach: relations and invitations."""

    def list_relations(self, cursor: Optional[str] = None,
                       limit: Optional[int] = None) -> dict:
        params: dict[str, Any] = {}
        if cursor:
            # The call's limit takes precedence over the one frozen in the cursor (#179).
            params["cursor"] = cursor_with_limit(cursor, limit) if limit else cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct("/users/me/relations"), params=params
        ))

    def list_invitations(self, direction: str = "received",
                         limit: Optional[int] = None,
                         cursor: Optional[str] = None,
                         offset: Optional[int] = None) -> dict:
        """Invitations — v2: `GET /v2/{account}/users/me/relation-requests`,
        `type=sent|received` (REQUIRED param on the Unipile side).

        ⚠️ This endpoint is paginated by `offset`, NOT by cursor. Unipile:
        "Pagination for this endpoint works with the `offset` parameter." It
        therefore never returns a `next_cursor`, and it REFUSES any param other than
        `limit`/`meta_only` alongside a `cursor`:

            Unipile 400: When cursor is provided, only "limit" and "meta_only"
            are allowed alongside it. Unexpected parameters: type.

        Since `type` is REQUIRED, sending a `cursor` was a dead end: page 1
        passed, every following page returned 400 — the invitations pagination
        was dead beyond the first screen, with nothing signaling it on the schema
        side (the tool announced "Paginated"). So we paginate by
        `offset` and FABRICATE the returned cursor (see `_invitations_cursor`)
        to keep the tool's contract.

        Advances by `limit` per page — Unipile contract: "increment the offset
        by the limit" — and stops when `data` is EMPTY, not on a short
        page: the provider can filter items WITHIN the window, and
        advancing by `len(data)` would then serve the same ones again. For an
        exhaustive export, deduplicate by `id` anyway.

        ⚠️ MECHANICAL STOP. That `offset` actually paginates this endpoint was
        never verified against the real service. If the assumption is wrong,
        upstream ignores `offset` and serves the same page every turn: the
        calling loop would never stop. The returned cursor therefore carries
        the fingerprint of the page that produced it, and NO cursor is fabricated
        when the returned page is identical to the previous turn's one — the
        loop then stops at the 2nd call, and `pagination_note` says why.
        The criterion is the IDENTICAL page, not the empty page: that's the real
        failure mode here, an empty page precisely never happens in this case."""
        seen = None
        if offset is None:
            offset, seen = _invitations_cursor(cursor)
        if limit is not None and not 1 <= limit <= _INV_LIMIT_MAX:
            raise UnipileError(
                f"list_invitations: limit must be between 1 and "
                f"{_INV_LIMIT_MAX} (got {limit}). Beyond that, Unipile returns an "
                "\"Invalid querystring\" that doesn't name the bound."
            )
        params: dict[str, Any] = {
            "type": "sent" if direction == "sent" else "received"
        }
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        out = self._norm(self._request(
            "GET", self._acct("/users/me/relation-requests"), params=params
        ))
        # Cursor fabricated ONLY if upstream doesn't return one (today it
        # never does): the day Unipile returns a real one, it wins.
        # Empty page = end of list → no cursor, the caller stops.
        if isinstance(out, dict) and not out.get("next_cursor"):
            page = out.get("data")
            if isinstance(page, list) and page:
                mark = _invitations_page_print(page)
                if mark == seen:
                    # Upstream just served the previous page again
                    # identically: it doesn't advance — `offset` doesn't paginate this
                    # endpoint. We fabricate NO cursor, otherwise the calling
                    # loop spins forever without ever meeting an end.
                    out["pagination_note"] = (
                        "Pagination stopped: upstream served the previous "
                        "page again identically, it doesn't advance on this "
                        "endpoint. The items above are the same as "
                        "those of the previous page; there is no next page "
                        "to ask for."
                    )
                else:
                    nxt = (f"{_INV_CURSOR}{offset + (limit or len(page))}"
                           f":{mark}")
                    out["next_cursor"] = nxt
                    out["cursor"] = nxt
        return out

    def send_invitation(self, provider_id: str,
                        message: Optional[str] = None) -> dict:
        """v2: `POST /users/me/relation-requests`, body `{user_id, message}`."""
        body: dict[str, Any] = {"user_id": provider_id}
        if message:
            body["message"] = message
        return self._request(
            "POST", self._acct("/users/me/relation-requests"), json=body
        )

    def handle_invitation(
        self, invitation_id: str, shared_secret: str, action: str = "accept"
    ) -> dict:
        """Accept/decline a RECEIVED invitation. v2: `request_id` is enough (no more
        `shared_secret`, kept in the signature for caller compat). accept →
        `/accept`; decline → `/cancel`."""
        if action not in ("accept", "decline"):
            raise UnipileError("handle_invitation: action = 'accept' or 'decline'.")
        verb = "accept" if action == "accept" else "cancel"
        return self._request(
            "POST",
            self._acct(
                f"/users/me/relation-requests/{quote(invitation_id, safe='')}/{verb}"
            ),
        )

    def cancel_invitation(self, invitation_id: str) -> dict:
        """Cancel a SENT invitation. v2: `/relation-requests/{id}/cancel`."""
        return self._request(
            "POST",
            self._acct(
                f"/users/me/relation-requests/{quote(invitation_id, safe='')}/cancel"
            ),
        )
