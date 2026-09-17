"""PayFit API client (https://partner-api.payfit.com) — payroll and HR software.

Reference: https://developers.payfit.io (API Reference, OpenAPI 3.0 "Partner API"
v1.0, read 2026-09-17). Paths, query parameters and envelopes below come from it.

Auth is a company API key sent as `Authorization: Bearer {api_key}`. A key is
created by an admin of the company in the PayFit app (Integrations → API),
carries the scopes chosen at creation, and gives access to that company only.

## Company id

Every path hangs under `/companies/{companyId}`. The id is not typed by the
caller: it is read from the token introspection endpoint
(`POST https://oauth.payfit.com/introspect`, body `{"token": ...}`), which
answers `{active, scope, company_id}`. It is cached process-wide, keyed by a
hash of the key — a server builds one client per call, so a cache held by the
instance would introspect on every call.

## Scope of this client

Read only: the company, the collaborators, their contracts, their absences.

The API also serves payslips, accounting and payment files, health-insurance
and provident-fund contracts, meal vouchers, UK tax documents, and write
endpoints (collaborators, contracts, absences). **None of those has a method
here.**

⚠️ In-scope responses still EMBED personal data, depending on the scopes the
key carries: a collaborator may come with social security number, IBAN/BIC,
birth date, nationality, personal addresses, phones and emails; a French
contract with social security number and the reason of termination; an
absence with a type that names a sick leave or a work accident. This client
returns responses as the API sends them; reducing them is the caller's
decision. A key created with only the scopes a use needs is the first
reduction.

## Protocol facts that shape a caller

- Lists are paginated by `maxResults` (1-50, default 10) and an opaque
  `nextPageToken`, and answer `{<items>: [...], meta: {nextPageToken, count}}`.
- `/contracts-fr` is the French variant of `/contracts`: it adds DSN fields
  (contract nature, conventional status, IDCC). Its deprecated `fields` query
  parameter is never sent.
- Absences default to status `approved` upstream; `status="all"` returns every
  status.
- Rate limits: 50 read requests per second per client application; a 429
  surfaces as `UpstreamHTTPError` like any other non-2xx — not retried here.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Sequence, Union

import requests

from ...config import require_secret
from ..common import UpstreamHTTPError, raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
_BASE_URL = "https://partner-api.payfit.com"
_INTROSPECT_URL = "https://oauth.payfit.com/introspect"
_MAX_LIMIT = 50
# Identifiers in the spec are Mongo ObjectIds, UUIDs or digits: a path segment
# is refused unless it is made of those characters only.
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

# {sha256(api_key): company_id} — process-wide, never a key in clear.
_COMPANY_IDS: Dict[str, str] = {}


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become 'None' in the URL."""
    return {k: v for k, v in params.items() if v is not None}


def _id(value: Any, name: str) -> str:
    text = str(value) if value is not None else ""
    if not _ID.fullmatch(text):
        raise ValueError(f"{name} invalide — reçu {value!r}.")
    return text


def _page(limit: int, cursor: Optional[str]) -> Dict[str, Any]:
    if not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit doit être entre 1 et {_MAX_LIMIT} — reçu {limit}.")
    return _clean({"maxResults": limit, "nextPageToken": cursor or None})


def _cred_key(api_key: str) -> str:
    return hashlib.sha256(f"{_INTROSPECT_URL}|{api_key}".encode()).hexdigest()


