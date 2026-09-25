"""Contrat du client Google Analytics 4 (compte de service, lecture seule).

Le VRAI client, une vraie `requests.Session`, et un transport simulé monté sur
la session (`_Transport`, un adaptateur `requests`) : l'échange de jeton et les
appels d'API passent par le même chemin qu'en production, sans réseau. La clé
de compte de service est générée à la volée (RSA 2048) — aucune clé réelle,
aucun identifiant réel (email, propriété) dans ce fichier.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from requests.adapters import BaseAdapter

from oto.tools.common import UpstreamHTTPError
from oto.tools.google_analytics import (
    GA4Client,
    GA4Error,
    GA4InvalidArgument,
    GA4PermissionDenied,
    GA4ServiceDisabled,
    ServiceAccountAuthError,
    build_filter,
    build_order_bys,
    flatten_report,
    parse_service_account_key,
    property_name,
)
from oto.tools.google_analytics import auth as ga_auth

EMAIL = "lecteur@projet-factice.iam.gserviceaccount.com"
PROP = "properties/123456789"


@pytest.fixture(scope="module")
def key() -> dict:
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    return {"type": "service_account", "project_id": "projet-factice",
            "private_key_id": "kid-factice", "private_key": pem,
            "client_email": EMAIL, "client_id": "1",
            "token_uri": "https://oauth2.googleapis.com/token"}


class _Transport(BaseAdapter):
    """Adaptateur `requests` : sert une réponse par (méthode, chemin) et journalise
    chaque requête préparée (URL, en-têtes, corps)."""

    def __init__(self):
        super().__init__()
        self.sent: list[requests.PreparedRequest] = []
        self.routes: dict[tuple[str, str], list[tuple[int, object]]] = {}

    def on(self, method: str, path: str, status: int, body) -> None:
        self.routes.setdefault((method, path), []).append((status, body))

    def send(self, request, **kwargs):
        self.sent.append(request)
        u = urlparse(request.url)
        path = u.path if u.netloc != "oauth2.googleapis.com" else "/token"
        queue = self.routes.get((request.method, path))
        if not queue:
            raise AssertionError(f"requête inattendue : {request.method} {request.url}")
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(body).encode()
        resp.headers["Content-Type"] = "application/json"
        resp.url = request.url
        resp.request = request
        return resp

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _cache_vide():
    ga_auth._TOKEN_CACHE.clear()
    yield
    ga_auth._TOKEN_CACHE.clear()


@pytest.fixture
def transport():
    t = _Transport()
    t.on("POST", "/token", 200, {"access_token": "jeton-1", "expires_in": 3599,
                                 "token_type": "Bearer"})
    return t


@pytest.fixture
def client(key, transport):
    s = requests.Session()
    s.mount("https://", transport)
    return GA4Client(json.dumps(key), session=s)


def _api(transport):
    return [r for r in transport.sent if "oauth2.googleapis.com" not in r.url]


# --- la clé -----------------------------------------------------------------

def test_la_cle_se_lit_en_texte_ou_en_dict(key):
    assert parse_service_account_key(json.dumps(key))["client_email"] == EMAIL
    assert parse_service_account_key(key)["client_email"] == EMAIL


def test_un_json_de_client_oauth_est_refuse_nommement():
    with pytest.raises(ValueError, match="client OAuth"):
        parse_service_account_key({"installed": {"client_id": "x"}})


def test_un_texte_qui_n_est_pas_du_json_est_refuse():
    with pytest.raises(ValueError, match="pas du JSON"):
        parse_service_account_key("AIzaFaux")


def test_une_cle_sans_cle_privee_est_refusee(key):
    with pytest.raises(ValueError, match="private_key"):
        parse_service_account_key({**key, "private_key": ""})


def test_une_cle_privee_illisible_est_refusee_a_la_lecture(key):
    with pytest.raises(ValueError, match="illisible"):
        parse_service_account_key({**key, "private_key": "-----BEGIN PRIVATE KEY-----\nxx"})


def test_un_token_uri_etranger_est_refuse(key):
    """La clé porte son propre `token_uri` : le suivre ferait d'un champ saisi la
    destination d'une assertion signée."""
    with pytest.raises(ValueError, match="token_uri"):
        parse_service_account_key({**key, "token_uri": "http://169.254.169.254/token"})


# --- le jeton ---------------------------------------------------------------

