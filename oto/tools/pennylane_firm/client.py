"""Pennylane Firm API v1 client: the firm-side API of Pennylane.

One firm token reaches every company of the firm, within the scopes chosen
when the token was created (companies:readonly, fiscal_years:readonly,
dms_files:readonly or dms_files:all). Every method but `list_companies` takes
`company_id`, the firm-side company id read from `list_companies`.

The methods implement the functions described in
`connectors/pennylane_firm/connector.yaml`, under the same names, plus
`upload_dms_file` (streamed multipart upload into the DMS), which the description
format cannot express.

Usage:
    client = PennylaneFirmClient(token="...")
    companies = client.list_companies(per_page=1000)["items"]
    files = client.list_dms_files(1234, parent_folder_id=567, all_pages=True)
"""

from __future__ import annotations

import json
import mimetypes
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

import requests

from ..common.credentials import require
from .errors import PennylaneFirmError, Refusals, error_from_response
from .multipart import MultipartStream
from .rate_limit import SHARED_LIMITER, TokenRateLimiter

BASE_URL = "https://app.pennylane.com/api/external/firm/v1"
TIMEOUT_S = 30
# (connect, read). urllib3 applies the connect value to each socket write
# of the body too: it bounds a stalled send, not the whole upload. The read
# value is the wait for Pennylane's answer once the file is sent.
UPLOAD_TIMEOUT_S = (30.0, 600.0)
DEFAULT_MAX_PAGES = 10
MAX_UPLOAD_NAME = 255

_COMPANY_NOT_FOUND = (
    "company_not_found",
    "No company with this id in the firm: read the ids from the list of "
    "companies.")
_FOLDER_REJECTED = (
    "folder_rejected",
    "Pennylane refused to create this folder: check the name and the parent "
    "folder.")
_START_DATE_TOO_OLD = (
    "start_date_too_old",
    "Pennylane keeps DMS file changes four weeks only: pick a start_date "
    "within that window.")
_FILE_REJECTED = (
    "file_rejected",
    "Pennylane refused this file: check its name, its type and the parent "
    "folder.")


def _encode_filter(clauses: Optional[List[dict]]) -> Optional[str]:
    """The `filter` query parameter: a JSON array serialised as a string."""
    if clauses is None:
        return None
    if not isinstance(clauses, list) or not clauses:
        raise ValueError("filter must be a non-empty list of "
                         "{field, operator, value} clauses")
    return json.dumps(clauses, separators=(",", ":"))


def _query(**params: Any) -> Dict[str, Any]:
    """Query parameters without the unset ones."""
    return {k: v for k, v in params.items() if v is not None}


