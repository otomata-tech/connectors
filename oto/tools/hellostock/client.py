"""HelloStock — client for the marketplace administration API (`/api/admin`).

Contract: OpenAPI 3.1 « HelloStock — admin API » `1.0.0`, served by
`GET /api/admin/openapi.json` behind authentication. Each method below
is ONE endpoint of this contract; bodies and responses pass through as-is, the
client invents no semantics and re-types nothing.

**Authentication**: `Authorization: Bearer hs_…`, a **personal token** created by
each user from their HelloStock account. It carries the rights of THEIR
user, and these routes require an administrator account:

- unknown or revoked token → **401**;
- valid token of a non-administrator account → **403**.

Both arrive as `UpstreamHTTPError` (`status_code`, `body = {"error": …}`):
it is up to the serving layer to tell the user, the client does not know the screen
where a token is recreated.

**Lists**: `limit` (1–200, server default 50) + opaque `cursor`; response
`{items, nextCursor, total}`, `nextCursor` null on the last page, `total` = everything
matching the filters. Requests and offers go from newest to oldest,
members and positionings by ascending identifier.

**Filters strictly validated by the server**: a value outside the reference list, a
malformed date or an out-of-bounds limit answer **400** with a message — never
an empty list. The client therefore does not revalidate business values: it relays them,
and the server's refusal is the answer. The reference lists published by the contract are
exposed below as constants, so that the serving layer announces them without
copying them; the service code (`service`) is not part of them — the contract refers
to a catalog it does not publish.

**Writes** — three, and they act on the production marketplace:

- `send_demande`: sends an **email** to the chosen members (the request's
  specs, without the buyer's identity), traced under the name of the administrator whose
  token it is. **Never retried**: upstream has no idempotency key, a
  response lost in flight would make the same people be written to twice;
- `update_demande_status`, `update_offre`: status, and keywords for an offer
  (public: they feed the search; normalized and filtered by the server).

**Deliberately absent** (not to be « completed » without a decision): deleting a
member (`DELETE /users/{id}`, cascading to everything they posted), replacing
a site content section (`PUT /content/{slug}`) and downloading a
positioning's quote (`GET /positionnements/{id}/devis`, a PDF).

**Redirects refused**: a 3xx is never followed. A base address that
redirects (other host, login page) would otherwise return an HTML page as a 200, or
lose the authentication header by changing host — both would read
as something other than a configuration error.

Requires: requests
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

SERVICE = "hellostock"
DEFAULT_BASE_URL = "https://hellostock.fr"
API_PREFIX = "/api/admin"

# (connect, read) — no unbounded wait.
_HTTP_TIMEOUT = (10, 30)

# Reference lists published by the contract (OpenAPI enumerations).
STATUSES = ("declared", "qualified", "published", "closed")
MATIERES = ("acier", "inox", "aluminium", "cuivre", "laiton", "autre")
CERTIFICATS = ("dispo", "verifie", "sans-mots-cles")
SECTORS = (
    "tolerie_chaudronnerie", "usinage_mecanique", "decoupe_service", "negoce_metaux",
    "recyclage_ferraille", "fonderie", "construction_metallique",
    "industrie_fabricant", "autre",
)

# Only READS are retried, on rate limiting and transient unavailability.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS = 3


class HelloStockProtocolError(Exception):
    """The server answered, but not like the API described by the contract
    (redirect, body that is not JSON). A configuration or deployment defect,
    not a business refusal: it is not fixed by changing the call."""


def _positive_id(value: Any, name: str) -> int:
    """A numeric identifier goes into the PATH: it is required to be an integer
    and positive, never a string that could carry a `/`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"`{name}` must be a positive integer (got {value!r}).")
    return value


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Parameters set to `None` dropped; booleans as `true`/`false` (requests would write
    `True`, which the server does not read as a boolean)."""
    out: Dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        out[k] = ("true" if v else "false") if isinstance(v, bool) else v
    return out


class HelloStockAdminClient:
    """HelloStock admin API client, Bearer auth with personal token `hs_…`."""

    def __init__(self, token: Optional[str] = None,
                 base_url: Optional[str] = None):
        """
        Args:
            token: personal API token.
            base_url: site root (default `https://hellostock.fr`).
        """
        self.token = require(token, "HELLOSTOCK_API_TOKEN")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        # Token in the HEADER only: in a query string it would enter the URL,
        # hence the message of any exception and the access logs.
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{API_PREFIX}{path}"
        retryable = method == "GET"
        resp = None
        for attempt in range(_MAX_ATTEMPTS):
            resp = self.session.request(
                method, url, params=_clean(params or {}) or None, json=body,
                timeout=_HTTP_TIMEOUT, allow_redirects=False)
            if (resp.status_code not in _RETRY_STATUSES or not retryable
                    or attempt == _MAX_ATTEMPTS - 1):
                break
            time.sleep(float(2 ** attempt))
        if 300 <= resp.status_code < 400:
            raise HelloStockProtocolError(
                f"HelloStock answered with a redirect ({resp.status_code} to "
                f"{resp.headers.get('Location')!r}) instead of the API: the base "
                f"address {self.base_url!r} does not point to the administration API.")
        raise_for_upstream(resp, service=SERVICE)
        try:
            return resp.json()
        except ValueError:
            ctype = resp.headers.get("Content-Type")
            raise HelloStockProtocolError(
                f"HelloStock answered {resp.status_code} without a JSON body "
                f"(Content-Type {ctype!r}) on {method} {API_PREFIX}{path}.") from None

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    # --- requests (demandes) ------------------------------------------------

    def list_demandes(self, *, status: Optional[str] = None,
                      since: Optional[str] = None, until: Optional[str] = None,
                      departement: Optional[str] = None,
                      matiere: Optional[str] = None, service: Optional[str] = None,
                      q: Optional[str] = None, limit: Optional[int] = None,
                      cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /demandes — from newest to oldest.

        `since`/`until`: `YYYY-MM-DD` or ISO 8601 datetime, on `createdAt`
        (`since` inclusive, `until` exclusive). `departement` is read from the postal code
        of the attached company: a lead without an account does not match it.
        """
        return self._get("/demandes", status=status, since=since, until=until,
                         departement=departement, matiere=matiere, service=service,
                         q=q, limit=limit, cursor=cursor)

    def get_demande(self, demande_id: int) -> Dict[str, Any]:
        """GET /demandes/{id} — the listing shape, plus its positionings and
        its sends (from newest to oldest)."""
        return self._get(f"/demandes/{_positive_id(demande_id, 'demande_id')}")

    def update_demande_status(self, demande_id: int, status: str) -> Dict[str, Any]:
        """PATCH /demandes/{id} `{status}` — returns `{success}`."""
        return self._request(
            "PATCH", f"/demandes/{_positive_id(demande_id, 'demande_id')}",
            body={"status": status})

    def send_demande(self, demande_id: int, user_ids: Sequence[int],
                     message: Optional[str] = None) -> Dict[str, Any]:
        """POST /demandes/{id}/envoyer `{userIds, message?}` — sends an email to
        each of the designated members, and traces each effective send.

        Returns `{success, envoyes, echecs, noop}`: `echecs` = the addresses whose
        send failed, `noop` = the server has no mail service configured (sends
        simulated, but traced). 502 if no email could go out. `message` goes
        into the email as-is.
        """
        ids = [_positive_id(u, "user_ids[]") for u in (user_ids or [])]
        if not ids:
            raise ValueError("`user_ids`: at least one recipient member.")
        body: Dict[str, Any] = {"userIds": ids}
        if message is not None:
            body["message"] = message
        return self._request(
            "POST", f"/demandes/{_positive_id(demande_id, 'demande_id')}/envoyer",
            body=body)

    # --- offers (offres) ----------------------------------------------------

    def list_offres(self, *, status: Optional[str] = None,
                    since: Optional[str] = None, until: Optional[str] = None,
                    departement: Optional[str] = None,
                    matiere: Optional[str] = None, certificat: Optional[str] = None,
                    q: Optional[str] = None, limit: Optional[int] = None,
                    cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /offres — from newest to oldest.

        `certificat=sans-mots-cles` is the enrichment queue: certificate attached
        and no keyword, to be handled by `update_offre(keywords=…)`.
        """
        return self._get("/offres", status=status, since=since, until=until,
                         departement=departement, matiere=matiere,
                         certificat=certificat, q=q, limit=limit, cursor=cursor)

    def get_offre(self, offre_id: int) -> Dict[str, Any]:
        """GET /offres/{id} — the listing shape, plus the detail of the certificate
        verdict (which carries the heat number: admin surface only)."""
        return self._get(f"/offres/{_positive_id(offre_id, 'offre_id')}")

    def update_offre(self, offre_id: int, *, status: Optional[str] = None,
                     keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """PATCH /offres/{id} `{status?, keywords?}` — at least one of the two.

        `keywords` REPLACES the existing list; the server normalizes (spaces,
        case, duplicates) and refuses outright a list that carries an identity
        (steelmaker, heat or order number). Returns `{success}`.
        """
        body: Dict[str, Any] = {}
        if status is not None:
            body["status"] = status
        if keywords is not None:
            body["keywords"] = list(keywords)
        if not body:
            raise ValueError("`status` or `keywords`: at least one of the two.")
        return self._request(
            "PATCH", f"/offres/{_positive_id(offre_id, 'offre_id')}", body=body)

    # --- members ------------------------------------------------------------

    def list_users(self, *, q: Optional[str] = None, sector: Optional[str] = None,
                   service: Optional[str] = None, is_admin: Optional[bool] = None,
                   has_offres: Optional[bool] = None,
                   has_demandes: Optional[bool] = None,
                   limit: Optional[int] = None,
                   cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /users — account directory, by ascending identifier (alphabetical
        sorting is left to the caller)."""
        return self._get("/users", q=q, sector=sector, service=service,
                         isAdmin=is_admin, hasOffres=has_offres,
                         hasDemandes=has_demandes, limit=limit, cursor=cursor)

    def get_user(self, user_id: int) -> Dict[str, Any]:
        """GET /users/{id} — the member, their detailed company and their activity
        (requests, offers, messaging threads where they are the buyer)."""
        return self._get(f"/users/{_positive_id(user_id, 'user_id')}")

    # --- positionings -------------------------------------------------------

    def list_positionnements(self, *, demande_id: Optional[int] = None,
                             user_id: Optional[int] = None,
                             since: Optional[str] = None,
                             limit: Optional[int] = None,
                             cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /positionnements — the suppliers who offered themselves on a
        request, by ascending identifier."""
        return self._get("/positionnements", demandeId=demande_id, userId=user_id,
                         since=since, limit=limit, cursor=cursor)


__all__ = [
    "CERTIFICATS", "DEFAULT_BASE_URL", "HelloStockAdminClient",
    "HelloStockProtocolError", "MATIERES", "SECTORS", "STATUSES",
]
