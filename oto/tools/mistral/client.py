"""
Mistral API client (OpenAI-compatible REST API).

Requires: requests

Authentication:
    MISTRAL_API_KEY: API key from https://console.mistral.ai/
"""

import json
from typing import Any, Dict, Iterable, List, Optional

import requests

from ...config import require_secret
from ..common.errors import raise_for_upstream
from .transcription import (
    DEFAULT_TRANSCRIPTION_MODEL,
    context_bias_terms,
    normalize_transcription,
)

# (connexion, lecture) d'une transcription : l'amont rend ~30 min d'audio en moins de
# 30 s, et accepte jusqu'à 3 h par requête. Surchargeable par appel.
TRANSCRIPTION_TIMEOUT = (10, 300)


class MistralClient:
    """
    Mistral API client.

    Features:
    - Chat completions
    - JSON mode
    - Multiple model support
    - Audio transcription (Voxtral) — `transcribe`
    """

    BASE_URL = "https://api.mistral.ai/v1"

    def __init__(self, api_key: str = None, model: str = "mistral-small-latest"):
        self.api_key = api_key or require_secret("MISTRAL_API_KEY")
        self.model = model

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
        model: str = None,
    ) -> Dict[str, Any]:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = requests.post(
            f"{self.BASE_URL}/chat/completions",
            headers=self._get_headers(),
            json=payload,
            timeout=60,
        )

        if not resp.ok:
            raise Exception(f"Mistral API error: {resp.status_code} {resp.text}")

        return resp.json()

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
        model: str = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        result = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            model=model,
        )

        return result["choices"][0]["message"]["content"]

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        model: str = None,
    ) -> Dict[str, Any]:
        content = self.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            model=model,
        )

        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        return json.loads(content)

    def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.3,
        max_tokens: int = 2048,
        model: str = None,
    ) -> Dict[str, Any]:
        """Chat completion with tool definitions. Returns raw API response.

        The agent loop (calling tools, appending results) is handled by the caller.
        """
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": "auto",
        }

        resp = requests.post(
            f"{self.BASE_URL}/chat/completions",
            headers=self._get_headers(),
            json=payload,
            timeout=120,
        )

        if not resp.ok:
            raise Exception(f"Mistral API error: {resp.status_code} {resp.text}")

        return resp.json()

    def list_models(self) -> Dict[str, Any]:
        """Modèles accessibles à la clé (`GET /v1/models`) — non facturé, sert de
        sonde d'authentification."""
        resp = requests.get(
            f"{self.BASE_URL}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=(10, 30),
        )
        raise_for_upstream(resp, service="Mistral")
        return resp.json()

    def transcribe(
        self,
        audio: bytes,
        filename: str,
        *,
        mime: Optional[str] = None,
        language: Optional[str] = None,
        timestamps: bool = False,
        diarize: bool = False,
        context_bias: Iterable[str] | str | None = None,
        model: Optional[str] = None,
        timeout: tuple = TRANSCRIPTION_TIMEOUT,
    ) -> Dict[str, Any]:
        """Transcrit un audio en un appel (`POST /v1/audio/transcriptions`, multipart).

        `diarize=True` fait porter un identifiant de locuteur à chaque segment, et
        envoie TOUJOURS `timestamp_granularities=segment` : l'amont refuse la
        diarisation sans horodatage par segment (422). `timestamps=True` demande
        l'horodatage sans diarisation. `language` (ex. `"fr"`) se combine avec les
        deux.
        `context_bias` = vocabulaire à privilégier, normalisé par
        `context_bias_terms` (termes sans espace, joints par des virgules).

        Rend `normalize_transcription(...)` plus `context_bias` (termes envoyés) et
        `context_bias_dropped` (fragments écartés). Lève `UpstreamHTTPError` sur un
        refus de l'amont."""
        termes, ecartes = context_bias_terms(context_bias)
        data: List[tuple] = [("model", model or DEFAULT_TRANSCRIPTION_MODEL)]
        if language:
            data.append(("language", language))
        if timestamps or diarize:
            data.append(("timestamp_granularities", "segment"))
        if diarize:
            data.append(("diarize", "true"))
        if termes:
            data.append(("context_bias", ",".join(termes)))
        resp = requests.post(
            f"{self.BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            data=data,
            files={"file": (filename, audio, mime or "application/octet-stream")},
            timeout=timeout,
        )
        raise_for_upstream(resp, service="Mistral")
        out = normalize_transcription(resp.json())
        out["context_bias"] = termes
        out["context_bias_dropped"] = ecartes
        return out
