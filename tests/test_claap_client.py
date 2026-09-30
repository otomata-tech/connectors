"""Contrat du client Claap (v1, `X-Claap-Key`, enregistrements de réunion).

Mocke `requests.Session.request` (et `requests.put` pour le transcript) : verbes
et chemins relevés dans l'OpenAPI éditeur, en-tête d'auth, bornes, énumérations,
re-tentatives en lecture seule — et les pièges propres à Claap, chacun verrouillé
par un test qui échouerait si la correction était retirée :

- `labels`/`sources` joints par virgule (pas de paramètre répété) ;
- `labels` sans `channel_id` refusé ;
- transcript `format="text"` rendu en `str` (réponse `text/plain`) ;
- transcript d'écriture `{start, end, speakerId}` ≠ forme de lecture ;
- échec du PUT de transcript : l'id de l'enregistrement déjà créé est rendu.
"""
from __future__ import annotations

import pytest

from oto.tools.claap import client as cl
from oto.tools.common.errors import UpstreamHTTPError


class _Resp:
    def __init__(self, status_code: int = 200, body=None, text=None):
        self.status_code = status_code
        self._body = body if body is not None else {"result": {}}
        self.content = b"x"
        self.text = text if text is not None else str(self._body)
        self.headers = {}

    def json(self):
        if self._body == "__not_json__":
            raise ValueError("not json")
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs,
                    headers=dict(self.headers))
        seen["calls"].append((method, url))
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200)

    monkeypatch.setattr(cl.requests.Session, "request", fake_request)
    monkeypatch.setattr(cl.time, "sleep", lambda _s: None)
    return seen


@pytest.fixture()
def cli():
    return cl.ClaapClient(api_key="cla_test")


BASE = "https://api.claap.io/v1"


# --- auth, sonde ------------------------------------------------------------

def test_auth_header_is_x_claap_key(capture, cli):
    cli.probe()
    assert capture["headers"]["X-Claap-Key"] == "cla_test"
    assert "Authorization" not in capture["headers"]
    assert capture["calls"] == [("GET", f"{BASE}/workspaces/mine")]


def test_missing_key_is_named_never_read_from_env(monkeypatch):
    """The library never reads secrets: an absent key is a named refusal."""
    from oto.tools.common.credentials import MissingCredential
    monkeypatch.setenv("CLAAP_API_KEY", "cla_env")
    with pytest.raises(MissingCredential, match="CLAAP_API_KEY"):
        cl.ClaapClient()


# --- list_recordings --------------------------------------------------------

def test_list_recordings_maps_params_to_camel_case(capture, cli):
    cli.list_recordings(cursor="c1", limit=50, sort="created_asc",
                        recorder_email="a@b.co", recorder_id="u1",
                        created_after="2026-01-01", created_before="2026-02-01",
                        view_id="v1")
    assert capture["calls"] == [("GET", f"{BASE}/recordings")]
    assert capture["kwargs"]["params"] == {
        "cursor": "c1", "limit": 50, "sort": "created_asc",
        "recorderEmail": "a@b.co", "recorderId": "u1",
        "createdAfter": "2026-01-01", "createdBefore": "2026-02-01",
        "viewId": "v1"}


def test_list_recordings_drops_none(capture, cli):
    cli.list_recordings()
    assert capture["kwargs"]["params"] is None


def test_labels_and_sources_are_comma_joined_not_repeated(capture, cli):
    """Claap lit `labels=a,b` — une liste passée brute à requests partirait en
    `labels=a&labels=b`, que l'amont ne lit pas comme deux labels."""
    cli.list_recordings(channel_id="ch", labels=["demo", "won"],
                        sources=["Zoom", "GoogleMeet"])
    params = capture["kwargs"]["params"]
    assert params["labels"] == "demo,won"
    assert params["sources"] == "Zoom,GoogleMeet"
    assert params["channelId"] == "ch"


def test_labels_without_channel_refused_before_round_trip(capture, cli):
    with pytest.raises(ValueError, match="channel_id"):
        cli.list_recordings(labels=["demo"])
    assert capture["calls"] == []


def test_label_containing_comma_refused(capture, cli):
    with pytest.raises(ValueError, match="comma"):
        cli.list_recordings(channel_id="ch", labels=["a,b"])
    assert capture["calls"] == []


def test_unknown_source_refused_with_valid_values(capture, cli):
    with pytest.raises(ValueError, match="GoogleMeet"):
        cli.list_recordings(sources=["Meet"])


@pytest.mark.parametrize("bad", [0, 101, "10", True])
def test_limit_bounds(capture, cli, bad):
    with pytest.raises(ValueError):
        cli.list_recordings(limit=bad)
    assert capture["calls"] == []


