"""1Password provider for oto secret resolution.

Cible de la dépréciation SOPS (oto-core#63) : le coffre d'entreprise 1Password,
déjà source de vérité pour tous les secrets vivants, devient la source pour
`oto` aussi. Contrairement à `SopsProvider`, ce fournisseur ne stocke AUCUN
secret sur disque — seulement des RÉFÉRENCES `op://<coffre>/<item>/<champ>`,
résolues à la demande par le CLI `op` (`op read`).

Fichier de config : `~/.otomata/secrets.1password.yaml`

    cles:
      SLACK_BOT_TOKEN: "op://Otomata/slack-bot/token"
      ...
    ambigues:
      DATABASE_URL:
        - "op://Otomata/movinmotion-db/url"
        - "op://Otomata/tulina-db/url"

`cles` liste une clé -> une référence unique. `ambigues` liste une clé qui
existe dans plusieurs items sans valeur transverse (même sémantique que
`_ambiguous` côté SOPS) : `lookup` lève `AmbiguousSecretError` plutôt que de
choisir arbitrairement l'une des références.

Résolution : un sous-processus `op read --no-newline <ref>` par clé (lecture
PARESSEUSE — rien n'est résolu tant que `lookup` n'est pas appelé), avec un
cache mémoire limité au process (jamais écrit sur disque, jamais journalisé).
`op` coûte environ 1s par appel ; pas de résolution groupée par défaut, la CLI
`oto` ne lit jamais assez de clés d'un coup pour que ça vaille la complexité
de `op inject` (voir la PR pour le détail de ce choix).

Un `op` absent ou qui refuse (session expirée, prompt d'autorisation rejeté)
lève une erreur explicite — JAMAIS de repli silencieux vers un autre
fournisseur : un secret d'entreprise mal résolu doit être bruyant, pas
"MISSING" par accident (c'est précisément le défaut qu'oto-core#63 a fermé
pour les autres fournisseurs).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import MISSING, STORE_ABSENT, AmbiguousSecretError

_cache: Dict[str, str] = {}


class OnePasswordError(RuntimeError):
    """`op` est indisponible ou refuse de résoudre une référence.

    Jamais attrapée pour retomber sur un autre fournisseur : une session `op`
    expirée ou une CLI absente doit être visible, pas confondue avec un
    secret réellement non défini (`MISSING`).
    """


def _config_path(path: Optional[str] = None) -> Path:
    return Path(path).expanduser() if path else Path.home() / ".otomata" / "secrets.1password.yaml"


def _load_table(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse `secrets.1password.yaml`. `None` si le fichier n'existe pas."""
    p = _config_path(path)
    if not p.is_file():
        return None
    import yaml
    with open(p, "r") as f:
        data = yaml.safe_load(f) or {}
    return {
        "cles": {str(k): str(v) for k, v in (data.get("cles") or {}).items()},
        "ambigues": {
            str(k): [str(r) for r in v]
            for k, v in (data.get("ambigues") or {}).items()
        },
    }


def _op_read(ref: str) -> str:
    try:
        result = subprocess.run(
            ["op", "read", "--no-newline", ref],
            capture_output=True, text=True,
        )
    except FileNotFoundError as e:
        raise OnePasswordError(
            "CLI `op` introuvable. Installe 1Password CLI : "
            "https://developer.1password.com/docs/cli/get-started/"
        ) from e
    if result.returncode != 0:
        raise OnePasswordError(
            f"`op read {ref}` a échoué (code {result.returncode}). "
            f"Session `op` expirée ou refusée ? Lance `op signin` puis réessaie.\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout


class OnePasswordProvider:
    """Resolve secrets from 1Password, via des références `op://` seulement."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        cfg = cfg or {}
        self._path = cfg.get("onepassword_file")

    def _table(self) -> Optional[Dict[str, Any]]:
        return _load_table(self._path)

    def lookup(self, name: str) -> object:
        table = self._table()
        if table is None:
            return STORE_ABSENT

        if name in table["ambigues"]:
            refs = table["ambigues"][name]
            raise AmbiguousSecretError(
                f"Secret '{name}' a plusieurs références 1Password sans valeur "
                f"transverse : {', '.join(refs)}. Résous-la directement "
                f"(`op read <ref>`) ou passe-la via l'environnement."
            )

        ref = table["cles"].get(name)
        if ref is None:
            return MISSING

        if ref in _cache:
            return _cache[ref]
        value = _op_read(ref)
        _cache[ref] = value
        return value

    def store_exists(self) -> bool:
        return self._table() is not None


def invalidate_cache() -> None:
    """Force une relecture au prochain `lookup` (utile après un `op signin`)."""
    _cache.clear()


__all__ = ["OnePasswordProvider", "OnePasswordError", "invalidate_cache"]
