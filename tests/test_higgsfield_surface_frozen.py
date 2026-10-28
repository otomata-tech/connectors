"""La surface de `HiggsfieldClient` est un CONTRAT, pas un détail.

Un consommateur épingle la lib par tag et importe `oto.tools.higgsfield` : renommer une méthode, changer un défaut ou
déplacer un symbole casse un consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC leurs signatures,
les noms importables et la correspondance entre les fonctions de la description et les méthodes du client ; les trois
méthodes publiques hors description (une génération et son prix, dont le chemin est le modèle) sont nommées.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la constante EN CONNAISSANCE DE CAUSE, jamais
par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.higgsfield as pkg
import oto.tools.higgsfield.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "higgsfield" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_request': "(self, method: 'str', path: 'str', *, body: 'Optional[Dict[str, Any]]' = None, headers: 'Optional[Dict[str, str]]' = None) -> 'Any'",
    'cancel_request': "(self, request_id: 'str') -> 'Any'",
    'create_upload_url': "(self, content_type: 'str') -> 'Any'",
    'delete_request': "(self, request_id: 'str') -> 'Any'",
    'estimate': "(self, model: 'str', params: 'Dict[str, Any]') -> 'Any'",
    'generate': "(self, model: 'str', params: 'Dict[str, Any]', *, idempotency_key: 'Optional[str]' = None, expires_after: 'Optional[int]' = None) -> 'Any'",
    'get_request': "(self, request_id: 'str') -> 'Any'",
    'list_models': "(self) -> 'Any'",
    'probe': "(self) -> 'Any'",
}

# Publiques, hors description : le modèle est le chemin, et le prix se lit dans deux formes de réponse.
NOT_DESCRIBED = {"probe", "estimate", "generate"}


def _members():
    return {n: str(inspect.signature(getattr(mod.HiggsfieldClient, n)))
            for n in dir(mod.HiggsfieldClient)
            if not n.startswith("__") and callable(getattr(mod.HiggsfieldClient, n))}


def _functions():
    return {f["name"]: f for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_constructeur_fige():
    assert str(inspect.signature(mod.HiggsfieldClient.__init__)) == "(self, api_key: 'Optional[str]' = None)"


def test_noms_importables():
    assert pkg.__all__ == ["HiggsfieldClient", "price_of"]
    assert pkg.HiggsfieldClient is mod.HiggsfieldClient
    assert pkg.price_of is mod.price_of
    assert str(inspect.signature(mod.price_of)) == "(estimate: 'Any', params: 'Dict[str, Any]') -> 'Dict[str, Any]'"
    assert mod.BASE_URL == "https://api.higgsfield.ai"


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert set(_functions()) == public - NOT_DESCRIBED


def test_modules_sous_500_lignes():
    for path in (ROOT / "oto" / "tools" / "higgsfield").glob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path.name