def test_unknown_sort_refused(cli, capture):
    with pytest.raises(ValueError, match="created_desc"):
        cli.list_recordings(sort="newest")


# --- get / transcript / delete / vues ---------------------------------------

def test_get_recording_path(capture, cli):
    cli.get_recording("rec_1")
    assert capture["calls"] == [("GET", f"{BASE}/recordings/rec_1")]


def test_transcript_text_returns_str(capture, cli):
    """`format=text` revient en text/plain : `.json()` y lèverait."""
    capture["responses"].append(
        _Resp(200, body="__not_json__", text="02:17 speaker_1: Hello there!"))
    out = cli.get_recording_transcript("rec_1", format="text", lang="fr")
    assert out == "02:17 speaker_1: Hello there!"
    assert capture["url"] == f"{BASE}/recordings/rec_1/transcript"
    assert capture["kwargs"]["params"] == {"format": "text", "lang": "fr"}


def test_transcript_json_returns_parsed(capture, cli):
    capture["responses"].append(_Resp(200, {"result": {"transcript": {"segments": []}}}))
    assert cli.get_recording_transcript("rec_1") == {
        "result": {"transcript": {"segments": []}}}


def test_transcript_unknown_format_refused(cli, capture):
    with pytest.raises(ValueError):
        cli.get_recording_transcript("rec_1", format="srt")


def test_delete_recording(capture, cli):
    cli.delete_recording("rec_1")
    assert capture["calls"] == [("DELETE", f"{BASE}/recordings/rec_1")]


def test_views_paths(capture, cli):
    cli.list_recording_views()
    cli.get_recording_view("v1")
    assert capture["calls"] == [("GET", f"{BASE}/recordings/views"),
                                ("GET", f"{BASE}/recordings/views/v1")]


# --- erreurs, re-tentatives -------------------------------------------------

def test_error_envelope_surfaces_as_upstream_error(capture, cli):
    capture["responses"].append(_Resp(400, {"error": {
        "type": "validation_error", "message": "bad", "path": "limit"}}))
    with pytest.raises(UpstreamHTTPError) as ei:
        cli.get_recording("x")
    assert ei.value.status_code == 400
    assert ei.value.body["error"]["path"] == "limit"


def test_get_retries_429(capture, cli):
    capture["responses"] += [_Resp(429), _Resp(200, {"result": {"ok": 1}})]
    assert cli.get_recording("x") == {"result": {"ok": 1}}
    assert len(capture["calls"]) == 2


def test_writes_never_retried(capture, cli):
    """Aucune clé d'idempotence : rejouer un POST créerait un doublon."""
    capture["responses"] += [_Resp(429), _Resp(200)]
    with pytest.raises(UpstreamHTTPError):
        cli.create_recording("a@b.co", video_url="https://x/v.mp4")
    assert len(capture["calls"]) == 1


def test_delete_never_retried(capture, cli):
    capture["responses"] += [_Resp(503), _Resp(200)]
    with pytest.raises(UpstreamHTTPError):
        cli.delete_recording("x")
    assert len(capture["calls"]) == 1


# --- create_recording -------------------------------------------------------

SEGMENTS = [{"speakerId": "1", "start": 0.5, "end": 1.5, "text": "Bonjour"}]


def test_create_download_mode_body(capture, cli):
    cli.create_recording("a@b.co", title="Démo", video_url="https://x/v.mp4",
                         source="Zoom", channel_id="ch")
    assert capture["calls"] == [("POST", f"{BASE}/recordings")]
    assert capture["kwargs"]["json"] == {
        "authorEmail": "a@b.co", "title": "Démo", "channelId": "ch",
        "source": "Zoom",
        "video": {"type": "download", "url": "https://x/v.mp4"}}


def test_create_never_sends_deprecated_download_url(capture, cli):
    cli.create_recording("a@b.co", video_url="https://x/v.mp4")
    assert "downloadUrl" not in capture["kwargs"]["json"]


def test_create_requires_media_or_transcript(capture, cli):
    with pytest.raises(ValueError, match="video_url"):
        cli.create_recording("a@b.co")
    assert capture["calls"] == []


def test_create_source_api_refused(cli, capture):
    """`Api` se lit en liste mais ne se déclare pas à la création."""
    with pytest.raises(ValueError):
        cli.create_recording("a@b.co", video_url="u", source="Api")


def test_create_deal_requires_meeting(cli, capture):
    with pytest.raises(ValueError, match="meeting"):
        cli.create_recording("a@b.co", video_url="u",
                             deal={"type": "hubspot", "id": "1"})


