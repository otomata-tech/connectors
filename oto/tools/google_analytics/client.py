"""Client Google Analytics 4 — LECTURE SEULE, par clé de compte de service.

Deux API REST de Google, un seul credential :

- **Analytics Admin v1beta** (`analyticsadmin.googleapis.com`) : les comptes et
  propriétés visibles (`accountSummaries`), les flux de données d'une propriété
  (`dataStreams`), ses événements clés (`keyEvents`) ;
- **Analytics Data v1beta** (`analyticsdata.googleapis.com`) : les rapports
  (`:runReport`), le temps réel (`:runRealtimeReport`), et le catalogue des
  dimensions et métriques d'une propriété (`/metadata`).

**Pourquoi un compte de service.** Le consentement OAuth d'un utilisateur au scope
`analytics.readonly` peut être bloqué par Google pour une application non
vérifiée. Un compte de service ajouté comme **Lecteur** d'une propriété GA4 lit
sans le compte Google de personne, et se coupe en retirant son accès dans GA4.

**Credential** = le contenu du fichier JSON de la clé du compte de service
(cf. `auth.parse_service_account_key`). Le jeton d'accès est émis et mis en
cache par `auth.access_token`.

**Aucune écriture.** Le client n'expose aucune méthode qui crée, modifie ou
supprime quoi que ce soit dans GA4 — et le scope demandé (`analytics.readonly`)
l'interdirait de toute façon.

**Refus typés** (sous-classes d'`UpstreamHTTPError`, lus sur le `status` canonique
de l'erreur Google et sur la `reason` de ses détails, jamais sur le texte) :

- `GA4InvalidArgument` — 400 `INVALID_ARGUMENT` : un nom de dimension ou de
  métrique inconnu de la propriété, une combinaison incompatible, une date mal
  formée. Le message de Google nomme le champ fautif ; il est rendu tel quel.
- `GA4PermissionDenied` — 403 `PERMISSION_DENIED` : le compte de service n'a pas
  accès à la propriété. Porte l'email du compte de service, qu'il faut ajouter
  comme Lecteur dans GA4.
- `GA4ServiceDisabled` — 403 dont la raison est `SERVICE_DISABLED` : l'API n'est
  pas activée dans le projet Google Cloud du compte de service.

Requires: requests, google-auth (extra `google`)
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence, Union

import requests

from ...config import require_secret
from ..common import UpstreamHTTPError
from . import auth

ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"

_HTTP_TIMEOUT = (10, 60)
_ADMIN_PAGE_SIZE = 200
# Borne du suivi de `nextPageToken` : au-delà, l'amont boucle — on le dit.
_MAX_PAGES = 50

#: Fenêtre par défaut d'un rapport : les 30 derniers jours complets (hier inclus,
#: aujourd'hui exclu — la journée en cours est incomplète). Même définition que
#: « 30 derniers jours » dans l'interface GA4.
DEFAULT_START_DATE = "30daysAgo"
DEFAULT_END_DATE = "yesterday"

_PROPERTY_RE = re.compile(r"^(?:properties/)?(\d+)$")

# Clés d'une FilterExpression GA4 : un filtre qui en porte une est passé tel quel.
_EXPRESSION_KEYS = frozenset({"andGroup", "orGroup", "notExpression", "filter"})

# Types de métrique rendus en entier ; tous les autres types numériques en flottant.
_INT_TYPES = frozenset({"TYPE_INTEGER"})


class GA4Error(UpstreamHTTPError):
    """Refus d'une API Google Analytics. `status` = statut canonique Google
    (`INVALID_ARGUMENT`, `PERMISSION_DENIED`…), `reason` = la raison d'`ErrorInfo`
    quand Google en donne une (`SERVICE_DISABLED`…), `message` = le texte de Google."""

    def __init__(self, status_code: int, body: Any, *, status: str = "",
                 reason: str = "", message: str = ""):
        self.status = status
        self.reason = reason
        self.message = message
        super().__init__(status_code, body, service="google_analytics")


class GA4InvalidArgument(GA4Error):
    """400 `INVALID_ARGUMENT` — dimension/métrique inconnue ou incompatible, date
    mal formée, filtre invalide."""


class GA4PermissionDenied(GA4Error):
    """403 — le compte de service n'a pas accès à la ressource demandée."""

    def __init__(self, *args, client_email: str = "", resource: str = "", **kw):
        self.client_email = client_email
        self.resource = resource
        super().__init__(*args, **kw)
        qui = client_email or "le compte de service"
        quoi = resource or "cette propriété"
        self.args = (
            f"google_analytics HTTP 403 : {qui} n'a pas accès à {quoi}. Ajoute cet "
            "email comme Lecteur de la propriété dans GA4 (Administration → Gestion "
            "des accès à la propriété).",)


