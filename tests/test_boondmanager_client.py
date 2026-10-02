"""Contrat du client BoondManager (sous-ensemble CRM, `X-Jwt-Client-BoondManager`).

Mocke `requests.Session.request` : la signature JWT (rejouée sur l'exemple de la
référence éditeur), l'en-tête signé à chaque appel en mode `normal`, chemins et
paramètres, refus locaux (préfixes d'id, pagination, champs requis), la
re-tentative unique d'un GET sur 429 — et jamais d'un POST.
"""
from __future__ import annotations

import base64
import json

import pytest

from oto.tools.boondmanager import client as bm
from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError


class _Resp:
    def __init__(self, status_code: int = 200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {"data": [], "meta": {}}
        self.content = b"x"
        self.text = str(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "sleeps": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs)
        seen["calls"].append((method, url, kwargs))
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200)

    monkeypatch.setattr(bm.requests.Session, "request", fake_request)
    monkeypatch.setattr(bm.time, "sleep", lambda s: seen["sleeps"].append(s))
    monkeypatch.setattr(bm.time, "time", lambda: 1_700_000_000.0)
    return seen


@pytest.fixture()
def cli():
    return bm.BoondManagerClient(client_token="CT", client_key="CK", user_token="UT")


def _params(capture):
    return capture["kwargs"].get("params") or {}


