"""Microsoft Graph delegated scopes, by surface, and the reading of a granted `scope`.

A consumer asks for the union of the surfaces it needs at sign-in
(`IDENTITY + FILES + MAIL`…) and checks, before calling a surface, that the grant
covers it: `short(FILES) <= normalize(grant.scope)`.

All the scopes below are consentable by the person themselves, except
`TEAMS_ADMIN`, which requires a tenant administrator (see
`auth.admin_consent_url`).
"""
from __future__ import annotations

GRAPH = "https://graph.microsoft.com/"

#: `offline_access` brings the refresh token, `User.Read` the person's identity (`/me`).
IDENTITY = ("offline_access", "User.Read")
#: SharePoint sites and libraries, OneDrive.
FILES = (GRAPH + "Files.ReadWrite.All", GRAPH + "Sites.ReadWrite.All")
#: The person's mailbox: read, draft, move, delete, send.
MAIL = (GRAPH + "Mail.ReadWrite", GRAPH + "Mail.Send")
#: The person's calendars: read and write events.
CALENDAR = (GRAPH + "Calendars.ReadWrite",)
#: Teams and channels the person belongs to, posting in channels, chats. No admin consent.
TEAMS = (GRAPH + "Team.ReadBasic.All", GRAPH + "Channel.ReadBasic.All",
         GRAPH + "ChannelMessage.Send", GRAPH + "Chat.ReadWrite")
#: Reading channel messages: requires a tenant administrator's consent.
TEAMS_ADMIN = (GRAPH + "ChannelMessage.Read.All",)
#: On refresh, ask for everything already consented (`.default`) rather than a list:
#: naming a scope that was never consented fails with AADSTS65001.
REFRESH = (GRAPH + ".default", "offline_access")

# OpenID Connect scopes: not Graph permissions, never prefixed with `GRAPH`.
OIDC = frozenset({"openid", "profile", "email", "offline_access"})


def _strip(scope: str) -> str:
    """`https://graph.microsoft.com/Files.ReadWrite.All` → `Files.ReadWrite.All`."""
    if scope.lower().startswith(GRAPH):
        return scope[len(GRAPH):]
    return scope


_CANONICAL = {_strip(s).lower(): _strip(s)
              for group in (IDENTITY, FILES, MAIL, CALENDAR, TEAMS, TEAMS_ADMIN, REFRESH)
              for s in group}
_CANONICAL.update({s: s for s in OIDC})


def _canonical(scope: str) -> str:
    name = _strip(scope)
    return _CANONICAL.get(name.lower(), name)


def short(scopes: tuple[str, ...]) -> frozenset[str]:
    """Our scope constants as short canonical names (`Files.ReadWrite.All`,
    `offline_access`), comparable with `normalize`."""
    return frozenset(_canonical(s) for s in scopes)


def normalize(scope: str) -> frozenset[str]:
    """The `scope` string returned by Entra (space-separated, short names or full
    URIs, any case, OIDC scopes included) as a set of short canonical names.

    A name absent from our constants is kept as returned, without its Graph prefix.
    """
    return frozenset(_canonical(s) for s in (scope or "").split())
