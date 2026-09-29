"""Client Planity (agenda + caisse d'un salon) — LECTURE SEULE.

`PlanityClient` est le point d'entrée : il s'authentifie avec l'email et le mot de
passe du compte, tient son jeton à jour, et sert le référentiel, les clientes,
l'agenda et les chiffres. Les modules voisins portent chacun un transport ; c'est
un détail d'implémentation, pas une surface.

Tout est **asynchrone**, et ce n'est pas un choix de style : l'amont l'impose, et
il n'y a pas d'équivalent synchrone à écrire.

⚠️ **Les coordonnées de Planity ne sont PAS ici.** `PlanityClient` exige un
`PlanityEndpoints` (clé d'API Firebase, App ID, racine des lambdas REST), sans
valeur par défaut : ce dépôt est public, un client qu'on y publie décrit un
protocole et n'embarque pas les constantes d'une entreprise tierce en dur. Elles
sont publiques par conception — tout navigateur qui ouvre `pro.planity.com` les
reçoit — donc les sortir d'ici n'est pas un geste de secret, c'en est un de
généricité : celui qui déploie le connecteur les pose, et répond de ce qu'il
appelle.

Aucune écriture n'est exposée : pas de création ni de modification de rendez-vous.
"""
from __future__ import annotations

#: Les modules que l'extra `planity` apporte, et rien d'autre. Nommés ici parce
#: que c'est le seul endroit qui sait POURQUOI ils manquent.
_MODULES_DE_L_EXTRA = ("httpx", "websockets")


def _refus_d_extra(e: ImportError) -> "ImportError | None":
    """L'erreur à lever À LA PLACE quand c'est l'extra qui manque — sinon `None`.

    Sans elle, un installateur sans l'extra rend « No module named 'httpx' ». Ce
    message est vrai et parfaitement inutile : il n'a jamais fait installer un
    extra à personne, et il ne dit pas que le connecteur est le seul concerné.
    Chez le consommateur (oto-backend), il devient une ligne de journal
    « planity tools disabled: No module named 'httpx' » sur laquelle on cherche un
    bug d'import pendant vingt minutes.

    La retraduction vit ICI, à l'origine, et pas chez le consommateur : ils sont
    plusieurs (oto-backend, un installateur qui essaie), et une règle
    posée chez l'un ne protège pas les autres.

    ⚠️ Elle ne s'applique QU'aux deux modules de l'extra. Un `ImportError` interne
    — un module du paquet renommé, un import circulaire — doit remonter tel quel :
    le déguiser en « installe l'extra » enverrait chercher la panne à l'opposé
    d'où elle est.
    """
    if getattr(e, "name", None) not in _MODULES_DE_L_EXTRA:
        return None
    return ImportError(
        f"le connecteur `planity` a besoin de l'extra du même nom — installe "
        f"`oto-core[planity]` (il manque `{e.name}`). Le cœur Planity parle le "
        f"protocole WebSocket du Realtime Database de Firebase et fait ses appels "
        f"en asynchrone : ni l'un ni l'autre n'existe dans `requests`, le socle du "
        f"reste de la lib — d'où un extra plutôt qu'une dépendance pour tous.")


try:
    from . import appointments, pos, services, stock
    from .auth import PlanityAuth, PlanityTokens
    from .client import Employee, PlanityClient, SalonInfo
    from .config import PlanityEndpoints
    from .date_range import ms_to_iso, resolve_range
except ImportError as _e:
    _refus = _refus_d_extra(_e)
    if _refus is None:
        raise
    raise _refus from _e

__all__ = [
    "Employee",
    "appointments",
    "pos",
    "services",
    "stock",
    "PlanityAuth",
    "PlanityClient",
    "PlanityEndpoints",
    "PlanityTokens",
    "SalonInfo",
    "ms_to_iso",
    "resolve_range",
]
