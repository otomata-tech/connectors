"""Le client de DONNÉES — comptes publicitaires, arbre campagne → ad set → pub,
insights. LECTURE SEULE.

Il ne connaît que le jeton. Synchrone, comme le reste de la lib.

Le brut de Meta est rendu tel quel ; la seule mise en forme est la pagination,
ramenée à `{data, next_cursor}` — le curseur `paging.cursors.after` n'a de sens
que s'il y a une page suivante (`paging.next`), et le rendre sinon ferait boucler
un appelant sur une page vide.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import requests

from . import _transport
from .config import (
    AD_ACCOUNT_FIELDS,
    DEFAULT_ATTRIBUTION_WINDOWS,
    DEFAULT_FIELDS,
    DEFAULT_INSIGHT_FIELDS,
    GRAPH_API_BASE,
    HTTP_TIMEOUT,
    INSIGHT_LEVELS,
    LEVELS,
)
from .errors import MetaAdsApiError

#: Code Graph « champ ou arête inexistant sur ce nœud ».
_CODE_ARETE_INCONNUE = 100


#: Ce qu'un identifiant Graph peut être, ici : des chiffres (objet, rapport), ou
#: `act_` + des chiffres (compte publicitaire). Rien d'autre ne va dans un chemin :
#: un `/`, un `?` ou un `..` y viserait un AUTRE nœud ou une autre arête — avec le
#: droit `business_management`, une écriture sur le portefeuille du client.
_ID_COMPTE = re.compile(r"^(act_)?\d+$")
_ID_NUMERIQUE = re.compile(r"^\d+$")


def _verifier(valeur: Any, motif: re.Pattern, nom: str, forme: str) -> str:
    v = str(valeur or "").strip()
    if not v:
        raise ValueError(f"{nom} is required.")
    if not motif.fullmatch(v):
        raise ValueError(f"{nom} must be {forme}; got {v!r}.")
    return v


def ad_account_id(valeur: str) -> str:
    """`act_<id>` — Graph exige le préfixe sur un compte publicitaire, et un id nu
    y désignerait un AUTRE nœud (refus opaque « unsupported get request »)."""
    v = _verifier(valeur, _ID_COMPTE, "ad_account_id",
                  "digits, optionally prefixed by act_")
    return v if v.startswith("act_") else f"act_{v}"


def objet_id(valeur: str) -> str:
    """Un objet de l'arbre (campagne, ad set, pub) ou un compte (`act_<id>`)."""
    return _verifier(valeur, _ID_COMPTE, "object_id",
                     "digits (a campaign, ad set or ad id) or act_<digits>")


def rapport_id(valeur: str) -> str:
    """Un rapport d'insights asynchrone : des chiffres seulement."""
    return _verifier(valeur, _ID_NUMERIQUE, "report_run_id", "digits")


def _page(payload: dict) -> dict:
    paging = payload.get("paging") or {}
    suivant = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
    return {"data": payload.get("data") or [], "next_cursor": suivant}


def _json(valeur: Any) -> Optional[str]:
    """Graph attend les paramètres structurés (`time_range`, `filtering`) en JSON."""
    if valeur is None or isinstance(valeur, str):
        return valeur
    return json.dumps(valeur, separators=(",", ":"))


def _csv(valeur: Any) -> Optional[str]:
    if valeur is None or isinstance(valeur, str):
        return valeur
    return ",".join(str(v) for v in valeur)


def _sans_vides(params: dict) -> dict:
    return {k: v for k, v in params.items() if v not in (None, "", [], ())}