class GA4ServiceDisabled(GA4Error):
    """403 `SERVICE_DISABLED` — l'API n'est pas activée dans le projet Google
    Cloud du compte de service."""


def property_name(prop: Union[str, int]) -> str:
    """`properties/<id>` depuis `<id>` ou `properties/<id>` — sinon `ValueError`.

    Un identifiant de MESURE (`G-XXXX`) ou de compte n'est pas une propriété :
    refusé avec ce qu'il faut à la place."""
    m = _PROPERTY_RE.match(str(prop).strip())
    if not m:
        raise ValueError(
            f"Propriété GA4 invalide : {prop!r}. Attendu l'identifiant numérique de la "
            "propriété (`123456789` ou `properties/123456789`) — pas un ID de mesure "
            "`G-…`, ni un ID de compte.")
    return f"properties/{m.group(1)}"


def _names(values: Union[str, Iterable[str], None], kind: str) -> list[str]:
    """Une liste de noms ; un texte seul se lit comme une liste séparée par des
    virgules (`"sessions,activeUsers"`), jamais comme une suite de caractères."""
    if isinstance(values, str):
        values = [v for v in values.split(",") if v.strip()]
    out = []
    for v in values or ():
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Nom de {kind} invalide : {v!r}.")
        out.append(v.strip())
    return out


def build_filter(spec: Optional[dict]) -> Optional[dict]:
    """Une `FilterExpression` GA4 depuis un filtre SIMPLE — ou l'expression telle
    quelle.

    Forme simple : `{"champ": valeur, …}`, combinés en ET. Une valeur texte est une
    égalité exacte, une liste une appartenance (`inListFilter`), un nombre une
    égalité numérique. Une expression GA4 complète (`andGroup`, `orGroup`,
    `notExpression`, `filter`) passe sans transformation — c'est la voie des
    opérateurs (contient, commence par, comparaisons)."""
    if spec is None:
        return None
    if not isinstance(spec, dict) or not spec:
        raise ValueError("Un filtre est un objet non vide : {\"champ\": valeur} ou une "
                         "FilterExpression GA4.")
    if _EXPRESSION_KEYS & set(spec):
        return spec
    exprs = []
    for field, value in spec.items():
        if isinstance(value, bool):
            raise ValueError(f"Filtre sur {field!r} : valeur booléenne non prise en charge.")
        if isinstance(value, str):
            f = {"stringFilter": {"matchType": "EXACT", "value": value}}
        elif isinstance(value, (list, tuple)):
            if not value or not all(isinstance(v, str) for v in value):
                raise ValueError(f"Filtre sur {field!r} : une liste de valeurs texte non vide.")
            f = {"inListFilter": {"values": list(value)}}
        elif isinstance(value, (int, float)):
            num = {"int64Value": str(value)} if isinstance(value, int) else {"doubleValue": value}
            f = {"numericFilter": {"operation": "EQUAL", "value": num}}
        else:
            raise ValueError(f"Filtre sur {field!r} : valeur {type(value).__name__} non "
                             "prise en charge (texte, liste de textes ou nombre).")
        exprs.append({"filter": {"fieldName": field, **f}})
    return exprs[0] if len(exprs) == 1 else {"andGroup": {"expressions": exprs}}


def build_order_bys(order_by: Optional[Sequence[str]], metrics: Sequence[str]) -> list[dict]:
    """`["-sessions", "date"]` → les `orderBys` GA4. Un `-` en tête = décroissant.
    Un nom présent dans `metrics` trie sur la métrique, sinon sur la dimension."""
    out = []
    for raw in order_by or ():
        if not isinstance(raw, str) or not raw.strip("- "):
            raise ValueError(f"Tri invalide : {raw!r}.")
        name = raw.strip()
        desc = name.startswith("-")
        name = name.lstrip("-")
        key = ({"metric": {"metricName": name}} if name in metrics
               else {"dimension": {"dimensionName": name}})
        out.append({**key, "desc": desc})
    return out


def _typed(value: Optional[str], metric_type: str) -> Any:
    if value is None:
        return None
    try:
        return int(value) if metric_type in _INT_TYPES else float(value)
    except ValueError:
        return value


