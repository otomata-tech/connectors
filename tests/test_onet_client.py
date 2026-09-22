"""Contrat du client O*NET Web Services v2 — sans réseau.

Mocke `requests.Session.get` : en-tête `X-API-Key` (jamais en query string), chemins,
pagination `start`/`end` compactée, normalisation du code O*NET-SOC, erreur 422 typée.
"""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.onet import client as onet


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"status": 200, "body": {"ok": True}}

    def fake_get(self, url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        return _Resp(seen["status"], seen["body"])

    monkeypatch.setattr(onet.requests.Session, "get", fake_get)
    return seen


def _client():
    return onet.ONetClient(api_key="k-test")


def test_key_travels_in_header_only(capture):
    c = _client()
    assert c.session.headers["X-API-Key"] == "k-test"
    c.search_occupations("architect")
    assert "k-test" not in str(capture["kwargs"]["params"])


def test_search_compacts_pagination(capture):
    _client().search_occupations("data engineer", end=50)
    assert capture["url"] == "https://api-v2.onetcenter.org/online/search"
    assert capture["kwargs"]["params"] == {"keyword": "data engineer", "end": 50}
    assert capture["kwargs"]["timeout"] == (10, 30)


@pytest.mark.parametrize("raw,path", [
    ("15-1299.08", "15-1299.08"), ("15-1299", "15-1299.00"), ("151299", "15-1299.00"),
])
def test_get_occupation_normalizes_code(capture, raw, path):
    _client().get_occupation(raw)
    assert capture["url"] == f"https://api-v2.onetcenter.org/online/occupations/{path}/"


def test_tasks_path(capture):
    _client().get_occupation_tasks("17-2051.00", end=30)
    assert capture["url"].endswith("/online/occupations/17-2051.00/summary/tasks")
    assert capture["kwargs"]["params"] == {"end": 30}


def test_bad_code_refused_before_any_call(capture):
    with pytest.raises(ValueError):
        _client().get_occupation("civil engineer")
    assert "url" not in capture


def test_422_is_typed(capture):
    capture.update(status=422, body={"error": "invalid O*NET-SOC code"})
    with pytest.raises(UpstreamHTTPError) as e:
        _client().get_occupation("99-9999.00")
    assert e.value.status_code == 422 and e.value.service == "onet"
