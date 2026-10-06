"""Contrat du connecteur Instagram (statistiques, Instagram Login).

Aucun réseau : une doublure de session capture ce qu'on appelle et rend ce qu'on
lui dit. Ce qui est vérifié ici est ce qui échoue silencieusement autrement :

- le **choix des métriques par type de média** — une métrique de fil demandée sur
  un reel fait échouer l'appel entier, et le message parle alors de la métrique ;
- la **politique de renouvellement** — ce jeton ne se renouvelle que tant qu'il
  vit, donc un seuil trop serré ne rate pas un appel : il perd la connexion ;
- la **traduction des refus** — « ton autorisation est morte » et « l'appel a
  raté » appellent deux gestes opposés, et Meta les rend tous les deux en 400 ;
- le fait qu'aucun **secret** ne parte dans le renouvellement, et qu'aucune URL
  ni corps brut ne ressorte dans un message d'erreur.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from oto.tools.instagram_meta import (
    InstagramApp,
    InstagramAuthExpired,
    InstagramAuthRefused,
    InstagramClient,
    compute_best_hours,
)
from oto.tools.instagram_meta import client as ig_client
from oto.tools.instagram_meta import config as ig_config
from oto.tools.instagram_meta import oauth as ig_oauth
from oto.tools.instagram_meta import tokens as ig_tokens
from oto.tools.instagram_meta.errors import InstagramApiError

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
APP = InstagramApp(app_id="app-de-test", app_secret="secret-de-test")


# ── Doublures ────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("pas du JSON")
        return self._payload


class _Session:
    """Session `requests` factice : file de réponses, journal des appels."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.calls: list[dict] = []

    def _suivante(self, methode, url, params=None, data=None, timeout=None):
        self.calls.append({"method": methode, "url": url,
                           "params": dict(params or {}), "data": dict(data or {}),
                           "timeout": timeout})
        if not self.reponses:
            raise AssertionError(f"appel non prévu : {methode} {url}")
        suite = self.reponses.pop(0)
        return suite(self.calls[-1]) if callable(suite) else suite

    def get(self, url, **kw):
        return self._suivante("GET", url, **kw)

    def post(self, url, **kw):
        return self._suivante("POST", url, **kw)


# ── Aplatissement des insights ───────────────────────────────────────────────

def test_flatten_lit_les_deux_formes_de_reponse():
    payload = {"data": [
        {"name": "reach", "total_value": {"value": 1234}},
        {"name": "views", "values": [{"value": 42, "end_time": "2026-09-09T07:00:00+0000"}]},
        {"name": "shares", "values": []},
        {"no_name": 1},
    ]}
    assert ig_client.flatten_insights(payload) == {
        "reach": 1234, "views": 42, "shares": 0}


def test_les_metriques_de_compte_retirees_ne_sont_pas_demandees():
    """Demander l'une des trois fait échouer l'appel ENTIER, pas la métrique."""
    for morte in ("profile_views", "website_clicks", "impressions"):
        assert morte not in ig_client.ACCOUNT_INSIGHT_METRICS
    assert "profile_links_taps" in ig_client.ACCOUNT_INSIGHT_METRICS


# ── Client de données ────────────────────────────────────────────────────────

def test_le_client_exige_jeton_et_compte():
    with pytest.raises(ValueError):
        InstagramClient("", "17841400000000000")
    with pytest.raises(ValueError):
        InstagramClient("tok", "")


def test_insights_de_compte_refuse_une_fenetre_trop_large():
    c = InstagramClient("tok", "1", session=_Session())
    with pytest.raises(ValueError, match="30"):
        c.get_account_insights(days=90)


