"""Transcription audio Mistral (Voxtral) — la FORME des échanges, sans transport.

Le transport vit dans `MistralClient.transcribe` ; ici, ce que l'amont attend et ce
qu'il rend, en fonctions pures :

- `context_bias_terms` — le vocabulaire passé en termes-clés. L'amont prend une liste
  de TERMES SANS ESPACE, jointe par des virgules, au plus 100. Un terme à espace est
  découpé en ses mots, et les fragments de moins de 3 caractères (articles,
  prépositions) sont écartés : un mot isolé garde l'orthographe qu'on veut imposer,
  une préposition isolée n'impose rien. Ce qui est écarté est rendu, jamais avalé.
- `normalize_transcription` — la réponse ramenée à une forme stable : texte, langue,
  durée facturée, segments `{start, end, text, speaker}`.

Contraintes de l'amont tenues par le client : la diarisation exige l'horodatage par
segment (`timestamp_granularities=segment`, sinon 422) ; jusqu'à 3 h d'audio par
requête. La langue se combine avec les deux — mesuré sur Voxtral Mini Transcribe 2,
malgré une incompatibilité annoncée par la doc de l'amont.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Voxtral Mini Transcribe 2, version datée : une version épinglée ne change pas de
# comportement sous l'appelant. Surchargeable par appel (`model=`).
DEFAULT_TRANSCRIPTION_MODEL = "voxtral-mini-2602"

MAX_CONTEXT_BIAS_TERMS = 100
MIN_TERM_LENGTH = 3

_SEPARATEURS = re.compile(r"[,\s]+")


def context_bias_terms(vocabulary: Iterable[str] | str | None) -> tuple[list[str], list[str]]:
    """Vocabulaire → `(termes envoyés, fragments écartés)`.

    Accepte une liste de termes ou une chaîne (séparateurs : virgule, espace, retour
    à la ligne). Découpe chaque terme en mots, écarte ceux de moins de
    `MIN_TERM_LENGTH` caractères, dédoublonne sans tenir compte de la casse (la
    première graphie gagne), et coupe au-delà de `MAX_CONTEXT_BIAS_TERMS` — ce qui
    dépasse est rendu dans les écartés, pas perdu en silence."""
    if vocabulary is None:
        return [], []
    brut = [vocabulary] if isinstance(vocabulary, str) else list(vocabulary)
    gardes: list[str] = []
    ecartes: list[str] = []
    vus: set[str] = set()
    for terme in brut:
        for mot in _SEPARATEURS.split(str(terme or "")):
            if not mot:
                continue
            if len(mot) < MIN_TERM_LENGTH:
                ecartes.append(mot)
                continue
            cle = mot.casefold()
            if cle in vus:
                continue
            vus.add(cle)
            if len(gardes) >= MAX_CONTEXT_BIAS_TERMS:
                ecartes.append(mot)
                continue
            gardes.append(mot)
    return gardes, ecartes


def _nombre(valeur: Any) -> float | None:
    return float(valeur) if isinstance(valeur, (int, float)) else None


def normalize_transcription(payload: dict) -> dict:
    """Réponse de `/v1/audio/transcriptions` → forme stable.

    `{text, language, model, duration_s, segments: [{start, end, text, speaker}]}` —
    `speaker` est l'identifiant de locuteur de l'amont (`speaker_id`), `None` sans
    diarisation ; `start`/`end` sont `None` quand l'amont n'horodate pas. `language`
    est ce que l'amont rend — souvent `None`, même en détection automatique.
    `duration_s` = secondes d'audio facturées (`usage.prompt_audio_seconds`)."""
    segments = []
    for s in payload.get("segments") or []:
        if not isinstance(s, dict):
            continue
        texte = str(s.get("text") or "").strip()
        if not texte:
            continue
        locuteur = s.get("speaker_id")
        segments.append({
            "start": _nombre(s.get("start")),
            "end": _nombre(s.get("end")),
            "text": texte,
            "speaker": str(locuteur) if locuteur is not None else None,
        })
    usage = payload.get("usage") or {}
    return {
        "text": str(payload.get("text") or ""),
        "language": payload.get("language"),
        "model": payload.get("model"),
        "duration_s": _nombre(usage.get("prompt_audio_seconds")),
        "segments": segments,
    }
