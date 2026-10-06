"""Key identity, public portal, and file import.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`).

`me()` is the connector's probe: **any authenticated key can
call it**, whatever its scopes. That is what makes it the right connection
test — it tells "invalid key" (401) apart from "valid key but without the
requested right" (403 elsewhere), where probing a resource would conflate the two.
It also returns the granted scopes and the workspace's Linear team selection,
so there is enough to explain a refusal BEFORE provoking it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class _MetaMixin:
    """Identity, portal, files."""

    # --- identity -----------------------------------------------------------

    def me(self) -> Any:
        """GET /me — key identity, granted scopes, workspace Linear teams.

        **No scope required**: callable by any authenticated key. It is the
        connector's authentication probe.
        """
        return self._request("GET", "/me")

    # --- public portal -------------------------------------------------------

    def get_roadmap(self, contact_email: Optional[str] = None,
                    language: Optional[str] = None) -> Any:
        """GET /portal/roadmap — the public roadmap, as the portal renders it.

        Scope `portal:read`. `contact_email` renders it from a contact's point of view
        (what they voted for, what concerns them).
        """
        return self._request("GET", "/portal/roadmap",
                             params={"contact_email": contact_email,
                                     "language": language})

    def get_customer_portal(self, email: str) -> Any:
        """GET /portal/customer-portal — what a contact sees in their portal.

        Scope `portal:read`, **Scale plan required**. `email` is mandatory: the
        view is that of a specific person, there is no "general" view.
        """
        if not email:
            raise ValueError(
                "`email` is required: this view is that of a given contact.")
        return self._request("GET", "/portal/customer-portal",
                             params={"email": email})

    def list_portal_instances(self) -> Any:
        """GET /portal/instances — workspace portal instances. Scope `portal:read`.

        ⚠️ The **Main (Root) portal is implicit**: it does not appear in this
        list, and is designated elsewhere by a `null` `portal_instance_id`. An
        empty list therefore does not mean "no portal".
        """
        return self._request("GET", "/portal/instances")

    # --- files ---------------------------------------------------------------

    def import_file(self, url: Optional[str] = None,
                    content_base64: Optional[str] = None,
                    file_name: Optional[str] = None,
                    content_type: Optional[str] = None) -> Any:
        """POST /files/import — store a file from a public URL or from base64.

        Returns a CDN URL usable in a changelog, a doc article or an
        attachment. Provide **either** `url` **or** `content_base64` — not
        both: upstream does not say which it would favor.
        """
        if not url and not content_base64:
            raise ValueError(
                "provide `url` (public source) or `content_base64` (inline "
                "content).")
        if url and content_base64:
            raise ValueError(
                "`url` and `content_base64` are mutually exclusive — pass one OR "
                "the other.")
        body: Dict[str, Any] = {}
        for key, value in (("url", url), ("content_base64", content_base64),
                           ("file_name", file_name),
                           ("content_type", content_type)):
            if value is not None:
                body[key] = value
        return self._request("POST", "/files/import", json=body)
