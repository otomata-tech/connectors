"""Mistral audio transcription (Voxtral) — the SHAPE of the exchanges, without transport.

Transport lives in `MistralClient.transcribe`; here, what upstream expects and what
it returns, as pure functions:

- `context_bias_terms` — the vocabulary passed as key terms. Upstream takes a list
  of TERMS WITHOUT SPACES, joined by commas, at most 100. A term with a space is
  split into its words, and fragments under 3 characters (articles,
  prepositions) are discarded: an isolated word keeps the spelling you want to impose,
  an isolated preposition imposes nothing. What is discarded is returned, never swallowed.
- `normalize_transcription` — the response brought to a stable shape: text, language,
  billed duration, segments `{start, end, text, speaker}`.

Upstream constraints held by the client: diarization requires per-segment timestamps
(`timestamp_granularities=segment`, otherwise 422); up to 3 h of audio per
request. Language combines with both — measured on Voxtral Mini Transcribe 2,
despite an incompatibility announced by the upstream docs.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Voxtral Mini Transcribe 2, dated version: a pinned version does not change
# behavior under the caller. Overridable per call (`model=`).
DEFAULT_TRANSCRIPTION_MODEL = "voxtral-mini-2602"

MAX_CONTEXT_BIAS_TERMS = 100
MIN_TERM_LENGTH = 3

_SEPARATEURS = re.compile(r"[,\s]+")


def context_bias_terms(vocabulary: Iterable[str] | str | None) -> tuple[list[str], list[str]]:
    """Vocabulary → `(terms sent, fragments discarded)`.

    Accepts a list of terms or a string (separators: comma, space, newline).
    Splits each term into words, discards those under
    `MIN_TERM_LENGTH` characters, deduplicates case-insensitively (the
    first spelling wins), and cuts beyond `MAX_CONTEXT_BIAS_TERMS` — what
    overflows is returned in the discarded ones, not lost silently."""
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
    """Response of `/v1/audio/transcriptions` → stable shape.

    `{text, language, model, duration_s, segments: [{start, end, text, speaker}]}` —
    `speaker` is upstream's speaker identifier (`speaker_id`), `None` without
    diarization; `start`/`end` are `None` when upstream does not timestamp. `language`
    is what upstream returns — often `None`, even with automatic detection.
    `duration_s` = billed seconds of audio (`usage.prompt_audio_seconds`)."""
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
