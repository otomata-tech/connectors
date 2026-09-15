"""
Lucca HR Suite REST API Client (v3, "legacy") — https://www.lucca.fr/

French HR platform: employee directory, leaves/absences, expense claims,
org structure. This client targets the **v3 "legacy" API** — the stable one,
used in production by thousands of Lucca customers — not v5 (OAuth2, newer,
partly beta).

Docs: https://developers.luccasoftware.com/api-reference/legacy/

Auth is a **static API key sent as a plain header** (NOT OAuth2, NOT Bearer):
    Authorization: lucca application={api_key}

Two secrets are needed — Lucca has no fixed base URL, each customer has its
own tenant subdomain:
  - LUCCA_API_KEY : the API key, generated from the Lucca account settings.
                    One key per customer instance.
  - LUCCA_DOMAIN  : the tenant subdomain only (e.g. "acme" for
                    acme.ilucca.net) — NOT the full URL, the client builds it.

Requires: requests

Usage:
    client = LuccaClient()  # resolves LUCCA_API_KEY + LUCCA_DOMAIN

    # Directory
    users = client.list_users(mail="jean.dupont@exemple.fr")
    user = client.get_user(42, fields="id,firstName,lastName,legalEntity[id,name]")

    # Absences — `date` is REQUIRED by Lucca on the list endpoint.
    leaves = client.list_leaves(date="between,2026-09-01,2026-09-30", owner_id=[42])
    reqs = client.list_leave_requests()
    one = client.get_leave_request(1234)

    # Notes de frais (expense claims)
    claims = client.list_expense_claims(status_id="Approved")

    # Organisation
    depts = client.list_departments()
    sites = client.list_establishments(is_archived=False)

    # Redact sensitive fields before they reach the agent (mask IBANs,
    # anonymize names) — see oto.tools.common.FieldFilter. Pass one in, or
    # let the constructor pick up a ~/.otomata/config.yaml "lucca" policy:
    from oto.tools.common import FieldFilter
    client = LuccaClient(field_filter=FieldFilter(rules=[
        {"fields": ["iban", "bic"], "action": "mask", "keep_last": 4},
        {"fields": ["nom", "prenom"], "action": "anonymize"},
    ]))
"""

import time
from typing import Any, Optional, Union

import requests

from ...config import require_secret
from ..common import FieldFilter, raise_for_upstream

# (connexion, lecture) — un host injoignable ne doit jamais bloquer indéfiniment.
_HTTP_TIMEOUT = (10, 60)


