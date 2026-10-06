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
    return WordPressClient("https://blog.example.com", "editor", "abcd efgh",
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
])
def test_normalize_site_url(raw, expected):
    assert normalize_site_url(raw) == expected


def test_http_refused_unless_explicitly_allowed():
    with pytest.raises(ValueError, match="in clear"):
        normalize_site_url("http://blog.example.com")
    with pytest.raises(ValueError, match="in clear"):
        WordPressClient("http://blog.example.com", "u", "p")
    assert normalize_site_url("http://localhost:8080", allow_http=True) == "http://localhost:8080"
    assert WordPressClient("http://localhost:8080", "u", "p", allow_http=True,
                           rest_mode="pretty").site_url == "http://localhost:8080"


@pytest.mark.parametrize("raw", ["https://user:secret@blog.example.com",
                                 "https://user@blog.example.com"])
def test_credentials_in_url_refused_and_not_echoed(raw):
    with pytest.raises(ValueError) as e:
        normalize_site_url(raw)
    assert "secret" not in str(e.value) and "credentials" in str(e.value)


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
    assert call["auth"] == ("editor", "abcd efgh")
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
    captured, replies = calls
    replies.append(_Resp([]))
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
    with pytest.raises(UpstreamHTTPError) as e:
        _client().me()
    assert e.value.status_code == 502 and e.value.is_server_error


@pytest.mark.parametrize("status", [300, 304, 305])
def test_other_3xx_is_an_error_not_an_empty_success(calls, status):
    _, replies = calls
    replies.append(_Resp({}, status_code=status, raw=b""))
    with pytest.raises(UpstreamHTTPError) as e:
        _client().me()
    assert e.value.status_code == 502


@pytest.mark.parametrize("call", [
    lambda c: c.create("wp/v2/posts", {"title": "x"}),
    lambda c: c.upload_media(b"PNG", "a.png", "image/png"),
])
def test_redirect_on_write_is_raised(calls, call):
    captured, replies = calls
    replies.append(_Resp({}, status_code=307, headers={"Location": "https://evil.example.net/"}))
    with pytest.raises(WordPressRedirect, match="evil.example.net"):
        call(_client())
    assert len(captured) == 1 and captured[0]["allow_redirects"] is False


def test_list_of_a_non_collection_is_an_error(calls):
    _, replies = calls
    replies.append(_Resp({"code": "x", "data": {}}))
    with pytest.raises(UpstreamHTTPError, match="list"):
        _client().list("wp/v2/settings")


@pytest.mark.parametrize("route", ["wp/v2/posts/../users", "wp/v2/posts?x=1",
                                   "wp/v2/posts#a", "wp/v2/po%2Fsts"])
def test_route_is_validated(calls, route):
    captured, _ = calls
    with pytest.raises(ValueError, match="route"):
        _client().list(route)
    assert captured == []


@pytest.mark.parametrize("item_id", ["7?force=true", "7", 0, -1, True, 1.5])
def test_item_id_must_be_a_positive_int(calls, item_id):
    captured, _ = calls
    with pytest.raises(ValueError, match="identifiant"):
        _client().delete("wp/v2/posts", item_id)
    assert captured == []


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


@pytest.mark.parametrize("name,sent", [
    ("photo €.png", "photo .png"),
    ("café.png", "cafe.png"),
    ("a;b.png", "ab.png"),
    ("a\\b.png", "ab.png"),
    ("€€€", "upload"),
])
def test_upload_filename_is_ascii_and_safe(calls, name, sent):
    captured, _ = calls
    _client().upload_media(b"x", name, "image/png")
    header = captured[0]["headers"]["Content-Disposition"]
    header.encode("ascii")
    assert header == f'attachment; filename="{sent}"'


def test_upload_fields_failure_carries_media_id(calls):
    from oto.tools.wordpress import WordPressMediaFieldsError

    _, replies = calls
    replies.extend([_Resp({"id": 42}),
                    _Resp({"code": "rest_invalid_param"}, status_code=400)])
    with pytest.raises(WordPressMediaFieldsError) as e:
        _client().upload_media(b"PNG", "a.png", "image/png", alt_text="A")
    assert e.value.media_id == 42 and "42" in str(e.value)
    assert e.value.status_code == 400


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


def test_probe_sends_no_credentials(monkeypatch):
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append(kw)
        return _Resp(INDEX)

    monkeypatch.setattr(wp_client.requests, "get", fake_get)
    assert WordPressClient("https://blog.example.com", "u", "p").rest_mode == "pretty"
    assert "auth" not in seen[0] and "Authorization" not in seen[0]["headers"]
    assert seen[0]["allow_redirects"] is False


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


def test_retry_after_http_date_is_read(calls, monkeypatch):
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    _, replies = calls
    slept = []
    monkeypatch.setattr(wp_client.time, "sleep", slept.append)
    soon = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=3), usegmt=True)
    replies.extend([_Resp({}, status_code=429, headers={"Retry-After": soon}),
                    _Resp({"id": 1})])
    assert _client().me() == {"id": 1}
    assert len(slept) == 1 and 0 <= slept[0] <= 3


@pytest.mark.parametrize("retry_after", ["120", "Wed, 21 Oct 2099 07:28:00 GMT"])
def test_long_retry_after_is_raised_not_slept(calls, monkeypatch, retry_after):
    from oto.tools.wordpress import WordPressRateLimited

    _, replies = calls
    monkeypatch.setattr(wp_client.time, "sleep", lambda s: pytest.fail("slept"))
    replies.append(_Resp({}, status_code=429, headers={"Retry-After": retry_after}))
    with pytest.raises(WordPressRateLimited) as e:
        _client().me()
    assert e.value.status_code == 429 and e.value.retry_after > 10


def test_unreadable_retry_after_backs_off_then_raises(calls, monkeypatch):
    from oto.tools.wordpress import WordPressRateLimited

    _, replies = calls
    slept = []
    monkeypatch.setattr(wp_client.time, "sleep", slept.append)
    replies.extend([_Resp({}, status_code=429, headers={"Retry-After": "soon"})] * 3)
    with pytest.raises(WordPressRateLimited):
        _client().me()
    assert slept == [1.0, 2.0]


def test_public_index_redirect_is_raised(monkeypatch):
    monkeypatch.setattr(wp_client.requests, "get", lambda url, params=None, **kw: _Resp(
        {}, status_code=302, headers={"Location": "https://other.example.net/"}))
    with pytest.raises(WordPressRedirect, match="other.example.net"):
        WordPressClient("https://blog.example.com", "-", "-", rest_mode="pretty").public_index()


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
        WordPressClient("https://blog.example.com", "editor", "")
    with pytest.raises(MissingCredential, match="WORDPRESS_APPLICATION_PASSWORD"):
        WordPressClient("https://blog.example.com", "editor", "   ")
    with pytest.raises(MissingCredential, match="WORDPRESS_USERNAME"):
        WordPressClient("https://blog.example.com", " ", "pw")