def _payload(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


# --- authentification ------------------------------------------------------

def test_signature_rejoue_l_exemple_de_la_reference():
    jeton = bm.sign_hs256({"userToken": "token1", "clientToken": "token2",
                           "time": 1528535249, "mode": "god"}, "secret")
    assert jeton == (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJ1c2VyVG9rZW4iOiJ0b2tlbjEiLCJjbGllbnRUb2tlbiI6InRva2VuMiIsInRpbWUiOjE1"
        "Mjg1MzUyNDksIm1vZGUiOiJnb2QifQ."
        "F03Zlu7B6qPbhgupuTRFDiETU6fOYW2z4xhq20VoJE4")


def test_les_trois_secrets_sont_exiges():
    for manquant in ("client_token", "client_key", "user_token"):
        kw = {"client_token": "a", "client_key": "b", "user_token": "c"}
        kw[manquant] = None
        with pytest.raises(MissingCredential):
            bm.BoondManagerClient(**kw)


def test_jeton_signe_par_appel_en_mode_normal(capture, cli):
    cli.current_user()
    headers = capture["kwargs"]["headers"]
    jeton = headers[bm.JWT_HEADER]
    assert _payload(jeton) == {"userToken": "UT", "clientToken": "CT",
                               "time": 1_700_000_000, "mode": "normal"}
    assert capture["url"] == "https://ui.boondmanager.com/api/application/current-user"
    # Aucun secret dans l'URL ni les paramètres.
    assert "CK" not in capture["url"] and not _params(capture)


# --- recherche ----------------------------------------------------------------

def test_recherche_contacts_parametres(capture, cli):
    cli.search("contacts", keywords="CSOC12 dupont", keywords_type="default",
               period="updated", start_date="2026-09-01",
               filters={"states": [1, 2]},
               sort="updateDate", order="desc", page=2, max_results=50)
    assert capture["method"] == "GET"
    assert capture["url"].endswith("/api/contacts")
    assert _params(capture) == {
        "keywords": "CSOC12 dupont", "keywordsType": "default",
        "period": "updated", "startDate": "2026-09-01", "states[]": [1, 2],
        "sort": "updateDate", "order": "desc", "page": 2, "maxResults": 50}


def test_prefixe_d_id_inconnu_de_l_entite_refuse(capture, cli):
    with pytest.raises(ValueError, match="AO7"):
        cli.search("companies", keywords="AO7")
    assert capture["calls"] == []


def test_mot_qui_n_est_pas_un_prefixe_boond_accepte(capture, cli):
    cli.search("companies", keywords="ACME ISO9001")
    assert _params(capture)["keywords"] == "ACME ISO9001"


@pytest.mark.parametrize("entity,cap", [("contacts", 500), ("actions", 100)])
def test_max_results_borne_par_entite(capture, cli, entity, cap):
    with pytest.raises(ValueError, match=str(cap)):
        cli.search(entity, max_results=cap + 1)
    cli.search(entity, max_results=cap)
    assert _params(capture)["maxResults"] == cap


def test_filtre_hors_liste_refuse(capture, cli):
    with pytest.raises(ValueError, match="exportToDownloadCenter"):
        cli.search("contacts", filters={"exportToDownloadCenter": True})
    assert capture["calls"] == []


def test_periode_sans_date_et_date_sans_periode_refusees(cli):
    with pytest.raises(ValueError, match="start_date"):
        cli.search("contacts", period="created")
    with pytest.raises(ValueError, match="period"):
        cli.search("contacts", start_date="2026-01-01")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        cli.search("contacts", period="created", start_date="01/01/2026")


def test_keywords_type_propre_a_l_entite(cli):
    with pytest.raises(ValueError, match="keywords_type"):
        cli.search("opportunities", keywords_type="emails")


def test_entite_inconnue_refusee(cli):
    with pytest.raises(ValueError, match="entity"):
        cli.search("candidates")


# --- lecture --------------------------------------------------------------------

def test_get_onglet_information(capture, cli):
    cli.get("contacts", 42)
    assert capture["url"].endswith("/api/contacts/42/information")
    cli.get("actions", "7")
    assert capture["url"].endswith("/api/actions/7")


def test_get_id_non_numerique_refuse(capture, cli):
    with pytest.raises(ValueError, match="numeric"):
        cli.get("contacts", "CCON42")
    assert capture["calls"] == []


def test_dictionnaire_et_gabarit(capture, cli):
    cli.dictionary(language="fr")
    assert capture["url"].endswith("/application/dictionary")
    assert _params(capture) == {"language": "fr"}
    cli.get_default("opportunities")
    assert capture["url"].endswith("/api/opportunities/default")


# --- création -------------------------------------------------------------------

def test_creation_contact_corps_json_api(capture, cli):
    cli.create("contacts", {"firstName": "Ada", "lastName": "Lovelace",
                            "email1": "ada@example.com"},
               {"company": {"type": "company", "id": 12}})
    assert capture["method"] == "POST"
    assert capture["url"].endswith("/api/contacts")
    assert capture["kwargs"]["json"] == {"data": {
        "type": "contact",
        "attributes": {"firstName": "Ada", "lastName": "Lovelace",
                       "email1": "ada@example.com"},
        "relationships": {"company": {"data": {"id": "12", "type": "company"}}}}}


@pytest.mark.parametrize("entity,attrs,rels,manque", [
    ("contacts", {"firstName": "A", "lastName": "B"}, None, "company"),
    ("contacts", {"firstName": "A"}, {"company": {"type": "company", "id": 1}},
     "lastName"),
    ("companies", {}, None, "name"),
    ("opportunities", {"reference": "x"}, None, "title"),
    ("actions", {"typeOf": 1}, None, "dependsOn"),
])
def test_creation_champs_requis_verifies_avant_l_appel(capture, cli, entity, attrs,
                                                       rels, manque):
    with pytest.raises(ValueError, match=manque):
        cli.create(entity, attrs, rels)
    assert capture["calls"] == []


def test_relation_mal_formee_refusee(capture, cli):
    with pytest.raises(ValueError, match="dependsOn"):
        cli.create("actions", {"typeOf": 1}, {"dependsOn": "CCON3"})
    assert capture["calls"] == []


# --- limites et erreurs -----------------------------------------------------------

def test_429_sur_get_retente_une_fois_si_attente_courte(capture, cli):
    capture["responses"] = [_Resp(429, headers={"Retry-After": "3"}), _Resp(200)]
    cli.current_user()
    assert capture["sleeps"] == [3.0] and len(capture["calls"]) == 2


def test_429_attente_longue_remonte(capture, cli):
    capture["responses"] = [_Resp(429, headers={"Retry-After": "120"})]
    with pytest.raises(UpstreamHTTPError):
        cli.current_user()
    assert capture["sleeps"] == []


def test_429_sur_creation_jamais_retente(capture, cli):
    capture["responses"] = [_Resp(429, headers={"Retry-After": "1"})]
    with pytest.raises(UpstreamHTTPError):
        cli.create("companies", {"name": "Acme"})
    assert len(capture["calls"]) == 1 and capture["sleeps"] == []


def test_refus_amont_type(capture, cli):
    capture["responses"] = [_Resp(401, body={"errors": [{"detail": "nope"}]})]
    with pytest.raises(UpstreamHTTPError) as exc:
        cli.current_user()
    assert exc.value.status_code == 401
