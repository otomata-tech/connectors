"""SignWell API client (https://www.signwell.com/api/v1) — documents sent for
signature, templates, bulk sends, webhooks, API applications.

API key sent as the `X-Api-Key` header (SignWell: Settings → API). One method
per REST endpoint (1 call = 1 endpoint): the public API has 26 operations,
small enough that no passthrough or consolidation trick is needed.

Derived from the OpenAPI definitions embedded in SignWell's reference pages
(`developers.signwell.com/reference/<operation>.md`, read 2026-09-16): required
fields, body shapes and enums come from the spec, not from doc prose.

## Protocol facts that shape a caller

- `get_me` answers with `{user, account}` — the cheap probe for a key.
- With `text_tags=True`, `{{…}}` tags in the uploaded file become fields.
  Detection is asynchronous: the create response can carry `fields: []` while
  a later `get_document` lists them.
- Document status moves `Created → Sending → Sent`; only `Sent` means the
  signature request went out.
- A `test_mode=True` document never emails its recipients: invitations go to
  the account owner, subject prefixed `[TEST]`.
- `embedded_signing=True` gives each recipient an `embedded_signing_url`
  (`signing_url` stays null); recipients are not emailed unless `send_email`.

## Spec details that bite

- **`create_document` sends immediately by default** (`draft` defaults to
  `false`): a real contract reaches real people in the same call. The client
  forwards whatever it is given; choosing a safer default is a tool-layer
  decision.
- **`get_completed_pdf` returns raw PDF/ZIP bytes** unless `url_only=True`. The
  transport returns `bytes` for any non-JSON 2xx body.
- **`get_bulk_send_csv_template` returns a CSV file** unless `base64=True`.
- **There is no "list documents" endpoint.** A document id must be kept by
  whoever created it.
- Recipient `id` is caller-chosen ("1", "2"…) and is what `fields[].recipient_id`
  and text tags' signer number point at.
- Rate limits: none published; a 429 surfaces as `UpstreamHTTPError` like any
  other non-2xx — not retried here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ...config import require_secret
from ..common import raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
_BASE_URL = "https://www.signwell.com/api/v1"


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become the literal
    string 'None' in the querystring, nor a null in a JSON body."""
    return {k: v for k, v in params.items() if v is not None}


