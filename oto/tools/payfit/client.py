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

**Everything the Partner API documents**, read and write:

- the company (global and French variants), the monthly payroll status;
- the collaborators (directory, creation), their contracts (global and French
  variants, creation), the worked time of a month;
- the absences (list, creation of an already-approved one, cancellation);
- the payslips (metadata and PDF), the company documents (PDF), the UK income
  tax and auto-enrolment documents;
- the payroll accounting of a month (structured entries and the raw export) and
  the bank payment file;
- the health-insurance and provident-fund contracts, an employee's affiliation
  to them and a regularization request;
- the meal vouchers of a month.

Two documented endpoints have **no method here**, and both are a deliberate
absence rather than an oversight:

- `POST /companies/{companyId}/billing-declarations` declares a PayFit
  *reseller's* billed volumes. It writes into PayFit's own billing of a partner,
  not into the company's HR data — nothing an HR or finance caller should reach.
- `POST /companies-fr/{companyId}/health-insurance-setup-sheets` uploads a PDF
  and an XML as `multipart/form-data`. A client method would have to take file
  bytes it has no way to obtain here.

⚠️ Responses EMBED personal data, depending on the scopes the key carries: a
collaborator may come with social security number, IBAN/BIC, birth date,
nationality, personal addresses, phones and emails; a French contract with the
social security number and the reason of termination; an absence with a type
that names a sick leave or a work accident; a payslip and an accounting entry
with pay. This client returns responses as the API sends them; **reducing them
is the caller's decision** — a key created with only the scopes a use needs is
the first reduction, and oto-backend applies a per-org field-redaction policy on
top.

## Protocol facts that shape a caller

- Lists are paginated by `maxResults` (1-50, default 10) and an opaque
  `nextPageToken`, and answer `{<items>: [...], meta: {nextPageToken, count}}`.
- A **month** is `YYYYMM` (January is `01`), never `YYYY-MM` — `payment-files`,
  `accounting`, `accounting-v2`, `payroll-status`, `contracts/time` and
  `meal-vouchers` all take it and all reject the dashed form.
- `/contracts-fr` is the French variant of `/contracts`: it adds DSN fields
  (contract nature, conventional status, IDCC), the working time modality
  (`forfait_jours`…) and the insurance contract ids. Its deprecated `fields`
  query parameter is never sent.
- Absences default to status `approved` upstream; `status="all"` returns every
  status. A created absence is **already approved** — there is no approval
  workflow in this API.
- Four endpoints answer **bytes, not JSON** (payslip PDF, company document PDF,
  accounting export, payment file): they go through `_get_file`, which never
  calls `.json()`.
- `DELETE /absences/{id}` carries a JSON body (the cancellation comment) —
  unusual, but that is what the spec documents.
- Rate limits: 50 read requests per second per client application; a 429
  surfaces as `UpstreamHTTPError` like any other non-2xx — not retried here.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

import requests

from ...config import require_secret
from ..common import UpstreamHTTPError, raise_for_upstream
from ._api import _AbsencesMixin, _ContractsMixin, _PayrollMixin, _PeopleMixin
from .params import clean as _clean
from .params import ident as _id

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
_BASE_URL = "https://partner-api.payfit.com"
_INTROSPECT_URL = "https://oauth.payfit.com/introspect"

# {sha256(api_key): company_id} — process-wide, never a key in clear.
_COMPANY_IDS: Dict[str, str] = {}


def _cred_key(api_key: str) -> str:
    return hashlib.sha256(f"{_INTROSPECT_URL}|{api_key}".encode()).hexdigest()


class PayfitClient(_PayrollMixin, _PeopleMixin, _ContractsMixin, _AbsencesMixin):
    """PayFit API client, company API key (Bearer).

    Construction and transport live here; the calls themselves are composed from
    `_api/` (one module per domain), so no file carries both.
    """

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

    def _request(self, method: str, path: str, *, params: Optional[dict] = None,
                 json_body: Optional[dict] = None) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", params=_clean(params or {}),
            json=json_body, timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="payfit")
        if not (resp.content or b"").strip():
            return None
        return resp.json()

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def _get_file(self, path: str, *, filename: str, mimetype: str,
                  **params: Any) -> Dict[str, Any]:
        """A body of BYTES — a PDF, a CSV export, a bank file.

        `.json()` is never called on it: on this API the very endpoints that
        matter for finance answer `application/pdf` or
        `application/octet-stream`, and parsing them as JSON would turn a
        working answer into a decode error. The upstream `Content-Type` wins
        over `mimetype`, which is only the fallback the spec announces.
        """
        resp = self.session.request(
            "GET", f"{self.BASE_URL}{path}", params=_clean(params), timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="payfit")
        served = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        return {"data": resp.content or b"", "filename": filename,
                "mimetype": served or mimetype}

    def _post(self, path: str, payload: Optional[dict] = None) -> Any:
        return self._request("POST", path, json_body=payload)

    def _put(self, path: str, payload: Optional[dict] = None) -> Any:
        return self._request("PUT", path, json_body=payload)

    def _delete(self, path: str, payload: Optional[dict] = None) -> Any:
        return self._request("DELETE", path, json_body=payload)

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

    def _company_path(self, suffix: str = "", *, fr: bool = False) -> str:
        """`/companies/{id}{suffix}` — or `/companies-fr/{id}{suffix}`, which is a
        DIFFERENT collection root, not a suffix on the global one."""
        root = "/companies-fr" if fr else "/companies"
        return f"{root}/{_id(self.get_company_id(), 'company_id')}{suffix}"