def test_create_transcript_only_puts_to_meta_url(capture, cli, monkeypatch):
    capture["responses"].append(_Resp(200, {"result": {"recording": {
        "id": "rec_9", "state": "Empty",
        "upload": {"metaUrl": "https://signed.example/meta"}}}}))
    puts = []

    def fake_put(url, json=None, headers=None, timeout=None):
        puts.append((url, json, headers))
        return _Resp(200)

    monkeypatch.setattr(cl.requests, "put", fake_put)
    out = cli.create_recording("a@b.co", transcript={"segments": SEGMENTS,
                                                     "langIso2": "fr"})
    body = capture["kwargs"]["json"]
    assert body["video"] == {"type": "none"}
    assert body["transcript"] == {"type": "upload"}
    assert puts == [("https://signed.example/meta",
                     {"transcript": {"segments": SEGMENTS, "langIso2": "fr"}},
                     {"Content-Type": "application/json"})]
    # la clé Claap ne part PAS vers l'URL présignée
    assert "X-Claap-Key" not in puts[0][2]
    assert out["result"]["recording"]["id"] == "rec_9"


def test_transcript_put_failure_carries_recording_id(capture, cli, monkeypatch):
    capture["responses"].append(_Resp(200, {"result": {"recording": {
        "id": "rec_9", "upload": {"metaUrl": "https://signed.example/meta"}}}}))
    monkeypatch.setattr(cl.requests, "put",
                        lambda *a, **k: _Resp(403, text="SignatureDoesNotMatch"))
    with pytest.raises(UpstreamHTTPError) as ei:
        cli.create_recording("a@b.co", transcript={"segments": SEGMENTS})
    assert ei.value.body["recording_id"] == "rec_9"


def test_missing_meta_url_carries_recording_id(capture, cli):
    capture["responses"].append(_Resp(200, {"result": {"recording": {"id": "rec_9"}}}))
    with pytest.raises(UpstreamHTTPError) as ei:
        cli.create_recording("a@b.co", transcript={"segments": SEGMENTS})
    assert ei.value.body["recording_id"] == "rec_9"


def test_read_shape_transcript_refused(cli, capture):
    """Le transcript LU (startedAt/endedAt/speaker) n'est pas celui qu'on ÉCRIT."""
    with pytest.raises(ValueError, match="READ shape"):
        cli.create_recording("a@b.co", transcript={"segments": [
            {"startedAt": 0, "endedAt": 1, "speaker": "A", "text": "x"}]})
    assert capture["calls"] == []


def test_wrapped_transcript_refused(cli, capture):
    with pytest.raises(ValueError, match="wrapping"):
        cli.create_recording("a@b.co", transcript={"transcript": {"segments": SEGMENTS}})


def test_transcript_segment_missing_field(cli, capture):
    with pytest.raises(ValueError, match="speakerId"):
        cli.create_recording("a@b.co", transcript={"segments": [
            {"start": 0, "end": 1, "text": "x"}]})


# --- ids dans le chemin, URL présignée -----------------------------------------

@pytest.mark.parametrize("call", [
    lambda c, v: c.get_recording(v),
    lambda c, v: c.get_recording_transcript(v),
    lambda c, v: c.delete_recording(v),
    lambda c, v: c.get_recording_view(v),
])
def test_id_with_slash_cannot_reach_another_endpoint(capture, cli, call):
    """`../users/x` avec une clé d'espace qui supprime : le `/` est échappé."""
    call(cli, "../users/x")
    method, url = capture["calls"][-1]
    path = url[len(BASE):]
    assert "/users/" not in path and "%2F" in path


@pytest.mark.parametrize("bad", ["..", ".", "", "  ", None])
def test_dot_or_empty_id_refused_before_round_trip(capture, cli, bad):
    with pytest.raises(ValueError):
        cli.delete_recording(bad)
    assert capture["calls"] == []


@pytest.mark.parametrize("meta", ["http://signed.example/meta",
                                  "https://127.0.0.1/meta",
                                  "https://[::1]/meta",
                                  "https://localhost/meta",
                                  "https://metadata.internal/x"])
def test_meta_url_must_be_public_https(capture, cli, monkeypatch, meta):
    capture["responses"].append(_Resp(200, {"result": {"recording": {
        "id": "rec_9", "upload": {"metaUrl": meta}}}}))
    puts = []
    monkeypatch.setattr(cl.requests, "put", lambda *a, **k: puts.append(a))
    with pytest.raises(UpstreamHTTPError) as ei:
        cli.create_recording("a@b.co", transcript={"segments": SEGMENTS})
    assert puts == []
    assert ei.value.body["recording_id"] == "rec_9"
