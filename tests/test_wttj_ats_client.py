"""WttjAtsClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + corps, sans réseau ni
jeton réel. Cible ce qui pourrait dériver en silence : le jeton en en-tête et
jamais en query string, une méthode par endpoint, le nettoyage des `None`, les
booléens en `true`/`false`, l'échappement d'une référence dans le chemin, le
déplacement d'un candidat par `job_stage_id`, et l'erreur typée d'un refus amont.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.wttj_ats import BASE_URL, WttjAtsClient


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Seen(list):
    """Les appels capturés, plus `responses` : réponses servies dans l'ordre."""


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        return responses.pop(0) if responses else _Resp([])

    monkeypatch.setattr("requests.Session.request", _request)
    seen.responses = responses
    return seen


@pytest.fixture
def client():
    return WttjAtsClient(api_key="wk_test")


METHODS = {
    "get_current_user", "get_organization", "list_jobs", "get_job",
    "list_candidates", "get_candidate", "create_candidate", "update_candidate",
    "create_comment", "list_emails", "list_moves",
}


def test_surface(client):
    public = {n for n in dir(WttjAtsClient)
              if not n.startswith("_") and callable(getattr(WttjAtsClient, n))}
    assert public == METHODS


def test_sans_jeton_refuse():
    with pytest.raises(MissingCredential, match="WTTJ_API_KEY"):
        WttjAtsClient(api_key="")


def test_jeton_en_bearer_jamais_en_query(calls, client):
    client.get_current_user(organizations=True)
    c = calls[0]
    assert (c["method"], c["url"]) == ("GET", f"{BASE_URL}/users/current")
    assert c["headers"]["Authorization"] == "Bearer wk_test"
    assert "access_token" not in c["params"]
    assert c["params"] == {"organizations": "true"}


def test_liste_des_offres_nettoie_les_none_et_ecrit_les_booleens(calls, client):
    client.list_jobs("org-ref", status="published", stages=False, page=2)
    c = calls[0]
    assert c["url"] == f"{BASE_URL}/jobs"
    assert c["params"] == {"organization_reference": "org-ref", "status": "published",
                           "stages": "false", "page": 2}
    assert c["json"] is None


def test_offre_avec_ses_etapes(calls, client):
    client.get_job("JOB_1", stages=True)
    assert calls[0]["url"] == f"{BASE_URL}/jobs/JOB_1"
    assert calls[0]["params"] == {"stages": "true"}


def test_reference_echappee_dans_le_chemin(calls, client):
    client.get_candidate("a/b c")
    assert calls[0]["url"] == f"{BASE_URL}/candidates/a%2Fb%20c"


@pytest.mark.parametrize("ref", ["", "  ", ".", ".."])
def test_reference_vide_ou_relative_refusee(calls, client, ref):
    with pytest.raises(ValueError):
        client.get_job(ref)
    assert calls == []


def test_candidats_d_une_offre(calls, client):
    client.list_candidates("JOB_1", job_stage_id=12, archived=False)
    c = calls[0]
    assert c["url"] == f"{BASE_URL}/candidates"
    assert c["params"] == {"job_reference": "JOB_1", "job_stage_id": 12,
                           "archived": "false"}


def test_creation_d_un_candidat(calls, client):
    client.create_candidate("org-ref", "JOB_1", 12, "a@b.c", "Ada", "Lovelace",
                            phone="0600000000", subtitle=None)
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{BASE_URL}/candidates")
    assert c["json"] == {"organization_reference": "org-ref", "job_reference": "JOB_1",
                         "job_stage_id": 12, "email": "a@b.c", "firstname": "Ada",
                         "lastname": "Lovelace", "phone": "0600000000"}


def test_deplacer_un_candidat_est_un_put_du_stage(calls, client):
    client.update_candidate("CAND_1", job_stage_id=34)
    c = calls[0]
    assert (c["method"], c["url"]) == ("PUT", f"{BASE_URL}/candidates/CAND_1")
    assert c["json"] == {"job_stage_id": 34}


def test_mise_a_jour_vide_refusee(calls, client):
    with pytest.raises(ValueError, match="nothing to update"):
        client.update_candidate("CAND_1")
    assert calls == []


def test_commentaire(calls, client):
    calls.responses.append(_Resp({"id": 1, "content": "ok", "raw_content": "ok"}))
    out = client.create_comment("CAND_1", "ok")
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{BASE_URL}/comments")
    assert c["json"] == {"candidate_reference": "CAND_1", "content": "ok"}
    assert out["id"] == 1


def test_commentaire_vide_refuse(calls, client):
    with pytest.raises(ValueError):
        client.create_comment("CAND_1", "   ")
    assert calls == []


def test_mouvements(calls, client):
    client.list_moves("org-ref", job_reference="JOB_1")
    assert calls[0]["url"] == f"{BASE_URL}/moves"
    assert calls[0]["params"] == {"organization_reference": "org-ref",
                                  "job_reference": "JOB_1"}


def test_emails_d_un_candidat_en_lecture(calls, client):
    client.list_emails("CAND_1", per_page=5)
    c = calls[0]
    assert (c["method"], c["url"]) == ("GET", f"{BASE_URL}/emails")
    assert c["params"] == {"candidate_reference": "CAND_1", "per_page": 5}


def test_l_historique_exige_le_job(client):
    with pytest.raises(TypeError):
        client.list_moves("org-ref")


def test_refus_amont_type(calls, client):
    calls.responses.append(_Resp({"error": "invalid_scope",
                                  "error_description": "missing jobs_r"}, 403))
    with pytest.raises(UpstreamHTTPError) as e:
        client.list_jobs("org-ref")
    assert e.value.status_code == 403
