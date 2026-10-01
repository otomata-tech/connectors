"""ThreeCXClient — the two grants (API client, user account), secrets in the
body, process-wide token cache, refusals typed 401, renewal on 401, call-log
paging and recorded-only filter, recording download.

Transport stubbed (`requests.post` for the token, `Session.get` for the API):
no network, no real credential.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.threecx import ThreeCXAuthError, ThreeCXClient
from oto.tools.threecx import auth as threecx_auth

BASE = "https://pbx.example.test"


class _Resp:
    def __init__(self, payload=None, status_code=200, content=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content if content is not None else (
            json.dumps(payload).encode() if payload is not None else b"")
        self.headers = headers or {"Content-Type": "application/json"}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


@pytest.fixture(autouse=True)
def _cache_vide():
    threecx_auth._TOKEN_CACHE.clear()
    yield
    threecx_auth._TOKEN_CACHE.clear()


@pytest.fixture
def token(monkeypatch):
    """Token endpoints stub: records each call; queue responses in `replies`."""
    calls, replies = [], []

    def fake_post(url, data=None, json=None, params=None, timeout=None, **kw):
        calls.append({"url": url, "data": data, "json": json, "params": params})
        if replies:
            return replies.pop(0)
        if url.endswith("/connect/token"):
            return _Resp({"access_token": f"tok-{len(calls)}", "expires_in": 3600})
        return _Resp({"Status": "AuthSuccess", "TwoFactorAuth": None,
                      "Token": {"access_token": f"tok-{len(calls)}", "expires_in": 3600}})

    monkeypatch.setattr(threecx_auth.requests, "post", fake_post)
    return calls, replies


@pytest.fixture
def api(monkeypatch):
    """API stub on `Session.get`; queue responses in `replies`."""
    calls, replies = [], []

    def fake_get(self, url, params=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "params": params, "headers": headers})
        return replies.pop(0) if replies else _Resp({"value": []})

    monkeypatch.setattr("requests.Session.get", fake_get)
    return calls, replies


def _user():
    return ThreeCXClient(BASE, username="u@example.test", password="pw")


# --- construction -------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://pbx.example.test", "pbx.example.test", "https://pbx.example.test/webclient", ""])
def test_base_url_invalide_refusee(url):
    with pytest.raises(ValueError, match="base_url"):
        ThreeCXClient(url, username="u", password="p")


def test_base_url_normalisee():
    assert ThreeCXClient(BASE + "/", username="u", password="p").base_url == BASE


def test_credential_incomplet_refuse(monkeypatch):
    monkeypatch.setattr("oto.tools.threecx.client.get_secret", lambda name, default=None: None)
    with pytest.raises(ValueError, match="client_id"):
        ThreeCXClient(BASE, username="u")


# --- auth ---------------------------------------------------------------------

def test_compte_utilisateur_mot_de_passe_dans_le_corps(token, api):
    calls, _ = token
    _user().list_calls("2026-09-01", "2026-09-02")
    assert calls[0]["url"] == f"{BASE}/webclient/api/Login/GetAccessToken"
    assert calls[0]["json"] == {"Username": "u@example.test", "Password": "pw", "SecurityCode": ""}
    assert calls[0]["params"] is None
    assert api[0][0]["headers"]["Authorization"] == "Bearer tok-1"


def test_client_api_client_credentials_dans_le_corps(token, api):
    calls, _ = token
    ThreeCXClient(BASE, client_id="cid", client_secret="sec").list_calls("2026-09-01", "2026-09-02")
    assert calls[0]["url"] == f"{BASE}/connect/token"
    assert calls[0]["data"] == {"grant_type": "client_credentials",
                                "client_id": "cid", "client_secret": "sec"}
    assert calls[0]["params"] is None


def test_jeton_en_cache_entre_clients(token, api):
    calls, _ = token
    _user().list_calls("2026-09-01", "2026-09-02")
    _user().list_calls("2026-09-01", "2026-09-02")
    assert len(calls) == 1


@pytest.mark.parametrize("reply", [
    _Resp({"Status": "AuthFailed", "Token": None, "TwoFactorAuth": None}),
    _Resp({"Status": "AuthFailed"}, status_code=401),
])
def test_compte_refuse_type_401(token, api, reply):
    token[1].append(reply)
    with pytest.raises(ThreeCXAuthError) as exc:
        _user().list_calls("2026-09-01", "2026-09-02")
    assert exc.value.status_code == 401
    assert "pw" not in str(exc.value)


def test_double_authentification_dite(token, api):
    token[1].append(_Resp({"Status": "AuthFailed", "TwoFactorAuth": {"Type": "Email"}}))
    with pytest.raises(ThreeCXAuthError, match="two-factor"):
        _user().list_calls("2026-09-01", "2026-09-02")


def test_client_api_refuse_type_401(token, api):
    token[1].append(_Resp({"error": "invalid_client"}, status_code=400))
    with pytest.raises(ThreeCXAuthError) as exc:
        ThreeCXClient(BASE, client_id="cid", client_secret="sec").list_calls(
            "2026-09-01", "2026-09-02")
    assert exc.value.status_code == 401
    assert "sec" not in str(exc.value)


def test_jeton_renouvele_une_fois_sur_401(token, api):
    api[1].extend([_Resp({"error": "expired"}, status_code=401), _Resp({"value": []})])
    _user().list_calls("2026-09-01", "2026-09-02")
    assert len(token[0]) == 2
    assert api[0][1]["headers"]["Authorization"] == "Bearer tok-2"


# --- call log -----------------------------------------------------------------

def test_journal_chemin_et_pagination(token, api):
    calls, replies = api
    replies.append(_Resp({"value": [{"SegmentId": i} for i in range(3)]}))
    out = _user().list_calls("2026-09-01", "2026-09-02T12:00:00+02:00", top=3, skip=6)
    url = calls[0]["url"]
    assert url.startswith(f"{BASE}/xapi/v1/ReportCallLogData/Pbx.GetCallLogData(")
    assert "periodFrom=2026-09-01T00:00:00Z" in url
    assert "periodTo=2026-09-02T12:00:00+02:00" in url
    assert calls[0]["params"] == {"$top": 3, "$skip": 6}
    assert out["next_skip"] == 9


def test_journal_derniere_page(token, api):
    api[1].append(_Resp({"value": [{"SegmentId": 1}]}))
    assert _user().list_calls("2026-09-01", "2026-09-02", top=3)["next_skip"] is None


def test_journal_enregistres_seuls_filtre_apres_pagination(token, api):
    api[1].append(_Resp({"value": [{"SrcRecId": 7}, {"SrcRecId": None}, {"DstRecId": 9}]}))
    out = _user().list_calls("2026-09-01", "2026-09-02", top=3, recorded_only=True)
    assert out == {"calls": [{"SrcRecId": 7}, {"DstRecId": 9}], "next_skip": 3}


@pytest.mark.parametrize("kw", [
    {"date_from": "01/09/2026"}, {"date_from": "2026-09-01T10:00"}, {"top": 0}, {"top": 501},
    {"skip": -1}])
def test_journal_arguments_invalides(token, api, kw):
    args = {"date_from": "2026-09-01", "date_to": "2026-09-02", **kw}
    with pytest.raises(ValueError):
        _user().list_calls(**args)
    assert api[0] == []


def test_journal_erreur_amont(token, api):
    api[1].append(_Resp({"error": "forbidden"}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        _user().list_calls("2026-09-01", "2026-09-02")
    assert exc.value.status_code == 403


# --- recordings ---------------------------------------------------------------

def test_telechargement_enregistrement(token, api):
    calls, replies = api
    replies.append(_Resp(content=b"RIFF....WAVE", headers={
        "Content-Type": "audio/x-wav",
        "Content-Disposition": 'attachment; filename="call.wav"'}))
    out = _user().download_recording(42)
    assert calls[0]["url"] == f"{BASE}/xapi/v1/Recordings/Pbx.DownloadRecording(recId=42)"
    assert out == {"content": b"RIFF....WAVE", "content_type": "audio/x-wav",
                   "filename": "call.wav"}


def test_telechargement_nom_par_defaut(token, api):
    api[1].append(_Resp(content=b"RIFF", headers={"Content-Type": "audio/x-wav"}))
    assert _user().download_recording(42)["filename"] == "recording-42.wav"


def test_enregistrement_introuvable(token, api):
    api[1].append(_Resp({"error": {"code": "400", "message": "WARNINGS.XAPI.FILE_NOT_FOUND"}},
                        status_code=400))
    with pytest.raises(UpstreamHTTPError):
        _user().download_recording(1)