class PennylaneFirmClient:
    """Client for the Pennylane Firm API v1 (firm token, Bearer).

    Every request, upload included, first waits for a slot of the token's
    5 requests per second (`rate_limiter`, shared process-wide by default).
    An upstream refusal raises a `PennylaneFirmError` (an `UpstreamHTTPError`):
    `PennylaneFirmRateLimited` on 429, `PennylaneFirmScopeMissing` on 403.
    """

    BASE_URL = BASE_URL

    def __init__(self, token: str = None, *,
                 rate_limiter: Optional[TokenRateLimiter] = None,
                 session: Optional[requests.Session] = None):
        """
        Args:
            token: Pennylane Firm API token (Bearer), supplied by the consumer.
            rate_limiter: pacing per token; defaults to the process-wide one.
            session: HTTP session; a new one by default.
        """
        self._token = require(token, "PENNYLANE_FIRM_TOKEN")
        self._limiter = rate_limiter or SHARED_LIMITER
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        })

    # --- Transport ------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[dict] = None, json_body: Optional[dict] = None,
                 data: Any = None, headers: Optional[dict] = None,
                 timeout: Union[float, Tuple[float, float]] = TIMEOUT_S,
                 refusals: Optional[Refusals] = None) -> Any:
        """ONE call, paced, and the only place that translates a refusal."""
        self._limiter.acquire(self._token)
        try:
            response = self.session.request(
                method, f"{self.BASE_URL}{path}", params=params, json=json_body,
                data=data, headers=headers, timeout=timeout,
                allow_redirects=False)
        except requests.RequestException as e:
            raise RuntimeError(
                f"pennylane_firm: {method} {path} — {type(e).__name__}: {e}"
            ) from e

        status = response.status_code
        if status >= 400:
            raise error_from_response(response, refusals)
        if 300 <= status < 400:
            raise PennylaneFirmError(
                status, "unexpected_redirect",
                f"Pennylane answered {method} {path} with a redirect "
                f"(to {response.headers.get('Location') or 'an unnamed target'}); "
                "redirects are not followed.")
        if status == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as e:
            raise RuntimeError(
                f"pennylane_firm: {method} {path} answered {status} without "
                f"readable JSON — {response.text[:200]!r}") from e

    def _cursor_pages(self, path: str, params: dict, *, cursor: Optional[str],
                      max_pages: int) -> dict:
        """Follow `next_cursor` while `has_more`, `max_pages` pages at most.

        Returns `{items, has_more, next_cursor, pages}`: when the bound stops
        the walk, `has_more` stays true and `next_cursor` resumes it — the
        result never passes for complete when it is not.
        """
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        items: list = []
        pages = 0
        has_more = False
        next_cursor = cursor
        while True:
            page = self._request("GET", path,
                                 params=_query(**params, cursor=next_cursor))
            items.extend(page.get("items") or [])
            pages += 1
            has_more = bool(page.get("has_more"))
            next_cursor = page.get("next_cursor")
            if not has_more or not next_cursor or pages >= max_pages:
                break
        return {"items": items, "has_more": has_more and bool(next_cursor),
                "next_cursor": next_cursor if has_more else None,
                "pages": pages}

    def _cursor_list(self, path: str, params: dict, *, cursor: Optional[str],
                     all_pages: bool, max_pages: int) -> dict:
        if all_pages:
            return self._cursor_pages(path, params, cursor=cursor,
                                      max_pages=max_pages)
        return self._request("GET", path, params=_query(**params, cursor=cursor))

    # --- Companies --------------------------------------------------------

    def list_companies(self, page: Optional[int] = None,
                       per_page: Optional[int] = None,
                       filter: Optional[List[dict]] = None) -> dict:
        """List the companies of the firm, one page (per_page <= 1000).

        Returns `{items, total_pages, current_page, total_items, per_page}`.
        `filter`: clauses on client_code, e.g.
        `[{"field": "client_code", "operator": "eq", "value": "0042"}]`.
        Scope companies:readonly.
        """
        return self._request("GET", "/companies", params=_query(
            page=page, per_page=per_page, filter=_encode_filter(filter)))

    def get_company(self, company_id: int) -> dict:
        """One company of the firm by its firm-side id. Scope companies:readonly."""
        return self._request("GET", f"/companies/{int(company_id)}",
                             refusals={404: _COMPANY_NOT_FOUND})

    # --- Fiscal years -----------------------------------------------------

    def list_fiscal_years(self, company_id: int, page: Optional[int] = None,
                          per_page: Optional[int] = None) -> dict:
        """List the fiscal years of one company, one page (per_page <= 100).
        Scope fiscal_years:readonly."""
        return self._request(
            "GET", f"/companies/{int(company_id)}/fiscal_years",
            params=_query(page=page, per_page=per_page))

    # --- DMS ----------------------------------------------------------------

    def list_dms_folders(self, company_id: int,
                         filter: Optional[List[dict]] = None,
                         limit: Optional[int] = None,
                         cursor: Optional[str] = None, *,
                         all_pages: bool = False,
                         max_pages: int = DEFAULT_MAX_PAGES) -> dict:
        """List the folders of one company's DMS (limit <= 100 per page).

        One page: `{items, has_more, next_cursor}` as Pennylane returns it.
        `all_pages=True`: follows the cursor, `max_pages` pages at most, and
        returns `{items, has_more, next_cursor, pages}`.
        `filter`: clauses on id. Scope dms_files:readonly.
        """
        return self._cursor_list(
            f"/companies/{int(company_id)}/dms/folders",
            _query(filter=_encode_filter(filter), limit=limit),
            cursor=cursor, all_pages=all_pages, max_pages=max_pages)

    def list_dms_files(self, company_id: int,
                       filter: Optional[List[dict]] = None,
                       limit: Optional[int] = None,
                       cursor: Optional[str] = None, *,
                       parent_folder_id: Optional[int] = None,
                       all_pages: bool = False,
                       max_pages: int = DEFAULT_MAX_PAGES) -> dict:
        """List the files of one company's DMS (limit <= 100 per page).

        `parent_folder_id` adds the clause `parent_folder_id eq <id>` to
        `filter` (clauses on id and parent_folder_id). Same paging as
        `list_dms_folders`: with `all_pages=True`, check `has_more` before
        concluding that a name is absent. Scope dms_files:readonly.
        """
        clauses = list(filter or [])
        if parent_folder_id is not None:
            clauses.append({"field": "parent_folder_id", "operator": "eq",
                            "value": str(int(parent_folder_id))})
        return self._cursor_list(
            f"/companies/{int(company_id)}/dms/files",
            _query(filter=_encode_filter(clauses or None), limit=limit),
            cursor=cursor, all_pages=all_pages, max_pages=max_pages)

    def create_dms_folder(self, company_id: int, name: str,
                          parent_folder_id: Optional[int] = None) -> dict:
        """Create a folder at the root, or under `parent_folder_id`.

        Folders cannot be renamed, moved or deleted through the API: list
        them first and reuse an existing one. Scope dms_files:all.
        """
        if not name:
            raise ValueError("create_dms_folder needs a folder name")
        body: Dict[str, Any] = {"name": name}
        if parent_folder_id is not None:
            body["parent_folder_id"] = int(parent_folder_id)
        return self._request(
            "POST", f"/companies/{int(company_id)}/dms/folders",
            json_body=body, refusals={422: _FOLDER_REJECTED})

    def list_dms_file_changes(self, company_id: int,
                              start_date: Optional[str] = None,
                              cursor: Optional[str] = None,
                              limit: Optional[int] = None) -> dict:
        """List the changes to one company's DMS files, oldest first.

        Start from `start_date` (RFC 3339, within the last four weeks), then
        pass `next_cursor` as `cursor` — never both. Up to 1000 per page.
        Scope dms_files:readonly.
        """
        if start_date is not None and cursor is not None:
            raise ValueError("list_dms_file_changes takes start_date or "
                             "cursor, never both")
        return self._request(
            "GET", f"/companies/{int(company_id)}/changelogs/dms_files",
            params=_query(start_date=start_date, cursor=cursor, limit=limit),
            refusals={422: _START_DATE_TOO_OLD})

    def upload_dms_file(self, company_id: int, fileobj: BinaryIO,
                        filename: str, parent_folder_id: int,
                        name: Optional[str] = None,
                        content_type: Optional[str] = None, *,
                        timeout: Union[float, Tuple[float, float]] = UPLOAD_TIMEOUT_S
                        ) -> dict:
        """Upload a file into a folder of one company's DMS (multipart).

        `fileobj` is a binary, seekable file object, read from its current
        position to its end in bounded chunks while the request is sent: the
        file is never held in memory whole. `parent_folder_id` is required:
        nothing is uploaded at the root. `name` (at most 255 characters) is
        the name shown in the DMS, the filename by default. `content_type` is
        guessed from `filename` when absent. `timeout` is (connect, read),
        see `UPLOAD_TIMEOUT_S`.

        Returns the file as Pennylane returns it
        (`{id, name, path, parent_folder, url, created_at, updated_at}`).
        The DMS has no delete through the API: an upload cannot be undone.
        Scope dms_files:all.
        """
        if not filename:
            raise ValueError("upload_dms_file needs a filename")
        if parent_folder_id is None:
            raise ValueError("upload_dms_file needs parent_folder_id: files "
                             "are not uploaded at the root of the DMS")
        if name is not None and not 0 < len(name) <= MAX_UPLOAD_NAME:
            raise ValueError(f"name must be 1 to {MAX_UPLOAD_NAME} characters")
        mime = (content_type or mimetypes.guess_type(filename)[0]
                or "application/octet-stream")
        fields: Dict[str, str] = {"parent_folder_id": str(int(parent_folder_id))}
        if name is not None:
            fields["name"] = name
        body = MultipartStream(fields, "file", fileobj, filename, mime)
        if body.file_size == 0:
            raise ValueError("upload_dms_file got an empty file")
        return self._request(
            "POST", f"/companies/{int(company_id)}/dms/files",
            data=body, headers={"Content-Type": body.content_type},
            timeout=timeout, refusals={422: _FILE_REJECTED})
