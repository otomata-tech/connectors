"""Les refus de ce connecteur — distincts parce qu'ils appellent des gestes distincts.

- autorisation **morte** (révoquée, mot de passe changé, accès retiré) → refaire le
  consentement ;
- consentement **refusé** par Meta (app non publiée, compte non testeur, retour non
  déclaré) → c'est l'exploitant qui règle ;
- appel **limité** (quota Marketing API) → attendre, puis réessayer ;
- tout le reste → l'appel a échoué, réessayer a un sens.

Aucun message d'ici ne nomme d'outil ni d'écran : la lib ne connaît pas sa surface.
"""
from __future__ import annotations

from typing import Optional


class MetaAdsError(RuntimeError):
    """Racine — tout ce que ce connecteur lève lui-même."""


class MetaAdsAuthExpired(MetaAdsError):
    """Le jeton ne vaut plus rien (code Graph 190/102, ou 401)."""


class MetaAdsAuthRefused(MetaAdsError):
    """Meta a refusé le consentement ou l'échange du code. `reason` = type rendu."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class MetaAdsApiError(MetaAdsError):
    """L'appel a échoué. `status` = HTTP, `code`/`subcode` = ceux de Graph.

    Le corps brut n'y entre jamais : l'écho de la requête peut porter un
    identifiant, et ce texte finit dans un transcript d'agent."""

    def __init__(self, message: str, status: Optional[int] = None,
                 code: Optional[int] = None, subcode: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode


class MetaAdsThrottled(MetaAdsApiError):
    """Quota Marketing API atteint (codes 4, 17, 613, 80000-80014…) — attendre."""