def flatten_report(resp: dict) -> dict:
    """Un rapport GA4 (`runReport` / `runRealtimeReport`) en TABLE : `columns`
    (dimensions puis métriques) et `rows` (listes de valeurs, métriques typées).

    Garde `row_count` (le total de lignes qui correspondent, pour paginer) et les
    avertissements de fiabilité que GA4 attache au rapport : échantillonnage,
    seuils de confidentialité, regroupement en « (other) ». Les retirer ferait
    lire un chiffre estimé comme exact."""
    dims = [h.get("name") for h in resp.get("dimensionHeaders") or ()]
    mets = resp.get("metricHeaders") or ()
    rows = []
    for r in resp.get("rows") or ():
        dv = [d.get("value") for d in r.get("dimensionValues") or ()]
        mv = [_typed(m.get("value"), h.get("type", ""))
              for m, h in zip(r.get("metricValues") or (), mets)]
        rows.append(dv + mv)
    out: dict[str, Any] = {
        "columns": dims + [h.get("name") for h in mets],
        "rows": rows,
        "row_count": resp.get("rowCount", 0),
    }
    for k in ("totals", "maximums", "minimums"):
        if resp.get(k):
            out[k] = flatten_report({"dimensionHeaders": resp.get("dimensionHeaders"),
                                     "metricHeaders": mets, "rows": resp[k]})["rows"]
    meta = resp.get("metadata") or {}
    kept = {k: meta[k] for k in ("currencyCode", "timeZone", "dataLossFromOtherRow",
                                 "samplingMetadatas", "subjectToThresholding",
                                 "emptyReason") if meta.get(k) not in (None, False, "")}
    if kept:
        out["metadata"] = kept
    if resp.get("propertyQuota"):
        out["property_quota"] = resp["propertyQuota"]
    return out


