"""YousignClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + body, sans réseau ni
clé réelle. Cible ce qui pourrait dériver en silence : l'en-tête d'auth Bearer,
le choix sandbox/prod, les méthodes une-par-endpoint, le nettoyage des `None`,
l'upload multipart d'un document, et le décodage d'une réponse binaire (PDF).
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.yousign import YousignClient

PROD = "https://api.yousign.app/v3"
SANDBOX = "https://api-sandbox.yousign.app/v3"


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
    return YousignClient(api_key="ys-test")


#: Le patron de la maison couvre le cycle de vie d'une demande de signature —
#: pas la surface identité/templates/branding, hors scope de ce lot.
METHODS = {
    "create_signature_request", "get_signature_request", "list_signature_requests",
    "activate_signature_request", "cancel_signature_request", "delete_signature_request",
    "add_document", "download_document", "download_audit_trail",
    "add_signer", "get_signer_documents",
}


def test_full_scope_coverage(client):
    public = {n for n in dir(YousignClient)
              if not n.startswith("_") and callable(getattr(YousignClient, n))}
    assert public == METHODS


def test_api_key_goes_in_bearer_header(calls, client):
    client.get_signature_request("sr1")
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == f"{PROD}/signature_requests/sr1"
    assert calls[0]["headers"]["Authorization"] == "Bearer ys-test"


def test_sandbox_picks_the_other_host(calls):
    c = YousignClient(api_key="ys-test", sandbox=True)
    c.get_signature_request("sr1")
    assert calls[0]["url"] == f"{SANDBOX}/signature_requests/sr1"


def test_create_signature_request_body_and_none_dropped(calls, client):
    client.create_signature_request("NDA — Partie A / Partie B",
                                    delivery_mode="none", expiration_date=None)
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{PROD}/signature_requests")
    assert c["json"] == {"name": "NDA — Partie A / Partie B", "delivery_mode": "none"}


def test_create_signature_request_defaults_to_email_delivery(calls, client):
    client.create_signature_request("Accord de confidentialité")
    assert calls[0]["json"]["delivery_mode"] == "email"


@pytest.mark.parametrize("call,method,path", [
    (lambda c: c.get_signature_request("sr1"), "GET", "/signature_requests/sr1"),
    (lambda c: c.list_signature_requests(), "GET", "/signature_requests"),
    (lambda c: c.activate_signature_request("sr1"), "POST", "/signature_requests/sr1/activate"),
    (lambda c: c.cancel_signature_request("sr1"), "POST", "/signature_requests/sr1/cancel"),
    (lambda c: c.delete_signature_request("sr1"), "DELETE", "/signature_requests/sr1"),
    (lambda c: c.download_document("sr1", "d1"), "GET",
     "/signature_requests/sr1/documents/d1/download"),
    (lambda c: c.download_audit_trail("sr1"), "GET",
     "/signature_requests/sr1/audit_trails/download"),
    (lambda c: c.get_signer_documents("sr1", "sg1"), "GET",
     "/signature_requests/sr1/signers/sg1/documents"),
])
def test_each_operation_hits_its_endpoint(calls, client, call, method, path):
    call(client)
    assert (calls[0]["method"], calls[0]["url"]) == (method, f"{PROD}{path}")


def test_delete_permanent_delete_query_param(calls, client):
    client.delete_signature_request("sr1", permanent_delete=True)
    assert calls[0]["params"] == {"permanent_delete": True}
    client.delete_signature_request("sr1")
    assert calls[1]["params"] == {"permanent_delete": False}


def test_add_document_is_multipart_not_json(calls, client):
    client.add_document("sr1", b"%PDF-1.7 ...", "nda.pdf", name="NDA", password=None)
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{PROD}/signature_requests/sr1/documents")
    assert c["files"] == {"file": ("nda.pdf", b"%PDF-1.7 ...")}
    assert c["data"] == {"nature": "signable_document", "name": "NDA"}
    assert "json" not in c or c["json"] is None


def test_add_document_attachment_nature(calls, client):
    client.add_document("sr1", b"...", "annexe.pdf", nature="attachment")
    assert calls[0]["data"]["nature"] == "attachment"


def test_add_signer_body_shape(calls, client):
    info = {"first_name": "Alice", "last_name": "Dupont",
            "email": "alice.dupont@example.com", "locale": "fr"}
    client.add_signer("sr1", info, signature_authentication_mode="no_otp")
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{PROD}/signature_requests/sr1/signers")
    assert c["json"] == {"info": info, "signature_level": "electronic_signature",
                         "signature_authentication_mode": "no_otp"}


def test_add_signer_defaults_to_electronic_signature_level(calls, client):
    info = {"first_name": "Bob", "last_name": "Martin",
            "email": "bob.martin@example.com", "locale": "en"}
    client.add_signer("sr1", info)
    assert calls[0]["json"]["signature_level"] == "electronic_signature"


def test_binary_body_is_returned_as_bytes(calls, client):
    calls.responses.append(_Resp(raw=b"%PDF-1.7 signed...", ctype="application/pdf"))
    assert client.download_document("sr1", "d1") == b"%PDF-1.7 signed..."


def test_204_returns_none(calls, client):
    calls.responses.append(_Resp(None, status_code=204))
    assert client.delete_signature_request("sr1") is None


def test_http_error_is_typed(calls, client):
    calls.responses.append(_Resp({"message": "Unauthorized"}, status_code=401))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_signature_request("sr1")
    assert exc.value.status_code == 401
    assert exc.value.service == "yousign"


def test_api_key_from_env(monkeypatch, calls):
    monkeypatch.setenv("YOUSIGN_API_KEY", "ys-env")
    c = YousignClient()
    c.get_signature_request("sr1")
    assert calls[0]["headers"]["Authorization"] == "Bearer ys-env"
