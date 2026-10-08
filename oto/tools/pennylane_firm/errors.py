"""Typed refusals of the Pennylane Firm API.

Every refusal is an `UpstreamHTTPError` (same family as the Company API
client), so consumers route it by `status_code` as a handled connector error.
`code` and `retryable` follow the error table of
`connectors/pennylane_firm/connector.yaml`; the upstream body is kept in
`body`, and a bounded excerpt of it goes into the message where it helps.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

from ..common.errors import UpstreamHTTPError

SERVICE = "pennylane_firm"
EXCERPT_CHARS = 300

# status -> (code, retryable, message)
_TABLE = {
    400: ("invalid_request", False,
          "Pennylane rejected the request as invalid: check the arguments."),
    401: ("token_invalid", False,
          "Pennylane refused the firm token as invalid: a valid token is "
          "needed, and retrying unchanged fails the same way."),
    403: ("scope_missing", False,
          "The firm token lacks the scope this action requires. A missing "
          "right is not an argument to fix: the token needs that scope."),
    404: ("not_found", False,
          "No such object for this firm: company_id is the firm-side id from "
          "the list of companies, and folder or file ids belong to that one "
          "company."),
    422: ("content_rejected", False,
          "Pennylane rejected the content: the values sent fail its checks. "
          "Fix them before calling again."),
    429: ("rate_limited", True,
          "Pennylane request rate exceeded (5 requests per second): wait "
          "about a minute before retrying."),
}
_UNAVAILABLE = ("upstream_unavailable", True,
                "Pennylane is temporarily unavailable: retry later.")

_SCOPE_RE = re.compile(r"\b([a-z_]+:(?:readonly|all))\b")

# Per-call override of the table: status -> (code, message).
Refusals = Mapping[int, Tuple[str, str]]


class PennylaneFirmError(UpstreamHTTPError):
    """A refusal of the Pennylane Firm API.

    `code` names the refusal (`token_invalid`, `scope_missing`…), `retryable`
    says whether the same call may succeed later, `body` is the upstream body.
    """

    def __init__(self, status_code: int, code: str, message: str, *,
                 retryable: bool = False, body: Any = None):
        super().__init__(status_code, body, service=SERVICE)
        self.code = code
        self.retryable = retryable
        self.args = (f"{SERVICE} HTTP {status_code} ({code}): {message}",)


class PennylaneFirmRateLimited(PennylaneFirmError):
    """429: the token's request rate was exceeded. Retryable after a pause;
    the client never waits it out by itself."""


class PennylaneFirmScopeMissing(PennylaneFirmError):
    """403: the token lacks a scope. `scope` is the one Pennylane names, when
    its message names one."""

    def __init__(self, status_code: int, code: str, message: str, *,
                 scope: Optional[str] = None, body: Any = None):
        super().__init__(status_code, code, message, retryable=False, body=body)
        self.scope = scope


def _body_of(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _excerpt(body: Any) -> str:
    """The upstream explanation, bounded: the message fields of a JSON body,
    or the raw text."""
    if isinstance(body, dict):
        parts = [str(body[k]) for k in ("error", "message", "errors", "details")
                 if body.get(k)]
        text = " — ".join(parts) if parts else str(body)
    else:
        text = str(body or "")
    text = " ".join(text.split())
    if len(text) > EXCERPT_CHARS:
        text = text[:EXCERPT_CHARS] + "…"
    return text


def error_from_response(response: Any,
                        refusals: Optional[Refusals] = None) -> PennylaneFirmError:
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
                                    "Pennylane refused the request.")
    if refusals and status in refusals:
        code, message = refusals[status]

    if status == 429:
        return PennylaneFirmRateLimited(status, code, message,
                                        retryable=True, body=body)
    if status == 403:
        found = _SCOPE_RE.search(excerpt)
        scope = found.group(1) if found else None
        detail = f" Required scope: {scope}." if scope else ""
        if excerpt:
            detail += f" Pennylane said: {excerpt}"
        return PennylaneFirmScopeMissing(status, code, message + detail,
                                         scope=scope, body=body)
    if excerpt and status != 401:
        message = f"{message} Pennylane said: {excerpt}"
    return PennylaneFirmError(status, code, message, retryable=retryable,
                              body=body)