def test_les_metriques_suivent_le_type_de_media():
    s = _Session(
        _Resp({"media_type": "VIDEO", "media_product_type": "REELS"}),
        _Resp({"data": [{"name": "reach", "values": [{"value": 7}]}]}),
    )
    c = InstagramClient("tok", "1", session=s)
    assert c.get_media_insights("999") == {"reach": 7}
    demandees = s.calls[-1]["params"]["metric"]
    assert "ig_reels_avg_watch_time" in demandees
    assert "profile_visits" not in demandees      # métrique de fil, pas de reel


def test_un_type_de_media_inconnu_est_nomme():
    s = _Session(_Resp({"media_type": "IMAGE", "media_product_type": "AUTRE_CHOSE"}))
    c = InstagramClient("tok", "1", session=s)
    with pytest.raises(ValueError, match="AUTRE_CHOSE"):
        c.get_media_insights("999")


def test_un_jeton_rejete_est_renouvele_puis_rejoue_une_fois():
    vus: list[str] = []

    def reponse(call):
        vus.append(call["params"]["access_token"])
        if call["params"]["access_token"] == "vieux":
            return _Resp({"error": {"message": "Session has expired",
                                    "type": "OAuthException", "code": 190}}, 400)
        return _Resp({"username": "compte-de-test"})

    s = _Session(reponse, reponse)
    c = InstagramClient("vieux", "1", renew=lambda: "neuf", session=s)
    assert c.get_profile() == {"username": "compte-de-test"}
    assert vus == ["vieux", "neuf"]


def test_un_second_refus_remonte_sans_boucler():
    s = _Session(*[_Resp({"error": {"message": "nope", "code": 190}}, 401)] * 2)
    c = InstagramClient("vieux", "1", renew=lambda: "neuf", session=s)
    with pytest.raises(InstagramAuthExpired):
        c.get_profile()
    assert len(s.calls) == 2      # un seul rattrapage, pas une boucle


def test_sans_renew_le_rejet_remonte_tel_quel():
    s = _Session(_Resp({"error": {"message": "nope", "code": 190}}, 400))
    with pytest.raises(InstagramAuthExpired):
        InstagramClient("vieux", "1", session=s).get_profile()


def test_une_panne_ne_se_confond_pas_avec_une_autorisation_morte():
    s = _Session(_Resp({"error": {"message": "please reduce the amount of data"}}, 500))
    with pytest.raises(InstagramApiError) as e:
        InstagramClient("tok", "1", session=s).get_profile()
    assert not isinstance(e.value, InstagramAuthExpired)


def test_un_message_d_erreur_ne_porte_ni_url_ni_corps_brut():
    """Le jeton voyage en paramètre d'URL : ni l'URL ni le corps ne doivent sortir."""
    s = _Session(_Resp({"error": {"message": "boom", "code": 1},
                        "echo": "access_token=tok-secret"}, 500))
    with pytest.raises(InstagramApiError) as e:
        InstagramClient("tok-secret", "1", session=s).get_profile()
    assert "tok-secret" not in str(e.value)
    assert "graph.instagram.com" not in str(e.value)


# ── Acquisition ──────────────────────────────────────────────────────────────