class PayfitClient:
    """PayFit API client, company API key (Bearer), read only."""

    BASE_URL = _BASE_URL
    INTROSPECT_URL = _INTROSPECT_URL

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: PayFit company API key (or secret `PAYFIT_API_KEY`),
                created by a company admin in the PayFit app.
        """
        self.api_key = api_key or require_secret("PAYFIT_API_KEY")
        self.session = requests.Session()
        # The key travels in a HEADER (and in the introspection BODY), never in
        # the query string.
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.session.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _get(self, path: str, **params: Any) -> Any:
        resp = self.session.request(
            "GET", f"{self.BASE_URL}{path}", params=_clean(params), timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="payfit")
        if not (resp.content or b"").strip():
            return None
        return resp.json()

    def get_company_id(self) -> str:
        """The company the key belongs to, from token introspection (cached).

        The introspection answer is never copied into an exception: only its
        status code is, so a message can never carry the key."""
        k = _cred_key(self.api_key)
        cached = _COMPANY_IDS.get(k)
        if cached:
            return cached
        resp = self.session.request(
            "POST", self.INTROSPECT_URL, json={"token": self.api_key},
            timeout=_HTTP_TIMEOUT)
        if resp.status_code >= 400:
            raise UpstreamHTTPError(resp.status_code, {"error": "introspection refused"},
                                    service="payfit")
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        company_id = payload.get("company_id") if isinstance(payload, dict) else None
        if not (isinstance(payload, dict) and payload.get("active") and company_id):
            raise UpstreamHTTPError(401, {"error": "inactive API key"}, service="payfit")
        _COMPANY_IDS[k] = str(company_id)
        return _COMPANY_IDS[k]

    def _company_path(self, suffix: str = "") -> str:
        return f"/companies/{_id(self.get_company_id(), 'company_id')}{suffix}"

    # ================================================================
    # Company
    # ================================================================

    def get_company(self) -> Any:
        """GET /companies/{companyId} — no scope required."""
        return self._get(self._company_path())

    # ================================================================
    # Collaborators — scope collaborators:read (+ management / contracts)
    # ================================================================

    def list_collaborators(self, *, limit: int = 50, cursor: Optional[str] = None,
                           email: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/collaborators.

        Args:
            email: only collaborators with this email in one of their contracts
                (the login email is not searched).
        """
        return self._get(self._company_path("/collaborators"),
                         **_page(limit, cursor), email=email)

    def get_collaborator(self, collaborator_id: str) -> Any:
        """GET /companies/{companyId}/collaborators/{collaboratorId}."""
        return self._get(self._company_path(
            f"/collaborators/{_id(collaborator_id, 'collaborator_id')}"))

    # ================================================================
    # Contracts — scope contracts:read
    # ================================================================

    def list_contracts(self, *, limit: int = 50, cursor: Optional[str] = None,
                       include_in_progress: Optional[bool] = None,
                       fr: bool = False) -> Any:
        """GET /companies/{companyId}/contracts (or /contracts-fr when `fr`).

        Active, pending and last year's archived contracts.

        Args:
            include_in_progress: also contracts still being created.
            fr: the French variant, with DSN fields.
        """
        flag = None if include_in_progress is None else (
            "true" if include_in_progress else "false")
        return self._get(self._company_path("/contracts-fr" if fr else "/contracts"),
                         **_page(limit, cursor), includeInProgressContracts=flag)

    def get_contract(self, contract_id: str, *, fr: bool = False) -> Any:
        """GET /companies/{companyId}/contracts/{contractId} (or /contracts-fr/…)."""
        base = "/contracts-fr" if fr else "/contracts"
        return self._get(self._company_path(
            f"{base}/{_id(contract_id, 'contract_id')}"))

    # ================================================================
    # Absences — scope time:read
    # ================================================================

    def list_absences(self, *, limit: int = 50, cursor: Optional[str] = None,
                      contract_id: Optional[str] = None,
                      status: Optional[Union[str, Sequence[str]]] = None,
                      begin_date: Optional[str] = None,
                      end_date: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/absences.

        Args:
            contract_id: only this contract's absences.
            status: approved (upstream default) | pending_approval | declined |
                cancelled | pending_cancellation | all — one or several.
            begin_date / end_date: YYYY-MM-DD; absences overlapping the window.
        """
        if contract_id is not None:
            contract_id = _id(contract_id, "contract_id")
        if status is not None and not isinstance(status, str):
            status = ",".join(status)
        return self._get(self._company_path("/absences"), **_page(limit, cursor),
                         contractId=contract_id, status=status or None,
                         beginDate=begin_date, endDate=end_date)
