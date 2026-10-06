"""Unipile connector — hosted LinkedIn (and other IM) via the Unipile API v2.

Unipile keeps the LinkedIn session server-side (real Chrome + residential
proxy), which sidesteps the two constraints of the local browser: TLS
fingerprint and session isolation (the cookie doesn't live on our datacenter IP,
so it neither exposes nor disconnects the user's session). See oto-mcp#5.
"""

from .client import UnipileClient, UnipileError, parse_feed


def make_unipile_client(api_key=None, dsn=None, account_id=None, provider=None):
    """Unipile client factory (construction seam consumed by oto-mcp).

    `provider` = the channel of the operated account (LINKEDIN, WHATSAPP, …). It decides
    the messaging endpoint shape (inbox vs flat): a caller that omits it is
    assumed to be LinkedIn, and the client recovers on Unipile's 501."""
    return UnipileClient(api_key=api_key, dsn=dsn, account_id=account_id,
                         provider=provider)


__all__ = ["UnipileClient", "UnipileError", "parse_feed", "make_unipile_client"]