def test_url_d_autorisation():
    url = urlparse(ig_oauth.authorize_url(APP, "https://exemple.test/retour", "sig"))
    assert (url.netloc, url.path) == ("www.instagram.com", "/oauth/authorize")
    q = parse_qs(url.query)
    assert q["client_id"] == ["app-de-test"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://exemple.test/retour"]
    assert q["state"] == ["sig"]
    # séparées par des VIRGULES : des espaces donnent un consentement partiel
    assert q["scope"] == [",".join(ig_config.SCOPES)]
    assert "instagram_business_manage_insights" in q["scope"][0]


def test_url_d_autorisation_exige_retour_et_state():
    for retour, state in (("", "sig"), ("https://exemple.test/retour", "")):
        with pytest.raises(ValueError):
            ig_oauth.authorize_url(APP, retour, state)


def test_une_application_sans_coordonnees_leve_a_la_construction():
    with pytest.raises(ValueError, match="app_secret"):
        InstagramApp(app_id="x", app_secret="  ")


def test_reponse_d_echange_plate_et_enveloppee():
    assert ig_oauth.parse_token_exchange(
        {"access_token": "court", "user_id": 17841400000000000}) == (
        "court", "17841400000000000")
    assert ig_oauth.parse_token_exchange(
        {"data": [{"access_token": "court", "user_id": "123"}]}) == ("court", "123")


@pytest.mark.parametrize("payload", [
    {}, {"data": []}, {"access_token": "seul"}, {"user_id": "123"},
    {"data": [{"access_token": "t"}]}, "pas un objet",
])
def test_reponse_d_echange_incomplete_refusee(payload):
    with pytest.raises(InstagramAuthRefused):
        ig_oauth.parse_token_exchange(payload)


def test_connect_enchaine_les_trois_etapes():
    s = _Session(
        _Resp({"access_token": "court", "user_id": "17841400000000000"}),
        _Resp({"access_token": "long", "expires_in": 5_184_000}),
        _Resp({"user_id": "17841400000000000", "username": "compte-de-test"}),
    )
    grant = ig_oauth.connect(APP, "le-code", "https://exemple.test/retour", session=s)
    assert (grant.access_token, grant.user_id) == ("long", "17841400000000000")
    assert grant.username == "compte-de-test"
    assert grant.expires_in == 5_184_000
    # le code et le secret partent en CORPS, jamais en query string
    assert s.calls[0]["method"] == "POST"
    assert s.calls[0]["params"] == {}
    assert s.calls[0]["data"]["client_secret"] == "secret-de-test"


def test_connect_refuse_nomme_ce_que_meta_a_dit():
    s = _Session(_Resp({"error_type": "OAuthException", "code": 400,
                        "error_message": "Invalid platform app"}, 400))
    with pytest.raises(InstagramAuthRefused, match="Invalid platform app"):
        ig_oauth.connect(APP, "le-code", "https://exemple.test/retour", session=s)


def test_connect_refuse_un_compte_sans_identifiant_professionnel():
    s = _Session(
        _Resp({"access_token": "court", "user_id": "1"}),
        _Resp({"access_token": "long", "expires_in": 100}),
        _Resp({"username": "compte-de-test"}),
    )
    with pytest.raises(InstagramAuthRefused, match="account identifier"):
        ig_oauth.connect(APP, "le-code", "https://exemple.test/retour", session=s)


# ── Renouvellement ───────────────────────────────────────────────────────────

def _dans(jours: float) -> str:
    return ig_tokens.iso(NOW + timedelta(days=jours))


def test_pas_de_renouvellement_sur_un_jeton_neuf():
    assert ig_tokens.needs_refresh(_dans(59), _dans(-1), now=NOW) is False


def test_renouvellement_bien_avant_l_echeance():
    """53 jours restants sur 60 : on renouvelle dès ~une semaine d'âge."""
    seuil = ig_config.RENEW_WHEN_REMAINING_DAYS
    assert ig_tokens.needs_refresh(_dans(seuil + 1), _dans(-8), now=NOW) is False
    assert ig_tokens.needs_refresh(_dans(seuil - 1), _dans(-8), now=NOW) is True


def test_pas_de_renouvellement_avant_24h_meta_le_refuserait():
    assert ig_tokens.needs_refresh(_dans(1), _dans(-0.5), now=NOW) is False
    assert ig_tokens.needs_refresh(_dans(1), _dans(-2), now=NOW) is True


def test_un_jeton_mort_ne_se_renouvelle_pas_il_se_reconsent():
    assert ig_tokens.is_expired(_dans(-1), now=NOW) is True
    assert ig_tokens.needs_refresh(_dans(-1), now=NOW) is False


def test_echeance_deduite_de_la_date_de_pose_a_defaut():
    ttl, marge = ig_config.LONG_LIVED_TTL_DAYS, ig_config.RENEW_WHEN_REMAINING_DAYS
    pose = _dans(-(ttl - marge - 1))
    assert ig_tokens.needs_refresh(None, pose, now=NOW) is False
    assert ig_tokens.needs_refresh(None, _dans(-(ttl - marge + 1)), now=NOW) is True


def test_sans_aucun_horodatage_on_ne_devine_pas():
    assert ig_tokens.needs_refresh(None, None, now=NOW) is False
    assert ig_tokens.is_expired(None, None, now=NOW) is False


def test_lecture_d_horodatage_tolerante():
    assert ig_tokens.parse_ts("2026-09-09T12:00:00Z") == NOW
    assert ig_tokens.parse_ts("2026-09-09T12:00:00") == NOW
    assert ig_tokens.parse_ts("pas une date") is None
    assert ig_tokens.parse_ts(None) is None


def test_le_renouvellement_ne_prend_pas_le_secret_de_l_application():
    s = _Session(_Resp({"access_token": "neuf", "expires_in": 5_184_000}))
    out = ig_tokens.refresh_long_lived("vieux", session=s)
    assert out == {"access_token": "neuf", "expires_in": 5_184_000}
    params = s.calls[0]["params"]
    assert params["grant_type"] == "ig_refresh_token"
    assert params["access_token"] == "vieux"
    assert "client_secret" not in params


def test_un_renouvellement_refuse_dit_que_l_autorisation_est_morte():
    s = _Session(_Resp({"error": {"message": "expired", "code": 190}}, 400))
    with pytest.raises(InstagramAuthExpired) as e:
        ig_tokens.refresh_long_lived("vieux", expires_at=_dans(-1), session=s)
    assert e.value.expires_at == _dans(-1)      # la DATE voyage avec le refus


def test_renouveler_sans_jeton_est_un_refus_pas_un_appel():
    s = _Session()
    with pytest.raises(InstagramAuthExpired):
        ig_tokens.refresh_long_lived("", session=s)
    assert s.calls == []


# ── Meilleures heures ────────────────────────────────────────────────────────

def test_meilleures_heures_classe_par_engagement_moyen():
    media = [
        {"timestamp": "2026-09-07T18:00:00+0000", "like_count": 100, "comments_count": 20},
        {"timestamp": "2026-09-08T18:30:00+0000", "like_count": 80, "comments_count": 0},
        {"timestamp": "2026-09-09T09:00:00+0000", "like_count": 5, "comments_count": 1},
    ]
    out = compute_best_hours(media)
    assert out["sample_size"] == 3
    assert out["by_hour"][0]["hour"] == 18
    assert out["by_hour"][0]["posts"] == 2
    assert out["by_hour"][0]["avg_engagement"] == 100
    assert out["by_weekday"][0]["weekday"] == "lundi"      # 2026-09-07


def test_meilleures_heures_sans_media_et_sans_horodatage():
    assert compute_best_hours([]) == {"by_hour": [], "by_weekday": [], "sample_size": 0}
    out = compute_best_hours([{"like_count": 3}, {"timestamp": "pas une date"}])
    assert out["by_hour"] == [] and out["sample_size"] == 2


def test_aucune_coordonnee_d_application_dans_le_paquet():
    """Le dépôt est public : ni App ID, ni secret, ni valeur par défaut.

    Le test lit les constantes du module plutôt que le fichier : c'est ce que les
    appelants voient, et c'est là qu'une valeur « juste pour dépanner » atterrit."""
    for nom, valeur in vars(ig_config).items():
        if nom.startswith("_") or not isinstance(valeur, str):
            continue
        assert not valeur.isdigit(), f"{nom} ressemble à un identifiant d'application"
    with pytest.raises(TypeError):
        InstagramApp()      # aucun défaut : l'appelant DOIT les fournir
    assert json.dumps(list(ig_config.SCOPES))      # sérialisable, donc pas d'objet caché
