"""Jev (TypeSafe) — un modèle de DÉCISION, pas un modèle de texte.

On lui envoie un **état** et des **questions typées** ; il rend des réponses typées
avec leurs probabilités. Aucun texte, aucune trace de raisonnement, aucun appel
d'outil : il remplace le « je demande au modèle et je parse sa réponse », pas le
modèle qui mène le travail.

Trois primitives :
- **noul**   : la condition tient-elle ? → `{"noul": 0.96}` (probabilité du oui) ;
- **choice** : laquelle de ces options ? → option retenue, probabilité par option,
  confiance ;
- **score**  : où sur cette échelle ordonnée ? → position pondérée, probabilité par
  échelon, légende.

Facturation : **l'entrée seule est facturée, la sortie est gratuite** ; l'état est
facturé UNE FOIS par requête et chaque question de plus ne coûte que ~48 jetons — poser toute la grille en un appel est donc la bonne façon de
s'en servir. Chaque réponse porte `usage.cost`, le coût RÉEL en dollars : c'est lui
qui sert de base de facturation en aval, jamais un barème recopié qui vieillirait.

Aujourd'hui l'accès passe par OpenRouter (`/v1/systemone`) ; `base_url` et `path`
sont surchargeables pour basculer un jour sur l'API TypeSafe en direct sans
réécrire ce client — le schéma de requête et de réponse est le même des deux côtés.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ...config import require_secret
from ..common import raise_for_upstream

#: Les trois primitives connues. Une question d'un autre type est refusée ICI :
#: le dire avant l'appel évite de facturer un aller-retour pour une faute de frappe.
TYPES = ("noul", "choice", "score")

#: Le SNAPSHOT daté, pas l'id nu. `typesafe/jev-1.13` résout vers la version courante :
#: un seuil calibré sur une version bougerait sous nous à la prochaine.
DEFAULT_MODEL = "typesafe/jev-1.13-20260917"

#: Les seuls éditeurs de modèle que ce client accepte. La promesse du connecteur (une
#: décision typée, jamais un modèle de texte) est tenue ICI, par construction : aucun
#: modèle d'un autre éditeur ne part sur la clé sans que quelqu'un l'ait décidé.
EDITEURS_SERVIS = ("typesafe/", "~typesafe/")


class JevClient:
    """Client Jev (System One / Decisions), auth Bearer OpenRouter `sk-or-…`."""

    BASE_URL = "https://openrouter.ai/api"
    PATH = "/v1/systemone"

    def __init__(self, api_key: str = None, base_url: str = None, path: str = None):
        """
        Args:
            api_key: clé OpenRouter (ou variable d'env `OPENROUTER_API_KEY`).
            base_url: racine de l'API (défaut OpenRouter ; le jour où Jev se prend en
                direct chez TypeSafe, c'est le seul réglage à changer).
            path: chemin de la requête de décision sous cette racine.
        """
        self.api_key = api_key or require_secret("OPENROUTER_API_KEY")
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.path = path or self.PATH
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _post(self, body: Dict[str, Any], *, timeout: int = 60) -> Dict[str, Any]:
        # Convention du dépôt : (connexion, lecture). Un hôte injoignable ne doit pas
        # consommer le budget de lecture entier.
        resp = self.session.post(f"{self.base_url}{self.path}", json=body,
                                 timeout=(10, timeout))
        raise_for_upstream(resp, service="jev")
        return resp.json() if resp.content else {}

    # --- la seule surface ---------------------------------------------------

    def decide(self, state: Dict[str, Any], questions: Dict[str, Any],
               model: Optional[str] = None, *, timeout: int = 60) -> Dict[str, Any]:
        """UNE décision : un état, N questions posées ensemble.

        ⚠️ Les questions d'une même requête sont répondues **en parallèle et ne se
        voient pas l'une l'autre** : une question ne peut pas s'appuyer sur la réponse
        d'une autre. Un enchaînement demande deux appels.

        Args:
            state: le contexte à juger (objet plat ou imbriqué, ≤ 32 000 jetons avec
                les questions).
            questions: `{nom: {"type": "noul|choice|score", "instructions": str,
                "criteria": …}}`. `criteria` est un dict pour `noul` (clés `true` /
                `false`) et `choice` (une clé par option), une LISTE ordonnée pour
                `score`.
            model: un autre id de modèle (défaut : le snapshot daté).
            timeout: budget de lecture, en secondes.

        Returns:
            Le corps de la réponse : `{id, model, provider, answers, usage}` —
            `usage` porte `input_tokens`, `output_tokens` et `cost` (USD réel).
            `model` nomme le snapshot qui a RÉELLEMENT servi : à conserver à côté de
            la réponse, c'est ce qui rend un seuil reproductible.

        Raises:
            ValueError: question sans type connu, ou sans `criteria`.
            UpstreamHTTPError: refus de l'amont (400 grille invalide ou état au-delà
                de 32 000 jetons, 401 clé, 402 crédits épuisés, 429, 5xx).
        """
        nom = model or DEFAULT_MODEL
        self.check_model(nom)
        self.check_questions(questions)
        return self._post({"model": nom, "state": state, "questions": questions},
                          timeout=timeout)

    # --- gardes -------------------------------------------------------------

    @staticmethod
    def check_model(model: str) -> None:
        """Refuse un modèle qui n'est pas un modèle de décision TypeSafe.

        Ce client sert UNE route (`/v1/systemone`) et UN éditeur : ce n'est pas un
        passe-plat vers le catalogue du fournisseur. La garde porte sur l'éditeur, pas
        sur une liste de modèles qui vieillirait en silence."""
        if not str(model).startswith(EDITEURS_SERVIS):
            raise ValueError(
                f"modèle {model!r} : ce connecteur ne sert que les modèles de décision "
                f"TypeSafe ({' ou '.join(EDITEURS_SERVIS)}…). Un modèle de texte ne "
                "répond pas à une question typée.")

    # --- garde de forme -----------------------------------------------------

    @staticmethod
    def check_questions(questions: Dict[str, Any]) -> None:
        """Refuse une grille mal formée AVANT l'appel, en nommant la question fautive.

        ⚠️ **`criteria` est exigé ici** : sans lui, la question n'a pas de sens défini,
        et une probabilité rendue sur une telle question aurait l'air d'une réponse
        sans en être une. Ce client est le seul endroit où l'exiger se voit.
        """
        if not isinstance(questions, dict) or not questions:
            raise ValueError("`questions` : au moins une question est attendue")
        for nom, q in questions.items():
            if not isinstance(q, dict):
                raise ValueError(f"question {nom!r} : un objet est attendu")
            t = q.get("type")
            if t not in TYPES:
                raise ValueError(
                    f"question {nom!r} : type {t!r} inconnu — attendu "
                    f"{' | '.join(TYPES)}")
            if not q.get("instructions"):
                raise ValueError(f"question {nom!r} : `instructions` est vide")
            crit = q.get("criteria")
            if t == "score":
                if not isinstance(crit, list) or len(crit) < 2:
                    raise ValueError(
                        f"question {nom!r} (score) : `criteria` est une LISTE ordonnée "
                        "d'au moins deux échelons")
            elif not isinstance(crit, dict) or len(crit) < 2:
                raise ValueError(
                    f"question {nom!r} ({t}) : `criteria` attend au moins deux entrées "
                    + ("(`true` et `false`)" if t == "noul" else "(une par option)"))