class LuccaClient:
    """Client for the Lucca HR suite REST API (v3, "legacy")."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        domain: Optional[str] = None,
        field_filter: Optional[FieldFilter] = None,
    ):
        """
        Initialize the Lucca client.

        Args:
            api_key: API key from the Lucca account settings (or LUCCA_API_KEY).
            domain: Tenant subdomain only, e.g. "acme" for acme.ilucca.net
                (or LUCCA_DOMAIN). Not a full URL.
            field_filter: Redacts sensitive fields (IBAN, names…) from every
                response. Defaults to the `field_filters.lucca` policy in
                ~/.otomata/config.yaml (no-op when none is configured).
        """
        self.api_key = api_key or require_secret("LUCCA_API_KEY")
        self.domain = domain or require_secret("LUCCA_DOMAIN")
        self.base_url = f"https://{self.domain}.ilucca.net"
        self.field_filter = field_filter or FieldFilter.from_config("lucca")
        self.session = requests.Session()

    # --- HTTP ---

    def _headers(self) -> dict:
        # La clé part en HEADER, jamais en query string (elle atterrirait dans
        # l'URL de toute exception requests, donc dans les logs/Sentry — fuite
        # vécue #284). Pas de "Bearer" : Lucca v3 veut ce format exact.
        return {
            "Authorization": f"lucca application={self.api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET `path` under the tenant base URL. Retries once on 429."""
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            resp = self.session.get(
                url, headers=self._headers(), params=params, timeout=_HTTP_TIMEOUT
            )
            if resp.status_code == 429 and attempt < 2:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            raise_for_upstream(resp, service="lucca")
            return self.field_filter.apply(resp.json()) if resp.content else {}

    def _list_v3(self, path: str, params: Optional[dict] = None) -> list:
        """GET a `/api/v3/...` list endpoint: unwraps `{"data": {"items": [...]}}`."""
        return self._get(path, params).get("data", {}).get("items", [])

    def _get_v3(self, path: str, params: Optional[dict] = None) -> dict:
        """GET a `/api/v3/...` detail endpoint: unwraps `{"data": {...}}`."""
        return self._get(path, params).get("data", {})

    def _list_org_structure(self, path: str, params: Optional[dict] = None) -> list:
        """GET an `/organization/structure/api/...` list endpoint.

        ⚠️ Pas la même enveloppe que le reste de la v3 legacy : les items sont
        à plat (`{"items": [...], "prev":..., "next":...}`, sans clé `data`).
        Même en-tête d'auth `lucca application=`, base différente.
        """
        return self._get(path, params).get("items", [])

    @staticmethod
    def _paging(offset: int, limit: int) -> str:
        """Build Lucca's `paging` query param: `"{offset},{limit}"`, max 1000."""
        if offset < 0:
            raise ValueError(f"offset doit être >= 0 — reçu {offset}.")
        if not (0 < limit <= 1000):
            raise ValueError(f"limit doit être entre 1 et 1000 (Lucca) — reçu {limit}.")
        return f"{offset},{limit}"

    @staticmethod
    def _bool_param(value: Optional[Union[bool, str]]) -> Optional[str]:
        """`True`/`False` Python -> `"true"`/`"false"`.

        `requests` sérialiserait sinon un bool Python en "True"/"False"
        (majuscule) — même piège que `onlyAssignedToMe` chez Folk. Non vérifié
        EN DIRECT sur Lucca (pas de clé de test) : normalisation défensive,
        conforme à la convention REST/OpenAPI habituelle pour un booléen de
        query string. Une chaîne déjà formée (ex. "true,false" pour
        isArchived) passe telle quelle.
        """
        if value is None or isinstance(value, str):
            return value
        return "true" if value else "false"

    # --- Directory (annuaire) ---

    def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[str] = None,
        ids: Optional[list] = None,
        mail: Optional[str] = None,
        login: Optional[str] = None,
        former_employees: Optional[bool] = None,
        dt_contract_start: Optional[str] = None,
        dt_contract_end: Optional[str] = None,
        modified_at: Optional[str] = None,
    ) -> list:
        """List employees. `GET /api/v3/users`.

        Args:
            fields: OData-style field selection, e.g.
                "id,firstName,lastName,legalEntity[id,name]".
            mail: Exact-match filter, supports `"like,..."` for partial match.
            login: Exact-match filter.
            former_employees: Include former employees (default: current only).
            dt_contract_start / dt_contract_end: `"({comparator},)?{date}"`,
                e.g. "since,2026-01-01"; dt_contract_end also accepts
                "notequal,null".
            modified_at: `"{comparator},{date-time}"`.
        """
        params: dict = {"paging": self._paging(offset, limit)}
        if fields:
            params["fields"] = fields
        if ids:
            params["id"] = ids
        if mail:
            params["mail"] = mail
        if login:
            params["login"] = login
        former = self._bool_param(former_employees)
        if former is not None:
            params["formerEmployees"] = former
        if dt_contract_start:
            params["dtContractStart"] = dt_contract_start
        if dt_contract_end:
            params["dtContractEnd"] = dt_contract_end
        if modified_at:
            params["modifiedAt"] = modified_at
        return self._list_v3("/api/v3/users", params)

    def get_user(self, user_id: Union[int, str], *, fields: Optional[str] = None) -> dict:
        """Fetch one employee by id. `GET /api/v3/users/{id}`.

        `fields="extendedData"` pulls the extended-data block.
        """
        params = {"fields": fields} if fields else None
        return self._get_v3(f"/api/v3/users/{user_id}", params)

    # --- Congés / absences (Timmi Absences) ---

    def list_leaves(
        self,
        date: str,
        *,
        offset: int = 0,
        limit: int = 1000,
        leave_account_id: Optional[list] = None,
        owner_id: Optional[list] = None,
        department_id: Optional[list] = None,
        legal_entity_id: Optional[list] = None,
    ) -> list:
        """List leaves (half-day absence records). `GET /api/v3/leaves`.

        Args:
            date: REQUIRED by Lucca (no unfiltered "all leaves"). Format:
                "yyyy-mm-dd" (equality), "since,yyyy-mm-dd", "until,yyyy-mm-dd",
                or "between,yyyy-mm-dd,yyyy-mm-dd".
            leave_account_id: Filter by leave account id(s) (PTO, RTT…).
            owner_id: Filter by employee id(s) — sent as `leavePeriod.ownerId`.
            department_id: Filter by department id(s).
            legal_entity_id: Filter by establishment id(s).
        """
        params: dict = {"date": date, "paging": self._paging(offset, limit)}
        if leave_account_id:
            params["leaveAccountId"] = leave_account_id
        if owner_id:
            params["leavePeriod.ownerId"] = owner_id
        if department_id:
            params["leavePeriod.owner.departmentId"] = department_id
        if legal_entity_id:
            params["leavePeriod.owner.legalEntityId"] = legal_entity_id
        return self._list_v3("/api/v3/leaves", params)

    def get_leave(self, leave_id: Union[int, str]) -> dict:
        """Fetch one leave by id. `GET /api/v3/leaves/{id}`."""
        return self._get_v3(f"/api/v3/leaves/{leave_id}")

    def list_leave_requests(self) -> list:
        """List leave requests (the workflow object behind an absence).
        `GET /api/v3/leaveRequests`.

        ⚠️ Lucca's OpenAPI spec documents **no query parameters at all** on
        this endpoint beyond the Authorization header — no `paging`, no
        filter (verified against developers.luccasoftware.com on
        2026-09-15; every other list endpoint on this client requires
        `paging`, this one has none). The response carries `data.count`
        alongside `data.items` — use it to tell whether you got everything.
        """
        return self._list_v3("/api/v3/leaveRequests")

    def get_leave_request(self, leave_request_id: Union[int, str]) -> dict:
        """Fetch one leave request by id. `GET /api/v3/leaveRequests/{id}`."""
        return self._get_v3(f"/api/v3/leaveRequests/{leave_request_id}")

    # --- Notes de frais (Cleemy Expenses) ---

    def list_expense_claims(
        self,
        *,
        offset: int = 0,
        limit: int = 1000,
        owner_id: Optional[list] = None,
        status_id: Optional[Union[int, str]] = None,
        declared_on: Optional[str] = None,
        order_by: Optional[str] = None,
    ) -> list:
        """List expense claims. `GET /api/v3/expenseClaims`.

        ⚠️ No `GET /api/v3/expenseClaims/{id}` exists in the legacy v3 spec —
        its OpenAPI doc declares only this list path (verified on
        2026-09-15). No `get_expense_claim` method on this client in
        consequence: adding one would promise an endpoint Lucca doesn't have.

        Args:
            owner_id: Employee id(s).
            status_id: Numeric id (1-9) or name — Created, PartiallyApproved,
                Approved, Controlled, ApprovedAndControlled, PaymentInitiated,
                Paid, Refused, Cancelled.
            declared_on: `"{comparator},{date}"`, e.g. "between,2026-01-01,2026-01-31".
            order_by: `"{field},{'asc'|'desc'}"`, e.g. "declaredOn,desc".
        """
        params: dict = {"paging": self._paging(offset, limit)}
        if owner_id:
            params["ownerId"] = owner_id
        if status_id is not None:
            params["statusId"] = status_id
        if declared_on:
            params["declaredOn"] = declared_on
        if order_by:
            params["orderBy"] = order_by
        return self._list_v3("/api/v3/expenseClaims", params)

    # --- Organisation ---

    def list_departments(
        self,
        *,
        offset: int = 0,
        limit: int = 1000,
        head_id: Optional[int] = None,
        parent_id: Optional[int] = None,
    ) -> list:
        """List departments. `GET /api/v3/departments`.

        This v3 endpoint is marked `deprecated: true` in Lucca's own OpenAPI
        spec (in favour of a v5 API, out of this lot's scope) but is still
        the only department listing sharing this client's auth/pagination/
        envelope conventions, and it works.
        """
        params: dict = {"paging": self._paging(offset, limit)}
        if head_id is not None:
            params["headId"] = head_id
        if parent_id is not None:
            params["parentId"] = parent_id
        return self._list_v3("/api/v3/departments", params)

    def get_department(self, department_id: Union[int, str]) -> dict:
        """Fetch one department by id. `GET /api/v3/departments/{id}`."""
        return self._get_v3(f"/api/v3/departments/{department_id}")

    def list_establishments(
        self,
        *,
        page: int = 1,
        limit: int = 10,
        ids: Optional[list] = None,
        legal_unit_id: Optional[list] = None,
        search: Optional[str] = None,
        is_archived: Optional[Union[bool, str]] = None,
    ) -> list:
        """List establishments. `GET /organization/structure/api/establishments`.

        ⚠️ NOT under `/api/v3/...` like the rest of this client: Lucca's
        legacy doc tree has no `GET /api/v3/establishments` at all (verified
        2026-09-15 — only this newer "organization structure" route is
        documented for establishments). Same `lucca application=` auth
        header, but a different base path, a different pagination
        convention (`page`/`limit`, 1-indexed, default page size 10 — not
        `paging`), and a response with no `data` wrapper (`_list_org_structure`
        handles that). No detail-by-id endpoint is documented either.

        Args:
            ids: Establishment id(s).
            legal_unit_id: Legal unit id(s).
            search: Name search.
            is_archived: bool, or the literal string "true,false" to get both.
        """
        params: dict = {"page": page, "limit": limit}
        if ids:
            params["id"] = ids
        if legal_unit_id:
            params["legalUnitId"] = legal_unit_id
        if search:
            params["search"] = search
        archived = self._bool_param(is_archived)
        if archived is not None:
            params["isArchived"] = archived
        return self._list_org_structure(
            "/organization/structure/api/establishments", params
        )
