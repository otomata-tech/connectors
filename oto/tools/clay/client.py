"""Clay client — Public API (routines, recherche, tables) + webhooks de table.

Clay est une plateforme d'enrichissement GTM : des **tables** où chaque colonne
peut appeler un fournisseur de données ou un modèle, et des **routines** (fonctions
Clay-managed, fonctions custom, Workflows) exécutables hors de l'UI.

Deux surfaces, deux auth, deux classes :

- `ClayClient` — la **Public API** (`https://api.clay.com/public/v0`), en-tête
  `clay-api-key` (clé PERSONNELLE, liée à un utilisateur Clay et à ses accès
  workspace ; se crée dans Settings → Account → API keys). Couvre les 16 opérations
  de l'OpenAPI publié (https://developers.clay.com/openapi.json), 1 méthode = 1
  endpoint. Tout y est en LECTURE ou en exécution : l'API ne sait ni créer une
  table, ni y écrire une ligne.
- `ClayTableWebhook` — le **webhook entrant d'une table** (source « Monitor
  webhook » ajoutée dans l'UI Clay) : la SEULE façon d'écrire des lignes dans une
  table Clay depuis l'extérieur. Un POST JSON = une ligne. Pas de clé API : l'URL
  elle-même (plus un jeton optionnel en en-tête) est le droit d'écrire. Aucune API
  ne crée ce webhook — il se copie depuis l'UI, d'où `parse_curl` pour accepter la
  commande cURL que Clay affiche telle quelle.

Coûts : chaque appel consomme les crédits Clay du workspace de la clé, comme le même
travail fait dans l'UI. Rate limit par workspace : 429 + `Retry-After` (secondes),
remonté tel quel dans `UpstreamHTTPError.body` (clé `retry_after`).

Docs : https://developers.clay.com — https://university.clay.com/docs/webhook-integration-guide

Requires: requests
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from ...config import require_secret
from ..common import UpstreamHTTPError, raise_for_upstream

SOURCE_TYPES = ("people", "companies")

# En-tête du jeton optionnel d'un webhook de table (« authentication token »).
WEBHOOK_AUTH_HEADER = "x-clay-webhook-auth"


def _raise(resp: Any) -> None:
    """`raise_for_upstream`, plus le `Retry-After` d'un 429 dans le corps levé."""
    if resp.status_code == 429:
        try:
            body = resp.json()
        except Exception:
            body = {"message": resp.text}
        if not isinstance(body, dict):
            body = {"message": body}
        retry = resp.headers.get("Retry-After")
        if retry:
            body["retry_after"] = retry
        raise UpstreamHTTPError(429, body, service="clay")
    raise_for_upstream(resp, service="clay")


