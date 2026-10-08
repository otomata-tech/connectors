"""Contrat du client Google Ads (REST, jeton OAuth de l'utilisateur, lecture seule).

Ce que ce fichier verrouille, parce que le scope `adwords` PERMET de modifier des
campagnes — Google ne tient pas la lecture seule à notre place :
1. LECTURE SEULE — seules trois URLs de lecture partent ; une requête qui n'est pas un
   `SELECT … FROM …` est refusée sans qu'aucun appel ne parte ;
2. le JETON — dans l'en-tête `Authorization`, jamais dans l'URL ; aucun developer
   token (retiré par Google le 2026-09-09) ; `login-customer-id` seulement s'il est
   donné ;
3. les refus Google LUS par leur code (`GoogleAdsError.codes`), pas par le texte.

Le VRAI client, une vraie `requests.Session`, un transport simulé monté sur la session.
"""
from __future__ import annotations

import json

import pytest
import requests
from requests.adapters import BaseAdapter

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.google.ads import (
    GoogleAdsClient,
    GoogleAdsError,
    check_select,
    customer_id,
    flatten_rows,
)

API = "https://googleads.googleapis.com/v25"
Q = "SELECT campaign.id, campaign.name, metrics.cost_micros FROM campaign"
MASK = "campaign.id,campaign.name,metrics.costMicros"


class _Transport(BaseAdapter):
    """Adaptateur `requests` : répond par la file `replies` (ou par `handler`) et
    journalise chaque requête préparée."""

    def __init__(self):
        super().__init__()
        self.sent: list[requests.PreparedRequest] = []
        self.replies: list = []
        self.handler = None

    def send(self, request, **kwargs):
        self.sent.append(request)
        if self.handler:
            status, body = 200, self.handler(json.loads(request.body))
        else:
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            status, body = reply if isinstance(reply, tuple) else (200, reply)
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(body).encode()
        resp.headers["Content-Type"] = "application/json"
        resp.url = request.url
        resp.request = request
        return resp

    def close(self):
        pass

    def bodies(self):
        return [json.loads(r.body) if r.body else None for r in self.sent]


@pytest.fixture
def transport():
    return _Transport()


@pytest.fixture
def client(transport):
    s = requests.Session()
    s.mount("https://", transport)
    return GoogleAdsClient("ya29.jeton", session=s)


def _ads_error(http, status, family, code, message="m", request_id="req-1"):
    return (http, {"error": {"code": http, "message": "Request contains an invalid argument.",
                             "status": status, "details": [{
                                 "@type": "type.googleapis.com/google.ads.googleads.v25"
                                          ".errors.GoogleAdsFailure",
                                 "errors": [{"errorCode": {family: code},
                                             "message": message}],
                                 "requestId": request_id}]}})


# --- 1. lecture seule ---------------------------------------------------------

@pytest.mark.parametrize("query", [
    "", "   ", "DELETE FROM campaign", "UPDATE campaign SET name = 'x'",
    "campaign.id FROM campaign", "SELECT campaign.id", "mutate campaigns",
])
def test_une_requete_qui_nest_pas_un_select_gaql_est_refusee_sans_appel(client, transport,
                                                                         query):
    with pytest.raises(ValueError, match="Read-only"):
        client.search("1234567890", query)
    assert transport.sent == []


def test_seules_trois_urls_de_lecture_partent(client, transport):
    transport.replies = [{"resourceNames": ["customers/1234567890"],
                          "results": [{"name": "campaign.id", "selectable": True}],
                          "fieldMask": "campaign.id"}]
    client.list_accessible_customers()
    client.search("1234567890", "SELECT campaign.id FROM campaign")
    client.describe_resource("campaign")
    urls = {(r.method, r.url) for r in transport.sent}
    assert urls == {
        ("GET", f"{API}/customers:listAccessibleCustomers"),
        ("POST", f"{API}/customers/1234567890/googleAds:search"),
        ("POST", f"{API}/googleAdsFields:search"),
    }


def test_le_client_n_expose_aucune_ecriture():
    publiques = {n for n in dir(GoogleAdsClient) if not n.startswith("_")}
    assert publiques == {"list_accessible_customers", "search", "search_fields",
                         "describe_resource"}


# --- 2. jeton et en-têtes -----------------------------------------------------

def test_le_jeton_part_en_entete_sans_developer_token(client, transport):
    transport.replies = [{"results": [], "fieldMask": MASK}]
    client.search("123-456-7890", Q)
    req = transport.sent[0]
    assert req.headers["Authorization"] == "Bearer ya29.jeton"
    assert "developer-token" not in req.headers
    assert "login-customer-id" not in req.headers
    assert "ya29" not in req.url
    assert req.url == f"{API}/customers/1234567890/googleAds:search"
    assert transport.bodies()[0] == {"query": Q, "returnTotalResultsCount": True}


def test_login_customer_id_part_en_entete_normalise(client, transport):
    transport.replies = [{"results": [], "fieldMask": MASK}]
    client.search("1234567890", Q, login_customer_id="987-654-3210")
    assert transport.sent[0].headers["login-customer-id"] == "9876543210"


def test_le_page_token_de_google_est_transmis(client, transport):
    transport.replies = [{"results": [], "fieldMask": MASK}]
    client.search("1234567890", Q, page_token="G2")
    assert transport.bodies()[0]["pageToken"] == "G2"


