"""Contrat du client Jev (System One / Decisions, Bearer OpenRouter).

Mocke `requests.Session.post` : vérifie l'URL, le corps envoyé, le modèle par
défaut, le typage des erreurs amont — et la garde de grille, qui refuse une question
sans `criteria` avant tout appel.
"""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.jev import client as jv

NOUL = {"type": "noul", "instructions": "La condition tient-elle ?",
        "criteria": {"true": "oui", "false": "non"}}


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.content = b"x"
        self.text = str(body)
        self.headers = {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {}

    def fake_post(self, url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        return _Resp(200, {"answers": {"q": {"type": "noul", "noul": 0.9}},
                           "model": jv.DEFAULT_MODEL,
                           "usage": {"input_tokens": 341, "cost": 1.4322e-05}})

    monkeypatch.setattr(jv.requests.Session, "post", fake_post)
    return seen


def _client(**kw):
    return jv.JevClient(api_key="sk-or-test", **kw)


def test_auth_header_is_bearer():
    assert _client().session.headers["Authorization"] == "Bearer sk-or-test"


def test_decide_poste_sur_systemone_avec_le_snapshot_date(capture):
    _client().decide({"a": "b"}, {"q": NOUL})
    assert capture["url"] == "https://openrouter.ai/api/v1/systemone"
    corps = capture["kwargs"]["json"]
    assert corps == {"model": jv.DEFAULT_MODEL, "state": {"a": "b"}, "questions": {"q": NOUL}}
    # Le snapshot DATÉ, pas l'id nu : un seuil calibré ne doit pas glisser de version.
    assert jv.DEFAULT_MODEL == "typesafe/jev-1.13-20260917"


def test_base_url_surchargeable_pour_une_bascule_en_direct(capture):
    _client(base_url="https://api.typesafe.ai", path="/v1/systemone").decide({"a": "b"}, {"q": NOUL})
    assert capture["url"] == "https://api.typesafe.ai/v1/systemone"


def test_modele_explicite_l_emporte(capture):
    _client().decide({"a": "b"}, {"q": NOUL}, model="~typesafe/jev-latest")
    assert capture["kwargs"]["json"]["model"] == "~typesafe/jev-latest"


def test_erreur_amont_typee(monkeypatch):
    monkeypatch.setattr(jv.requests.Session, "post",
                        lambda self, url, **kw: _Resp(400, {"error": {"message": "nope"}}))
    with pytest.raises(UpstreamHTTPError) as e:
        _client().decide({"a": "b"}, {"q": NOUL})
    assert e.value.status_code == 400 and e.value.is_client_error


@pytest.mark.parametrize("questions, attendu", [
    ({}, "au moins une question"),
    ({"q": {"type": "bool", "instructions": "?", "criteria": {"true": "x", "false": "y"}}}, "inconnu"),
    ({"q": {"type": "noul", "instructions": "", "criteria": {"true": "x", "false": "y"}}}, "instructions"),
    # ⚠️ Le cas qui motive la garde : une question sans `criteria`.
    ({"q": {"type": "noul", "instructions": "?"}}, "criteria"),
    ({"q": {"type": "choice", "instructions": "?", "criteria": {"une": "seule"}}}, "criteria"),
    ({"q": {"type": "score", "instructions": "?", "criteria": {"pas": "une liste"}}}, "LISTE"),
])
def test_grille_mal_formee_refusee_avant_l_appel(questions, attendu):
    with pytest.raises(ValueError, match=attendu):
        _client().decide({"a": "b"}, questions)


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5.5", "openai/gpt-6-sol",
                                   "z-ai/glm-5.3-flashx", "mistral/mistral-large-2512"])
def test_un_modele_d_un_autre_editeur_est_refuse_ici(model, capture):
    """La garde rend la promesse du connecteur vraie de NOTRE côté, et économise un
    aller-retour facturé pour une faute de frappe."""
    with pytest.raises(ValueError, match="modèles de décision"):
        _client().decide({"a": "b"}, {"q": NOUL}, model=model)
    assert not capture


def test_la_garde_porte_sur_l_editeur_pas_sur_le_catalogue(capture):
    """Un modèle du même éditeur passe la garde, quel qu'il soit : trier ici par
    modèle demanderait de tenir à jour une liste qui vieillirait en silence."""
    _client().decide({"a": "b"}, {"q": NOUL}, model="typesafe/jev-router")
    assert capture["kwargs"]["json"]["model"] == "typesafe/jev-router"


def test_l_alias_typesafe_passe(capture):
    _client().decide({"a": "b"}, {"q": NOUL}, model="~typesafe/jev-latest")
    assert capture["kwargs"]["json"]["model"] == "~typesafe/jev-latest"


def test_grille_valide_passe(capture):
    r = _client().decide({"a": "b"}, {
        "n": NOUL,
        "c": {"type": "choice", "instructions": "?", "criteria": {"a": "x", "b": "y"}},
        "s": {"type": "score", "instructions": "?", "criteria": ["bas", "moyen", "haut"]}})
    assert r["usage"]["cost"] == 1.4322e-05