def test_le_jeton_s_obtient_par_assertion_signee_en_corps(client, transport):
    transport.on("GET", "/v1beta/accountSummaries", 200, {"accountSummaries": []})
    client.account_summaries()
    tok = transport.sent[0]
    assert tok.url == "https://oauth2.googleapis.com/token"  # rien en query string
    form = parse_qs(tok.body)
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    assert form["assertion"][0].count(".") == 2
    assert _api(transport)[0].headers["Authorization"] == "Bearer jeton-1"


def test_le_jeton_est_mis_en_cache_process_wide(key, transport):
    """Le serveur construit un client par appel : le cache doit survivre au client."""
    transport.on("GET", "/v1beta/accountSummaries", 200, {"accountSummaries": []})
    for _ in range(3):
        s = requests.Session()
        s.mount("https://", transport)
        GA4Client(key, session=s).account_summaries()
    assert sum("oauth2.googleapis.com" in r.url for r in transport.sent) == 1
    assert all(len(k) == 64 and EMAIL not in k for k in ga_auth._TOKEN_CACHE)


def test_un_refus_du_serveur_de_jetons_est_un_refus_de_credential(client, transport):
    transport.routes[("POST", "/token")] = [(400, {"error": "invalid_grant",
                                                   "error_description": "Invalid JWT Signature."})]
    with pytest.raises(ServiceAccountAuthError) as ei:
        client.account_summaries()
    assert ei.value.status_code == 401 and ei.value.upstream_status == 400
    assert "assertion" not in str(ei.value)


# --- Admin ------------------------------------------------------------------

def test_les_resumes_de_comptes_suivent_la_pagination(client, transport):
    transport.on("GET", "/v1beta/accountSummaries", 200,
                 {"accountSummaries": [{"account": "accounts/1"}], "nextPageToken": "p2"})
    transport.on("GET", "/v1beta/accountSummaries", 200,
                 {"accountSummaries": [{"account": "accounts/2"}]})
    out = client.account_summaries()
    assert [a["account"] for a in out] == ["accounts/1", "accounts/2"]
    second = _api(transport)[1]
    assert parse_qs(urlparse(second.url).query)["pageToken"] == ["p2"]


def test_flux_et_evenements_cles_visent_la_propriete(client, transport):
    transport.on("GET", f"/v1beta/{PROP}/dataStreams", 200, {"dataStreams": [{"type": "WEB"}]})
    transport.on("GET", f"/v1beta/{PROP}/keyEvents", 200, {"keyEvents": [{"eventName": "achat"}]})
    assert client.list_data_streams("123456789") == [{"type": "WEB"}]
    assert client.list_key_events(PROP) == [{"eventName": "achat"}]
    assert all(urlparse(r.url).netloc == "analyticsadmin.googleapis.com" for r in _api(transport))


@pytest.mark.parametrize("bad", ["G-ABC123", "accounts/1", "", "properties/"])
def test_un_identifiant_qui_n_est_pas_une_propriete_est_refuse(bad):
    with pytest.raises(ValueError, match="Propriété GA4 invalide"):
        property_name(bad)


# --- Data -------------------------------------------------------------------

def test_run_report_par_defaut_les_30_derniers_jours(client, transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 200, {"rowCount": 0})
    client.run_report(PROP, metrics=["sessions"], dimensions="date,country", limit=10)
    req = _api(transport)[0]
    assert urlparse(req.url).netloc == "analyticsdata.googleapis.com"
    body = json.loads(req.body)
    assert body["dateRanges"] == [{"startDate": "30daysAgo", "endDate": "yesterday"}]
    assert body["dimensions"] == [{"name": "date"}, {"name": "country"}]
    assert body["metrics"] == [{"name": "sessions"}] and body["limit"] == 10


def test_run_report_filtres_et_tri(client, transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 200, {"rowCount": 0})
    client.run_report(PROP, metrics=["eventCount"], dimensions=["eventName"],
                      dimension_filter={"eventName": ["achat", "inscription"]},
                      metric_filter={"eventCount": 5}, order_by=["-eventCount", "eventName"])
    body = json.loads(_api(transport)[0].body)
    assert body["dimensionFilter"] == {"filter": {"fieldName": "eventName", "inListFilter": {
        "values": ["achat", "inscription"]}}}
    assert body["metricFilter"]["filter"]["numericFilter"]["operation"] == "EQUAL"
    assert body["orderBys"] == [{"metric": {"metricName": "eventCount"}, "desc": True},
                                {"dimension": {"dimensionName": "eventName"}, "desc": False}]


def test_run_report_sans_metrique_ni_dimension_est_refuse(client):
    with pytest.raises(ValueError, match="au moins une"):
        client.run_report(PROP)


