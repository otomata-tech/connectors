"""Webhooks of a form: list, read, create or update, delete.

The HMAC signing `secret` of a webhook is accepted on write and never returned.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..params import _segment, _without_secret

#: Event types a webhook can subscribe to, as the Webhooks API reference shows them.
EVENT_TYPES = frozenset({"form_response", "form_response_partial"})


def _webhook_path(form_id: str, tag: Optional[str] = None) -> str:
    path = f"/forms/{_segment('form_id', form_id)}/webhooks"
    return path if tag is None else f"{path}/{_segment('tag', tag)}"


class _WebhooksMixin:
    """Webhooks (scopes webhooks:read, webhooks:write). Transport from the client."""

    def list_webhooks(self, form_id: str) -> Any:
        """GET /forms/{form_id}/webhooks — `{items: [{id, tag, url, enabled,
        event_types, verify_ssl, form_id, created_at, updated_at}]}`, without
        the signing secret."""
        payload = self._get(_webhook_path(form_id))
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return {**payload, "items": [_without_secret(w) for w in payload["items"]]}
        return payload

    def get_webhook(self, form_id: str, tag: str) -> Any:
        """GET /forms/{form_id}/webhooks/{tag} — one webhook, without its
        signing secret."""
        return _without_secret(self._get(_webhook_path(form_id, tag)))

    def upsert_webhook(self, form_id: str, tag: str, *, url: str, enabled: bool,
                       event_types: Optional[Dict[str, bool]] = None,
                       secret: Optional[str] = None,
                       verify_ssl: Optional[bool] = None) -> Any:
        """PUT /forms/{form_id}/webhooks/{tag} — creates the webhook named
        `tag`, or replaces it. Returns it, without its signing secret.

        ⚠️ Every new response of the form is then sent to `url`.

        Args:
            url: https endpoint that receives the responses.
            enabled: true to start sending at once.
            event_types: `{"form_response": bool, "form_response_partial": bool}`.
            secret: if set, payloads are signed with HMAC SHA256 by it.
            verify_ssl: true to have Typeform check the endpoint's certificate.
        """
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("`url` must be an https:// address.")
        if not isinstance(enabled, bool):
            raise ValueError("`enabled` must be a boolean.")
        if event_types is not None:
            unknown = set(event_types) - EVENT_TYPES
            if not event_types or unknown:
                raise ValueError("`event_types` takes form_response and/or "
                                 "form_response_partial, each a boolean.")
        body = {"url": url, "enabled": enabled, "event_types": event_types,
                "secret": secret, "verify_ssl": verify_ssl}
        body = {k: v for k, v in body.items() if v is not None}
        return _without_secret(self._send("PUT", _webhook_path(form_id, tag), json=body))

    def delete_webhook(self, form_id: str, tag: str) -> None:
        """DELETE /forms/{form_id}/webhooks/{tag} — its endpoint stops
        receiving responses. 204, nothing returned."""
        self._send("DELETE", _webhook_path(form_id, tag))
