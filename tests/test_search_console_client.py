"""Contrat du client Search Console (compte de service).

Le VRAI client et une session à transport simulé, comme `test_google_analytics_client` :
aucun réseau, aucune clé réelle (RSA générée à la volée), aucun site réel.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from requests.adapters import BaseAdapter

from oto.tools.google_analytics import auth as ga_auth
from oto.tools.search_console import (
    SearchConsoleClient,
    SearchConsoleDenied,
    SearchConsoleError,
    SearchConsoleInvalid,
    SearchConsoleServiceDisabled,
    build_filters,
    flatten_inspection,
    flatten_performance,
    site_url,
)
from oto.tools.search_console import client as sc

EMAIL = "lecteur@projet-factice.iam.gserviceaccount.com"
SITE = "sc-domain:exemple.test"
SITE_PATH = "/webmasters/v3/sites/sc-domain%3Aexemple.test"


@pytest.fixture(scope="module")
def key_json() -> str:
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "private_key_id": "kid-factice",
                       "private_key": pem, "client_email": EMAIL,
                       "token_uri": "https://oauth2.googleapis.com/token"})


class _Transport(BaseAdapter):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.routes = {("POST", "/token"): (200, {"access_token": "jeton", "expires_in": 3599})}

    def on(self, method, path, status, body):
        self.routes[(method, path)] = (status, body)

    def send(self, request, **kwargs):
        self.sent.append(request)
        u = urlparse(request.url)
        path = "/token" if u.netloc == "oauth2.googleapis.com" else u.path
        if (request.method, path) not in self.routes:
            raise AssertionError(f"requête inattendue : {request.method} {request.url}")
        status, body = self.routes[(request.method, path)]
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(body).encode() if body is not None else b""
        resp.url = request.url
        resp.request = request
        return resp

    def close(self):
        pass


@pytest.fixture
def transport():
    ga_auth._TOKEN_CACHE.clear()
    return _Transport()


@pytest.fixture
def client(key_json, transport):
    session = requests.Session()
    session.mount("https://", transport)
    return SearchConsoleClient(key_json, session=session)


def _refus(status, reason="", etat=""):
    return {"error": {"code": status, "message": "refus factice", "status": etat,
                      "errors": [{"reason": reason}] if reason else []}}


def test_site_url_normalise_et_refuse():
    assert site_url(SITE) == SITE
    assert site_url("https://exemple.test") == "https://exemple.test/"
    with pytest.raises(ValueError, match="sc-domain"):
        site_url("exemple.test")


def test_list_sites_lit_avec_le_scope_lecture(client, transport):
    transport.on("GET", "/webmasters/v3/sites", 200,
                 {"siteEntry": [{"siteUrl": SITE, "permissionLevel": "siteFullUser"}]})
    assert client.list_sites()[0]["siteUrl"] == SITE
    assertion = transport.sent[0].body
    from google.auth import jwt
    token = assertion.split("assertion=")[1].split("&")[0]
    assert jwt.decode(token, verify=False)["scope"] == sc.SCOPE_READONLY


def test_performance_envoie_le_corps_et_se_rend_en_table(client, transport):
    transport.on("POST", f"{SITE_PATH}/searchAnalytics/query", 200, {
        "rows": [{"keys": ["requete"], "clicks": 3, "impressions": 40, "ctr": 0.075,
                  "position": 7.2}],
        "responseAggregationType": "byPage"})
    resp = client.search_analytics(SITE, start_date="2026-10-01", end_date="2026-10-28",
                                   dimensions=["query"], filters={"page": "https://exemple.test/a"})
    body = json.loads(transport.sent[-1].body)
    assert body["startDate"] == "2026-10-01" and body["dimensions"] == ["query"]
    assert body["dimensionFilterGroups"][0]["filters"] == [
        {"dimension": "page", "operator": "equals", "expression": "https://exemple.test/a"}]
    assert "type" not in body
    table = flatten_performance(resp, ["query"])
    assert table["columns"] == ["query", "clicks", "impressions", "ctr", "position"]
    assert table["rows"] == [["requete", 3, 40, 0.075, 7.2]]


def test_une_date_mal_formee_ne_part_pas(client, transport):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        client.search_analytics(SITE, start_date="30daysAgo", end_date="2026-10-28")
    assert transport.sent == []


def test_build_filters_laisse_passer_les_filtres_google():
    brut = [{"dimension": "page", "operator": "contains", "expression": "/articles/"}]
    assert build_filters(brut) == [{"groupType": "and", "filters": brut}]
    assert build_filters(None) is None
    with pytest.raises(ValueError):
        build_filters({})


def test_inspection_ne_garde_que_ce_qui_dit_l_indexation(client, transport):
    transport.on("POST", "/v1/urlInspection/index:inspect", 200, {"inspectionResult": {
        "inspectionResultLink": "https://search.google.com/x",
        "indexStatusResult": {"verdict": "NEUTRAL",
                              "coverageState": "Discovered - currently not indexed",
                              "robotsTxtState": "ALLOWED", "referringUrls": ["https://a"]}}})
    resp = client.inspect_url(SITE, "https://exemple.test/a")
    assert json.loads(transport.sent[-1].body) == {"siteUrl": SITE,
                                                   "inspectionUrl": "https://exemple.test/a"}
    vue = flatten_inspection(resp)
    assert vue["verdict"] == "NEUTRAL" and vue["coverage"].startswith("Discovered")
    assert "referringUrls" not in vue and "last_crawl" not in vue


def test_add_sitemap_demande_le_scope_ecriture(client, transport):
    path = f"{SITE_PATH}/sitemaps/https%3A%2F%2Fexemple.test%2Fsitemap.xml"
    transport.on("PUT", path, 204, None)
    client.add_sitemap(SITE, "https://exemple.test/sitemap.xml")
    from google.auth import jwt
    token = transport.sent[0].body.split("assertion=")[1].split("&")[0]
    assert jwt.decode(token, verify=False)["scope"] == sc.SCOPE_WRITE


@pytest.mark.parametrize("status,raison,classe", [
    (403, "forbidden", SearchConsoleDenied),
    (403, "accessNotConfigured", SearchConsoleServiceDisabled),
    (400, "badRequest", SearchConsoleInvalid),
    (429, "rateLimitExceeded", SearchConsoleError),
])
def test_les_refus_sont_types(client, transport, status, raison, classe):
    transport.on("GET", f"{SITE_PATH}/sitemaps", status, _refus(status, raison))
    with pytest.raises(classe) as e:
        client.list_sitemaps(SITE)
    assert e.value.status_code == status


def test_un_refus_d_acces_nomme_le_compte_de_service(client, transport):
    transport.on("GET", f"{SITE_PATH}/sitemaps", 403, _refus(403, "forbidden"))
    with pytest.raises(SearchConsoleDenied, match=EMAIL):
        client.list_sitemaps(SITE)
