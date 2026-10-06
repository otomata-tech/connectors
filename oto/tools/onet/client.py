"""O*NET client — O*NET Web Services, API version 2.0 (api-v2.onetcenter.org).

The US Department of Labor's occupation reference: ~1,000 occupations
coded **O*NET-SOC** (8 digits, `15-1299.08`), each with its description,
tasks and the job titles actually encountered.

REST API, **GET only**, auth via the `X-API-Key` header (free key, reserved for
registered developers; refused in the query string). Errors: 422 with a body
`{"error": …}` (missing parameter, nonexistent or obsolete O*NET-SOC code, data
missing for this occupation); 429 when the service is saturated — wait at least
200 ms before retrying. Lists paginated by `start`/`end` (1-based index,
2,000 items at most per page); the response carries `start`, `end`, `total`, and
`next`/`prev` when they exist.

O*NET OnLine services served here: keyword search, an occupation's record, and its
tasks (summary report).

Requires: requests
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class ONetClient:
    """O*NET Web Services v2 client (https://api-v2.onetcenter.org), `X-API-Key` header."""

    BASE_URL = "https://api-v2.onetcenter.org"

    def __init__(self, api_key: str = None):
        """
        Args:
            api_key: O*NET Web Services key.
        """
        self.api_key = require(api_key, "ONET_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.api_key,
                                     "Accept": "application/json"})

    # --- transport ----------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
             timeout: int = 30) -> Dict[str, Any]:
        resp = self.session.get(f"{self.BASE_URL}{path}",
                                params={k: v for k, v in (params or {}).items()
                                        if v is not None},
                                timeout=(10, timeout))
        raise_for_upstream(resp, service="onet")
        return resp.json() if resp.content else {}

    @staticmethod
    def normalize_code(code: str) -> str:
        """`15-1299.08`, `15-1299` ou `151299` → code O*NET-SOC (`15-1299.08`,
        `15-1299.00`). A 6-digit SOC designates the SOC-level occupation: `.00`."""
        m = re.fullmatch(r"(\d{2})-?(\d{4})(?:\.(\d{2}))?", str(code or "").strip())
        if not m:
            raise ValueError(f"invalid O*NET-SOC code: {code!r} — expected '15-1299.08' "
                             "(or a 6-digit SOC, read as '.00')")
        return f"{m.group(1)}-{m.group(2)}.{m.group(3) or '00'}"

    # --- O*NET OnLine -------------------------------------------------------

    def search_occupations(self, keyword: str, start: Optional[int] = None,
                           end: Optional[int] = None) -> Dict[str, Any]:
        """GET /online/search — occupations by word, phrase, title or code (even
        partial). 20 results by default, closest first.

        Returns `{"start", "end", "total", "next"?, "occupation": [{"code", "title",
        "href", "tags"}]}`.
        """
        return self._get("/online/search",
                         {"keyword": keyword, "start": start, "end": end})

    def get_occupation(self, code: str) -> Dict[str, Any]:
        """GET /online/occupations/{code}/ — an occupation's record: `code`, `title`,
        `description`, `sample_of_reported_titles`, `also_see`, `tags`,
        `bright_outlook`, and the links to its reports. Not every property is
        present for every occupation."""
        return self._get(f"/online/occupations/{self.normalize_code(code)}/")

    def get_occupation_tasks(self, code: str, start: Optional[int] = None,
                             end: Optional[int] = None) -> Dict[str, Any]:
        """GET /online/occupations/{code}/summary/tasks — the occupation's tasks.
        5 by default; `end` widens the page. Returns `{"start", "end", "total",
        "task": [{"id", "title", "related"}]}`; 422 if the occupation has no tasks."""
        return self._get(f"/online/occupations/{self.normalize_code(code)}/summary/tasks",
                         {"start": start, "end": end})