def test_realtime_et_metadonnees(client, transport):
    transport.on("POST", f"/v1beta/{PROP}:runRealtimeReport", 200, {"rowCount": 0})
    transport.on("GET", f"/v1beta/{PROP}/metadata", 200, {"dimensions": [], "metrics": []})
    client.run_realtime_report(PROP, metrics=["activeUsers"], minutes_ago=10)
    assert json.loads(_api(transport)[0].body)["minuteRanges"] == [
        {"startMinutesAgo": 9, "endMinutesAgo": 0}]
    assert client.get_metadata(PROP) == {"dimensions": [], "metrics": []}


# --- refus typés ------------------------------------------------------------

def _google_error(code, status, message, reason=None):
    err = {"code": code, "status": status, "message": message}
    if reason:
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                           "reason": reason}]
    return {"error": err}


def test_un_nom_invalide_est_un_invalid_argument_avec_le_message_de_google(client, transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 400, _google_error(
        400, "INVALID_ARGUMENT", "Field fauxNom is not a valid metric."))
    with pytest.raises(GA4InvalidArgument) as ei:
        client.run_report(PROP, metrics=["fauxNom"])
    assert ei.value.message == "Field fauxNom is not a valid metric."
    assert isinstance(ei.value, UpstreamHTTPError) and ei.value.status_code == 400


def test_sans_acces_le_refus_nomme_le_compte_de_service_et_la_propriete(client, transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 403, _google_error(
        403, "PERMISSION_DENIED", "User does not have sufficient permissions for this property."))
    with pytest.raises(GA4PermissionDenied) as ei:
        client.run_report(PROP, metrics=["sessions"])
    assert ei.value.client_email == EMAIL and ei.value.resource == PROP
    assert EMAIL in str(ei.value) and "Lecteur" in str(ei.value)


def test_api_non_activee_est_distinguee_d_un_manque_d_acces(client, transport):
    transport.on("GET", "/v1beta/accountSummaries", 403, _google_error(
        403, "PERMISSION_DENIED", "API has not been used in project", "SERVICE_DISABLED"))
    with pytest.raises(GA4ServiceDisabled):
        client.account_summaries()


def test_un_autre_refus_reste_un_ga4_error_generique(client, transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 429, _google_error(
        429, "RESOURCE_EXHAUSTED", "quota"))
    with pytest.raises(GA4Error) as ei:
        client.run_report(PROP, metrics=["sessions"])
    assert type(ei.value) is GA4Error and ei.value.status == "RESOURCE_EXHAUSTED"


# --- aides pures ------------------------------------------------------------

def test_filtre_simple_combine_en_et_et_expression_passe_telle_quelle():
    f = build_filter({"country": "France", "deviceCategory": "mobile"})
    assert [e["filter"]["fieldName"] for e in f["andGroup"]["expressions"]] == [
        "country", "deviceCategory"]
    brut = {"notExpression": {"filter": {"fieldName": "country",
                                         "stringFilter": {"value": "France"}}}}
    assert build_filter(brut) is brut
    with pytest.raises(ValueError):
        build_filter({"country": True})
    with pytest.raises(ValueError):
        build_filter({})


def test_tri_decroissant_par_tiret():
    assert build_order_bys(["-sessions"], ["sessions"]) == [
        {"metric": {"metricName": "sessions"}, "desc": True}]


def test_rapport_a_plat_types_et_avertissements():
    resp = {
        "dimensionHeaders": [{"name": "date"}],
        "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"},
                          {"name": "engagementRate", "type": "TYPE_FLOAT"}],
        "rows": [{"dimensionValues": [{"value": "20260901"}],
                  "metricValues": [{"value": "12"}, {"value": "0.5"}]}],
        "rowCount": 30,
        "metadata": {"currencyCode": "EUR", "timeZone": "Europe/Paris",
                     "subjectToThresholding": True, "dataLossFromOtherRow": False},
        "kind": "analyticsData#runReport",
    }
    out = flatten_report(resp)
    assert out["columns"] == ["date", "sessions", "engagementRate"]
    assert out["rows"] == [["20260901", 12, 0.5]] and out["row_count"] == 30
    assert out["metadata"] == {"currencyCode": "EUR", "timeZone": "Europe/Paris",
                               "subjectToThresholding": True}


def test_aucune_methode_d_ecriture():
    """Lecture seule : une écriture devrait repasser par une PR, pas par un appel."""
    publiques = {n for n in dir(GA4Client) if not n.startswith("_")}
    assert publiques == {"account_summaries", "list_data_streams", "list_key_events",
                         "get_metadata", "run_report", "run_realtime_report"}
