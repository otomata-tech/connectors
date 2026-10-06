"""MetabaseClient contract (instance URL, x-api-key, paths, failed queries).

Mocks `requests.Session.request`: no network, no real key. Checks URL
normalisation, the key header, that redirects are never followed, the payloads
of the query endpoints, that a `status: failed` answer is raised, and id checks.
"""
from __future__ import annotations

import json

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.metabase import client as mb
from oto.tools.metabase import (MetabaseClient, MetabaseQueryError,
                                MetabaseRedirect, normalize_instance_url,
                                records)


class _Resp:
    def __init__(self, body=None, status_code=200, headers=None, raw=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.content = raw if raw is not None else json.dumps(body).encode()

    def json(self):
        return json.loads(self.content)

    @property
    def text(self):
        return self.content.decode()


@pytest.fixture()
def calls(monkeypatch):
    seen, replies = [], []

    def fake_request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url,
                     "headers": dict(self.headers), **kwargs})
        return replies.pop(0) if replies else _Resp({})

    monkeypatch.setattr(mb.requests.Session, "request", fake_request)
    return seen, replies


def _client():
    return MetabaseClient("https://bi.example.com", "mb_key")


# --- instance URL and key -----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("bi.example.com", "https://bi.example.com"),
    ("https://bi.example.com/", "https://bi.example.com"),
    ("https://bi.example.com/dashboard/3-sales?tab=1", "https://bi.example.com"),
    ("https://example.com/metabase/question/12", "https://example.com/metabase"),
    ("https://bi.example.com/api/card", "https://bi.example.com"),
    ("https://bi.example.com/apis", "https://bi.example.com/apis"),
])
def test_normalize_instance_url(raw, expected):
    assert normalize_instance_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "ftp://bi.example.com",
                                 "https://u:p@bi.example.com",
                                 "http://bi.example.com"])
def test_bad_instance_url_is_refused(raw):
    with pytest.raises(ValueError):
        normalize_instance_url(raw)


def test_http_only_with_allow_http():
    assert normalize_instance_url("http://localhost:3000",
                                  allow_http=True) == "http://localhost:3000"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_api_key_is_required(key):
    with pytest.raises(MissingCredential) as exc:
        MetabaseClient("https://bi.example.com", key)
    assert exc.value.name == "METABASE_API_KEY"


def test_key_sent_in_header_never_in_query(calls):
    seen, _ = calls
    _client().current_user()
    call = seen[0]
    assert call["url"] == "https://bi.example.com/api/user/current"
    assert call["headers"]["x-api-key"] == "mb_key"
    assert "mb_key" not in json.dumps(call.get("params"))
    assert call["allow_redirects"] is False
    assert call["timeout"]


# --- transport ------------------------------------------------------------------

def test_redirect_is_raised_with_its_target(calls):
    _, replies = calls
    replies.append(_Resp(None, 302, {"Location": "https://other.example.com/"},
                         raw=b""))
    with pytest.raises(MetabaseRedirect) as exc:
        _client().list_databases()
    assert exc.value.location == "https://other.example.com/"


def test_other_3xx_is_an_upstream_error(calls):
    _, replies = calls
    replies.append(_Resp(None, 304, raw=b""))
    with pytest.raises(UpstreamHTTPError) as exc:
        _client().list_databases()
    assert exc.value.status_code == 502


def test_http_error_is_typed(calls):
    _, replies = calls
    replies.append(_Resp(None, 403, raw=b"You don't have permissions to do that."))
    with pytest.raises(UpstreamHTTPError) as exc:
        _client().get_card(3)
    assert exc.value.status_code == 403


def test_non_json_answer_is_raised(calls):
    _, replies = calls
    replies.append(_Resp(None, 200, raw=b"<html>login</html>"))
    with pytest.raises(UpstreamHTTPError) as exc:
        _client().list_collections()
    assert exc.value.status_code == 502


# --- reads ----------------------------------------------------------------------

def test_search_repeats_models(calls):
    seen, _ = calls
    _client().search("revenue", models=["card", "dashboard"], limit=10)
    assert seen[0]["url"] == "https://bi.example.com/api/search"
    assert seen[0]["params"] == [("q", "revenue"), ("limit", 10),
                                 ("models", "card"), ("models", "dashboard")]


