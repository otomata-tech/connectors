"""Inqom accounting API client (https://api.inqom.com).

Reference: the OpenAPI document served at `https://api.inqom.com/api/documentation`
(read 2026-09-17) and Inqom's help centre, integrations section
(`help.inqom.com/fr/intégrations`).

## Auth

OAuth2 Resource Owner grant (`grant_type=password`, scope `openid apidata`):
the application's `client_id`/`client_secret` plus an Inqom account's
`username`/`password`. The resulting Bearer token carries that account's
rights — a firm's system account sees all of the firm's dossiers, a named user
only those assigned to them. See `auth.py`.

## Vocabulary

- **company** (`companyId`): a firm (cabinet) or an SME the account can access.
- **dossier** (`dossierId`): one accounting file, i.e. one client company's books.
  The same id is called `accountingFolderId` or `enterpriseId` elsewhere in the API.
- Third parties (tiers) are auxiliary accounts of the chart of accounts
  (e.g. prefixes `401`, `411`): they are read through `list_accounts`.

## Protocol facts that shape a caller

- Dates are sent as `yyyy-MM-dd`.
- Entry lines paginate by `pageNumber` (1-indexed, at most 1000 lines per page);
  `count_entry_lines` gives the number of pages for the same filters.
- `create_entries` posts entries immediately: there is no draft state in this
  endpoint. Choosing a preview step is a tool-layer decision.
- Rate limit: 15 requests per second per client id + user, sliding window;
  over it, HTTP 429. Retried here with the documented backoff (0.5s, 1s, 2s, 4s).
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import requests

from ...config import require_secret
from ..common import raise_for_upstream
from . import auth

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
_BACKOFF = (0.5, 1, 2, 4)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ACCOUNT_TYPES = ("All", "Impactable")
BALANCE_SCOPES = ("Impacted", "All")


def _date(value: str, name: str) -> str:
    if not isinstance(value, str) or not _DATE.match(value):
        raise ValueError(f"{name} doit être une date yyyy-MM-dd — reçu {value!r}.")
    return value


def _choice(value: Optional[str], name: str, allowed: tuple) -> Optional[str]:
    if value is not None and value not in allowed:
        raise ValueError(f"{name} doit valoir l'un de {list(allowed)} — reçu {value!r}.")
    return value


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None`: an omitted kwarg must not become the string 'None'."""
    return {k: v for k, v in params.items() if v is not None}


