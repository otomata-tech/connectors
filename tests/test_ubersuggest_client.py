"""UbersuggestClient + auth Ubersuggest — verrouille le contrat construit par la lib.

Mocke `requests.post` (serveur d'autorisation) et `requests.Session.post` (serveur
MCP), sans réseau ni credential réel. Cible ce qui pourrait dériver en silence : les
jetons dans le corps et jamais dans l'URL, PKCE et `resource` à chaque étape, le
classement « autorisation morte » contre « refus », la poignée de main MCP
(initialize → notification → appel, id de session renvoyé), la lecture d'un flux
d'événements, la reprise unique sur session expirée et le refus d'un outil.
"""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.ubersuggest import (UbersuggestAuthError, UbersuggestClient,
                                   UbersuggestGrantExpired, UbersuggestToolError)
from oto.tools.ubersuggest import auth as ub_auth
from oto.tools.ubersuggest import client as ub_client

MCP = "https://ubersuggest-mcp.neilpatelapi.com/mcp"
REDIRECT = "https://oto.example/api/ubersuggest/oauth/callback"


class _Seen(list):
    """The captured calls, plus queued replies served in order."""


class _Resp:
    def __init__(self, payload=None, status_code=200, headers=None, text=None, lines=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = text if text is not None else ("" if payload is None else json.dumps(payload))
        self._lines = lines  # an iterator: a stream the server may keep open
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        return self._lines if self._lines is not None else iter(self.text.splitlines())

    def close(self):
        self.closed = True

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def token_calls(monkeypatch):
    seen = _Seen()
    seen_responses = []

    def fake_post(url, **kw):
        seen.append({"url": url, **kw})
        return seen_responses.pop(0) if seen_responses else _Resp(
            {"access_token": "AT", "refresh_token": "RT2", "expires_in": 3600,
             "scope": "keywords domain"})

    monkeypatch.setattr(ub_auth.requests, "post", fake_post)
    seen.responses = seen_responses
    return seen


# --- auth -------------------------------------------------------------------

def test_authorize_url_porte_pkce_resource_et_scopes():
    url = ub_auth.authorize_url("cid", REDIRECT, "st", "chal")
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == ub_auth.AUTHORIZE_URL
    assert q["client_id"] == ["cid"] and q["redirect_uri"] == [REDIRECT]
    assert q["code_challenge"] == ["chal"] and q["code_challenge_method"] == ["S256"]
    assert q["resource"] == [MCP] and q["state"] == ["st"]
    assert set(q["scope"][0].split()) == set(ub_auth.SCOPES)


def test_authorize_url_sans_client_id_refuse():
    with pytest.raises(MissingCredential):
        ub_auth.authorize_url("", REDIRECT, "st", "chal")


def test_exchange_code_jetons_dans_le_corps(token_calls):
    grant = ub_auth.exchange_code("cid", "the-code", REDIRECT, "verif")
    call = token_calls[0]
    assert call["url"] == ub_auth.TOKEN_URL and "params" not in call
    assert call["data"] == {"grant_type": "authorization_code", "client_id": "cid",
                            "code": "the-code", "redirect_uri": REDIRECT,
                            "code_verifier": "verif", "resource": MCP}
    assert (grant.access_token, grant.refresh_token, grant.expires_in) == ("AT", "RT2", 3600)


def test_refresh_garde_l_ancien_refresh_token_si_non_tourne(token_calls):
    token_calls.responses.append(_Resp({"access_token": "AT3", "expires_in": 60}))
    grant = ub_auth.refresh("cid", "RT1")
    assert token_calls[0]["data"]["refresh_token"] == "RT1"
    assert token_calls[0]["data"]["resource"] == MCP
    assert grant.refresh_token == "RT1" and grant.access_token == "AT3"


def test_refresh_invalid_grant_est_une_autorisation_morte(token_calls):
    token_calls.responses.append(_Resp({"error": "invalid_grant"}, status_code=400))
    with pytest.raises(UbersuggestGrantExpired) as e:
        ub_auth.refresh("cid", "RT1")
    assert e.value.status_code == 400


def test_autre_refus_n_est_pas_une_autorisation_morte(token_calls):
    token_calls.responses.append(_Resp({"error": "invalid_client"}, status_code=401))
    with pytest.raises(UbersuggestAuthError) as e:
        ub_auth.refresh("cid", "RT1")
    assert not isinstance(e.value, UbersuggestGrantExpired)


def test_register_client_rend_le_client_id(token_calls):
    token_calls.responses.append(_Resp({"client_id": "got-it"}, status_code=201))
    assert ub_auth.register_client([REDIRECT], client_name="oto") == "got-it"
    body = token_calls[0]["json"]
    assert body["redirect_uris"] == [REDIRECT]
    assert body["token_endpoint_auth_method"] == "none"


def test_register_client_refus_nomme(token_calls):
    token_calls.responses.append(_Resp({"error": "invalid_redirect_uri"}, status_code=400))
    with pytest.raises(UbersuggestAuthError, match="invalid_redirect_uri"):
        ub_auth.register_client([REDIRECT], client_name="oto")


# --- MCP transport ----------------------------------------------------------

@pytest.fixture
def mcp(monkeypatch):
    """Serves `initialize`, the notification, then `script` for every other call."""
    seen = _Seen()
    script = []

    def fake_post(self, url, json=None, headers=None, timeout=None, stream=False):
        seen.append({"url": url, "body": json, "headers": dict(headers or {}),
                     "session_headers": dict(self.headers), "stream": stream})
        method = json.get("method")
        if method == "initialize":
            return _Resp({"jsonrpc": "2.0", "id": json["id"],
                          "result": {"protocolVersion": "2025-06-18"}},
                         headers={"Content-Type": "application/json", "Mcp-Session-Id": "S1"})
        if method == "notifications/initialized":
            return _Resp(None, status_code=202, text="")
        reply = script.pop(0)
        return reply(json) if callable(reply) else reply

    monkeypatch.setattr(ub_client.requests.Session, "post", fake_post)
    seen.script = script
    return seen


def _ok(result):
    return lambda body: _Resp({"jsonrpc": "2.0", "id": body["id"], "result": result})


def test_appel_fait_la_poignee_de_main_puis_renvoie_la_session(mcp):
    mcp.script.append(_ok({"content": [{"type": "text", "text": '{"search_volume": 880}'}]}))
    out = UbersuggestClient("AT").call("keyword_overview", {"keyword": "crm", "locId": None})
    assert out == {"search_volume": 880}
    assert [c["body"].get("method") for c in mcp] == [
        "initialize", "notifications/initialized", "tools/call"]
    call = mcp[2]
    assert call["url"] == MCP
    assert call["body"]["params"] == {"name": "keyword_overview",
                                      "arguments": {"keyword": "crm"}}
    assert call["headers"]["Mcp-Session-Id"] == "S1"
    assert call["headers"]["MCP-Protocol-Version"] == ub_client.PROTOCOL_VERSION
    assert call["session_headers"]["Authorization"] == "Bearer AT"


def test_flux_d_evenements_et_contenu_structure(mcp):
    def sse(body):
        msg = {"jsonrpc": "2.0", "id": body["id"],
               "result": {"structuredContent": {"rows": [1, 2]}, "content": []}}
        text = ('event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                f"event: message\ndata: {json.dumps(msg)}\n\n")
        return _Resp(None, headers={"Content-Type": "text/event-stream"}, text=text)
    mcp.script.append(sse)
    assert UbersuggestClient("AT").call("domain_keywords", {"domain": "x.com"}) == {"rows": [1, 2]}


def test_texte_non_json_rendu_tel_quel(mcp):
    mcp.script.append(_ok({"content": [{"type": "text", "text": "Logged in as a@b.c / Tier: free"}]}))
    out = UbersuggestClient("AT").call("auth_status")
    assert out == "Logged in as a@b.c / Tier: free"
    # typed: the consumer knows it holds third-party prose, not data
    assert isinstance(out, ub_client.UbersuggestText)


def test_is_error_est_un_refus_de_l_outil(mcp):
    mcp.script.append(_ok({"isError": True,
                           "content": [{"type": "text", "text": "Plan limit reached"}]}))
    with pytest.raises(UbersuggestToolError) as e:
        UbersuggestClient("AT").call("backlinks", {"domain": "x.com"})
    assert e.value.status_code == 422 and "Plan limit reached" in str(e.value)


def test_session_expiree_reprise_une_fois(mcp):
    mcp.script.append(_Resp({"error": "session not found"}, status_code=404))
    mcp.script.append(_ok({"content": [{"type": "text", "text": "[]"}]}))
    assert UbersuggestClient("AT").call("keyword_lists") == []
    assert [c["body"].get("method") for c in mcp] == [
        "initialize", "notifications/initialized", "tools/call",
        "initialize", "notifications/initialized", "tools/call"]


def test_jeton_refuse_remonte_en_401(mcp, monkeypatch):
    def fake_post(self, url, json=None, headers=None, timeout=None, stream=False):
        return _Resp({"error": "invalid_token"}, status_code=401)
    monkeypatch.setattr(ub_client.requests.Session, "post", fake_post)
    with pytest.raises(UpstreamHTTPError) as e:
        UbersuggestClient("AT").call("auth_status")
    assert e.value.status_code == 401


def test_erreur_json_rpc_de_requete_est_un_400(mcp):
    mcp.script.append(lambda body: _Resp({"jsonrpc": "2.0", "id": body["id"],
                                          "error": {"code": -32602, "message": "bad locId"}}))
    with pytest.raises(UpstreamHTTPError) as e:
        UbersuggestClient("AT").call("keyword_overview", {"keyword": "crm"})
    assert e.value.status_code == 400 and "bad locId" in str(e.value)


def test_erreur_json_rpc_serveur_est_un_502(mcp):
    mcp.script.append(lambda body: _Resp({"jsonrpc": "2.0", "id": body["id"],
                                          "error": {"code": -32603, "message": "boom"}}))
    with pytest.raises(UpstreamHTTPError) as e:
        UbersuggestClient("AT").call("keyword_overview", {"keyword": "crm"})
    assert e.value.status_code == 502


def test_outil_inconnu_refuse_sans_requete(mcp):
    with pytest.raises(ValueError, match="unknown Ubersuggest tool"):
        UbersuggestClient("AT").call("drop_database")
    assert mcp == []


def test_list_tools_suit_le_curseur(mcp):
    mcp.script.append(_ok({"tools": [{"name": "a"}], "nextCursor": "c2"}))
    mcp.script.append(_ok({"tools": [{"name": "b"}]}))
    assert [t["name"] for t in UbersuggestClient("AT").list_tools()] == ["a", "b"]
    assert mcp[-1]["body"]["params"] == {"cursor": "c2"}


def test_sans_jeton_refuse():
    with pytest.raises(MissingCredential):
        UbersuggestClient("")


def _flux(rid, *, avant=(), apres_infini=False):
    """An event stream: `avant` lines, our answer, then (optionally) pings forever."""
    def gen():
        yield from avant
        msg = {"jsonrpc": "2.0", "id": rid, "result": {"structuredContent": {"ok": 1}}}
        yield f"data: {json.dumps(msg)}"
        yield ""
        while apres_infini:
            yield ": ping"
    return gen()


def test_flux_garde_ouvert_lu_jusqu_a_notre_reponse_puis_lache(mcp):
    """Un serveur qui garde le flux ouvert (pings) ne tient pas l'appel : la lecture
    s'arrête au message qui répond à notre id, et le flux est fermé."""
    resps = []

    def sse(body):
        r = _Resp(None, headers={"Content-Type": "text/event-stream"},
                  lines=_flux(body["id"], avant=(": ping", "data: {\"jsonrpc\":\"2.0\","
                                                 "\"method\":\"notifications/progress\"}", ""),
                              apres_infini=True))
        resps.append(r)
        return r
    mcp.script.append(sse)
    assert UbersuggestClient("AT").call("domain_keywords", {"domain": "x.com"}) == {"ok": 1}
    assert mcp[-1]["stream"] is True and resps[0].closed


def test_echeance_depassee_leve_une_erreur_nommee(mcp, monkeypatch):
    """Des pings sans fin et jamais notre réponse : l'échéance globale coupe l'appel,
    même si chaque lecture rend à temps."""
    def pings(body):
        def gen():
            while True:
                yield ": ping"
        return _Resp(None, headers={"Content-Type": "text/event-stream"}, lines=gen())
    mcp.script.append(pings)
    horloge = iter(range(0, 10_000, 30))
    monkeypatch.setattr(ub_client.time, "monotonic", lambda: next(horloge))
    with pytest.raises(ub_client.UbersuggestDeadlineExceeded) as e:
        UbersuggestClient("AT").call("site_audit", {"domain": "x.com"})
    assert e.value.status_code == 504


def test_session_reprise_sans_poignee_de_main(mcp):
    """Un client construit avec une session déjà ouverte n'en rouvre pas."""
    mcp.script.append(_ok({"content": [{"type": "text", "text": "[]"}]}))
    c = UbersuggestClient("AT", session_id="S0")
    assert c.call("keyword_lists") == []
    assert [x["body"].get("method") for x in mcp] == ["tools/call"]
    assert mcp[0]["headers"]["Mcp-Session-Id"] == "S0" and c.session_id == "S0"