class ClayClient:
    """Client de la Clay Public API — 1 méthode = 1 endpoint."""

    BASE_URL = "https://api.clay.com/public/v0"

    def __init__(self, api_key: Optional[str] = None, timeout: tuple = (10, 40)):
        """Initialise le client.

        Args:
            api_key: clé Clay Public API (ou env `CLAY_API_KEY`).
            timeout: (connect, read) en secondes.
        """
        self.api_key = api_key or require_secret("CLAY_API_KEY")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "clay-api-key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", timeout=self.timeout, **kwargs)
        _raise(resp)
        return resp.json() if resp.content else {}

    # --- compte ---------------------------------------------------------------

    def get_me(self) -> Dict[str, Any]:
        """`GET /me` — utilisateur et workspace de la clé (`{user, workspace}`)."""
        return self._request("GET", "/me")

    def get_credit_balance(self) -> Dict[str, Any]:
        """`GET /credits/balance` — soldes du workspace
        (`{balance, action_execution_balance}`)."""
        return self._request("GET", "/credits/balance")

    # --- routines -------------------------------------------------------------

    def run_routine(
        self,
        routine_id: str,
        items: List[Dict[str, Any]],
        webhook_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`POST /routines/{routine_id}/run` — lance une routine sur 1 à 100 items.

        Toujours ASYNCHRONE : renvoie `{routine_run_id, status: "in_progress"}`,
        jamais les résultats — les lire avec `get_run_results`.

        Args:
            routine_id: ex. `function:t_abc123` (fonction custom). Aucun endpoint
                ne liste les routines : l'id se copie depuis l'app ou le CLI Clay.
            items: `[{id, inputs: {...}}]` — `id` (≤ 64 car.) est rendu avec le
                résultat de l'item pour l'apparier.
            webhook_id: webhook Clay enregistré à notifier en fin de run.
        """
        body: Dict[str, Any] = {"items": items}
        if webhook_id:
            body["webhook_id"] = webhook_id
        return self._request("POST", f"/routines/{routine_id}/run", json=body)

    def get_run_results(
        self,
        routine_run_id: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`GET /routines/run/{routine_run_id}/results` — progression + résultats.

        `{routine_run_id, status, finished, total, data, cursor}`. `status` passe à
        `complete` quand le run est fini ; un run complet peut contenir des items
        `failed`. `cursor` présent = page suivante (limit 1-100, défaut 20)."""
        params: Dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._request(
            "GET", f"/routines/run/{routine_run_id}/results", params=params)

    def create_batch_upload_url(self, routine_id: str) -> Dict[str, Any]:
        """`POST /routines/{routine_id}/run-batch/upload-url` — URL présignée PUT
        pour le fichier JSONL d'entrée (`{file_id, upload_url}`)."""
        return self._request(
            "POST", f"/routines/{routine_id}/run-batch/upload-url", json={})

    def upload_batch_file(self, upload_url: str, jsonl: str) -> None:
        """PUT du JSONL sur l'URL présignée (hors API : pas d'en-tête de clé)."""
        resp = requests.put(upload_url, data=jsonl.encode("utf-8"),
                            timeout=(10, 120))
        _raise(resp)

    def start_batch_run(
        self,
        routine_id: str,
        file_id: str,
        webhook_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`POST /routines/{routine_id}/run-batch/start` — run asynchrone sur le
        JSONL déposé (`{routine_run_id, status}`)."""
        body: Dict[str, Any] = {"file_id": file_id}
        if webhook_id:
            body["webhook_id"] = webhook_id
        return self._request(
            "POST", f"/routines/{routine_id}/run-batch/start", json=body)

    def get_batch_run_results(self, routine_run_id: str) -> Dict[str, Any]:
        """`GET /routines/run-batch/{routine_run_id}/results` — progression et
        résultats d'un run batch."""
        return self._request(
            "GET", f"/routines/run-batch/{routine_run_id}/results")

    # --- recherche (base GTM de Clay) ---------------------------------------

    def list_search_fields(self, source_type: str) -> Dict[str, Any]:
        """`GET /search/filters-mode/fields` — filtres disponibles pour
        `people` ou `companies` (`{source_type, fields, guidance}`)."""
        return self._request(
            "GET", "/search/filters-mode/fields",
            params={"source_type": source_type})

    def create_filters_search(
        self, source_type: str, filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        """`POST /search/filters-mode` — crée une recherche à filtres structurés
        (`{search_id}`). Aucun résultat avant `run_filters_search`."""
        return self._request(
            "POST", "/search/filters-mode",
            json={"source_type": source_type, "filters": filters})

    def run_filters_search(
        self, search_id: str, limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /search/filters-mode/{search_id}/run` — page SUIVANTE de
        l'itérateur (`{data, has_more, period_quota}`, limit 1-500, défaut 20).
        Chaque appel avance : rappeler = page d'après."""
        body = {"limit": limit} if limit else {}
        return self._request(
            "POST", f"/search/filters-mode/{search_id}/run", json=body)

    def get_query_reference(self) -> Dict[str, Any]:
        """`GET /search/query-mode/reference` — grammaire des requêtes Clay."""
        return self._request("GET", "/search/query-mode/reference")

    def create_query_search(self, query: str) -> Dict[str, Any]:
        """`POST /search/query-mode` — crée une recherche depuis une requête Clay
        (`{search_id, source_type}`)."""
        return self._request("POST", "/search/query-mode", json={"query": query})

    def run_query_search(
        self, search_id: str, limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /search/query-mode/{search_id}/run` — page suivante (limit
        1-500, défaut 20)."""
        body = {"limit": limit} if limit else {}
        return self._request(
            "POST", f"/search/query-mode/{search_id}/run", json=body)

    # --- tables (Enterprise) --------------------------------------------------

    def query_tables(
        self,
        query: Dict[str, Any],
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /tables/query` — requête structurée sur une ou plusieurs tables
        connues (`{data, fields, cursor, truncated}`, limit 1-100, défaut 50).

        Réservé au plan Enterprise (sync des tables par API). Lecture seule. Le
        parcours suit l'ordre de dernière mise à jour : une ligne modifiée en cours
        de scan peut revenir — dédupliquer par id."""
        body: Dict[str, Any] = {"query": query}
        if cursor:
            body["cursor"] = cursor
        if limit:
            body["limit"] = limit
        return self._request("POST", "/tables/query", json=body)

    # --- runs de workflows (bêta) ---------------------------------------------

    def get_runs_query_reference(self) -> Dict[str, Any]:
        """`GET /workflows/runs/query/reference` — grammaire de recherche de runs."""
        return self._request("GET", "/workflows/runs/query/reference")

    def query_workflow_runs(
        self,
        query: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /workflows/runs/query` — recherche de runs (bêta)."""
        body: Dict[str, Any] = {"query": query}
        if cursor:
            body["cursor"] = cursor
        if limit:
            body["limit"] = limit
        return self._request("POST", "/workflows/runs/query", json=body)


# --- webhooks de table ------------------------------------------------------


def is_clay_webhook_url(url: str) -> bool:
    """True si `url` est une URL https sur un hôte `clay.com` (ou sous-domaine).

    Garde de destination : un « webhook Clay » qui pointerait ailleurs ferait
    d'oto un relais POST vers n'importe quel hôte."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "clay.com" or host.endswith(".clay.com"))


def parse_curl(text: str) -> Dict[str, Optional[str]]:
    """Extrait `{webhook_url, auth_token}` d'une commande cURL copiée depuis Clay.

    Accepte aussi une URL nue (alors `auth_token` = None). Tolère les `\\` de fin
    de ligne et les guillemets simples/doubles. Lève `ValueError` si aucune URL
    https n'est trouvée."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty input")
    if not raw.lower().startswith("curl"):
        return {"webhook_url": raw, "auth_token": None}
    # Les `\` de continuation de ligne deviennent des blancs — y compris quand un
    # champ une ligne a déjà retiré le saut (`'url'\ -H …`). Aucune URL ni jeton
    # Clay ne porte d'antislash ; le corps `-d`, lui, est ignoré.
    flat = re.sub(r"\\[ \t]*(?:\r?\n)?", " ", raw)
    try:
        tokens = shlex.split(flat)
    except ValueError as e:
        raise ValueError(f"unparseable cURL command: {e}") from None
    url: Optional[str] = None
    token: Optional[str] = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-H", "--header") and i + 1 < len(tokens):
            name, _, value = tokens[i + 1].partition(":")
            if name.strip().lower() == WEBHOOK_AUTH_HEADER:
                token = value.strip() or None
            i += 2
            continue
        if t in ("-d", "--data", "--data-raw", "--data-binary", "-X", "--request"):
            i += 2
            continue
        if url is None and re.match(r"^https?://", t):
            url = t
        i += 1
    if not url:
        raise ValueError("no URL found in the cURL command")
    return {"webhook_url": url, "auth_token": token}


class ClayTableWebhook:
    """Écrit des lignes dans UNE table Clay via son webhook entrant.

    Un `push` = un POST = une ligne. Pas de lot côté Clay : l'appelant boucle.
    Jeton absent ou faux sur un webhook protégé → 401.
    Plafond Clay : 50 000 envois par webhook (hors Enterprise « auto-delete »),
    compteur non remis à zéro par la suppression de lignes — au-delà, il faut
    recréer un webhook dans l'UI."""

    def __init__(self, webhook_url: str, auth_token: Optional[str] = None,
                 timeout: tuple = (5, 10)):
        if not is_clay_webhook_url(webhook_url):
            raise ValueError("not a Clay webhook URL (https://…clay.com/… expected)")
        self.webhook_url = webhook_url.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        if auth_token:
            self.session.headers[WEBHOOK_AUTH_HEADER] = auth_token

    def push(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """POST une ligne. L'objet entier arrive dans la colonne Webhook de la table
        (ses clés se relient aux colonnes côté Clay) ; un tableau JSON n'est pas
        découpé et ne fait qu'une ligne."""
        resp = self.session.post(self.webhook_url, json=row, timeout=self.timeout)
        _raise(resp)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"response": resp.text[:200]}
