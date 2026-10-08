"""PennylaneFirmClient: paths, queries, cursor paging, refusals.

No network: the client's session is replaced by a recorder that returns
prepared responses, so the transport (`_request`) is exercised as in use.
The streamed upload has its own tests (`test_pennylane_firm_upload.py`).
"""

import io
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.pennylane_firm import (PennylaneFirmClient, PennylaneFirmError,
                                      PennylaneFirmRateLimited,
                                      PennylaneFirmScopeMissing, TokenRateLimiter)

BASE = "https://app.pennylane.com/api/external/firm/v1"


class _Response:
    def __init__(self, status_code=200, json_body=None, text=None, headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text if text is not None else (
            json.dumps(json_body) if json_body is not None else "")
        self.content = self.text.encode()
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no JSON")
        return self._json


class _Session:
    """Records every request and answers the prepared responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        data = kwargs.get("data")
        if data is not None and not isinstance(data, (dict, bytes)):
            kwargs["body"] = b"".join(data)       # sent, as the transport would
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class _CountingLimiter(TokenRateLimiter):
    def __init__(self):
        super().__init__(10**6, 1.0)
        self.acquired = []

    def acquire(self, token):
        self.acquired.append(token)


def _client(*responses):
    limiter = _CountingLimiter()
    c = PennylaneFirmClient(token="tok", rate_limiter=limiter,
                            session=_Session(*responses))
    return c, c.session, limiter


def test_token_is_required_and_sent_as_bearer_header():
    with pytest.raises(MissingCredential) as ei:
        PennylaneFirmClient(token="")
    assert ei.value.name == "PENNYLANE_FIRM_TOKEN"
    c, session, _ = _client(_Response(json_body={"items": []}))
    assert session.headers["Authorization"] == "Bearer tok"
    c.list_companies()
    assert "tok" not in json.dumps(session.calls[0]["params"])


def test_list_companies_query_and_json_filter():
    c, s, _ = _client(_Response(json_body={"items": [], "total_pages": 1}))
    clauses = [{"field": "client_code", "operator": "eq", "value": "0042"}]
    c.list_companies(page=2, per_page=1000, filter=clauses)
    call = s.calls[0]
    assert (call["method"], call["url"]) == ("GET", f"{BASE}/companies")
    assert call["params"]["page"] == 2 and call["params"]["per_page"] == 1000
    assert isinstance(call["params"]["filter"], str)
    assert json.loads(call["params"]["filter"]) == clauses
    assert call["allow_redirects"] is False


def test_unset_arguments_stay_out_of_the_query():
    c, s, _ = _client(_Response(json_body={"items": []}))
    c.list_companies()
    assert s.calls[0]["params"] == {}


def test_get_company_and_fiscal_years_paths():
    c, s, _ = _client(_Response(json_body={"id": 7}),
                      _Response(json_body={"items": []}))
    assert c.get_company(7) == {"id": 7}
    c.list_fiscal_years(7, page=1, per_page=100)
    assert s.calls[0]["url"] == f"{BASE}/companies/7"
    assert s.calls[1]["url"] == f"{BASE}/companies/7/fiscal_years"
    assert s.calls[1]["params"] == {"page": 1, "per_page": 100}


def test_list_dms_folders_one_page():
    page = {"items": [{"id": 1}], "has_more": True, "next_cursor": "c2"}
    c, s, _ = _client(_Response(json_body=page))
    clauses = [{"field": "id", "operator": "in", "value": ["1", "2"]}]
    assert c.list_dms_folders(7, filter=clauses, limit=50, cursor="c1") == page
    call = s.calls[0]
    assert call["url"] == f"{BASE}/companies/7/dms/folders"
    assert call["params"]["limit"] == 50 and call["params"]["cursor"] == "c1"
    assert json.loads(call["params"]["filter"]) == clauses


def test_list_dms_files_parent_folder_all_pages():
    c, s, _ = _client(
        _Response(json_body={"items": [{"id": 1}], "has_more": True, "next_cursor": "c2"}),
        _Response(json_body={"items": [{"id": 2}], "has_more": True, "next_cursor": "c3"}),
        _Response(json_body={"items": [{"id": 3}], "has_more": False, "next_cursor": None}))
    out = c.list_dms_files(7, parent_folder_id=123, all_pages=True)
    assert [f["id"] for f in out["items"]] == [1, 2, 3]
    assert out["has_more"] is False and out["pages"] == 3
    assert [call["params"].get("cursor") for call in s.calls] == [None, "c2", "c3"]
    for call in s.calls:
        assert call["url"] == f"{BASE}/companies/7/dms/files"
        assert json.loads(call["params"]["filter"]) == [
            {"field": "parent_folder_id", "operator": "eq", "value": "123"}]


def test_all_pages_bound_keeps_has_more_and_the_cursor():
    pages = [_Response(json_body={"items": [{"id": i}], "has_more": True,
                                  "next_cursor": f"c{i + 1}"}) for i in range(3)]
    c, s, _ = _client(*pages)
    out = c.list_dms_files(7, all_pages=True, max_pages=2)
    assert len(s.calls) == 2
    assert out["has_more"] is True and out["next_cursor"] == "c2"


def test_parent_folder_is_added_to_the_given_filter():
    c, s, _ = _client(_Response(json_body={"items": []}))
    c.list_dms_files(7, filter=[{"field": "id", "operator": "gt", "value": "10"}],
                     parent_folder_id=5)
    assert json.loads(s.calls[0]["params"]["filter"]) == [
        {"field": "id", "operator": "gt", "value": "10"},
        {"field": "parent_folder_id", "operator": "eq", "value": "5"}]


def test_create_dms_folder_posts_json():
    c, s, _ = _client(_Response(201, json_body={"id": 9, "path": "/A/2026"}))
    assert c.create_dms_folder(7, "2026", parent_folder_id=567)["id"] == 9
    call = s.calls[0]
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/companies/7/dms/folders")
    assert call["json"] == {"name": "2026", "parent_folder_id": 567}


def test_create_dms_folder_at_root_omits_parent():
    c, s, _ = _client(_Response(201, json_body={"id": 9}))
    c.create_dms_folder(7, "Root")
    assert s.calls[0]["json"] == {"name": "Root"}


def test_list_dms_file_changes_query_and_exclusive_arguments():
    c, s, _ = _client(_Response(json_body={"items": []}))
    c.list_dms_file_changes(7, start_date="2026-10-01T00:00:00Z", limit=1000)
    assert s.calls[0]["url"] == f"{BASE}/companies/7/changelogs/dms_files"
    assert s.calls[0]["params"] == {"start_date": "2026-10-01T00:00:00Z", "limit": 1000}
    with pytest.raises(ValueError):
        c.list_dms_file_changes(7, start_date="2026-10-01T00:00:00Z", cursor="x")


def test_upload_dms_file_posts_a_streamed_multipart():
    created = {"id": 11, "name": "Invoice.pdf", "path": "/A/Invoice.pdf",
               "parent_folder": {"id": 567}, "url": "u", "created_at": "t",
               "updated_at": "t"}
    c, s, limiter = _client(_Response(201, json_body=created))
    out = c.upload_dms_file(7, io.BytesIO(b"%PDF-1.7"), "scan.pdf", 567,
                            name="Invoice.pdf")
    assert out == created
    call = s.calls[0]
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/companies/7/dms/files")
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert "files" not in call and call["json"] is None
    assert len(call["data"]) == len(call["body"])
    assert b'filename="scan.pdf"' in call["body"] and b"%PDF-1.7" in call["body"]
    assert call["allow_redirects"] is False
    assert call["timeout"] == (30.0, 600.0)
    assert limiter.acquired == ["tok"]


@pytest.mark.parametrize("kwargs", [
    {"fileobj": io.BytesIO(b""), "filename": "a.pdf", "parent_folder_id": 1},
    {"fileobj": io.BytesIO(b"x"), "filename": "", "parent_folder_id": 1},
    {"fileobj": io.BytesIO(b"x"), "filename": "a.pdf", "parent_folder_id": None},
    {"fileobj": io.BytesIO(b"x"), "filename": "a.pdf", "parent_folder_id": 1,
     "name": "n" * 256},
])
def test_upload_refuses_locally_without_sending(kwargs):
    c, s, _ = _client()
    with pytest.raises(ValueError):
        c.upload_dms_file(7, **kwargs)
    assert s.calls == []


def test_upload_redirect_is_not_followed():
    c, _, _ = _client(_Response(302, text="", headers={"Location": "https://elsewhere"}))
    with pytest.raises(PennylaneFirmError) as ei:
        c.upload_dms_file(7, io.BytesIO(b"x"), "a.pdf", 1)
    assert ei.value.code == "unexpected_redirect"


def test_every_method_waits_for_the_limiter():
    page = {"items": [], "has_more": False}
    c, _, limiter = _client(*[_Response(json_body=page) for _ in range(7)],
                            _Response(201, json_body={"id": 1}))
    c.list_companies()
    c.get_company(1)
    c.list_fiscal_years(1)
    c.list_dms_folders(1)
    c.list_dms_files(1, all_pages=True)
    c.create_dms_folder(1, "x")
    c.list_dms_file_changes(1)
    c.upload_dms_file(1, io.BytesIO(b"x"), "a.pdf", 2)
    assert limiter.acquired == ["tok"] * 8


# --- Refusals ---------------------------------------------------------------

def test_401_names_an_invalid_token():
    c, _, _ = _client(_Response(401, json_body={"error": "Unauthorized"}))
    with pytest.raises(PennylaneFirmError) as ei:
        c.list_companies()
    e = ei.value
    assert isinstance(e, UpstreamHTTPError)
    assert (e.status_code, e.code, e.retryable) == (401, "token_invalid", False)
    assert "invalid" in str(e)


def test_403_relays_the_scope_pennylane_names():
    c, _, _ = _client(_Response(403, json_body={
        "error": "This action requires scope dms_files:all"}))
    with pytest.raises(PennylaneFirmScopeMissing) as ei:
        c.create_dms_folder(7, "x")
    e = ei.value
    assert (e.status_code, e.code, e.scope) == (403, "scope_missing", "dms_files:all")
    assert "dms_files:all" in str(e)


def test_429_raises_a_retryable_named_error_without_retrying():
    c, s, _ = _client(_Response(429, json_body={
        "error": "You made too many requests. Retry in 60s !"}))
    with pytest.raises(PennylaneFirmRateLimited) as ei:
        c.upload_dms_file(7, io.BytesIO(b"x"), "a.pdf", 1)
    assert ei.value.retryable is True and ei.value.code == "rate_limited"
    assert len(s.calls) == 1


def test_404_uses_the_function_refusal():
    c, _, _ = _client(_Response(404, json_body={"error": "Not found"}))
    with pytest.raises(PennylaneFirmError) as ei:
        c.get_company(99)
    assert ei.value.code == "company_not_found"


def test_422_carries_a_bounded_excerpt_of_the_body():
    c, _, _ = _client(_Response(422, json_body={"error": "Name invalid " + "x" * 1000}))
    with pytest.raises(PennylaneFirmError) as ei:
        c.create_dms_folder(7, "bad")
    e = ei.value
    assert e.code == "folder_rejected" and "Name invalid" in str(e)
    assert len(str(e)) < 700


def test_5xx_is_retryable():
    c, _, _ = _client(_Response(503, text="<html>down</html>"))
    with pytest.raises(PennylaneFirmError) as ei:
        c.get_company(1)
    assert ei.value.retryable is True and ei.value.code == "upstream_unavailable"


def test_network_failure_raises():
    import requests

    c, s, _ = _client()

    def broken(*a, **kw):
        raise requests.ConnectionError("connection reset")

    s.request = broken
    with pytest.raises(RuntimeError, match="connection reset"):
        c.list_companies()
