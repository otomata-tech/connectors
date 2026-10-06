"""Leexi call notes — what the prompts produced on a call.

This mixin is never instantiated on its own: it is composed into `LeexiClient`, which
provides the transport (`_request`, `_list`).

⚠️ Notes follow the key's *call access scope*, like calls: the
notes of an out-of-scope call are **not** returned — and upstream answers with
an empty list, not a refusal. An empty list therefore does not prove that a call
has no notes.
"""
from __future__ import annotations

from typing import Any, Optional


class _NotesMixin:
    """Call notes."""

    def list_call_notes(self, call_uuid: str, page: Optional[int] = None,
                        items: Optional[int] = None,
                        prompt_uuid: Optional[str] = None) -> Any:
        """GET /v1/call_notes — notes of a call. Scope `read_calls`.

        `call_uuid` is **required by upstream** (there is no global list
        of notes); `prompt_uuid` filters on the prompt that produced them.

        ⚠️ Only notes coming from prompts of the `summary` or `text` categories
        are returned: the others do not exist for this API, and their absence
        is not a defect of the key.
        """
        if not call_uuid:
            raise ValueError(
                "`call_uuid` is required to list call notes "
                "(the Leexi API exposes no global list).")
        return self._list("/call_notes", page, items,
                          {"call_uuid": call_uuid, "prompt_uuid": prompt_uuid})

    def get_call_note(self, uuid: str) -> Any:
        """GET /v1/call_notes/{uuid} — one note. Scope `read_calls`."""
        return self._request("GET", f"/call_notes/{uuid}")

    def update_call_note(self, uuid: str, locale: str, text: str) -> Any:
        """PATCH /v1/call_notes/{uuid} — rewrites a note. Scope `write_calls`.

        `locale` AND `text` are both required by upstream: it is a
        REPLACEMENT of the text for a given language, not a merge — the previous
        content of that language is lost.
        """
        if not locale or not text:
            raise ValueError(
                "`locale` and `text` are both required: the Leexi API replaces "
                "the text of a language, it does not merge.")
        return self._request("PATCH", f"/call_notes/{uuid}",
                             json={"locale": locale, "text": text})

    def delete_call_note(self, uuid: str) -> Any:
        """DELETE /v1/call_notes/{uuid} — deletes a note. Scope `write_calls`.

        ⚠️ Real deletion, no trash on the API side — unlike
        `deactivate_user`, whose DELETE only deactivates.
        """
        return self._request("DELETE", f"/call_notes/{uuid}")
