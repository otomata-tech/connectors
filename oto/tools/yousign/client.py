"""Yousign API v3 client (https://developers.yousign.com) — signature requests:
documents, signers, activation, download of the signed result.

Bearer token auth (Yousign: API keys page, org or workspace scope). One method
per REST endpoint (1 call = 1 endpoint) — the public v3 surface is large;
this client covers the signature-request lifecycle (create → add document →
add signer → activate → poll status → download signed document), not the
identity-verification, template or branding sub-APIs.

Derived from the OpenAPI reference pages at `developers.yousign.com/reference/
<operation>` (redirects to `developers.youtrust.com`, the platform's current
brand — the API host and `v3` surface are unchanged), read 2026-09-17:
required fields, body shapes and enums come from the spec, not from doc prose.

## Protocol facts that shape a caller

- Two distinct hosts, not an environment query param: sandbox
  `api-sandbox.yousign.app`, production `api.yousign.app`. `sandbox=True`
  picks the sandbox host; there is no shared key between the two.
- A `SignatureRequest` is built in three calls before it can go out: create
  (`POST /signature_requests`, `status=draft`), attach at least one document,
  attach at least one signer — then `activate` is what actually sends it.
  Creating alone never emails anyone.
- `activate`'s response carries each signer's `signature_link` — sensitive,
  treat like a credential (Yousign's own guidance): it lets that signer sign
  without further auth beyond what `signature_authentication_mode` requires.
- Status moves through `draft → ongoing → done` on the happy path;
  `expired`/`canceled`/`declined`/`rejected` are terminal failures,
  `paused`/`approval` are intermediate. Only `done` means every signer signed.
- **Downloading the signed document is a SEPARATE call per document id**,
  not a single "give me everything" endpoint — `download_document` returns
  one PDF at a time. A multi-document request means one download call per
  document kept from `create_document`'s response.
- Document upload is `multipart/form-data` (binary `file` + `nature`), not
  JSON — the one method here that doesn't send `json_body`.

## Spec details that bite

- **`add_signer`'s `info` is REQUIRED when building a signer from scratch**
  (the common case — no existing Yousign user/contact to reference): omit it
  and the call is refused. `locale` inside `info` is itself required (enum,
  no default) — a signer without a chosen language is refused, not defaulted
  to French.
- **`delete_signature_request` has TWO behaviors behind one flag**:
  `permanent_delete=False` (the default) is a soft trash — `status` becomes
  `deleted`, the request can still be read back. `permanent_delete=True` is
  irreversible. Neither works on a request still `ongoing`/`approval`:
  cancel it first.
- **Webhooks are not covered here.** Yousign's webhook surface is
  subscription-based (endpoint URL + secret + event filters) and its sandbox
  behavior is gated by account trial status in ways this client hasn't
  verified against the spec — rather than guess a shape, poll
  `get_signature_request` for status. Add webhook methods once the exact
  subscription contract is confirmed.
- Rate limits: none published; a 429 surfaces as `UpstreamHTTPError` like any
  other non-2xx — not retried here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ...config import require_secret
from ..common import raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
_PROD_URL = "https://api.yousign.app/v3"
_SANDBOX_URL = "https://api-sandbox.yousign.app/v3"


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become the literal
    string 'None' in the querystring, nor a null in a JSON body."""
    return {k: v for k, v in params.items() if v is not None}


