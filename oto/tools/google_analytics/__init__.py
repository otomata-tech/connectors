"""Google Analytics 4 — read access via a service account key (Admin + Data v1beta).

`GA4Client` is the entry point; `auth` handles token issuance. Read-only:
no method writes to GA4.
"""
from __future__ import annotations

try:
    from .client import (
        DEFAULT_END_DATE,
        DEFAULT_START_DATE,
        GA4Client,
        GA4Error,
        GA4InvalidArgument,
        GA4PermissionDenied,
        GA4ServiceDisabled,
        build_filter,
        build_order_bys,
        flatten_report,
        property_name,
    )
    from .auth import ServiceAccountAuthError, parse_service_account_key
except ImportError as e:
    # The assertion signature comes from `google-auth` (extra `google`). Without
    # it, "No module named 'google'" would say neither what to install nor that only
    # this connector is affected — retranslated here, at the source. An ImportError
    # internal to the package bubbles up as-is.
    if not (getattr(e, "name", None) or "").startswith("google"):
        raise
    raise ImportError(
        "the `google_analytics` connector needs the `google` extra — install "
        f"`oto-core[google]` (`{e.name}` is missing): the service account key "
        "signs its assertion with `google-auth`.") from e

__all__ = [
    "DEFAULT_END_DATE", "DEFAULT_START_DATE", "GA4Client", "GA4Error",
    "GA4InvalidArgument", "GA4PermissionDenied", "GA4ServiceDisabled",
    "ServiceAccountAuthError", "build_filter", "build_order_bys", "flatten_report",
    "parse_service_account_key", "property_name",
]
