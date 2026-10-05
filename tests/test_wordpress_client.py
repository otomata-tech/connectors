"""WordPressClient — verrouille le contrat HTTP (URL, auth, params, redirections).

Mocke `requests.request`/`requests.get` : aucun réseau, aucune clé réelle.
"""
import json

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.wordpress import client as wp_client
from oto.tools.wordpress.client import (WordPressClient, WordPressRedirect,
                                        normalize_site_url)


class _Resp:
    def __init__(self, payload, status_code=200, headers=None, raw=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self.content = raw if raw is not None else json.dumps(payload).encode()

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    @property
    def text(self):
        return self.content.decode()


INDEX = {"name": "Blog", "namespaces": ["wp/v2"]}


def _client(mode="pretty"):
    return WordPressClient("https://blog.example.com", "julien", "abcd efgh",
                           rest_mode=mode)


@pytest.fixture
def calls(monkeypatch):
    captured = []
    replies = []

    def fake_request(method, url, **kwargs):
        captured.append({"method": method, "url": url, **kwargs})
        return replies.pop(0) if replies else _Resp({})

    monkeypatch.setattr(wp_client.requests, "request", fake_request)
    return captured, replies


@pytest.mark.parametrize("raw,expected", [
    ("blog.example.com", "https://blog.example.com"),
    ("https://blog.example.com/", "https://blog.example.com"),
    ("https://example.com/blog/wp-admin/", "https://example.com/blog"),
    ("https://example.com/wp-json/wp/v2/posts", "https://example.com"),
    ("http://localhost:8080", "http://localhost:8080"),
])
def test_normalize_site_url(raw, expected):
    assert normalize_site_url(raw) == expected


def test_normalize_rejects_empty_and_bad_scheme():
    with pytest.raises(ValueError):
        normalize_site_url("  ")
    with pytest.raises(ValueError):
        normalize_site_url("ftp://example.com")


def test_pretty_url_basic_auth_and_no_redirects(calls):
    captured, replies = calls
    replies.append(_Resp([{"id": 1}], headers={"X-WP-Total": "7", "X-WP-TotalPages": "4"}))
    out = _client().list("wp/v2/posts", page=2, per_page=2, status="draft", search=None)
    call = captured[0]
    assert call["url"] == "https://blog.example.com/wp-json/wp/v2/posts"
    assert call["auth"] == ("julien", "abcd efgh")
    assert call["allow_redirects"] is False
    assert call["params"] == {"page": 2, "per_page": 2, "status": "draft"}
    assert out == {"items": [{"id": 1}], "total": 7, "total_pages": 4, "page": 2}


def test_query_mode_uses_rest_route(calls):
    captured, _ = calls
    _client("query").get("wp/v2/posts", 5)
    call = captured[0]
    assert call["url"] == "https://blog.example.com/"
    assert call["params"] == {"rest_route": "/wp/v2/posts/5", "context": "edit"}


def test_per_page_capped_at_100(calls):
    captured, _ = calls
    _client().list("wp/v2/posts", per_page=500)
    assert captured[0]["params"]["per_page"] == 100


def test_redirect_is_raised_not_followed(calls):
    _, replies = calls
    replies.append(_Resp({}, status_code=301,
                         headers={"Location": "https://www.blog.example.com/wp-json/"}))
    with pytest.raises(WordPressRedirect) as e:
        _client().me()
    assert "www.blog.example.com" in str(e.value)


def test_upstream_error_keeps_wp_code(calls):
    _, replies = calls
    replies.append(_Resp({"code": "rest_cannot_create", "message": "no"}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as e:
        _client().create("wp/v2/posts", {"title": "x"})
    assert e.value.status_code == 403
    assert e.value.body["code"] == "rest_cannot_create"


def test_non_json_200_is_an_error(calls):
    _, replies = calls
    replies.append(_Resp(ValueError("nope"), raw=b"<html>cache page</html>"))
    with pytest.raises(UpstreamHTTPError):
        _client().me()


def test_delete_trash_by_default_force_explicit(calls):
    captured, _ = calls
    c = _client()
    c.delete("wp/v2/posts", 3)
    c.delete("wp/v2/categories", 4, force=True)
    assert captured[0]["method"] == "DELETE" and captured[0]["params"] == {}
    assert captured[1]["params"] == {"force": "true"}


def test_update_is_post_on_item(calls):
    captured, _ = calls
    _client().update("wp/v2/pages", 9, {"title": "t"})
    assert captured[0]["method"] == "POST"
    assert captured[0]["url"].endswith("/wp-json/wp/v2/pages/9")
    assert captured[0]["json"] == {"title": "t"}


def test_upload_media_raw_body_then_fields(calls):
    captured, replies = calls
    replies.extend([_Resp({"id": 42}), _Resp({"id": 42, "alt_text": "A"})])
    out = _client().upload_media(b"PNG", 'cover".png', "image/png", alt_text="A", caption=None)
    first, second = captured
    assert first["data"] == b"PNG"
    assert first["headers"]["Content-Type"] == "image/png"
    assert first["headers"]["Content-Disposition"] == 'attachment; filename="cover.png"'
    assert second["url"].endswith("/wp/v2/media/42") and second["json"] == {"alt_text": "A"}
    assert out["alt_text"] == "A"


def test_probe_prefers_pretty_then_falls_back_to_query(monkeypatch):
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append((url, params))
        if url.endswith("/wp-json/"):
            return _Resp({}, status_code=404)
        return _Resp(INDEX)

    monkeypatch.setattr(wp_client.requests, "get", fake_get)
    c = WordPressClient("https://blog.example.com", "u", "p")
    assert c.rest_mode == "query"
    assert seen == [("https://blog.example.com/wp-json/", {}),
                    ("https://blog.example.com/", {"rest_route": "/"})]


def test_probe_rejects_non_wordpress(monkeypatch):
    monkeypatch.setattr(wp_client.requests, "get",
                        lambda url, params=None, **kw: _Resp({"hello": 1}))
    with pytest.raises(ValueError, match="WordPress"):
        WordPressClient("https://example.com", "u", "p").rest_mode


def test_retry_on_429(calls, monkeypatch):
    _, replies = calls
    monkeypatch.setattr(wp_client.time, "sleep", lambda s: None)
    replies.extend([_Resp({}, status_code=429, headers={"Retry-After": "1"}),
                    _Resp({"id": 1})])
    assert _client().me() == {"id": 1}


def test_probe_redirect_on_wp_json_falls_back_to_query(monkeypatch):
    def fake_get(url, params=None, **kw):
        if url.endswith("/wp-json/"):
            return _Resp({}, status_code=302, headers={"Location": "/wp-json/"})
        return _Resp(INDEX)

    monkeypatch.setattr(wp_client.requests, "get", fake_get)
    assert WordPressClient("https://blog.example.com", "u", "p").rest_mode == "query"


def test_probe_redirect_raised_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(wp_client.requests, "get", lambda url, params=None, **kw: _Resp(
        {}, status_code=301, headers={"Location": "https://www.blog.example.com/"}))
    with pytest.raises(WordPressRedirect, match="www.blog.example.com"):
        WordPressClient("https://blog.example.com", "u", "p").rest_mode


def test_public_index_sends_no_credentials(monkeypatch):
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append(kw)
        return _Resp(INDEX)

    monkeypatch.setattr(wp_client.requests, "get", fake_get)
    out = WordPressClient("https://blog.example.com", "-", "-", rest_mode="pretty").public_index()
    assert out == INDEX
    assert "auth" not in seen[0] and "Authorization" not in seen[0]["headers"]


def test_missing_credential_is_named():
    from oto.tools.common.credentials import MissingCredential

    with pytest.raises(MissingCredential, match="WORDPRESS_APPLICATION_PASSWORD"):
        WordPressClient("https://blog.example.com", "julien", "")
