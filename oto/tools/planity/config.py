"""The SHAPE of Planity's endpoints, and the time bounds. **No values.**

⚠️ **This module carries, and will carry, no Planity constant.** It carried
three until 2026-09-09. They are public by design (any browser that opens
`pro.planity.com` receives them), so removing them is not a security gesture: it
is a gesture of GENERICITY. This repo is public and open source; a client
published here describes a protocol, it does not hard-code a third-party
company's endpoints, as if it were its official integration. They are now
**supplied by the caller**, with no default value: whoever deploys the connector
sets them, and answers for what they call.

No default, and that is the point: a default value would have put the constant
back here under another name, and nobody would have seen the difference.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

#: Bound on EACH outgoing HTTP call, set at call time and not only at client
#: construction: a shared client may be passed in by the caller, and a bare call
#: then waits forever. See `tests/test_http_timeouts.py`.
HTTP_TIMEOUT = 30.0

#: Customer search is interactive: it gets less waiting time than the
#: statistics lambdas, which aggregate months of receipts.
SEARCH_TIMEOUT = 15.0


@dataclass(frozen=True)
class PlanityEndpoints:
    """Where to call, and under which application identity — supplied by the caller.

    These are not secrets: they identify the Planity application, they authorize
    nothing on their own (what authorizes is the person's password).
    They can therefore appear in an error message or a debug log without being a
    leak.

    - `firebase_api_key` — Firebase Auth API key, sent as `?key=` to the
      `identitytoolkit` endpoints;
    - `firebase_app_id` — Firebase application identifier, sent as `p=` in the
      Realtime Database WebSocket handshake;
    - `rest_api` — root of the REST lambdas (statistics, receipts, search
      credentials), WITHOUT a trailing slash.
    """

    firebase_api_key: str
    firebase_app_id: str
    rest_api: str

    def __post_init__(self) -> None:
        """An empty field RAISES here, rather than on the first request.

        An empty string builds a perfectly valid object and produces, later and
        elsewhere, a Firebase 400 — which then reads as "wrong password". It is
        the most expensive failure mode of this family: it blames the user for
        an operator's configuration error."""
        vides = [f.name for f in fields(self) if not str(getattr(self, f.name)).strip()]
        if vides:
            raise ValueError(
                f"PlanityEndpoints: {', '.join(vides)} missing. These values are "
                f"set by whoever deploys the connector — there is no "
                f"default, and there never will be.")
        object.__setattr__(self, "rest_api", self.rest_api.rstrip("/"))
