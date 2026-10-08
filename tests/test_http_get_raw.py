"""`HttpConnectorClient.get_raw` : les octets d'un corps-fichier, bornés, sans suivre
de redirection — avec l'authentification du connecteur."""
from __future__ import annotations

import pytest
import requests

from oto.tools.http import HttpConnectorClient


class _Rep:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, n):
        for i in range(0, len(self._body), 3):
            yield self._body[i:i + 3]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def close(self):
        self.closed = True


def _client(monkeypatch, reponses, vus):
    c = HttpConnectorClient("https://api.example.test", "bearer", {"token": "SECRET"})
    s = c._ready()

    def get(url, **kw):
        vus.append((url, kw, dict(s.headers)))
        return reponses.pop(0)

    monkeypatch.setattr(s, "get", get)
    return c


def test_rend_les_octets_et_le_type(monkeypatch):
    vus = []
    c = _client(monkeypatch, [_Rep(body=b"a,b\n1,2\n", headers={"Content-Type": "text/csv"})], vus)
    assert c.get_raw("/ops/x.csv", {"ids": "1"}, max_bytes=100) == (b"a,b\n1,2\n", "text/csv")
    url, kw, headers = vus[0]
    assert url == "https://api.example.test/ops/x.csv"
    assert kw["allow_redirects"] is False and kw["stream"] is True and kw["timeout"]
    assert kw["params"] == {"ids": "1"} and headers["Authorization"] == "Bearer SECRET"


def test_refuse_une_redirection(monkeypatch):
    c = _client(monkeypatch, [_Rep(302, headers={"Location": "https://evil.test/"})], [])
    with pytest.raises(ValueError, match="redirect refused"):
        c.get_raw("/x", max_bytes=100)


def test_borne_la_taille_declaree_et_lue(monkeypatch):
    c = _client(monkeypatch, [_Rep(body=b"x" * 10, headers={"Content-Length": "10"})], [])
    with pytest.raises(ValueError, match="> limit"):
        c.get_raw("/x", max_bytes=5)
    c = _client(monkeypatch, [_Rep(body=b"x" * 10)], [])
    with pytest.raises(ValueError, match="> limit"):
        c.get_raw("/x", max_bytes=5)


def test_une_erreur_amont_leve_httperror(monkeypatch):
    c = _client(monkeypatch, [_Rep(500, body=b"boom")], [])
    with pytest.raises(requests.HTTPError):
        c.get_raw("/x", max_bytes=100)


def test_le_chemin_doit_etre_relatif(monkeypatch):
    c = _client(monkeypatch, [], [])
    with pytest.raises(ValueError, match="path"):
        c.get_raw("https://evil.test/x", max_bytes=100)
