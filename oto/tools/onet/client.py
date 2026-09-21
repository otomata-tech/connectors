"""O*NET client — O*NET Web Services, API version 2.0 (api-v2.onetcenter.org).

Le référentiel des métiers du Department of Labor des États-Unis : ~1 000 métiers
codés **O*NET-SOC** (8 chiffres, `15-1299.08`), chacun avec sa description, ses
tâches et les intitulés de poste réellement rencontrés.

API REST en **GET seul**, auth par en-tête `X-API-Key` (clé gratuite, réservée aux
développeurs inscrits ; refusée en query string). Erreurs : 422 avec un corps
`{"error": …}` (paramètre manquant, code O*NET-SOC inexistant ou obsolète, donnée
absente pour ce métier) ; 429 quand le service est saturé — attendre au moins
200 ms avant de réessayer. Listes paginées par `start`/`end` (index à partir de 1,
2 000 éléments au plus par page) ; la réponse porte `start`, `end`, `total`, et
`next`/`prev` quand ils existent.

Services O*NET OnLine servis ici : recherche par mot-clé, fiche d'un métier, et ses
tâches (rapport résumé).

Requires: requests
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import requests

from ...config import require_secret
from ..common import raise_for_upstream


class ONetClient:
    """Client O*NET Web Services v2 (https://api-v2.onetcenter.org), en-tête `X-API-Key`."""

    BASE_URL = "https://api-v2.onetcenter.org"

    def __init__(self, api_key: str = None):
        """
        Args:
            api_key: clé O*NET Web Services (ou variable d'env `ONET_API_KEY`).
        """
        self.api_key = api_key or require_secret("ONET_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.api_key,
                                     "Accept": "application/json"})

    # --- transport ----------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
             timeout: int = 30) -> Dict[str, Any]:
        resp = self.session.get(f"{self.BASE_URL}{path}",
                                params={k: v for k, v in (params or {}).items()
                                        if v is not None},
                                timeout=(10, timeout))
        raise_for_upstream(resp, service="onet")
        return resp.json() if resp.content else {}

    @staticmethod
    def normalize_code(code: str) -> str:
        """`15-1299.08`, `15-1299` ou `151299` → code O*NET-SOC (`15-1299.08`,
        `15-1299.00`). Un SOC à 6 chiffres désigne le métier de niveau SOC : `.00`."""
        m = re.fullmatch(r"(\d{2})-?(\d{4})(?:\.(\d{2}))?", str(code or "").strip())
        if not m:
            raise ValueError(f"code O*NET-SOC invalide : {code!r} — attendu '15-1299.08' "
                             "(ou un SOC à 6 chiffres, lu comme '.00')")
        return f"{m.group(1)}-{m.group(2)}.{m.group(3) or '00'}"

    # --- O*NET OnLine -------------------------------------------------------

    def search_occupations(self, keyword: str, start: Optional[int] = None,
                           end: Optional[int] = None) -> Dict[str, Any]:
        """GET /online/search — métiers par mot, expression, intitulé ou code (même
        partiel). 20 résultats par défaut, les plus proches d'abord.

        Rend `{"start", "end", "total", "next"?, "occupation": [{"code", "title",
        "href", "tags"}]}`.
        """
        return self._get("/online/search",
                         {"keyword": keyword, "start": start, "end": end})

    def get_occupation(self, code: str) -> Dict[str, Any]:
        """GET /online/occupations/{code}/ — la fiche d'un métier : `code`, `title`,
        `description`, `sample_of_reported_titles`, `also_see`, `tags`,
        `bright_outlook`, et les liens vers ses rapports. Toutes les propriétés ne
        sont pas présentes pour tous les métiers."""
        return self._get(f"/online/occupations/{self.normalize_code(code)}/")

    def get_occupation_tasks(self, code: str, start: Optional[int] = None,
                             end: Optional[int] = None) -> Dict[str, Any]:
        """GET /online/occupations/{code}/summary/tasks — les tâches du métier.
        5 par défaut ; `end` élargit la page. Rend `{"start", "end", "total",
        "task": [{"id", "title", "related"}]}` ; 422 si le métier n'a pas de tâches."""
        return self._get(f"/online/occupations/{self.normalize_code(code)}/summary/tasks",
                         {"start": start, "end": end})
