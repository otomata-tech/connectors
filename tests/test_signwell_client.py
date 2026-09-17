"""SignWellClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + body, sans réseau ni
clé réelle. Cible ce qui pourrait dériver en silence : l'en-tête d'auth, les 26
opérations (une méthode par endpoint), le nettoyage des `None`, le paramètre
répété `template_ids[]`, et le décodage d'une réponse 204 / binaire.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.signwell import SignWellClient

BASE = "https://www.signwell.com/api/v1"


class _Resp:
    def __init__(self, payload=None, status_code=200, raw=None, ctype="application/json"):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": ctype}
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
def calls(monkeypatch):
    seen = _Seen()
    responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        return responses.pop(0) if responses else _Resp({"ok": True})

    monkeypatch.setattr("requests.Session.request", _request)
    seen.responses = responses
    return seen


@pytest.fixture
def client():
    return SignWellClient(api_key="sw-test")


#: The 26 operations of the public API, one client method each.
METHODS = {
    "get_me", "get_api_application", "delete_api_application",
    "create_document", "get_document", "delete_document", "send_document",
    "send_reminder", "update_recipients", "update_authentication",
    "get_completed_pdf", "get_nom151_certificate",
    "create_template", "get_template", "update_template", "delete_template",
    "create_document_from_template",
    "list_bulk_sends", "get_bulk_send", "get_bulk_send_documents",
    "get_bulk_send_csv_template", "validate_bulk_send_csv", "create_bulk_send",
    "list_webhooks", "create_webhook", "delete_webhook",
}


def test_full_api_coverage_is_26_operations():
    public = {n for n in dir(SignWellClient)
              if not n.startswith("_") and callable(getattr(SignWellClient, n))}
    assert public == METHODS


def test_api_key_goes_in_x_api_key_header(calls, client):
    client.get_me()
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == f"{BASE}/me"
    assert calls[0]["headers"]["X-Api-Key"] == "sw-test"
    assert "Authorization" not in calls[0]["headers"]


def test_create_document_body_and_none_dropped(calls, client):
    client.create_document(
        files=[{"name": "nda.pdf", "file_url": "https://x/nda.pdf"}],
        recipients=[{"id": "1", "name": "A", "email": "a@example.com"}],
        draft=True, subject=None)
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{BASE}/documents")
    assert c["json"] == {"files": [{"name": "nda.pdf", "file_url": "https://x/nda.pdf"}],
                         "recipients": [{"id": "1", "name": "A", "email": "a@example.com"}],
                         "draft": True}


@pytest.mark.parametrize("call,method,path", [
    (lambda c: c.get_document("d1"), "GET", "/documents/d1"),
    (lambda c: c.delete_document("d1"), "DELETE", "/documents/d1"),
    (lambda c: c.send_document("d1", test_mode=True), "POST", "/documents/d1/send"),
    (lambda c: c.send_reminder("d1"), "POST", "/documents/d1/remind"),
    (lambda c: c.update_recipients("d1", [{"id": "1", "name": "A", "email": "a@x.io"}]),
     "PATCH", "/documents/d1/recipients"),
    (lambda c: c.update_authentication("d1", [{"id": "1", "passcode": "p"}]),
     "PATCH", "/documents/d1/authentication"),
    (lambda c: c.get_completed_pdf("d1", url_only=True), "GET", "/documents/d1/completed_pdf"),
    (lambda c: c.get_nom151_certificate("d1"), "GET", "/documents/d1/nom151_certificate"),
    (lambda c: c.create_template([{"name": "t.pdf", "file_url": "u"}], [{"id": "1", "name": "Client"}]),
     "POST", "/document_templates"),
    (lambda c: c.get_template("t1"), "GET", "/document_templates/t1"),
    (lambda c: c.update_template("t1", name="n"), "PUT", "/document_templates/t1"),
    (lambda c: c.delete_template("t1"), "DELETE", "/document_templates/t1"),
    (lambda c: c.create_document_from_template([{"id": "1"}], template_id="t1"),
     "POST", "/document_templates/documents"),
    (lambda c: c.list_bulk_sends(page=2), "GET", "/bulk_sends"),
    (lambda c: c.get_bulk_send("b1"), "GET", "/bulk_sends/b1"),
    (lambda c: c.get_bulk_send_documents("b1", limit=5), "GET", "/bulk_sends/b1/documents"),
    (lambda c: c.validate_bulk_send_csv(["t1"], "Y3N2"), "POST", "/bulk_sends/validate_csv"),
    (lambda c: c.create_bulk_send(["t1"], "Y3N2"), "POST", "/bulk_sends"),
    (lambda c: c.list_webhooks(), "GET", "/hooks"),
    (lambda c: c.create_webhook("https://hook"), "POST", "/hooks"),
    (lambda c: c.delete_webhook("h1"), "DELETE", "/hooks/h1"),
    (lambda c: c.get_api_application("a1"), "GET", "/api_applications/a1"),
    (lambda c: c.delete_api_application("a1"), "DELETE", "/api_applications/a1"),
])
def test_each_operation_hits_its_endpoint(calls, client, call, method, path):
    call(client)
    assert (calls[0]["method"], calls[0]["url"]) == (method, f"{BASE}{path}")


def test_remind_without_recipients_sends_no_body(calls, client):
    client.send_reminder("d1")
    assert calls[0]["json"] is None
    client.send_reminder("d1", recipients=[{"email": "a@example.com"}])
    assert calls[1]["json"] == {"recipients": [{"email": "a@example.com"}]}


def test_csv_template_uses_repeated_bracket_param(calls, client):
    client.get_bulk_send_csv_template(["t1", "t2"], base64=True)
    assert calls[0]["params"] == {"template_ids[]": ["t1", "t2"], "base64": True}


def test_webhook_create_drops_absent_application(calls, client):
    client.create_webhook("https://hook")
    assert calls[0]["json"] == {"callback_url": "https://hook"}


def test_204_returns_none(calls, client):
    calls.responses.append(_Resp(None, status_code=204))
    assert client.delete_document("d1") is None


def test_binary_body_is_returned_as_bytes(calls, client):
    calls.responses.append(_Resp(raw=b"%PDF-1.7 ...", ctype="application/pdf"))
    assert client.get_completed_pdf("d1") == b"%PDF-1.7 ..."


def test_http_error_is_typed(calls, client):
    calls.responses.append(_Resp({"message": "Unauthorized"}, status_code=401))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_me()
    assert exc.value.status_code == 401
    assert exc.value.service == "signwell"
