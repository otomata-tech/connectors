"""HelloStock — client de l'API d'administration de la marketplace (`/api/admin`).

Contrat : OpenAPI 3.1 « HelloStock — API admin » `1.0.0`, servi par
`GET /api/admin/openapi.json` derrière l'authentification. Chaque méthode ci-dessous
est UN endpoint de ce contrat ; les corps et les réponses passent tels quels, le
client n'invente aucune sémantique et ne re-type rien.

**Authentification** : `Authorization: Bearer hs_…`, un **jeton personnel** créé par
chaque utilisateur depuis son compte HelloStock. Il porte les droits de SON
utilisateur, et ces routes exigent un compte administrateur :

- jeton inconnu ou révoqué → **401** ;
- jeton valide d'un compte qui n'est pas administrateur → **403**.

Les deux arrivent en `UpstreamHTTPError` (`status_code`, `body = {"error": …}`) :
c'est à la face servie de les dire à l'utilisateur, le client ne connaît pas l'écran
où l'on recrée un jeton.

**Listes** : `limit` (1–200, défaut 50 côté serveur) + `cursor` opaque ; réponse
`{items, nextCursor, total}`, `nextCursor` nul sur la dernière page, `total` = tout
ce qui répond aux filtres. Demandes et offres vont du plus récent au plus ancien,
membres et positionnements par identifiant croissant.

**Filtres validés strictement par le serveur** : une valeur hors référentiel, une
date mal formée ou une limite hors bornes répondent **400** avec un message — jamais
une liste vide. Le client ne revalide donc pas les valeurs métier : il les relaie,
et le refus du serveur est la réponse. Les référentiels publiés par le contrat sont
exposés ci-dessous en constantes, pour que la face servie les annonce sans les
recopier ; le code de service (`service`) n'en fait pas partie — le contrat renvoie
à un catalogue qu'il ne publie pas.

**Écritures** — trois, et elles agissent sur la marketplace de production :

- `send_demande` : envoie un **courriel** aux membres choisis (les specs de la
  demande, sans l'identité de l'acheteur), tracé au nom de l'administrateur dont
  c'est le jeton. **Jamais re-tenté** : l'amont n'a pas de clé d'idempotence, une
  réponse perdue en vol ferait écrire deux fois aux mêmes personnes ;
- `update_demande_status`, `update_offre` : statut, et mots-clés pour une offre
  (publics : ils alimentent la recherche ; normalisés et filtrés par le serveur).

**Délibérément absents** (à ne pas « compléter » sans décision) : la suppression d'un
membre (`DELETE /users/{id}`, en cascade sur tout ce qu'il a déposé), le remplacement
d'une section de contenu du site (`PUT /content/{slug}`) et le téléchargement du
devis d'un positionnement (`GET /positionnements/{id}/devis`, un PDF).

**Redirections refusées** : une 3xx n'est jamais suivie. Une adresse de base qui
redirige (autre hôte, page de connexion) rendrait sinon une page HTML en 200, ou
perdrait l'en-tête d'authentification en changeant d'hôte — les deux se liraient
comme autre chose qu'une erreur de configuration.

Requires: requests
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

SERVICE = "hellostock"
DEFAULT_BASE_URL = "https://hellostock.fr"
API_PREFIX = "/api/admin"

# (connexion, lecture) — aucune attente illimitée.
_HTTP_TIMEOUT = (10, 30)

# Référentiels publiés par le contrat (énumérations OpenAPI).
STATUSES = ("declared", "qualified", "published", "closed")
MATIERES = ("acier", "inox", "aluminium", "cuivre", "laiton", "autre")
CERTIFICATS = ("dispo", "verifie", "sans-mots-cles")
SECTORS = (
    "tolerie_chaudronnerie", "usinage_mecanique", "decoupe_service", "negoce_metaux",
    "recyclage_ferraille", "fonderie", "construction_metallique",
    "industrie_fabricant", "autre",
)

# Seules les LECTURES sont re-tentées, sur débit et indisponibilité passagère.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS = 3


class HelloStockProtocolError(Exception):
    """Le serveur a répondu, mais pas comme l'API décrite par le contrat
    (redirection, corps qui n'est pas du JSON). Un défaut de configuration ou de
    déploiement, pas un refus métier : il ne se corrige pas en changeant l'appel."""


def _positive_id(value: Any, name: str) -> int:
    """Un identifiant numérique entre dans le CHEMIN : il est exigé entier et
    positif, jamais une chaîne qui pourrait porter un `/`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"`{name}` doit être un entier positif (reçu {value!r}).")
    return value


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Paramètres à `None` retirés ; booléens en `true`/`false` (requests écrirait
    `True`, que le serveur ne lit pas comme un booléen)."""
    out: Dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        out[k] = ("true" if v else "false") if isinstance(v, bool) else v
    return out


class HelloStockAdminClient:
    """Client de l'API admin HelloStock, auth Bearer par jeton personnel `hs_…`."""

    def __init__(self, token: Optional[str] = None,
                 base_url: Optional[str] = None):
        """
        Args:
            token: jeton d'API personnel.
            base_url: racine du site (défaut `https://hellostock.fr`).
        """
        self.token = require(token, "HELLOSTOCK_API_TOKEN")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        # Jeton en EN-TÊTE uniquement : en query string il entrerait dans l'URL,
        # donc dans le message de toute exception et dans les journaux d'accès.
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{API_PREFIX}{path}"
        retryable = method == "GET"
        resp = None
        for attempt in range(_MAX_ATTEMPTS):
            resp = self.session.request(
                method, url, params=_clean(params or {}) or None, json=body,
                timeout=_HTTP_TIMEOUT, allow_redirects=False)
            if (resp.status_code not in _RETRY_STATUSES or not retryable
                    or attempt == _MAX_ATTEMPTS - 1):
                break
            time.sleep(float(2 ** attempt))
        if 300 <= resp.status_code < 400:
            raise HelloStockProtocolError(
                f"HelloStock a répondu par une redirection ({resp.status_code} vers "
                f"{resp.headers.get('Location')!r}) au lieu de l'API : l'adresse de "
                f"base {self.base_url!r} ne pointe pas sur l'API d'administration.")
        raise_for_upstream(resp, service=SERVICE)
        try:
            return resp.json()
        except ValueError:
            ctype = resp.headers.get("Content-Type")
            raise HelloStockProtocolError(
                f"HelloStock a répondu {resp.status_code} sans corps JSON "
                f"(Content-Type {ctype!r}) sur {method} {API_PREFIX}{path}.") from None

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    # --- demandes -----------------------------------------------------------

    def list_demandes(self, *, status: Optional[str] = None,
                      since: Optional[str] = None, until: Optional[str] = None,
                      departement: Optional[str] = None,
                      matiere: Optional[str] = None, service: Optional[str] = None,
                      q: Optional[str] = None, limit: Optional[int] = None,
                      cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /demandes — du plus récent au plus ancien.

        `since`/`until` : `YYYY-MM-DD` ou datetime ISO 8601, sur `createdAt`
        (`since` inclus, `until` exclu). `departement` est lu sur le code postal de
        l'entreprise rattachée : une piste sans compte n'y répond pas.
        """
        return self._get("/demandes", status=status, since=since, until=until,
                         departement=departement, matiere=matiere, service=service,
                         q=q, limit=limit, cursor=cursor)

    def get_demande(self, demande_id: int) -> Dict[str, Any]:
        """GET /demandes/{id} — la forme du listing, plus ses positionnements et
        ses envois (du plus récent au plus ancien)."""
        return self._get(f"/demandes/{_positive_id(demande_id, 'demande_id')}")

    def update_demande_status(self, demande_id: int, status: str) -> Dict[str, Any]:
        """PATCH /demandes/{id} `{status}` — rend `{success}`."""
        return self._request(
            "PATCH", f"/demandes/{_positive_id(demande_id, 'demande_id')}",
            body={"status": status})

    def send_demande(self, demande_id: int, user_ids: Sequence[int],
                     message: Optional[str] = None) -> Dict[str, Any]:
        """POST /demandes/{id}/envoyer `{userIds, message?}` — envoie un courriel à
        chacun des membres désignés, et trace chaque envoi effectif.

        Rend `{success, envoyes, echecs, noop}` : `echecs` = les adresses dont
        l'envoi a échoué, `noop` = le serveur n'a pas de messagerie configurée (envois
        simulés, mais tracés). 502 si aucun courriel n'a pu partir. `message` part
        tel quel dans le courriel.
        """
        ids = [_positive_id(u, "user_ids[]") for u in (user_ids or [])]
        if not ids:
            raise ValueError("`user_ids` : au moins un membre destinataire.")
        body: Dict[str, Any] = {"userIds": ids}
        if message is not None:
            body["message"] = message
        return self._request(
            "POST", f"/demandes/{_positive_id(demande_id, 'demande_id')}/envoyer",
            body=body)

    # --- offres -------------------------------------------------------------

    def list_offres(self, *, status: Optional[str] = None,
                    since: Optional[str] = None, until: Optional[str] = None,
                    departement: Optional[str] = None,
                    matiere: Optional[str] = None, certificat: Optional[str] = None,
                    q: Optional[str] = None, limit: Optional[int] = None,
                    cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /offres — du plus récent au plus ancien.

        `certificat=sans-mots-cles` est la file d'enrichissement : certificat joint
        et aucun mot-clé, à traiter par `update_offre(keywords=…)`.
        """
        return self._get("/offres", status=status, since=since, until=until,
                         departement=departement, matiere=matiere,
                         certificat=certificat, q=q, limit=limit, cursor=cursor)

    def get_offre(self, offre_id: int) -> Dict[str, Any]:
        """GET /offres/{id} — la forme du listing, plus le détail du verdict
        certificat (qui porte le numéro de coulée : surface admin seulement)."""
        return self._get(f"/offres/{_positive_id(offre_id, 'offre_id')}")

    def update_offre(self, offre_id: int, *, status: Optional[str] = None,
                     keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """PATCH /offres/{id} `{status?, keywords?}` — au moins l'un des deux.

        `keywords` REMPLACE la liste existante ; le serveur normalise (espaces,
        casse, doublons) et refuse en bloc une liste qui porte une identité
        (aciériste, numéro de coulée ou de commande). Rend `{success}`.
        """
        body: Dict[str, Any] = {}
        if status is not None:
            body["status"] = status
        if keywords is not None:
            body["keywords"] = list(keywords)
        if not body:
            raise ValueError("`status` ou `keywords` : au moins l'un des deux.")
        return self._request(
            "PATCH", f"/offres/{_positive_id(offre_id, 'offre_id')}", body=body)

    # --- membres ------------------------------------------------------------

    def list_users(self, *, q: Optional[str] = None, sector: Optional[str] = None,
                   service: Optional[str] = None, is_admin: Optional[bool] = None,
                   has_offres: Optional[bool] = None,
                   has_demandes: Optional[bool] = None,
                   limit: Optional[int] = None,
                   cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /users — annuaire des comptes, par identifiant croissant (le tri
        alphabétique est à faire côté appelant)."""
        return self._get("/users", q=q, sector=sector, service=service,
                         isAdmin=is_admin, hasOffres=has_offres,
                         hasDemandes=has_demandes, limit=limit, cursor=cursor)

    def get_user(self, user_id: int) -> Dict[str, Any]:
        """GET /users/{id} — le membre, son entreprise détaillée et son activité
        (demandes, offres, fils de messagerie où il est acheteur)."""
        return self._get(f"/users/{_positive_id(user_id, 'user_id')}")

    # --- positionnements ----------------------------------------------------

    def list_positionnements(self, *, demande_id: Optional[int] = None,
                             user_id: Optional[int] = None,
                             since: Optional[str] = None,
                             limit: Optional[int] = None,
                             cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /positionnements — les fournisseurs qui se sont proposés sur une
        demande, par identifiant croissant."""
        return self._get("/positionnements", demandeId=demande_id, userId=user_id,
                         since=since, limit=limit, cursor=cursor)


__all__ = [
    "CERTIFICATS", "DEFAULT_BASE_URL", "HelloStockAdminClient",
    "HelloStockProtocolError", "MATIERES", "SECTORS", "STATUSES",
]
