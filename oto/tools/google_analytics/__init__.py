"""Google Analytics 4 — lecture par clé de compte de service (Admin + Data v1beta).

`GA4Client` est le point d'entrée ; `auth` porte l'émission du jeton. Lecture
seule : aucune méthode n'écrit dans GA4.
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
    # La signature de l'assertion vient de `google-auth` (extra `google`). Sans
    # lui, « No module named 'google' » ne dirait ni quoi installer ni que seul
    # ce connecteur est concerné — retraduit ici, à l'origine. Un ImportError
    # interne au paquet remonte tel quel.
    if not (getattr(e, "name", None) or "").startswith("google"):
        raise
    raise ImportError(
        "le connecteur `google_analytics` a besoin de l'extra `google` — installe "
        f"`oto-core[google]` (il manque `{e.name}`) : la clé du compte de service "
        "signe son assertion avec `google-auth`.") from e

__all__ = [
    "DEFAULT_END_DATE", "DEFAULT_START_DATE", "GA4Client", "GA4Error",
    "GA4InvalidArgument", "GA4PermissionDenied", "GA4ServiceDisabled",
    "ServiceAccountAuthError", "build_filter", "build_order_bys", "flatten_report",
    "parse_service_account_key", "property_name",
]