class InqomClient:
    """Inqom accounting API client, OAuth2 Bearer auth (Resource Owner grant)."""

    API_BASE = "https://api.inqom.com"
    TOKEN_URL = "https://auth.inqom.com/identity/connect/token"
    SCOPE = "openid apidata"

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None):
        """
        Args:
            client_id / client_secret: the API application's keys, issued by
                Inqom (or `INQOM_CLIENT_ID` / `INQOM_CLIENT_SECRET`).
            username / password: the Inqom account the token acts as
                (or `INQOM_USERNAME` / `INQOM_PASSWORD`).
        """
        self.client_id = client_id or require_secret("INQOM_CLIENT_ID")
        self.client_secret = client_secret or require_secret("INQOM_CLIENT_SECRET")
        self.username = username or require_secret("INQOM_USERNAME")
        self._password = password or require_secret("INQOM_PASSWORD")
        self._key = auth.cred_key(self.TOKEN_URL, self.client_id, self.username,
                                  self.client_secret + "|" + self._password)
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _token(self) -> str:
        return auth.get_access_token(
            self.TOKEN_URL, client_id=self.client_id, client_secret=self.client_secret,
            username=self.username, password=self._password, scope=self.SCOPE,
            key=self._key)

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                 json_body: Any = None) -> Any:
        url = f"{self.API_BASE}{path}"
        renewed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self._token()}",
                       "Accept": "application/json"}
            resp = self.session.request(method, url, headers=headers,
                                        params=_clean(params or {}), json=json_body,
                                        timeout=_HTTP_TIMEOUT)
            if resp.status_code == 401 and not renewed:
                # A cached token can be revoked before its expiry: renew once.
                auth.invalidate(self._key)
                renewed = True
                continue
            if resp.status_code == 429 and attempt < len(_BACKOFF):
                time.sleep(_BACKOFF[attempt])
                attempt += 1
                continue
            raise_for_upstream(resp, service="inqom")
            if resp.status_code == 204 or not (resp.content or b"").strip():
                return None
            return resp.json()

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    # ------------------------------------------------------------------
    # companies and dossiers
    # ------------------------------------------------------------------

    def list_companies(self) -> List[dict]:
        """GET /provisioning/users/internal-access — the firms/SMEs the account
        can access. For `AccessType == "Company"` the company id is `Id`; for
        `AccessType == "Pme"` it is `CompanyId`."""
        return self._get("/provisioning/users/internal-access") or []

    def list_dossiers(self, company_id: int) -> List[dict]:
        """GET /provisioning/companies/{companyId}/accounting-folders — the
        dossiers of a firm/SME that the account is assigned to (all of them
        for a firm's system account)."""
        return self._get(f"/provisioning/companies/{int(company_id)}/accounting-folders") or []

    def get_dossier(self, dossier_id: int) -> dict:
        """GET /v1/dossiers/{dossierId}/dossier-permanent/general — the general
        section of a dossier's permanent file (identity, SIREN, legal form,
        addresses, accounting settings)."""
        return self._get(f"/v1/dossiers/{int(dossier_id)}/dossier-permanent/general") or {}

    def list_accounting_periods(self, dossier_id: int) -> List[dict]:
        """GET /v1/dossiers/{dossierId}/accounting-periods — fiscal years."""
        return self._get(f"/v1/dossiers/{int(dossier_id)}/accounting-periods") or []

    # ------------------------------------------------------------------
    # chart of accounts, journals, balances
    # ------------------------------------------------------------------

    def list_accounts(self, dossier_id: int, *, number_prefix: Optional[str] = None,
                      account_type: Optional[str] = None) -> List[dict]:
        """GET /v1/dossiers/{dossierId}/accounts — the chart of accounts.

        Args:
            number_prefix: keep accounts whose number starts with it
                (e.g. "401" suppliers, "411" customers).
            account_type: "Impactable" (postable accounts only) or "All".
        """
        return self._get(
            f"/v1/dossiers/{int(dossier_id)}/accounts",
            accountNumberPrefix=number_prefix,
            accountType=_choice(account_type, "account_type", ACCOUNT_TYPES)) or []

    def list_journals(self, dossier_id: int) -> List[dict]:
        """GET /v1/dossiers/{dossierId}/journals."""
        return self._get(f"/v1/dossiers/{int(dossier_id)}/journals") or []

    def list_balances(self, dossier_id: int, start_date: str, end_date: str, *,
                      account_numbers: Optional[List[str]] = None,
                      balance_scope: Optional[str] = None) -> List[dict]:
        """GET /v1/dossiers/{dossierId}/balances — trial balance over a period.

        Args:
            account_numbers: restrict to these accounts.
            balance_scope: "Impacted" (accounts with movements in the period,
                Inqom's default) or "All".
        """
        return self._get(
            f"/v1/dossiers/{int(dossier_id)}/balances",
            startDate=_date(start_date, "start_date"), endDate=_date(end_date, "end_date"),
            accountNumbers=list(account_numbers) if account_numbers else None,
            balanceScope=_choice(balance_scope, "balance_scope", BALANCE_SCOPES)) or []

    # ------------------------------------------------------------------
    # entries
    # ------------------------------------------------------------------

    def count_entry_lines(self, dossier_id: int, start_date: str, end_date: str, *,
                          account_number: Optional[str] = None) -> dict:
        """GET /v1/dossiers/{dossierId}/entry-lines/count —
        `{TotalLinesCount, TotalPagesCount}` for the same filters."""
        return self._get(
            f"/v1/dossiers/{int(dossier_id)}/entry-lines/count",
            startDate=_date(start_date, "start_date"), endDate=_date(end_date, "end_date"),
            accountNumber=account_number) or {}

    def list_entry_lines(self, dossier_id: int, start_date: str, end_date: str,
                         page_number: int = 1, *, account_number: Optional[str] = None,
                         journal_id: Optional[int] = None) -> dict:
        """GET /v1/dossiers/{dossierId}/entry-lines — one page (≤ 1000 lines)
        of entry lines dated in the period: `{EntryLines, CurrentPage}`."""
        if int(page_number) < 1:
            raise ValueError(f"page_number commence à 1 — reçu {page_number}.")
        return self._get(
            f"/v1/dossiers/{int(dossier_id)}/entry-lines",
            startDate=_date(start_date, "start_date"), endDate=_date(end_date, "end_date"),
            pageNumber=int(page_number), accountNumber=account_number,
            journalId=journal_id) or {}

    def create_entries(self, dossier_id: int, entries: List[dict]) -> List[dict]:
        """POST /v1/dossiers/{dossierId}/entries — post entries, immediately.

        Each entry: `{JournalId, Date, Lines: [{AccountNumber, Label, Currency,
        DebitAmount | CreditAmount}], EntryRef?, ExternalId?, Document?:
        {Reference?, Date}, DueDate?}` (Inqom's `CreateEntryCommand_V1`).
        Returns the inserted entries with their ids.
        """
        if not entries:
            raise ValueError("entries ne peut pas être vide.")
        return self._request("POST", f"/v1/dossiers/{int(dossier_id)}/entries",
                             json_body=list(entries)) or []

    # ------------------------------------------------------------------
    # accounting documents
    # ------------------------------------------------------------------

    def get_accounting_document(self, dossier_id: int, document_id: int) -> dict:
        """GET /v1/dossiers/{dossierId}/accounting-documents/{documentId} —
        `{Id, Url}`, the document's download URL."""
        return self._get(
            f"/v1/dossiers/{int(dossier_id)}/accounting-documents/{int(document_id)}") or {}