class GA4Client:
    """Client GA4 (Admin + Data v1beta), auth par clé de compte de service."""

    def __init__(self, service_account_key: Union[str, bytes, dict, None] = None, *,
                 session: Optional[requests.Session] = None):
        """
        Args:
            service_account_key: le contenu JSON de la clé du compte de service
                (texte ou dict), ou la variable `GA4_SERVICE_ACCOUNT_JSON`.
            session: transport HTTP (défaut : une `requests.Session` neuve).
        """
        raw = (service_account_key if service_account_key is not None
               else require_secret("GA4_SERVICE_ACCOUNT_JSON"))
        self._key = auth.parse_service_account_key(raw)
        self.client_email: str = self._key["client_email"]
        self.session = session or requests.Session()

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, url: str, *, params: Optional[dict] = None,
                 json_body: Any = None, resource: str = "") -> Any:
        token = auth.access_token(self._key, session=self.session)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self.session.request(
            method, url, params=clean or None, json=json_body,
            headers={"Authorization": f"Bearer {token}"}, timeout=_HTTP_TIMEOUT)
        if resp.status_code >= 400:
            self._raise(resp, resource)
        return resp.json() if resp.content else {}

    def _raise(self, resp, resource: str) -> None:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        status = err.get("status") or ""
        message = err.get("message") or ""
        reasons = [d.get("reason") for d in err.get("details") or ()
                   if isinstance(d, dict) and d.get("reason")]
        reason = reasons[0] if reasons else ""
        kw = dict(status=status, reason=reason, message=message)
        if resp.status_code == 403 and reason == "SERVICE_DISABLED":
            raise GA4ServiceDisabled(resp.status_code, body, **kw)
        if resp.status_code == 403 or status == "PERMISSION_DENIED":
            raise GA4PermissionDenied(resp.status_code, body, client_email=self.client_email,
                                      resource=resource, **kw)
        if status == "INVALID_ARGUMENT":
            raise GA4InvalidArgument(resp.status_code, body, **kw)
        raise GA4Error(resp.status_code, body, **kw)

    def _paged(self, url: str, items_key: str, resource: str = "") -> list[dict]:
        items: list[dict] = []
        token = None
        for _ in range(_MAX_PAGES):
            page = self._request("GET", url, params={"pageSize": _ADMIN_PAGE_SIZE,
                                                      "pageToken": token},
                                 resource=resource)
            items.extend(page.get(items_key) or ())
            token = page.get("nextPageToken")
            if not token:
                return items
        raise RuntimeError(f"google_analytics : plus de {_MAX_PAGES} pages de "
                           f"`{items_key}` — pagination interrompue.")

    # --- Admin API -----------------------------------------------------------

    def account_summaries(self) -> list[dict]:
        """Les comptes GA visibles, chacun avec ses propriétés (`propertySummaries`)."""
        return self._paged(f"{ADMIN_BASE}/accountSummaries", "accountSummaries")

    def list_data_streams(self, prop: Union[str, int]) -> list[dict]:
        """Les flux de données (web, iOS, Android) d'une propriété."""
        name = property_name(prop)
        return self._paged(f"{ADMIN_BASE}/{name}/dataStreams", "dataStreams", name)

    def list_key_events(self, prop: Union[str, int]) -> list[dict]:
        """Les événements clés (ex-« conversions ») configurés sur une propriété."""
        name = property_name(prop)
        return self._paged(f"{ADMIN_BASE}/{name}/keyEvents", "keyEvents", name)

    # --- Data API ------------------------------------------------------------

    def get_metadata(self, prop: Union[str, int]) -> dict:
        """Les dimensions et métriques utilisables sur la propriété (standard et
        personnalisées). `properties/0` rend le catalogue commun à toutes."""
        name = property_name(prop)
        return self._request("GET", f"{DATA_BASE}/{name}/metadata", resource=name)

    def run_report(self, prop: Union[str, int], *,
                   metrics: Optional[Sequence[str]] = None,
                   dimensions: Optional[Sequence[str]] = None,
                   start_date: str = DEFAULT_START_DATE,
                   end_date: str = DEFAULT_END_DATE,
                   dimension_filter: Optional[dict] = None,
                   metric_filter: Optional[dict] = None,
                   order_by: Optional[Sequence[str]] = None,
                   limit: Optional[int] = None, offset: Optional[int] = None,
                   keep_empty_rows: bool = False) -> dict:
        """`:runReport` — réponse brute GA4 (cf. `flatten_report` pour une table).

        Dates : `YYYY-MM-DD`, `today`, `yesterday` ou `NdaysAgo`. Par défaut les 30
        derniers jours complets. Filtres : cf. `build_filter`. Tri : cf.
        `build_order_bys`."""
        name = property_name(prop)
        mets, dims = _names(metrics, "métrique"), _names(dimensions, "dimension")
        if not mets and not dims:
            raise ValueError("Un rapport GA4 demande au moins une métrique ou une dimension.")
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [{"name": d} for d in dims],
            "metrics": [{"name": m} for m in mets],
        }
        body.update(self._common(dimension_filter, metric_filter, order_by, mets, limit))
        if offset:
            body["offset"] = offset
        if keep_empty_rows:
            body["keepEmptyRows"] = True
        return self._request("POST", f"{DATA_BASE}/{name}:runReport", json_body=body,
                             resource=name)

    def run_realtime_report(self, prop: Union[str, int], *,
                            metrics: Optional[Sequence[str]] = None,
                            dimensions: Optional[Sequence[str]] = None,
                            dimension_filter: Optional[dict] = None,
                            metric_filter: Optional[dict] = None,
                            order_by: Optional[Sequence[str]] = None,
                            limit: Optional[int] = None,
                            minutes_ago: Optional[int] = None) -> dict:
        """`:runRealtimeReport` — l'activité des dernières minutes (30 par défaut
        côté GA4, 60 sur GA4 360). `minutes_ago` borne la fenêtre à N minutes."""
        name = property_name(prop)
        mets, dims = _names(metrics, "métrique"), _names(dimensions, "dimension")
        if not mets and not dims:
            raise ValueError("Un rapport temps réel demande au moins une métrique ou une "
                             "dimension.")
        body: dict[str, Any] = {"dimensions": [{"name": d} for d in dims],
                                "metrics": [{"name": m} for m in mets]}
        body.update(self._common(dimension_filter, metric_filter, order_by, mets, limit))
        if minutes_ago is not None:
            if minutes_ago < 1:
                raise ValueError("`minutes_ago` est un nombre de minutes ≥ 1.")
            body["minuteRanges"] = [{"startMinutesAgo": minutes_ago - 1,
                                     "endMinutesAgo": 0}]
        return self._request("POST", f"{DATA_BASE}/{name}:runRealtimeReport",
                             json_body=body, resource=name)

    @staticmethod
    def _common(dimension_filter, metric_filter, order_by, mets, limit) -> dict:
        body: dict[str, Any] = {}
        if dimension_filter is not None:
            body["dimensionFilter"] = build_filter(dimension_filter)
        if metric_filter is not None:
            body["metricFilter"] = build_filter(metric_filter)
        if order_by:
            body["orderBys"] = build_order_bys(order_by, mets)
        if limit is not None:
            if limit < 1:
                raise ValueError("`limit` est un nombre de lignes ≥ 1.")
            body["limit"] = limit
        return body
