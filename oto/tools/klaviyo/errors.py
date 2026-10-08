"""Typed refusals of the Klaviyo API.

Every upstream refusal is an `UpstreamHTTPError`, so consumers route it by
`status_code` as a handled connector error. `code` and `retryable` follow the
error table of `connectors/klaviyo/connector.yaml`; the upstream body is kept
in `body`, and a bounded excerpt of its JSON:API `errors` goes into the
message.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from ..common.errors import UpstreamHTTPError

SERVICE = "klaviyo"
EXCERPT_CHARS = 300

# status -> (code, retryable, message)
_TABLE = {
    400: ("invalid_request", False,
          "Klaviyo rejected the request as invalid (filter syntax, unknown "
          "field or malformed body)."),
    401: ("key_invalid", False,
          "Klaviyo did not accept the private API key: missing, revoked or "
          "mistyped. Retrying unchanged fails the same way."),
    403: ("scope_missing", False,
          "The private API key lacks the scope this action needs. Scopes "
          "cannot be added to an existing key: a new key with that scope is "
          "needed."),
    404: ("not_found", False,
          "No such object in this Klaviyo account: check the id."),
    409: ("conflict", False,
          "Klaviyo refused the change because it conflicts with existing "
          "data, such as an identifier already held by another profile."),
    429: ("rate_limited", True,
          "Klaviyo rate limit reached for this endpoint: wait the Retry-After "
          "seconds before retrying."),
}
_UNAVAILABLE = ("upstream_unavailable", True,
                "Klaviyo is temporarily unavailable: retry later.")

# Per-call override of the table: status -> (code, message).
Refusals = Mapping[int, Tuple[str, str]]


class KlaviyoError(UpstreamHTTPError):
    """A refusal of the Klaviyo API.

    `code` names the refusal (`key_invalid`, `scope_missing`…), `retryable`
    says whether the same call may succeed later, `body` is the upstream body.
    """

    def __init__(self, status_code: int, code: str, message: str, *,
                 retryable: bool = False, body: Any = None):
        super().__init__(status_code, body, service=SERVICE)
        self.code = code
        self.retryable = retryable
        self.args = (f"{SERVICE} HTTP {status_code} ({code}): {message}",)


class KlaviyoRateLimited(KlaviyoError):
    """429: the endpoint's rate for this account is spent. `retry_after` is
    the wait in seconds, when known. `local` is true when the client refused
    to send because its own pacing would have waited longer than allowed."""

    def __init__(self, status_code: int, code: str, message: str, *,
                 retry_after: Optional[float] = None, local: bool = False,
                 body: Any = None):
        super().__init__(status_code, code, message, retryable=True, body=body)
        self.retry_after = retry_after
        self.local = local


class KlaviyoPaginationError(RuntimeError):
    """A `links.next` that the client refuses to follow: it does not point to
    the Klaviyo API over https."""


def _body_of(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _excerpt(body: Any) -> str:
    """The JSON:API errors, bounded: `detail` (or `title`) and the faulty
    pointer or parameter of each, or the raw text."""
    if isinstance(body, dict) and isinstance(body.get("errors"), list):
        parts = []
        for error in body["errors"]:
            if not isinstance(error, dict):
                continue
            text = str(error.get("detail") or error.get("title") or "")
            source = error.get("source") or {}
            where = source.get("pointer") or source.get("parameter")
            parts.append(f"{text} ({where})" if where else text)
        text = " — ".join(p for p in parts if p)
    else:
        text = str(body or "")
    text = " ".join(text.split())
    if len(text) > EXCERPT_CHARS:
        text = text[:EXCERPT_CHARS] + "…"
    return text


def _retry_after(response: Any) -> Optional[float]:
    value = (getattr(response, "headers", None) or {}).get("Retry-After")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def error_from_response(response: Any,
                        refusals: Optional[Refusals] = None) -> KlaviyoError:
    """Translate an HTTP error response (status >= 400) into a typed refusal."""
    status = response.status_code
    body = _body_of(response)
    excerpt = _excerpt(body)
    if status in _TABLE:
        code, retryable, message = _TABLE[status]
    elif status >= 500:
        code, retryable, message = _UNAVAILABLE
    else:
        code, retryable, message = (f"http_{status}", False,
                                    "Klaviyo refused the request.")
    if refusals and status in refusals:
        code, message = refusals[status]
    if excerpt and status != 401:
        message = f"{message} Klaviyo said: {excerpt}"

    if status == 429:
        wait = _retry_after(response)
        if wait is not None:
            message = f"{message} Retry after {wait:g} s."
        return KlaviyoRateLimited(status, code, message, retry_after=wait,
                                  body=body)
    return KlaviyoError(status, code, message, retryable=retryable, body=body)
