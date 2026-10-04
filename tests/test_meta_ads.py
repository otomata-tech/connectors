"""Contrat du connecteur Meta Ads (Marketing API, lecture seule).

Aucun réseau : une doublure de session capture ce qu'on appelle. Vérifié ici ce
qui échoue en silence autrement — le dialogue Facebook Login for Business
(`config_id`, pas `scope`), le préfixe `act_`, la pagination, la traduction des
refus (jeton mort / débit / trop de données), et qu'aucun secret ni URL ne
ressorte dans un message.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from oto.tools.meta_ads import (
    MetaAdsApiError,
    MetaAdsApp,
    MetaAdsAuthExpired,
    MetaAdsAuthRefused,
    MetaAdsClient,
    MetaAdsThrottled,
    ad_account_id,
    authorize_url,
    objet_id,
    rapport_id,
    connect,
)
from oto.tools.meta_ads import config as cfg

APP = MetaAdsApp(app_id="app-test", app_secret="secret-test", config_id="cfg-test")


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not JSON")
        return self._payload


class _Session:
    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.calls: list[dict] = []

    def _next(self, method, url, params=None, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params or {},
                           "data": data or {}, "headers": headers or {},
                           "timeout": timeout})
        return self.reponses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._next("GET", url, params=params, headers=headers, timeout=timeout)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._next("POST", url, data=data, headers=headers, timeout=timeout)


def _err(code, message="boom", subcode=None, status=400):
    bloc = {"message": message, "type": "OAuthException", "code": code}
    if subcode:
        bloc["error_subcode"] = subcode
    return _Resp({"error": bloc}, status)


# ── app & dialogue ───────────────────────────────────────────────────────────

def test_app_refuses_empty_fields():
    with pytest.raises(ValueError, match="config_id"):
        MetaAdsApp(app_id="a", app_secret="s", config_id=" ")


def test_authorize_url_uses_config_id_not_scope():
    url = authorize_url(APP, "https://oto.test/cb", "st")
    q = parse_qs(urlparse(url).query)
    assert url.startswith(cfg.DIALOG_URL)
    assert q["config_id"] == ["cfg-test"]
    assert q["response_type"] == ["code"]
    assert q["override_default_response_type"] == ["true"]
    assert "scope" not in q
    assert "secret-test" not in url


@pytest.mark.parametrize("redirect,state", [("", "st"), ("https://x", "")])
def test_authorize_url_requires_redirect_and_state(redirect, state):
    with pytest.raises(ValueError):
        authorize_url(APP, redirect, state)


# ── échange du code ──────────────────────────────────────────────────────────

def test_connect_bisu_token_without_expiry():
    s = _Session(_Resp({"access_token": "tok", "token_type": "bearer"}),
                 _Resp({"id": "su1", "name": "Acme SU", "client_business_id": "biz9"}))
    g = connect(APP, "code1", "https://oto.test/cb", session=s)
    assert (g.access_token, g.expires_in, g.client_business_id) == ("tok", None, "biz9")
    echange = s.calls[0]
    assert echange["method"] == "POST" and echange["params"] == {}
    assert echange["data"]["code"] == "code1"
    assert echange["data"]["client_secret"] == "secret-test"
    assert s.calls[1]["headers"] == {"Authorization": "Bearer tok"}


def test_connect_user_token_falls_back_without_client_business_id():
    s = _Session(_Resp({"access_token": "tok", "expires_in": 5_184_000}),
                 _err(100, "nonexisting field client_business_id"),
                 _Resp({"id": "u1", "name": "Jane"}))
    g = connect(APP, "c", "https://oto.test/cb", session=s)
    assert (g.expires_in, g.user_id, g.client_business_id) == (5_184_000, "u1", "")
    assert s.calls[2]["params"]["fields"] == "id,name"


def test_connect_refusal_hides_secret_and_url():
    s = _Session(_err(100, "Invalid verification code format.", status=400))
    with pytest.raises(MetaAdsAuthRefused) as e:
        connect(APP, "c", "https://oto.test/cb", session=s)
    assert "secret-test" not in str(e.value)
    assert "graph.facebook.com" not in str(e.value)
    assert "Invalid verification code" in str(e.value)


# ── client ───────────────────────────────────────────────────────────────────

def test_ad_account_id_prefix():
    assert ad_account_id("123") == "act_123"
    assert ad_account_id("act_123") == "act_123"
    with pytest.raises(ValueError):
        ad_account_id("")


_INJECTIONS = ["../../me/accounts", "123/business_users", "123?role=ADMIN",
               "123/../456", "act_12/x", "12 3", "act_", "abc"]


@pytest.mark.parametrize("valeur", _INJECTIONS)
def test_ids_que_graph_ne_doit_jamais_voir(valeur):
    """Un id va tel quel dans le chemin Graph : `/`, `?` ou `..` y viseraient un
    autre nœud — avec `business_management`, une écriture sur le portefeuille."""
    with pytest.raises(ValueError):
        ad_account_id(valeur)
    with pytest.raises(ValueError):
        objet_id(valeur)
    with pytest.raises(ValueError):
        rapport_id(valeur)


@pytest.mark.parametrize("geste", [
    lambda c: c.get_object("../../me/accounts"),
    lambda c: c.get_insights("123/business_users"),
    lambda c: c.start_insights_report("123/business_users?email=x&role=ADMIN&x="),
    lambda c: c.get_report_status("1/../me"),
    lambda c: c.get_report_insights("act_1"),
    lambda c: c.list_objects("1/../2", "campaign"),
])
def test_aucun_appel_ne_part_avec_un_id_injecte(geste):
    s = _Session()
    with pytest.raises(ValueError):
        geste(MetaAdsClient("tok", session=s))
    assert s.calls == []


def test_ids_valides():
    assert objet_id("120200000001") == "120200000001"
    assert objet_id("act_1") == "act_1"
    assert rapport_id(" 777 ") == "777"


def test_list_ad_accounts_pagination_and_bisu_fallback():
    s = _Session(_err(100, "Tried accessing nonexisting field (adaccounts)"),
                 _Resp({"data": [{"id": "act_1"}],
                        "paging": {"cursors": {"after": "A"}, "next": "https://n"}}))
    res = MetaAdsClient("tok", session=s).list_ad_accounts(limit=10)
    assert res == {"data": [{"id": "act_1"}], "next_cursor": "A"}
    assert s.calls[1]["url"].endswith("/me/assigned_ad_accounts")


def test_no_next_cursor_without_next_page():
    s = _Session(_Resp({"data": [], "paging": {"cursors": {"after": "A"}}}))
    assert MetaAdsClient("tok", session=s).list_ad_accounts()["next_cursor"] is None


def test_list_objects_level_edge_and_json_params():
    s = _Session(_Resp({"data": [{"id": "c1"}]}))
    MetaAdsClient("tok", session=s).list_objects(
        "42", "campaign", effective_status=["ACTIVE"])
    call = s.calls[0]
    assert call["url"].endswith("/act_42/campaigns")
    assert json.loads(call["params"]["effective_status"]) == ["ACTIVE"]
    assert call["params"]["fields"] == cfg.DEFAULT_FIELDS["campaign"]
    assert "after" not in call["params"]


def test_list_objects_rejects_unknown_level():
    with pytest.raises(ValueError, match="level"):
        MetaAdsClient("tok", session=_Session()).list_objects("1", "creative")


def test_insights_params():
    s = _Session(_Resp({"data": [{"spend": "12.3"}]}))
    MetaAdsClient("tok", session=s).get_insights(
        "act_1", level="campaign", fields=["spend", "clicks"],
        time_range={"since": "2026-09-01", "until": "2026-09-30"},
        breakdowns=["age", "gender"])
    p = s.calls[0]["params"]
    assert p["fields"] == "spend,clicks"
    assert json.loads(p["time_range"]) == {"since": "2026-09-01", "until": "2026-09-30"}
    assert p["breakdowns"] == "age,gender"
    assert "date_preset" not in p


def test_insights_rejects_preset_and_range():
    with pytest.raises(ValueError, match="OR"):
        MetaAdsClient("tok", session=_Session()).get_insights(
            "act_1", date_preset="last_7d", time_range={"since": "a", "until": "b"})


def test_async_report_is_a_post():
    s = _Session(_Resp({"report_run_id": "777"}))
    run = MetaAdsClient("tok", session=s).start_insights_report("act_1",
                                                                date_preset="last_30d")
    assert run == "777"
    assert s.calls[0]["method"] == "POST"
    assert s.calls[0]["data"]["date_preset"] == "last_30d"
    # Même fenêtre que le GET, posée explicitement (défauts différents chez Meta).
    assert json.loads(s.calls[0]["data"]["action_attribution_windows"]) == [
        "7d_click", "1d_view"]


def test_token_never_in_url_or_body():
    """Le jeton part en en-tête : ni `params` (URL) ni `data`."""
    s = _Session(_Resp({"data": []}), _Resp({"report_run_id": "1"}))
    c = MetaAdsClient("tok", session=s)
    c.list_objects("1", "ad")
    c.start_insights_report("act_1")
    for call in s.calls:
        assert "tok" not in str(call["params"]) and "tok" not in str(call["data"])
        assert call["headers"] == {"Authorization": "Bearer tok"}


# ── traduction des refus ─────────────────────────────────────────────────────

def test_dead_token_is_auth_expired():
    s = _Session(_err(190, "Error validating access token"))
    with pytest.raises(MetaAdsAuthExpired):
        MetaAdsClient("tok", session=s).get_object("1")


@pytest.mark.parametrize("code", [4, 17, 613, 80004])
def test_rate_limits_are_throttled(code):
    s = _Session(_err(code, "User request limit reached"))
    with pytest.raises(MetaAdsThrottled) as e:
        MetaAdsClient("tok", session=s).get_object("1")
    assert e.value.code == code


def test_too_much_data_names_the_fix():
    s = _Session(_err(100, "Please reduce the amount of data", subcode=1487534))
    with pytest.raises(MetaAdsApiError, match="async report") as e:
        MetaAdsClient("tok", session=s).get_insights("act_1")
    assert not isinstance(e.value, MetaAdsThrottled)


def test_error_never_carries_token_or_url():
    s = _Session(_Resp("<html>tok https://graph.facebook.com/?access_token=tok</html>",
                       500))
    with pytest.raises(MetaAdsApiError) as e:
        MetaAdsClient("tok", session=s).get_object("1")
    assert "access_token" not in str(e.value) and "graph" not in str(e.value)
