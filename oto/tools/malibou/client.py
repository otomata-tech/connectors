"""Malibou Public API client — French payroll and HR (read-only).

Public API v1 (`https://app.malibou.com/api/public/v1`), authenticated by
`Authorization: Bearer <api key>`. Every route is scoped to one organization
(`/organizations/{organization_id}/…`): the key and the organization id are
created together by an organization admin, under Admin → Company → API keys.
One method = one endpoint; responses are returned as-is, the client invents no
semantics.

Scope: the self-serve surface — employees (collaborators), absences and work
locations.

What the caller needs to know, and cannot guess:

- **Scopes decide the fields.** A key carries the scopes ticked at creation.
  Without `org:employee:details:read`, collaborators come without social
  security number, address, phone, bank account, manager or team; without
  `org:absence:type:read`, absences come without their leave `type`. A missing
  scope on a whole route answers 403.

- **Lists are paged by number**: `page` from 1 and `limit`, both positive (the
  API answers 400 otherwise). Every list answers `{data, total, page, limit}`;
  the last page holds fewer than `limit` items. Collaborators are ordered by
  last then first name, absences and work locations by start date.

- **`get_collaborator` is NOT wrapped** in `data`, unlike the lists, and names
  the social security number `socialSecurityNumber` where the list says `ssn`.

- **Absences and work locations need a window**: `start_date` and `end_date`
  (`YYYY-MM-DD`) are required, and every record that OVERLAPS the window is
  returned.

Requires: requests
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..http.client import refuse_redirect

# (connect, read) — never an unbounded wait.
HTTP_TIMEOUT = (10, 60)

BASE_URL = "https://app.malibou.com/api/public/v1"

CONTRACT_STATUSES = ("past", "active", "future")
RECORD_STATUSES = ("approved", "pending", "rejected")

# Every route here is a GET: a 429/5xx is safe to replay.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

# The organization id and a collaborator id land in the path: letters, digits,
# `-` and `_` only, so that no value can reshape the URL.
_PATH_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DAY = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")


class MalibouClient:
    """Malibou Public API v1, read-only, one organization per client."""

    BASE_URL = BASE_URL

    def __init__(self, api_key: Optional[str] = None,
                 organization_id: Optional[str] = None):
        """
        Args:
            api_key: Malibou API key (Bearer), shown once at creation.
            organization_id: id of the organization the key belongs to.
        """
        self.api_key = require(api_key, "MALIBOU_API_KEY")
        self.organization_id = self._path_id(
            "organization_id", require(organization_id, "MALIBOU_ORGANIZATION_ID"))
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _path_id(name: str, value: Any) -> str:
        text = value.strip() if isinstance(value, str) else ""
        if not _PATH_ID.match(text):
            raise ValueError(
                f"`{name}` must be an id of letters, digits, '-' or '_'; got {value!r}.")
        return text

    @staticmethod
    def _day(name: str, value: Any) -> str:
        if not isinstance(value, str) or not _DAY.match(value.strip()):
            raise ValueError(f"`{name}` must be a date YYYY-MM-DD; got {value!r}.")
        return value.strip()

    @staticmethod
    def _positive(name: str, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"`{name}` must be an integer >= 1; got {value!r}.")
        return value

    @staticmethod
    def _choice(name: str, value: Optional[str], allowed: tuple) -> Optional[str]:
        if value is None:
            return None
        if value not in allowed:
            raise ValueError(f"`{name}` invalid: {value!r}. Accepted values: "
                             + ", ".join(allowed) + ".")
        return value

    @classmethod
    def _window(cls, start_date: Any, end_date: Any) -> Dict[str, str]:
        start = cls._day("start_date", start_date)
        end = cls._day("end_date", end_date)
        if end < start:
            raise ValueError("`end_date` must not be before `start_date`.")
        return {"startDate": start, "endDate": end}

    # --- transport ----------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET a route of the organization, with retry on 429/5xx. Redirects are
        refused: the key must never follow a 3xx to another host."""
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.BASE_URL}/organizations/{self.organization_id}{path}"
        last = None
        for attempt in range(MAX_ATTEMPTS):
            last = self.session.request(
                "GET", url, params=query or None, timeout=HTTP_TIMEOUT,
                allow_redirects=False)
            refuse_redirect(last)
            if last.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS - 1:
                break
            time.sleep(float(2 ** attempt))
        raise_for_upstream(last, service="malibou")
        return last.json() if last.content else {}

    # --- probe --------------------------------------------------------------

    def probe(self) -> Any:
        """Check that the key and the organization id authenticate, for the cost
        of one call (401 on a wrong key, 403 on a missing employee scope)."""
        return self.list_collaborators(limit=1)

    # --- reads ---------------------------------------------------------------

    def list_collaborators(self, establishment_id: Optional[str] = None,
                           contract_status: Optional[str] = None,
                           page: Optional[int] = None,
                           limit: Optional[int] = None) -> Any:
        """GET /collaborators — the organization's employees.

        `contract_status`: `active` (at least one active contract), `past` (all
        contracts ended), `future` (only an upcoming contract).
        """
        return self._get("/collaborators", {
            "establishmentId": establishment_id,
            "contractStatus": self._choice(
                "contract_status", contract_status, CONTRACT_STATUSES),
            "page": self._positive("page", page),
            "limit": self._positive("limit", limit),
        })

    def get_collaborator(self, collaborator_id: str,
                         establishment_id: Optional[str] = None) -> Any:
        """GET /collaborators/{collaborator_id} — one employee, with all their
        contracts. Not wrapped in `data`."""
        cid = self._path_id("collaborator_id", collaborator_id)
        return self._get(f"/collaborators/{cid}",
                         {"establishmentId": establishment_id})

    def list_absences(self, start_date: str, end_date: str, *,
                      status: Optional[str] = None,
                      establishment_id: Optional[str] = None,
                      page: Optional[int] = None,
                      limit: Optional[int] = None) -> Any:
        """GET /absences — absences overlapping [start_date, end_date]."""
        params: Dict[str, Any] = self._window(start_date, end_date)
        params.update({
            "status": self._choice("status", status, RECORD_STATUSES),
            "establishmentId": establishment_id,
            "page": self._positive("page", page),
            "limit": self._positive("limit", limit),
        })
        return self._get("/absences", params)

    def list_work_locations(self, start_date: str, end_date: str, *,
                            status: Optional[str] = None,
                            establishment_id: Optional[str] = None,
                            page: Optional[int] = None,
                            limit: Optional[int] = None) -> Any:
        """GET /work-locations — declared remote or on-site work overlapping
        [start_date, end_date]."""
        params: Dict[str, Any] = self._window(start_date, end_date)
        params.update({
            "status": self._choice("status", status, RECORD_STATUSES),
            "establishmentId": establishment_id,
            "page": self._positive("page", page),
            "limit": self._positive("limit", limit),
        })
        return self._get("/work-locations", params)