class MetaAdsClient:
    """Lecture seule sur les comptes publicitaires que le jeton atteint."""

    def __init__(self, access_token: str, *,
                 session: Optional[requests.Session] = None):
        if not access_token:
            raise ValueError("MetaAdsClient: an access token is required "
                             "(it comes from the consent, `oauth.connect`).")
        self.access_token = access_token
        self._http = session or requests

    def _entetes(self) -> dict:
        # Le jeton part en en-tête, jamais dans l'URL : une URL finit dans les
        # journaux et les messages d'exception.
        return {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, chemin: str, geste: str, **params: Any) -> dict:
        r = self._http.get(f"{GRAPH_API_BASE}/{chemin.lstrip('/')}",
                           params=_sans_vides(params), headers=self._entetes(),
                           timeout=HTTP_TIMEOUT)
        return _transport.lire(r, geste)

    def _post(self, chemin: str, geste: str, **params: Any) -> dict:
        r = self._http.post(f"{GRAPH_API_BASE}/{chemin.lstrip('/')}",
                            data=_sans_vides(params), headers=self._entetes(),
                            timeout=HTTP_TIMEOUT)
        return _transport.lire(r, geste)

    # --- comptes ---------------------------------------------------------------

    def list_ad_accounts(self, limit: int = 50, after: Optional[str] = None,
                         fields: Optional[str] = None) -> dict:
        """Les comptes publicitaires que ce jeton atteint.

        Jeton utilisateur → arête `adaccounts` ; jeton d'utilisateur SYSTÈME (BISU)
        → `assigned_ad_accounts`. On essaie la première, et Graph répond code 100
        sur le mauvais type de nœud : on bascule alors sur l'autre."""
        fields = fields or AD_ACCOUNT_FIELDS
        try:
            res = self._get("me/adaccounts", "listing ad accounts",
                            fields=fields, limit=limit, after=after)
        except MetaAdsApiError as e:
            if e.code != _CODE_ARETE_INCONNUE:
                raise
            res = self._get("me/assigned_ad_accounts", "listing ad accounts",
                            fields=fields, limit=limit, after=after)
        return _page(res)

    # --- l'arbre publicitaire ---------------------------------------------------

    def list_objects(self, ad_account: str, level: str, *,
                     fields: Optional[str] = None,
                     effective_status: Optional[list[str]] = None,
                     filtering: Optional[list[dict]] = None,
                     limit: int = 50, after: Optional[str] = None) -> dict:
        """Campagnes, ad sets ou pubs d'un compte."""
        arete = LEVELS.get(level)
        if not arete:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}.")
        res = self._get(f"{ad_account_id(ad_account)}/{arete}", f"listing {arete}",
                        fields=fields or DEFAULT_FIELDS[level],
                        effective_status=_json(effective_status),
                        filtering=_json(filtering), limit=limit, after=after)
        return _page(res)

    def get_object(self, object_id: str, fields: Optional[str] = None) -> dict:
        """Un objet (campagne, ad set, pub, compte) par son id."""
        return self._get(objet_id(object_id), "reading the object",
                         fields=fields)

    # --- insights --------------------------------------------------------------

    @staticmethod
    def _insight_params(*, level, fields, date_preset, time_range, time_increment,
                        breakdowns, action_attribution_windows, filtering,
                        sort) -> dict:
        if level and level not in INSIGHT_LEVELS:
            raise ValueError(f"level must be one of {', '.join(INSIGHT_LEVELS)}.")
        if date_preset and time_range:
            raise ValueError("Pass date_preset OR time_range, not both.")
        return {
            "level": level,
            "fields": _csv(fields) or DEFAULT_INSIGHT_FIELDS,
            "date_preset": date_preset,
            "time_range": _json(time_range),
            "time_increment": time_increment,
            "breakdowns": _csv(breakdowns),
            "action_attribution_windows": _json(action_attribution_windows),
            "filtering": _json(filtering),
            "sort": _json(sort),
        }

    def get_insights(self, object_id: str, *, level: Optional[str] = None,
                     fields: Any = None, date_preset: Optional[str] = None,
                     time_range: Optional[dict] = None,
                     time_increment: Any = None, breakdowns: Any = None,
                     action_attribution_windows: Optional[list[str]] = None,
                     filtering: Optional[list[dict]] = None,
                     sort: Optional[list[str]] = None,
                     limit: int = 100, after: Optional[str] = None) -> dict:
        """Insights SYNCHRONES. Pour un gros volume, `start_insights_report`."""
        params = self._insight_params(
            level=level, fields=fields, date_preset=date_preset,
            time_range=time_range, time_increment=time_increment,
            breakdowns=breakdowns,
            action_attribution_windows=action_attribution_windows,
            filtering=filtering, sort=sort)
        res = self._get(f"{objet_id(object_id)}/insights",
                        "reading insights",
                        **params, limit=limit, after=after)
        return _page(res)

    def start_insights_report(self, object_id: str, *, level: Optional[str] = None,
                              fields: Any = None, date_preset: Optional[str] = None,
                              time_range: Optional[dict] = None,
                              time_increment: Any = None, breakdowns: Any = None,
                              action_attribution_windows: Optional[list[str]] = None,
                              filtering: Optional[list[dict]] = None,
                              sort: Optional[list[str]] = None) -> str:
        """Lance un rapport ASYNCHRONE ; rend son `report_run_id` (valable 30 jours).

        ⚠️ Meta documente un défaut d'attribution DIFFÉRENT en POST (`7d_view,1d_click`)
        de celui du GET (`7d_click,1d_view`) : on pose celui du GET explicitement,
        sinon le même rapport donne deux chiffres selon le mode."""
        params = self._insight_params(
            level=level, fields=fields, date_preset=date_preset,
            time_range=time_range, time_increment=time_increment,
            breakdowns=breakdowns,
            action_attribution_windows=(action_attribution_windows
                                        or DEFAULT_ATTRIBUTION_WINDOWS),
            filtering=filtering, sort=sort)
        res = self._post(f"{objet_id(object_id)}/insights",
                         "starting an insights report",
                         **params)
        run_id = res.get("report_run_id")
        if not run_id:
            raise MetaAdsApiError("Meta returned no report_run_id for the report.")
        return str(run_id)

    def get_report_status(self, report_run_id: str) -> dict:
        """`async_status` (« Job Completed », « Job Failed »…) et l'avancement."""
        return self._get(rapport_id(report_run_id),
                         "reading the report status",
                         fields="id,async_status,async_percent_completion,"
                                "date_start,date_stop")

    def get_report_insights(self, report_run_id: str, limit: int = 100,
                            after: Optional[str] = None) -> dict:
        res = self._get(f"{rapport_id(report_run_id)}/insights",
                        "reading the report results",
                        limit=limit, after=after)
        return _page(res)
