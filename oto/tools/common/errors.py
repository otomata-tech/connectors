"""Typed connector errors.

`UpstreamHTTPError` distinguishes a **refusal by the third-party API** (HTTP status >= 400:
rejected input, invalid credential, missing target, rate limit…) from an **internal bug**
in the code. The `status_code` lets consumers (MCP adapter, error tracking)
route a 4xx as a *handled connector error* — traced in the call backlog,
returned cleanly to the agent — rather than as a backend defect to alert on.

`raise_for_upstream(resp, service=...)` replaces the duplicated block
`if resp.status_code >= 400: parse body; raise Exception(...)` found in every
client. Agnostic to `requests`/`httpx` (same `.status_code` / `.json()` / `.text`).
"""
from __future__ import annotations

from typing import Any, Optional


class UpstreamHTTPError(Exception):
    """A third-party API answered with an error (status >= 400).

    `status_code` = upstream HTTP code, `body` = parsed body (dict) or raw text,
    `service` = connector name (prefixes the message, e.g. « folk HTTP 422: … »).
    """

    def __init__(self, status_code: int, body: Any = None, *, service: Optional[str] = None):
        self.status_code = status_code
        self.body = body
        self.service = service
        prefix = f"{service} " if service else ""
        super().__init__(f"{prefix}HTTP {status_code}: {body}")

    @property
    def is_client_error(self) -> bool:
        """4xx — the request was bad (our input / our credentials)."""
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        """5xx — the upstream is broken."""
        return 500 <= self.status_code < 600


def raise_for_upstream(resp: Any, *, service: Optional[str] = None) -> None:
    """Raise `UpstreamHTTPError` if `resp.status_code >= 400`, otherwise no-op.

    Parses the body as JSON, falling back to raw text. Compatible with `requests.Response`
    and `httpx.Response`.
    """
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise UpstreamHTTPError(resp.status_code, body, service=service)