class SignWellClient:
    """SignWell API client (https://www.signwell.com/api/v1), `X-Api-Key` auth."""

    BASE_URL = _BASE_URL

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: SignWell API key (or env var `SIGNWELL_API_KEY`), created
                in SignWell under Settings → API. The key acts as the account
                that created it: documents are sent in that person's name.
        """
        self.api_key = api_key or require_secret("SIGNWELL_API_KEY")
        self.session = requests.Session()
        self.session.headers["X-Api-Key"] = self.api_key
        self.session.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json_body: Any = None) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", params=_clean(params or {}),
            json=json_body, timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="signwell")
        content = resp.content or b""
        if resp.status_code == 204 or not content.strip():
            return None
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            return resp.json()
        # PDF, ZIP or CSV: handed back as-is, never decoded as text.
        return content

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("POST", path, json_body=_clean(body) if body else None)

    def _put(self, path: str, body: Dict[str, Any]) -> Any:
        return self._request("PUT", path, json_body=_clean(body))

    def _patch(self, path: str, body: Dict[str, Any]) -> Any:
        return self._request("PATCH", path, json_body=_clean(body))

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ================================================================
    # Account
    # ================================================================

    def get_me(self) -> Any:
        """GET /me — the user, account and workspace behind the key."""
        return self._get("/me")

    def get_api_application(self, application_id: str) -> Any:
        """GET /api_applications/{id} — one API application's settings."""
        return self._get(f"/api_applications/{application_id}")

    def delete_api_application(self, application_id: str) -> Any:
        """DELETE /api_applications/{id} — delete an API application (204)."""
        return self._delete(f"/api_applications/{application_id}")

    # ================================================================
    # Documents
    # ================================================================

    def create_document(self, files: List[Dict[str, Any]],
                        recipients: List[Dict[str, Any]], **body: Any) -> Any:
        """POST /documents — create a document and, unless `draft=True`, send it.

        Args:
            files: `[{name, file_url | file_base64}]` — exactly one of the two
                sources per file. `file_url` must be publicly fetchable.
            recipients: `[{id, name, email, …}]` — `id` is caller-chosen and is
                what fields and text tags refer to.
            **body: `draft`, `test_mode`, `name`, `subject`, `message`,
                `text_tags`, `fields`, `apply_signing_order`, `embedded_signing`,
                `expires_in`, `reminders`, `copied_contacts`, `labels`,
                `metadata`, `language`, `redirect_url`, `allow_decline`,
                `allow_reassign`, `attachment_requests`, `checkbox_groups`,
                `conditional_rules`, … (see the spec). ⚠️ `draft` defaults to
                `false` on SignWell's side: omitted, the document is SENT.
        """
        return self._post("/documents", {"files": files, "recipients": recipients, **body})

    def get_document(self, document_id: str) -> Any:
        """GET /documents/{id} — status, recipients (with their signing links),
        fields and files."""
        return self._get(f"/documents/{document_id}")

    def delete_document(self, document_id: str) -> Any:
        """DELETE /documents/{id} — delete a document; cancels signing if in
        progress (204)."""
        return self._delete(f"/documents/{document_id}")

    def send_document(self, document_id: str, **body: Any) -> Any:
        """POST /documents/{id}/send — update a draft's settings and send it.

        Args:
            **body: `test_mode`, `name`, `subject`, `message`, `expires_in`,
                `reminders`, `apply_signing_order`, `embedded_signing`,
                `redirect_url`, `labels`, `metadata`, … (see the spec).
        """
        return self._post(f"/documents/{document_id}/send", body)

    def send_reminder(self, document_id: str,
                      recipients: Optional[List[Dict[str, Any]]] = None) -> Any:
        """POST /documents/{id}/remind — remind recipients who have not signed.

        Args:
            recipients: `[{name?, email?}]` to target; omitted, every recipient
                who has not signed yet is reminded.
        """
        return self._post(f"/documents/{document_id}/remind",
                          {"recipients": recipients} if recipients else None)

    def update_recipients(self, document_id: str,
                          recipients: List[Dict[str, Any]]) -> Any:
        """PATCH /documents/{id}/recipients — change a sent document's recipients.

        Args:
            recipients: `[{id, name, email, subject?, message?, passcode?,
                passcode_delivery?}]` — `id`, `name` and `email` are all required.
        """
        return self._patch(f"/documents/{document_id}/recipients", {"recipients": recipients})

    def update_authentication(self, document_id: str,
                              recipients: List[Dict[str, Any]]) -> Any:
        """PATCH /documents/{id}/authentication — change recipients' passcodes.

        Args:
            recipients: `[{id, passcode?, passcode_delivery?}]`.
        """
        return self._patch(f"/documents/{document_id}/authentication",
                           {"recipients": recipients})

    def get_completed_pdf(self, document_id: str, **params: Any) -> Any:
        """GET /documents/{id}/completed_pdf — the signed document.

        Args:
            **params: `url_only` (true → `{file_url}` JSON; default false →
                raw PDF/ZIP bytes), `audit_page` (append the audit trail),
                `file_format` ("pdf" or "zip").
        """
        return self._get(f"/documents/{document_id}/completed_pdf", **params)

    def get_nom151_certificate(self, document_id: str, **params: Any) -> Any:
        """GET /documents/{id}/nom151_certificate — the Mexican NOM-151
        certificate of a completed document (422 where it does not apply).

        Args:
            **params: `url_only`, `object_only`.
        """
        return self._get(f"/documents/{document_id}/nom151_certificate", **params)

    # ================================================================
    # Templates
    # ================================================================

    def create_template(self, files: List[Dict[str, Any]],
                        placeholders: List[Dict[str, Any]], **body: Any) -> Any:
        """POST /document_templates — create a reusable template.

        Args:
            files: `[{name, file_url | file_base64}]`.
            placeholders: `[{id, name, …}]` — roles filled with real people
                when a document is created from the template.
            **body: `name`, `subject`, `message`, `draft`, `text_tags`,
                `fields`, `copied_placeholders`, `expires_in`, `reminders`,
                `apply_signing_order`, `labels`, `metadata`, … (see the spec).
        """
        return self._post("/document_templates",
                          {"files": files, "placeholders": placeholders, **body})

    def get_template(self, template_id: str) -> Any:
        """GET /document_templates/{id} — one template."""
        return self._get(f"/document_templates/{template_id}")

    def update_template(self, template_id: str, **body: Any) -> Any:
        """PUT /document_templates/{id} — change a template's settings (`name`,
        `subject`, `message`, `draft`, `expires_in`, `reminders`, `labels`, …)."""
        return self._put(f"/document_templates/{template_id}", body)

    def delete_template(self, template_id: str) -> Any:
        """DELETE /document_templates/{id} — delete a template (204)."""
        return self._delete(f"/document_templates/{template_id}")

    def create_document_from_template(self, recipients: List[Dict[str, Any]],
                                      **body: Any) -> Any:
        """POST /document_templates/documents — create (and by default SEND) a
        document from one or more templates.

        Args:
            recipients: `[{id, placeholder_name, name, email, …}]`.
            **body: `template_id` or `template_ids`, `draft`, `test_mode`,
                `template_fields` (prefilled values), `name`, `subject`,
                `message`, `embedded_signing`, … (see the spec). ⚠️ `draft`
                defaults to `false`: omitted, the document is SENT.
        """
        return self._post("/document_templates/documents", {"recipients": recipients, **body})

    # ================================================================
    # Bulk sends
    # ================================================================

    def list_bulk_sends(self, **params: Any) -> Any:
        """GET /bulk_sends — bulk sends, paginated.

        Args:
            **params: `page`, `limit`, `user_email`, `api_application_id`.
        """
        return self._get("/bulk_sends", **params)

    def get_bulk_send(self, bulk_send_id: str) -> Any:
        """GET /bulk_sends/{id} — one bulk send."""
        return self._get(f"/bulk_sends/{bulk_send_id}")

    def get_bulk_send_documents(self, bulk_send_id: str, **params: Any) -> Any:
        """GET /bulk_sends/{id}/documents — the documents a bulk send produced.

        Args:
            **params: `page`, `limit`.
        """
        return self._get(f"/bulk_sends/{bulk_send_id}/documents", **params)

    def get_bulk_send_csv_template(self, template_ids: List[str], **params: Any) -> Any:
        """GET /bulk_sends/csv_template — the CSV to fill for a bulk send.

        Args:
            template_ids: sent as the repeated `template_ids[]` query parameter.
            **params: `base64` (true → JSON with the CSV base64-encoded;
                default → the CSV file itself).
        """
        return self._get("/bulk_sends/csv_template", **{"template_ids[]": template_ids},
                         **params)

    def validate_bulk_send_csv(self, template_ids: List[str], bulk_send_csv: str) -> Any:
        """POST /bulk_sends/validate_csv — check a filled CSV before sending.

        Args:
            bulk_send_csv: the CSV, RFC 4648 base64-encoded.
        """
        return self._post("/bulk_sends/validate_csv",
                          {"template_ids": template_ids, "bulk_send_csv": bulk_send_csv})

    def create_bulk_send(self, template_ids: List[str], bulk_send_csv: str,
                         **body: Any) -> Any:
        """POST /bulk_sends — send one document per CSV row. Emails real people.

        Args:
            bulk_send_csv: the CSV, RFC 4648 base64-encoded.
            **body: `name`, `subject`, `message`, `skip_row_errors`,
                `apply_signing_order`, `custom_requester_name`,
                `custom_requester_email`, `api_application_id`.
        """
        return self._post("/bulk_sends", {"template_ids": template_ids,
                                          "bulk_send_csv": bulk_send_csv, **body})

    # ================================================================
    # Webhooks
    # ================================================================

    def list_webhooks(self) -> Any:
        """GET /hooks — registered webhooks (a bare array)."""
        return self._get("/hooks")

    def create_webhook(self, callback_url: str,
                       api_application_id: Optional[str] = None) -> Any:
        """POST /hooks — register a URL that receives every account event.

        SignWell signs each delivery: `event.hash` is an HMAC-SHA256 of
        `"<event.type>@<event.time>"` keyed with the WEBHOOK ID.
        """
        return self._post("/hooks", {"callback_url": callback_url,
                                     "api_application_id": api_application_id})

    def delete_webhook(self, webhook_id: str) -> Any:
        """DELETE /hooks/{id} — unregister a webhook (204)."""
        return self._delete(f"/hooks/{webhook_id}")
