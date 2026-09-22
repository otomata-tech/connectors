"""MistralClient.transcribe (Voxtral) — verrouille le contrat HTTP de
`POST /v1/audio/transcriptions` (multipart : champs de formulaire + fichier), les
contraintes de l'amont (la diarisation exige l'horodatage par segment), la
normalisation du vocabulaire en termes-clés et celle de la réponse.

Transport simulé (`requests.post` remplacé) : aucun réseau, aucune clé réelle.
"""
import json

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.mistral import client as mistral_client
from oto.tools.mistral import MistralClient, context_bias_terms, normalize_transcription
from oto.tools.mistral.transcription import (
    DEFAULT_TRANSCRIPTION_MODEL,
    MAX_CONTEXT_BIAS_TERMS,
)

REPONSE = {
    "model": "voxtral-mini-2602",
    "text": "Bonjour. On regarde la toiture.",
    "language": "fr",
    "segments": [
        {"type": "transcription_segment", "text": " Bonjour. ", "start": 0.0,
         "end": 1.2, "speaker_id": "speaker_0"},
        {"type": "transcription_segment", "text": "On regarde la toiture.",
         "start": 1.4, "end": 3.0, "speaker_id": "speaker_1"},
        {"type": "transcription_segment", "text": "   ", "start": 3.0, "end": 3.1},
    ],
    "usage": {"prompt_audio_seconds": 3, "prompt_tokens": 10, "total_tokens": 20,
              "completion_tokens": 10},
}


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Appels(list):
    """Les appels capturés, et la réponse que le faux transport rendra."""
    reponse = None


@pytest.fixture
def calls(monkeypatch):
    captured = _Appels()
    captured.reponse = _Resp(REPONSE)

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        captured.append({"url": url, "headers": headers, "data": data,
                         "files": files, "timeout": timeout})
        return captured.reponse

    monkeypatch.setattr(mistral_client.requests, "post", fake_post)
    return captured


@pytest.fixture
def c():
    return MistralClient(api_key="test-key")


def _champs(call) -> dict:
    out: dict = {}
    for k, v in call["data"]:
        out.setdefault(k, []).append(v)
    return out


# --- contrat HTTP -------------------------------------------------------------

def test_multipart_vers_l_endpoint_de_transcription(calls, c):
    c.transcribe(b"ID3audio", "visite.m4a", mime="audio/mp4")
    call = calls[0]
    assert call["url"] == "https://api.mistral.ai/v1/audio/transcriptions"
    assert call["headers"] == {"Authorization": "Bearer test-key"}
    assert call["files"] == {"file": ("visite.m4a", b"ID3audio", "audio/mp4")}
    assert _champs(call) == {"model": [DEFAULT_TRANSCRIPTION_MODEL]}


def test_la_cle_ne_part_jamais_dans_le_formulaire(calls, c):
    c.transcribe(b"x", "a.mp3", language="fr", diarize=True, context_bias=["Placo"])
    assert all("test-key" not in str(v) for _, v in calls[0]["data"])


def test_langue_diarisation_et_vocabulaire_partent_ensemble(calls, c):
    """La combinaison du banc (28 min, API réelle) : langue + diarisation +
    horodatage par segment, acceptée ensemble par Voxtral Mini Transcribe 2."""
    c.transcribe(b"x", "a.mp3", language="fr", diarize=True,
                 context_bias=["Placo", "pare-vapeur"])
    assert _champs(calls[0]) == {
        "model": [DEFAULT_TRANSCRIPTION_MODEL], "language": ["fr"],
        "timestamp_granularities": ["segment"], "diarize": ["true"],
        "context_bias": ["Placo,pare-vapeur"]}


def test_la_diarisation_envoie_toujours_l_horodatage_par_segment(calls, c):
    """Sans lui, l'amont refuse en 422 (« When diarize is set to True and streaming
    is disabled, the timestamp granularity must be set to ['segment'] »)."""
    c.transcribe(b"x", "a.mp3", diarize=True)
    assert _champs(calls[0])["timestamp_granularities"] == ["segment"]