def test_unknown_search_model_is_refused():
    with pytest.raises(ValueError, match="models"):
        _client().search("x", models=["question"])


def test_collection_items_root_and_id(calls):
    seen, _ = calls
    c = _client()
    c.list_collection_items()
    c.list_collection_items(7, models="dashboard")
    assert seen[0]["url"].endswith("/api/collection/root/items")
    assert seen[1]["url"].endswith("/api/collection/7/items")
    assert seen[1]["params"] == [("models", "dashboard")]


def test_list_cards_filter(calls):
    seen, _ = calls
    _client().list_cards("database", model_id=2)
    assert seen[0]["params"] == [("f", "database"), ("model_id", 2)]
    with pytest.raises(ValueError):
        _client().list_cards("everything")


def test_list_databases_include_tables(calls):
    seen, _ = calls
    _client().list_databases(include_tables=True)
    _client().list_databases()
    assert seen[0]["params"] == [("include", "tables")]
    assert seen[1]["params"] is None


@pytest.mark.parametrize("bad", [0, -1, "3", "../user", True, None])
def test_ids_are_positive_integers(bad):
    with pytest.raises(ValueError):
        _client().get_card(bad)


# --- queries ----------------------------------------------------------------------

def test_run_card_posts_parameters(calls):
    seen, _ = calls
    p = [{"type": "category", "value": ["EU"],
          "target": ["variable", ["template-tag", "region"]]}]
    _client().run_card(12, parameters=p)
    assert seen[0]["method"] == "POST"
    assert seen[0]["url"].endswith("/api/card/12/query")
    assert seen[0]["json"] == {"parameters": p}


def test_run_dashboard_card_path(calls):
    seen, _ = calls
    _client().run_dashboard_card(3, 41, 12)
    assert seen[0]["url"].endswith("/api/dashboard/3/dashcard/41/card/12/query")
    assert seen[0]["json"] == {"parameters": []}


def test_run_native_query_payload(calls):
    seen, replies = calls
    replies.append(_Resp({"status": "completed", "row_count": 1,
                          "data": {"cols": [{"name": "n"}], "rows": [[3]]}}, 202))
    out = _client().run_native_query(
        2, "select count(*) as n from orders where region = {{region}}",
        template_tags={"region": {"name": "region", "display-name": "Region",
                                  "type": "text"}},
        parameters=[{"type": "category", "value": "EU",
                     "target": ["variable", ["template-tag", "region"]]}])
    body = seen[0]["json"]
    assert seen[0]["url"].endswith("/api/dataset")
    assert body["database"] == 2 and body["type"] == "native"
    assert "template-tags" in body["native"] and body["parameters"]
    assert records(out) == [{"n": 3}]


def test_empty_sql_is_refused():
    with pytest.raises(ValueError, match="sql"):
        _client().run_native_query(2, "  ")


def test_failed_query_is_raised(calls):
    _, replies = calls
    replies.append(_Resp({"status": "failed", "error": "Table \"X\" not found",
                          "row_count": 0, "data": {"rows": [], "cols": []}}, 202))
    with pytest.raises(MetabaseQueryError) as exc:
        _client().run_native_query(2, "select * from x")
    assert exc.value.status_code == 422
    assert "not found" in str(exc.value)
    assert exc.value.body["status"] == "failed"


def test_run_query_needs_database():
    with pytest.raises(ValueError):
        _client().run_query({"type": "query", "query": {"source-table": 1}})


def test_no_write_method():
    """Read and query only: no method that creates, updates or deletes."""
    verbs = ("create", "update", "delete", "archive", "put", "patch")
    names = [n for n in dir(MetabaseClient) if not n.startswith("_")]
    assert not [n for n in names if n.startswith(verbs)]


def test_records_keeps_column_order():
    out = records({"data": {"cols": [{"name": "a"}, {"name": "b"}],
                            "rows": [[1, 2], [3, 4]]}})
    assert out == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert records({}) == []