def test_sans_jeton_le_client_ne_se_construit_pas():
    with pytest.raises(MissingCredential):
        GoogleAdsClient("")


@pytest.mark.parametrize("cid", ["123", "12345678901", "abc-def-ghij", "1234567890/x", None])
def test_un_customer_id_hors_forme_est_refuse(cid):
    with pytest.raises(ValueError, match="10 digits"):
        customer_id(cid)


def test_un_customer_id_hors_forme_ne_part_pas(client, transport):
    with pytest.raises(ValueError):
        client.search("1234567890/x", Q)
    assert transport.sent == []


def test_check_select_rend_la_requete_nettoyee():
    assert check_select(f"  {Q}\n") == Q


# --- 3. lignes ----------------------------------------------------------------

def test_les_colonnes_suivent_le_field_mask_et_les_lignes_leur_ordre():
    page = {"results": [{"campaign": {"id": "7", "name": "Marque"}}], "fieldMask": MASK}
    assert flatten_rows(page) == (["campaign.id", "campaign.name", "metrics.costMicros"],
                                  [["7", "Marque", None]])


def test_une_page_vide_n_a_ni_colonne_ni_ligne():
    assert flatten_rows({}) == ([], [])


def test_des_lignes_sans_field_mask_sont_un_refus():
    with pytest.raises(ValueError, match="fieldMask"):
        flatten_rows({"results": [{"campaign": {"id": "1"}}]})


# --- 4. refus lus par leur code -----------------------------------------------

def test_le_refus_porte_ses_codes_et_l_identifiant_de_requete(client, transport):
    transport.replies = [_ads_error(403, "PERMISSION_DENIED", "authorizationError",
                                    "USER_PERMISSION_DENIED", "User doesn't have permission",
                                    request_id="abc123")]
    with pytest.raises(GoogleAdsError) as e:
        client.search("1234567890", Q)
    err = e.value
    assert isinstance(err, UpstreamHTTPError) and err.status_code == 403
    assert err.status == "PERMISSION_DENIED"
    assert err.codes == {"USER_PERMISSION_DENIED"}
    assert err.families == {"authorizationError"}
    assert err.request_id == "abc123"
    assert err.detail == "User doesn't have permission"
    assert "google_ads HTTP 403" in str(err)


def test_une_raison_error_info_est_un_code(client, transport):
    transport.replies = [(403, {"error": {
        "code": 403, "status": "PERMISSION_DENIED",
        "message": "Google Ads API has not been used in project 1 before",
        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                     "reason": "SERVICE_DISABLED"}]}})]
    with pytest.raises(GoogleAdsError) as e:
        client.list_accessible_customers()
    assert e.value.codes == {"SERVICE_DISABLED"}
    assert e.value.detail.startswith("Google Ads API has not been used")


def test_un_corps_d_erreur_non_json_reste_lisible(client, transport):
    transport.replies = [(502, "Bad Gateway")]
    with pytest.raises(GoogleAdsError) as e:
        client.list_accessible_customers()
    assert e.value.status_code == 502 and e.value.codes == set()
    assert e.value.detail == "Bad Gateway"


# --- comptes et champs --------------------------------------------------------

def test_les_comptes_accessibles_perdent_leur_prefixe(client, transport):
    transport.replies = [{"resourceNames": ["customers/1234567890", "customers/1112223334"]}]
    assert client.list_accessible_customers() == ["1234567890", "1112223334"]


def test_les_champs_sont_ranges_par_famille(client, transport):
    own = {"results": [
        {"name": "campaign.id", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "campaign.name", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "campaign.url_custom_parameters", "category": "ATTRIBUTE",
         "selectable": True}]}
    linked = {"results": [
        {"name": "metrics.clicks", "category": "METRIC", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "segments.date", "category": "SEGMENT", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "customer.currency_code", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "campaign.id", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True}]}
    transport.handler = lambda body: own if "LIKE 'campaign.%'" in body["query"] else linked
    out = client.describe_resource("campaign")
    assert out["attributes"] == ["campaign.id", "campaign.name",
                                 "campaign.url_custom_parameters"]
    assert out["metrics"] == ["metrics.clicks"] and out["segments"] == ["segments.date"]
    assert out["related"] == ["customer.currency_code"]
    assert out["not_filterable"] == ["campaign.url_custom_parameters"]
    assert out["not_sortable"] == ["campaign.url_custom_parameters"]


def test_une_ressource_inconnue_est_un_refus_nomme(client, transport):
    transport.replies = [{"results": []}]
    with pytest.raises(ValueError, match="no resource `campagne`"):
        client.describe_resource("campagne")
    assert len(transport.sent) == 1


@pytest.mark.parametrize("resource", ["Campaign", "campaign' OR 1", "ad-group", "", None])
def test_un_nom_de_ressource_hors_forme_est_refuse_sans_appel(client, transport, resource):
    with pytest.raises(ValueError, match="snake_case"):
        client.describe_resource(resource)
    assert transport.sent == []


def test_les_pages_de_champs_sont_suivies_puis_bornees(client, transport):
    transport.replies = [{"results": [{"name": "a"}], "nextPageToken": "n"}]
    with pytest.raises(RuntimeError, match="more than 5 pages"):
        client.search_fields("SELECT name WHERE name LIKE 'campaign.%'")
    assert [b.get("pageToken") for b in transport.bodies()] == [None, "n", "n", "n", "n"]
