"""`HttpConnectorClient.request` ne suit JAMAIS une redirection : l'auth injectée
(en-tête personnalisé, paramètre de requête) partirait avec elle — `requests` ne
retire que `Authorization` d'une redirection inter-hôte. Deux vrais serveurs locaux :
la cible ne doit recevoir aucune requête, la clé ne doit jamais y arriver."""
from __future__ import annotations

import http.server
import threading

import pytest

from oto.tools.http import HttpConnectorClient, RedirectRefused


def _serveur(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def cible():
    vus: list = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            vus.append((self.path, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"vole": true}')

        do_POST = do_GET

        def log_message(self, *a):
            pass

    srv, base = _serveur(H)
    yield base, vus
    srv.shutdown()


def _source(location: str, code: int = 302):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/ok"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                return
            self.send_response(code)
            self.send_header("Location", location)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *a):
            pass

    return _serveur(H)


@pytest.mark.parametrize("mode,fields", [
    ("header", {"header_name": "X-Api-Key", "token": "SECRET"}),
    ("query", {"query_param": "api_key", "token": "SECRET"}),
])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_une_redirection_vers_un_autre_hote_est_refusee(cible, mode, fields, method):
    base_cible, vus = cible
    srv, base = _source(base_cible + "/vol")
    try:
        c = HttpConnectorClient(base, mode, fields, timeout=5)
        with pytest.raises(RedirectRefused) as e:
            c.request(method, "/x", json={"a": 1} if method == "POST" else None)
        assert e.value.status == 302 and e.value.location == base_cible + "/vol"
        assert vus == [], "la cible de la redirection a reçu une requête"
    finally:
        srv.shutdown()


def test_une_redirection_vers_le_meme_hote_est_refusee_aussi():
    srv, base = _source("/ok")
    try:
        c = HttpConnectorClient(base, "bearer", {"token": "SECRET"}, timeout=5)
        with pytest.raises(RedirectRefused, match="redirect refused \\(302 to /ok\\)"):
            c.get("/x")
    finally:
        srv.shutdown()


def test_une_reponse_200_reste_inchangee():
    srv, base = _source("/ignore")
    try:
        c = HttpConnectorClient(base, "bearer", {"token": "SECRET"}, timeout=5)
        assert c.get("/ok") == {"ok": True}
    finally:
        srv.shutdown()


def test_get_raw_leve_la_meme_exception():
    srv, base = _source("https://evil.test/")
    try:
        c = HttpConnectorClient(base, "bearer", {"token": "SECRET"}, timeout=5)
        with pytest.raises(RedirectRefused):
            c.get_raw("/x", max_bytes=100)
    finally:
        srv.shutdown()


def test_la_location_rendue_ne_porte_ni_requete_ni_identifiants():
    """Un 3xx garde souvent la requête d'origine : la clé injectée en paramètre
    (`?api_key=…`) ou une userinfo ne doit jamais sortir dans le message ni dans
    l'attribut que l'appelant affiche."""
    from oto.tools.http.client import RedirectRefused

    e = RedirectRefused(302, "https://user:pw@other.test:8443/v2/x?api_key=SECRET#frag")
    assert e.location == "https://other.test:8443/v2/x"
    assert "SECRET" not in str(e) and "pw" not in str(e)
    rel = RedirectRefused(301, "/v2/y?api_key=SECRET")
    assert rel.location == "/v2/y" and "SECRET" not in str(rel)
