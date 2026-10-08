"""Doublures HTTP des clients Microsoft : `requests.post` (serveur d'autorisation
Entra) et `requests.Session.request` (Graph), sans réseau ni credential réel.

Les fixtures `token_calls` et `calls` rendent la liste des appels capturés ;
`.responses` empile les réponses servies dans l'ordre (à défaut : un succès)."""
import json

import pytest

from oto.tools.microsoft import auth as ms_auth

G = "https://graph.microsoft.com/v1.0"
CID, SECRET, REDIRECT = "client-guid", "s3cr3t-value", "https://oto.example/cb"


class _Resp:
    def __init__(self, payload=None, status_code=200, raw=None):
        self.status_code = status_code
        self._payload = payload
        if raw is not None:
            self.content = raw
            self.text = raw.decode(errors="replace")
        else:
            self.content = b"" if payload is None else json.dumps(payload).encode()
            self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Seen(list):
    """The captured calls, plus `responses`: queued replies, served in order."""


@pytest.fixture
def token_calls(monkeypatch):
    seen = _Seen()
    seen.responses = []

    def fake_post(url, **kw):
        seen.append({"url": url, **kw})
        return seen.responses.pop(0) if seen.responses else _Resp(
            {"access_token": "AT", "refresh_token": "RT2", "expires_in": 3599,
             "scope": "Files.ReadWrite.All"})

    monkeypatch.setattr(ms_auth.requests, "post", fake_post)
    return seen


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    seen.responses = responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url,
                     "headers": {**dict(self.headers), **(kwargs.pop("headers", None) or {})},
                     **kwargs})
        return responses.pop(0) if responses else _Resp({"id": "new-id", "ok": True})

    monkeypatch.setattr("requests.Session.request", _request)
    return seen