def test_horodatage_sans_diarisation(calls, c):
    c.transcribe(b"x", "a.mp3", language="fr", timestamps=True)
    champs = _champs(calls[0])
    assert champs["timestamp_granularities"] == ["segment"]
    assert champs["language"] == ["fr"] and "diarize" not in champs


def test_un_delai_borne_toujours_l_attente(calls, c):
    c.transcribe(b"x", "a.mp3")
    connexion, lecture = calls[0]["timeout"]
    assert connexion and lecture
    c.transcribe(b"x", "a.mp3", timeout=(5, 42))
    assert calls[1]["timeout"] == (5, 42)


def test_modele_surchargeable(calls, c):
    c.transcribe(b"x", "a.mp3", model="voxtral-mini-latest")
    assert _champs(calls[0])["model"] == ["voxtral-mini-latest"]


def test_un_refus_de_l_amont_porte_son_statut(calls, c):
    calls.reponse = _Resp({"message": "Unauthorized"}, status_code=401)
    with pytest.raises(UpstreamHTTPError) as e:
        c.transcribe(b"x", "a.mp3")
    assert e.value.status_code == 401


# --- réponse ------------------------------------------------------------------

def test_reponse_normalisee(calls, c):
    out = c.transcribe(b"x", "a.mp3", diarize=True, context_bias="Placo, de")
    assert out["duration_s"] == 3.0
    assert out["language"] == "fr"
    assert out["segments"] == [
        {"start": 0.0, "end": 1.2, "text": "Bonjour.", "speaker": "speaker_0"},
        {"start": 1.4, "end": 3.0, "text": "On regarde la toiture.",
         "speaker": "speaker_1"},
    ]
    assert out["context_bias"] == ["Placo"]
    assert out["context_bias_dropped"] == ["de"]


def test_reponse_sans_horodatage_ni_locuteur():
    out = normalize_transcription({"text": "t", "segments": [
        {"text": "t", "start": None, "end": None}]})
    assert out["segments"] == [{"start": None, "end": None, "text": "t", "speaker": None}]
    assert out["duration_s"] is None


# --- vocabulaire → termes-clés --------------------------------------------------

def test_un_terme_a_espace_est_decoupe_et_ses_fragments_courts_ecartes():
    gardes, ecartes = context_bias_terms(["pompe à chaleur", "Leroy Merlin"])
    assert gardes == ["pompe", "chaleur", "Leroy", "Merlin"]
    assert ecartes == ["à"]


def test_chaine_libre_virgules_et_retours_a_la_ligne():
    gardes, _ = context_bias_terms("BA13, Placo\nfermette ,  ")
    assert gardes == ["BA13", "Placo", "fermette"]


def test_dedoublonne_sans_la_casse_premiere_graphie_gagne():
    gardes, _ = context_bias_terms(["Placo", "placo", "PLACO"])
    assert gardes == ["Placo"]


def test_au_dela_du_plafond_rien_n_est_perdu_en_silence():
    vocab = [f"terme{i:03d}" for i in range(MAX_CONTEXT_BIAS_TERMS + 5)]
    gardes, ecartes = context_bias_terms(vocab)
    assert len(gardes) == MAX_CONTEXT_BIAS_TERMS
    assert ecartes == vocab[MAX_CONTEXT_BIAS_TERMS:]
    assert all(" " not in t and "," not in t for t in gardes)


def test_vocabulaire_absent():
    assert context_bias_terms(None) == ([], [])
    assert context_bias_terms([]) == ([], [])


# --- sonde ----------------------------------------------------------------------

def test_list_models_est_borne_et_authentifie(monkeypatch, c):
    vu = {}

    def fake_get(url, headers=None, timeout=None):
        vu.update(url=url, headers=headers, timeout=timeout)
        return _Resp({"data": []})

    monkeypatch.setattr(mistral_client.requests, "get", fake_get)
    assert c.list_models() == {"data": []}
    assert vu["url"] == "https://api.mistral.ai/v1/models"
    assert vu["headers"] == {"Authorization": "Bearer test-key"}
    assert vu["timeout"]
