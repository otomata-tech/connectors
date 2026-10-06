"""
Kaspr API Client for LinkedIn profile enrichment.

Requires: requests
"""

import re
from typing import Optional, Dict, Any, List

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely

# Kaspr wants the BARE SLUG: a full URL (or a slash/query) causes a 500
# (verified live: `john-doe` → 200, `https://.../in/john-doe/` → 500).
_LINKEDIN_IN = re.compile(r"/in/([^/?#]+)", re.IGNORECASE)

# The names Kaspr accepts in `dataToGet` — the enum from ITS OpenAPI (`info.version`
# 2.0, `items.enum` of the `POST /profile/linkedin` body), not our recollection.
# A name outside the enum does not return a readable 400: the upstream parser crashes and the caller
# gets a **500** (`TypeError: Cannot read properties of undefined (reading
# 'push')`) — i.e. an outage, where it has a typo. Reproduced on
# 2026-09-01 on a sentinel profile, without consuming a credit:
#   ["emails", "phones", "company"] → 500 ;  ["workEmail", "phone"] → 402 ;  [] → 200.
#
# Those three names were no accident, and that is the lesson of the batch: `emails`,
# `phones` and `company` are field names of Kaspr's RESPONSE (`personalEmails`,
# `phones`, `company`) — reused as if they were INPUT values, then frozen
# in the docstring of the `kaspr_enrich_linkedin` MCP tool from its creation (2026-05-22).
# An agent that reads the schema applies what it reads there. Hence the LOCAL refusal below,
# which NAMES the accepted values: a correctable first attempt instead of an outage
# that looks upstream — and so gets retried in a loop.
DATA_TO_GET = ("workEmail", "directEmail", "phone")

# ⚠️ `personalEmail` is TOLERATED, not documented: it is not in Kaspr's enum,
# where the personal email is called `directEmail`. It has lingered in our docstrings since the
# client was created and has NEVER been measured — so it has exactly the shape of the defect
# being fixed here (`personalEmails` is, by contrast, a response field). We accept it
# anyway: refusing it would break a caller that may be using it successfully, and
# this batch should not settle by guesswork what a one-second probe would settle.
# To be measured on the sentinel id (500 ⇒ remove it from here and treat it like `emails`).
DATA_TO_GET_TOLERES = ("personalEmail",)

_ACCEPTES = DATA_TO_GET + DATA_TO_GET_TOLERES

# What Kaspr receives when the caller of THIS client asks for nothing. ⚠️ This is not
# the API's default: when omitted, Kaspr selects "all allowed fields". But the client
# never omits it — it substitutes the list below. "Defaults to all" was therefore
# wrong here, whatever the vendor's doc says.
DATA_TO_GET_DEFAUT = ["workEmail", "phone"]


def linkedin_slug(raw: str) -> str:
    """Normalizes a LinkedIn identifier (bare slug OR profile URL) → bare slug."""
    raw = (raw or "").strip()
    m = _LINKEDIN_IN.search(raw)
    if m:
        return m.group(1)
    return raw.rstrip("/").split("?")[0].split("#")[0]


class KasprClient:
    """
    Kaspr API client for:
    - LinkedIn profile enrichment
    - Email and phone number retrieval
    """

    BASE_URL = "https://api.developers.kaspr.io"
    # (connect, read) — Kaspr answers in <1s nominally; without a read timeout an upstream
    # blip leaves the call hanging FOREVER (lost thread, never logged —
    # seen 2026-07-22, signal #252: call invisible in the calllog, MCP client gone
    # at 60s, server hung). A timeout turns the blip into an actionable error.
    TIMEOUT = (10, 50)

    def __init__(self, api_key: str = None):
        """
        Initialize Kaspr client.

        Args:
            api_key: Kaspr API key
        """
        self.api_key = require(api_key, "KASPR_API_KEY")

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make API request."""
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "accept-version": "v2.0",
        }

        kwargs.setdefault("timeout", self.TIMEOUT)
        response = requests.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def verify_key(self) -> Dict[str, Any]:
        """
        Validate the API key.

        Kaspr v2.0 exposes no `/user` or `/me` endpoint — we check
        auth via a sentinel POST on `/profile/linkedin` with an id
        that is obviously not found. The API authenticates before looking up the
        profile so we get 401 if the key is bad, 200 + empty body
        otherwise (verified live 22/05).

        Returns: `{"valid": True}` if the key is OK, otherwise raises the HTTPError.
        """
        self._request(
            "POST", "profile/linkedin",
            json={
                "id": "__oto_verify_key__",
                "name": "__verify__",
                "dataToGet": [],
            },
        )
        return {"valid": True}

    def enrich_linkedin(
        self,
        linkedin_id: str,
        name: str = None,
        is_phone_required: bool = False,
        data_to_get: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Enrich a LinkedIn profile.

        Args:
            linkedin_id: LinkedIn slug ("john-doe-12345") or full profile URL
                ("https://www.linkedin.com/in/john-doe-12345/") — the bare slug
                is extracted automatically (a full URL makes Kaspr 500).
            name: Full name (helps matching)
            is_phone_required: Require phone number
            data_to_get: field names to retrieve — Kaspr's own enum, i.e.
                `DATA_TO_GET` ("workEmail", "directEmail", "phone"), plus the
                tolerated `DATA_TO_GET_TOLERES`. Anything else is refused HERE,
                because Kaspr answers 500 on a name it does not know.
                Omitted → `DATA_TO_GET_DEFAUT` (NOT every field).

        Returns:
            Enriched profile with emails and phones

        Raises:
            ValueError: `data_to_get` carries a name Kaspr does not know.
        """
        if data_to_get is not None:
            inconnus = [str(d) for d in data_to_get if d not in _ACCEPTES]
            if inconnus:
                raise ValueError(
                    "Kaspr only accepts the following in `dataToGet`: "
                    + ", ".join(DATA_TO_GET)
                    + " — received: " + ", ".join(inconnus)
                    + ". (An unknown name is not a clean refusal at Kaspr: "
                      "it causes a 500 server error.)")
        slug = linkedin_slug(linkedin_id)
        data = {"id": slug, "name": name or slug}
        if is_phone_required:
            data["isPhoneRequired"] = True
        data["dataToGet"] = data_to_get or list(DATA_TO_GET_DEFAUT)

        try:
            return self._request("POST", "profile/linkedin", json=data)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 402 and "phone" in data["dataToGet"]:
                data["dataToGet"] = [d for d in data["dataToGet"] if d != "phone"]
                return self._request("POST", "profile/linkedin", json=data)
            raise