class YousignClient:
    """Yousign API v3 client (https://developers.yousign.com), Bearer auth.

    Two hosts, chosen at construction — never mixed mid-session."""

    def __init__(self, api_key: Optional[str] = None, *, sandbox: bool = False):
        """
        Args:
            api_key: Yousign API key (or env var `YOUSIGN_API_KEY`), created
                under the org's API keys page. Sandbox and production keys
                are DIFFERENT — a production key on the sandbox host (or the
                reverse) is refused, not silently ignored.
            sandbox: use `api-sandbox.yousign.app` instead of `api.yousign.app`.
        """
        self.api_key = api_key or require_secret("YOUSIGN_API_KEY")
        self.BASE_URL = _SANDBOX_URL if sandbox else _PROD_URL
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.session.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json_body: Any = None,
                 files: Optional[Dict[str, Any]] = None,
                 data: Optional[Dict[str, Any]] = None) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", params=_clean(params or {}),
            json=json_body, files=files, data=data, timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="yousign")
        content = resp.content or b""
        if resp.status_code == 204 or not content.strip():
            return None
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            return resp.json()
        # PDF (a downloaded document, or an audit trail): handed back as-is.
        return content

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("POST", path, json_body=_clean(body) if body else None)

    def _delete(self, path: str, **params: Any) -> Any:
        return self._request("DELETE", path, params=params)

    # ================================================================
    # Signature requests
    # ================================================================

    def create_signature_request(self, name: str, *,
                                 delivery_mode: str = "email",
                                 **body: Any) -> Any:
        """POST /signature_requests — create a DRAFT request. Sends nothing:
        `activate_signature_request` is the call that emails signers.

        Args:
            name: 1-128 chars.
            delivery_mode: `"email"` (Yousign emails the signature link) or
                `"none"` (caller distributes the link itself).
            **body: `expiration_date` (yyyy-mm-dd), `ordered_signers`,
                `ordered_approvers`, `custom_recipient_order`,
                `reminder_settings`, `timezone` (default "Europe/Paris"),
                `template_id`, `external_id`, `signers_allowed_to_decline`,
                `email_notification`, `labels`, `custom_experience_id`
                (see the spec).
        """
        return self._post("/signature_requests",
                          {"name": name, "delivery_mode": delivery_mode, **body})

    def get_signature_request(self, signature_request_id: str) -> Any:
        """GET /signature_requests/{id} — status, signers, documents.

        `status` ∈ draft | ongoing | done | deleted | expired | canceled |
        approval | rejected | declined | paused. Only `done` means every
        signer signed — poll this to follow a request after activation."""
        return self._get(f"/signature_requests/{signature_request_id}")

    def list_signature_requests(self, **params: Any) -> Any:
        """GET /signature_requests — every request in the organization.

        Args:
            **params: pagination/filter params (see the spec).
        """
        return self._get("/signature_requests", **params)

    def activate_signature_request(self, signature_request_id: str) -> Any:
        """POST /signature_requests/{id}/activate — leaves DRAFT, notifies
        signers/approvers/followers if `delivery_mode` is not `"none"`.

        ⚠️ The response's `signature_link` per signer is SENSITIVE (treat like
        a credential): it lets that signer sign directly."""
        return self._post(f"/signature_requests/{signature_request_id}/activate")

    def cancel_signature_request(self, signature_request_id: str) -> Any:
        """POST /signature_requests/{id}/cancel — only valid from `ongoing`
        or `approval`; the request becomes `canceled`, a terminal state."""
        return self._post(f"/signature_requests/{signature_request_id}/cancel")

    def delete_signature_request(self, signature_request_id: str, *,
                                 permanent_delete: bool = False) -> Any:
        """DELETE /signature_requests/{id} — refused while `ongoing`/`approval`
        (cancel first).

        Args:
            permanent_delete: `False` (default) soft-trashes — `status`
                becomes `deleted`, still readable. `True` is IRREVERSIBLE.
        """
        return self._delete(f"/signature_requests/{signature_request_id}",
                            permanent_delete=permanent_delete)

    # ================================================================
    # Documents
    # ================================================================

    def add_document(self, signature_request_id: str, file_bytes: bytes,
                     filename: str, *, nature: str = "signable_document",
                     **fields: Any) -> Any:
        """POST /signature_requests/{id}/documents — `multipart/form-data`,
        up to 50 documents per request. Accepted file formats: PDF, DOCX,
        JPEG, JPG, PNG — but JPEG/JPG/PNG can only be `nature="attachment"`.

        Args:
            file_bytes: the file's raw content.
            filename: sent as the multipart filename.
            nature: `"signable_document"` (default — what signers sign) or
                `"attachment"` (visible, never signed).
            **fields: `name` (display name, no leading/trailing space, no
                `/`/`\\`, ≤128 chars), `password` (protected PDFs),
                `insert_after_id`, `parse_anchors` (Smart Anchor detection),
                `flatten`, `initials`, `excluded_signers`, `excluded_approvers`
                (see the spec).
        """
        data = _clean({"nature": nature, **fields})
        return self._request(
            "POST", f"/signature_requests/{signature_request_id}/documents",
            files={"file": (filename, file_bytes)}, data=data)

    def download_document(self, signature_request_id: str, document_id: str) -> Any:
        """GET /signature_requests/{id}/documents/{document_id}/download —
        the PDF bytes for ONE document. There is no bulk "everything" download:
        call this once per document id kept from `add_document`'s response.

        ⚠️ Cannot be called twice in parallel for the same document (Yousign
        limitation) — sequence repeated downloads.

        Meaningful once the request's `status` is `done`; before that it
        returns the unsigned original."""
        return self._get(
            f"/signature_requests/{signature_request_id}/documents/{document_id}/download")

    def download_audit_trail(self, signature_request_id: str) -> Any:
        """GET /signature_requests/{id}/audit_trails/download — the PDF proof
        of the signing process (who signed, when, from where)."""
        return self._get(f"/signature_requests/{signature_request_id}/audit_trails/download")

    # ================================================================
    # Signers
    # ================================================================

    def add_signer(self, signature_request_id: str, info: Dict[str, Any], *,
                   signature_level: str = "electronic_signature",
                   **fields: Any) -> Any:
        """POST /signature_requests/{id}/signers — a signer built FROM SCRATCH
        (the common case; `user_id`/`contact_id`/`verified_identity_id`
        variants exist in the spec but aren't covered here).

        Args:
            info: `{first_name, last_name, email, locale, phone_number?}` —
                `first_name`/`last_name`/`email`/`locale` are ALL required by
                Yousign (no defaults). `locale` ∈ en|fr|de|it|nl|es|pl|pt|ro.
                `phone_number` (E.164) is required only if
                `signature_authentication_mode` needs it (e.g. `"otp_sms"`).
            signature_level: `"electronic_signature"` (default) |
                `"advanced_electronic_signature"` |
                `"qualified_electronic_signature"`.
            **fields: `fields` (signature field placements), `insert_after_id`,
                `group_with_id`, `signature_authentication_mode` (`"otp_email"`
                default | `"otp_sms"` | `"no_otp"`), `redirect_urls`,
                `custom_text`, `delivery_mode`, `sms_notification`,
                `email_notification`, `excluded_documents`,
                `disabled_signing_contexts` (see the spec).
        """
        return self._post(f"/signature_requests/{signature_request_id}/signers",
                          {"info": info, "signature_level": signature_level, **fields})

    def get_signer_documents(self, signature_request_id: str, signer_id: str) -> Any:
        """GET /signature_requests/{id}/signers/{signer_id}/documents — the
        documents THIS signer is asked to sign (may be a subset, see
        `excluded_documents` on `add_signer`)."""
        return self._get(
            f"/signature_requests/{signature_request_id}/signers/{signer_id}/documents")
