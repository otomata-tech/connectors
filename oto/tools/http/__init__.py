"""Generic `http` connector — read-only multi-auth HTTP node (ADR 0037).

Consumed by the `oto_mcp/tools/http.py` adapter (oto-backend's `http` connector).
Pure (`requests` only). SSRF protection is a network egress
control at the platform level, not code here."""
from .client import (
    AUTH_MODES,
    ApiKeyHeader,
    ApiKeyQuery,
    BasicAuth,
    HttpConnectorClient,
    NoAuth,
    OAuth2ClientCredentials,
    RedirectRefused,
    StaticBearer,
    UpstreamAuth,
    build_auth,
)

__all__ = [
    "AUTH_MODES",
    "ApiKeyHeader",
    "ApiKeyQuery",
    "BasicAuth",
    "HttpConnectorClient",
    "NoAuth",
    "OAuth2ClientCredentials",
    "RedirectRefused",
    "StaticBearer",
    "UpstreamAuth",
    "build_auth",
]
