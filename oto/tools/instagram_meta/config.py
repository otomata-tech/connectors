"""Where we call Meta, and under which application identity.

Two kinds of values, and the line between them is the subject of this module.

**The addresses stay here.** `graph.instagram.com`, `api.instagram.com`, the
authorization dialog, the requested permissions: these are the PUBLIC
coordinates of the "Instagram API with Instagram Login" product, documented by
Meta and identical for everyone who calls it. Naming what you call is
a client's job — a client that names nothing calls nothing.

**The application identity is not here, and never will be.** `InstagramApp`
(App ID + secret) is supplied by the caller, with no default value. This repo
is public: a Meta application belongs to whoever created it, who answers
for what it requests and for whom it invites. A default would have put the constant
back here under another name.

The split matters at runtime too: **the DATA client only needs the
token**. The App ID and secret are only used at two moments — the code exchange
and the switch to a long-lived token — that is, in `oauth.py`, never in
`client.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

#: Graph API version for DATA calls (profile, media, insights).
#: The OAuth endpoints, on the other hand, are not versioned — hence the two roots.
GRAPH_API_VERSION = "v25.0"

#: NON-versioned root: it serves the two token exchanges (`ig_exchange_token`
#: to switch to a long-lived token, `ig_refresh_token` to renew it).
GRAPH_ROOT = "https://graph.instagram.com"

#: Base of data calls, versioned.
GRAPH_API_BASE = f"{GRAPH_ROOT}/{GRAPH_API_VERSION}"

#: The authorization dialog. There is NO Facebook in this variant: the
#: person logs in with their Instagram account, and no Page is required.
AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"

#: Exchange of the code for a short-lived token (POST, form-encoded body).
TOKEN_URL = "https://api.instagram.com/oauth/access_token"

#: The permissions requested in the dialog. They must ALSO be ticked
#: on the application side, otherwise the consent ends in a token that cannot
#: read insights — a late failure, on a call that looks like a missing permission.
#: ⚠️ Instagram expects these values separated by COMMAS, not by spaces
#: (this is where this dialog differs most visibly from ordinary OAuth2).
SCOPES = ("instagram_business_basic", "instagram_business_manage_insights")

#: Bound of each outgoing HTTP call, set at call time.
HTTP_TIMEOUT = 30.0

#: Lifetime of a long-lived token, as Meta issues it.
LONG_LIVED_TTL_DAYS = 60

#: We renew as soon as LESS than this remains. Deliberately high (hence
#: frequent renewal): this token only renews WHILE IT LIVES — there is
#: no `refresh_token` that would survive its expiry. A tight threshold
#: would make the connection depend on the luck of a use in the last days;
#: one extra call on sporadic usage is the price of its survival.
RENEW_WHEN_REMAINING_DAYS = 53

#: Meta refuses to renew a token younger than 24 h. The threshold above
#: cannot lead there as long as Meta does issue 60 days; this bound guards the
#: case where it would issue less, where renewing immediately would cause a
#: refusal loop instead of a working connection.
MIN_TOKEN_AGE_HOURS = 24


@dataclass(frozen=True)
class InstagramApp:
    """The Meta application on whose behalf consent is requested.

    Supplied by whoever deploys the connector, no default. `app_secret` is a
    REAL secret (it signs the code exchange and the switch to a long-lived token): it must
    appear in no error message or log — which is why this
    module builds its own errors rather than letting those of the HTTP layer
    bubble up, which carry the called URL.
    """

    app_id: str
    app_secret: str

    def __post_init__(self) -> None:
        """An empty field RAISES here, not on the first request.

        An empty string builds a perfectly valid object and later, elsewhere,
        produces a Meta refusal that is then read as "the user
        did not authorize" — that is, the person gets blamed for an operator
        configuration error."""
        vides = [f.name for f in fields(self) if not str(getattr(self, f.name)).strip()]
        if vides:
            raise ValueError(
                f"InstagramApp: {', '.join(vides)} missing. These values are set "
                f"by whoever deploys the connector — there is no default, and there "
                f"never will be.")
